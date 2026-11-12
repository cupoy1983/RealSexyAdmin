"""刷单核对（ADR-0028，§5.1 第7-9点）。

订单备注中的刷单标记必须与钉钉「销量权重」审批明细按订单号+金额逐笔核对（精确到分），
不是汇总金额对比——一条审批可能包含多个订单，必须展开到明细行级别逐一匹配，否则
会把 A 订单的刷单金额错配给 B 订单却因为汇总金额碰巧相等而被误判为匹配成功。

任何一笔存在缺失/重复/金额不一致都是「重大错误」，阻断整个月份结算（不是只阻断这一笔
订单）；刷单退款核对是单向条件：存在正向审批则必须有金额精确匹配的负向审批，不存在
正向审批则不要求负向审批；部分退款永远是重大错误。
"""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from .model import OrderModel
from .schema import ScamApprovalDetailSchema, ScamReconciliationResultSchema

MATCH_STATUS_MATCHED = "matched"
MATCH_STATUS_MISSING_APPROVAL = "missing_approval"
MATCH_STATUS_DUPLICATE_APPROVAL = "duplicate_approval"
MATCH_STATUS_AMOUNT_MISMATCH = "amount_mismatch"
MATCH_STATUS_MISSING_NEGATIVE = "missing_negative"
MATCH_STATUS_PARTIAL_REFUND_ERROR = "partial_refund_error"

# 阻断结算的「重大错误」状态集合（不含 matched / not_shua 场景）
MAJOR_ERROR_STATUSES: frozenset[str] = frozenset(
    {
        MATCH_STATUS_MISSING_APPROVAL,
        MATCH_STATUS_DUPLICATE_APPROVAL,
        MATCH_STATUS_AMOUNT_MISMATCH,
        MATCH_STATUS_MISSING_NEGATIVE,
        MATCH_STATUS_PARTIAL_REFUND_ERROR,
    }
)


def _order_platform_no(order: OrderModel) -> str:
    """主订单的平台订单号（平台订单号已下沉到子订单级，取首个子订单的值用于审批匹配）。

    注意：刷单核对逻辑待需求方最终确认（§5.1 第10点），此处为保持可运行的最小实现。
    """
    for d in order.details:
        if d.platform_order_no:
            return d.platform_order_no
    return ""


def reconcile_orders(
    orders: Sequence[OrderModel],
    approval_details: list[ScamApprovalDetailSchema],
) -> list[ScamReconciliationResultSchema]:
    """对一批标注刷单的订单逐笔核对钉钉销量权重审批明细。

    只处理 ``is_shua_order=True`` 的订单；非刷单订单不产生核对结果。

    参数:
    - orders (Sequence[OrderModel]): 待核对订单（应已筛选 is_shua_order=True，并预加载 details）
    - approval_details (list[ScamApprovalDetailSchema]): 本月销量权重审批明细（正向/负向混合）

    返回:
    - list[ScamReconciliationResultSchema]: 每笔刷单订单的核对结果
    """
    approvals_by_order: dict[str, list[ScamApprovalDetailSchema]] = defaultdict(list)
    for detail in approval_details:
        approvals_by_order[detail.platform_order_no].append(detail)

    results: list[ScamReconciliationResultSchema] = []
    for order in orders:
        if not order.is_shua_order:
            continue
        results.append(_reconcile_single_order(order, approvals_by_order.get(_order_platform_no(order), [])))
    return results


def _reconcile_single_order(
    order: OrderModel,
    matched_details: list[ScamApprovalDetailSchema],
) -> ScamReconciliationResultSchema:
    """核对单笔刷单订单。"""
    positive_details = [d for d in matched_details if d.amount > 0]
    negative_details = [d for d in matched_details if d.amount < 0]

    order_amount: Decimal = order.buyer_paid_amount
    platform_order_no = _order_platform_no(order)

    # 部分退款是重大错误，与审批匹配情况无关——先判定，不被后续逻辑掩盖
    if order.refund_status == "partial":
        return ScamReconciliationResultSchema(
            order_id=order.id,
            system_order_no=order.system_order_no,
            platform_order_no=platform_order_no,
            order_amount=order_amount,
            positive_approval_amount=positive_details[0].amount if positive_details else None,
            negative_approval_amount=negative_details[0].amount if negative_details else None,
            match_status=MATCH_STATUS_PARTIAL_REFUND_ERROR,
            reason="刷单订单发生部分退款，属重大错误",
        )

    if not positive_details:
        return ScamReconciliationResultSchema(
            order_id=order.id,
            system_order_no=order.system_order_no,
            platform_order_no=platform_order_no,
            order_amount=order_amount,
            positive_approval_amount=None,
            negative_approval_amount=negative_details[0].amount if negative_details else None,
            match_status=MATCH_STATUS_MISSING_APPROVAL,
            reason="缺少正向销量权重审批",
        )

    if len(positive_details) > 1:
        return ScamReconciliationResultSchema(
            order_id=order.id,
            system_order_no=order.system_order_no,
            platform_order_no=platform_order_no,
            order_amount=order_amount,
            positive_approval_amount=positive_details[0].amount,
            negative_approval_amount=negative_details[0].amount if negative_details else None,
            match_status=MATCH_STATUS_DUPLICATE_APPROVAL,
            reason=f"存在 {len(positive_details)} 条正向销量权重审批，重复核对",
        )

    positive_amount = positive_details[0].amount
    if positive_amount != order_amount:
        return ScamReconciliationResultSchema(
            order_id=order.id,
            system_order_no=order.system_order_no,
            platform_order_no=platform_order_no,
            order_amount=order_amount,
            positive_approval_amount=positive_amount,
            negative_approval_amount=negative_details[0].amount if negative_details else None,
            match_status=MATCH_STATUS_AMOUNT_MISMATCH,
            reason=f"审批金额 {positive_amount} 与订单金额 {order_amount} 不一致",
        )

    # 存在正向审批且金额精确匹配：整单退款时必须有金额精确匹配的负向审批
    if order.refund_status == "full":
        expected_negative = -positive_amount
        negative_match = next((d for d in negative_details if d.amount == expected_negative), None)
        if negative_match is None:
            return ScamReconciliationResultSchema(
                order_id=order.id,
                system_order_no=order.system_order_no,
                platform_order_no=platform_order_no,
                order_amount=order_amount,
                positive_approval_amount=positive_amount,
                negative_approval_amount=negative_details[0].amount if negative_details else None,
                match_status=MATCH_STATUS_MISSING_NEGATIVE,
                reason="刷单订单整单退款，但缺少金额精确匹配的负向销量权重审批",
            )
        return ScamReconciliationResultSchema(
            order_id=order.id,
            system_order_no=order.system_order_no,
            platform_order_no=platform_order_no,
            order_amount=order_amount,
            positive_approval_amount=positive_amount,
            negative_approval_amount=negative_match.amount,
            match_status=MATCH_STATUS_MATCHED,
            reason=None,
        )

    return ScamReconciliationResultSchema(
        order_id=order.id,
        system_order_no=order.system_order_no,
        platform_order_no=platform_order_no,
        order_amount=order_amount,
        positive_approval_amount=positive_amount,
        negative_approval_amount=negative_details[0].amount if negative_details else None,
        match_status=MATCH_STATUS_MATCHED,
        reason=None,
    )



def has_major_error(results: list[ScamReconciliationResultSchema]) -> bool:
    """判断本批核对结果中是否存在任意一条「重大错误」。

    供 profit 子域结算门禁读取：阻断范围是整个月份，不是按店铺/订单维度局部判断。
    """
    return any(r.match_status in MAJOR_ERROR_STATUSES for r in results)
