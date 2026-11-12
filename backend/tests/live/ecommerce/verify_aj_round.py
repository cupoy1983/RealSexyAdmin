"""AJ 轮（「重新订正」整条删除／已恢复是终点）后端＋静态验证。

全程在一条事务里写、结尾显式回滚，库里不留东西；预期失败的调用套在 SAVEPOINT 里，
和真实请求级事务（`app/core/dependencies.py` 的 `session.begin()`）同构——报错即整笔退回。

覆盖设计文档 §23：
① 三个只服务于「退回上一次订正」的函数真的没了，代码里没有任何地方再解析留痕文字；
② 一条账单行只有一次认领机会：已恢复行再带单号进来直接被拒，且**一条数据都不改**；
③ 已恢复行仍然不许删、不许不带单号再改对（终点，不是可反复的单行道）；
④ 闸的顺序（§23.1 末段）：未确认批次报「已经是已恢复」，已确认批次报「批次已确认，一律不动」；
⑤ 前端组件与 api 注释里「重新订正」的分支确实清干净了（读文件比对；弹窗交互跑不起来的那部分不测）。

测试数据用 2020 年这两个月：真实账期在 2026，AI 用 2019，AB/CV 用 2021，互不撞批次号。
"""

import asyncio
import io
import sys
from decimal import Decimal
from pathlib import Path

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.logistics import service as logistics_service  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import (  # noqa: E402
    BillColumnMapping,
    BillParseRuleCreateSchema,
)
from app.plugin.module_ecommerce.logistics.service import (  # noqa: E402
    BillParseRuleService,
    LogisticsService,
    _claim_marker_key,
)
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []

RULE = "ajtest"
SHOP = "AJSHOP"
MAIN = "AJTESTMAIN"
M1, M2 = "2020-05", "2020-06"
B1 = f"bill-{RULE}-{M1}"
REPO_ROOT = Path(__file__).resolve().parents[4]
ORDER_HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号", "重量kg(SKU)",
]
BILL_HEADERS = ["运单号", "账单日期", "账单重量", "其它费用", "收件省"]


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    same = actual == expected
    (ok if same else bad)(cid, "符合" if same else f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, "符合" if cond else detail)


class BytesUpload:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.filename = "aj.xlsx"

    async def read(self) -> bytes:
        return self.data

    async def close(self) -> None:
        return None


def xlsx(headers: list[str], rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def order_row(idx: int, did: str, order_no: str, tracking: str | None, weight: str, day: str,
              erp: str = "ERP发货", qty: int = 1) -> list:
    return [str(idx), did, order_no, SHOP, "AJ测试店铺", "AJ淘宝", "已完成",
            MAIN, None, "AJ测试商品", "10.00", "0.00", "0.00", qty, 0,
            f"{day} 10:00:00", erp, tracking, weight]


def summary_row(paid: str) -> list:
    return ["汇总", None, None, None, None, None, None, None, None, None, paid, "0.00", "0.00",
            None, None, None, None, None, None]


def bill_row(waybill: str, day: str, weight: str, other: str) -> list:
    return [waybill, day, weight, other, "浙江"]


async def main() -> None:
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()

    async def did_of(source_id: str) -> int:
        return (await session.execute(select(OrderDetailModel.id).where(
            OrderDetailModel.source_detail_id == source_id))).scalar_one()

    async def detail_of(source_id: str) -> OrderDetailModel:
        return (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.source_detail_id == source_id))).scalar_one()

    async def bill_of(waybill: str, status: str | None = None) -> CourierBillModel:
        stmt = select(CourierBillModel).where(CourierBillModel.waybill_no == waybill).order_by(CourierBillModel.id)
        if status:
            stmt = stmt.where(CourierBillModel.status == status)
        return (await session.execute(stmt)).scalars().first()

    async def bid_of(waybill: str, status: str | None = None) -> int:
        return (await bill_of(waybill, status)).id

    async def snap_orders() -> list:
        return sorted((await session.execute(
            select(OrderDetailModel.source_detail_id, OrderDetailModel.tracking_no,
                   OrderDetailModel.update_remark, OrderDetailModel.freight_cost)
            .where(OrderDetailModel.source_detail_id.like("AJD%"))
        )).all())

    async def snap_bills() -> list:
        return sorted((await session.execute(
            select(CourierBillModel.waybill_no, CourierBillModel.id, CourierBillModel.status,
                   CourierBillModel.actual_freight, CourierBillModel.anomaly_remark, CourierBillModel.is_deleted)
            .where(CourierBillModel.waybill_no.like("AJW%"))
        )).all())

    async def expect(cid: str, contains: str, call) -> None:
        """跑一次注定失败的调用：报错方向要对，且两张表一条都不许变（整笔拒绝、半写不留）。"""
        before_o, before_b = await snap_orders(), await snap_bills()
        try:
            async with session.begin_nested():
                await call()
            bad(cid, f"预期报错「{contains}」，结果没报错")
            return
        except CustomException as exc:
            msg = str(exc).replace("\n", " ")
            truthy(cid, contains in msg, f"报错方向不对：期望含「{contains}」，实际 {msg[:150]}")
        except Exception as exc:  # noqa: BLE001
            bad(cid, f"抛错了类型：{type(exc).__name__}: {str(exc)[:200]}")
            return
        eq(f"{cid} ‖ 不改任何东西", (await snap_orders(), await snap_bills()), (before_o, before_b))

    try:
        orders, sku = OrderService(auth, session), SkuCostService(auth, session)
        rules, bills = BillParseRuleService(auth, session), LogisticsService(auth, session)

        # ══ A 组：撤销那条线整条拆干净了（§23.2 删除清单）══════════════════════
        src = (REPO_ROOT / "backend/app/plugin/module_ecommerce/logistics/service.py").read_text("utf-8")
        for name in ("_unclaim_previous", "_claim_revert_marker", "_claim_previous_value"):
            truthy(f"AJ01-{name} 服务层里再也没有这个名字",
                   not hasattr(logistics_service, name) and not hasattr(LogisticsService, name) and name not in src,
                   f"{name} 仍存在（模块={hasattr(logistics_service, name)} 类={hasattr(LogisticsService, name)}）")
        truthy("AJ02 服务层代码里撤销那条线拆干净（撤回标记文案与 was_restored 分支都没了）",
               all(k not in src for k in ("撤回本票写入", "was_restored", "_claim_revert_marker")),
               f"src 残留：撤回={'撤回本票写入' in src} was_restored={'was_restored' in src}")
        truthy("AJ03 留痕标记常量仍在（它降级成只写不读的人工记录）",
               logistics_service._CLAIM_MARKER_HEAD == "账单订正写入快递单号"
               and hasattr(logistics_service, "_claim_marker"),
               "标记常量或 _claim_marker 不见了")

        vue = (REPO_ROOT / "frontend/web/src/views/module_ecommerce/logistics/components/BillDetailTab.vue").read_text("utf-8")
        api = (REPO_ROOT / "frontend/web/src/api/module_ecommerce/logistics.ts").read_text("utf-8")
        truthy("AJ04 账单详情组件里没有「重新订正」／canRestore／restored 分支",
               all(k not in vue for k in ("重新订正", "canRestore", 'status === "restored"')),
               f"vue 残留：重新订正={'重新订正' in vue} canRestore={'canRestore' in vue}")
        truthy("AJ05 api 注释同步（写明已恢复是终点）",
               "重新订正" not in api and "已恢复是终点" in api, "logistics.ts 注释没跟上")

        # ══ 建档：SKU 成本 + 解析规则 + 订单 + 账单 ═════════════════════════════
        await sku.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="AJ测试商品",
                                            cost_price=Decimal("2.00")))
        await rules.create(BillParseRuleCreateSchema(
            bill_type=RULE, carrier_name="AJ测试快递",
            column_mapping=BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                             bill_other_fee="其它费用", receiver_province="收件省"),
            waybill_fee=Decimal("1.00")))

        await orders.import_orders(ship_month=M1, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AJD1A", "AJO-1", None, "1.0", "2020-05-05"),
            order_row(2, "AJD1B", "AJO-1", None, "3.0", "2020-05-05"),
            summary_row("20.00")])))
        await orders.import_orders(ship_month=M2, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AJD2A", "AJO-2", "AJWWX", "1.0", "2020-06-05"),
            order_row(2, "AJD2B", "AJO-2", "AJWWZ", "0.1", "2020-06-05"),
            order_row(3, "AJD3A", "AJO-3", None, "1.0", "2020-06-06"),
            summary_row("30.00")])))

        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AJWV1", "2020-05-11", "4.0", "9.00")])), bill_type=RULE, bill_month=M1)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AJWWX", "2020-06-11", "1.0", "0.00"),
            bill_row("AJWWX", "2020-06-11", "1.0", "0.00"),
            bill_row("AJWWZ", "2020-06-12", "10.0", "0.00"),
            bill_row("AJWVD", "2020-06-13", "1.0", "0.00")])), bill_type=RULE, bill_month=M2)
        await session.flush()

        v1_id = await bid_of("AJWV1")
        wx_id = await bid_of("AJWWX")
        dup_id = await bid_of("AJWWX", "duplicate")
        wz_id = await bid_of("AJWWZ")
        vd_id = await bid_of("AJWVD")
        d1a, d1b = await did_of("AJD1A"), await did_of("AJD1B")
        d2a, d3a = await did_of("AJD2A"), await did_of("AJD3A")

        eq("AJ06 前置：挂不上=虚假、同票第二行=重复、超称=重量省份异常",
           [(await bill_of("AJWV1")).status, (await bill_of("AJWWX")).status,
            (await bill_of("AJWWX", "duplicate")).status, (await bill_of("AJWWZ")).status],
           ["fake", "matched", "duplicate", "weight_province"])

        # ══ B 组：认领一次就到底（§23.1「虚假」那一行）═════════════════════════
        await bills.restore_bill_rows([v1_id], remark="发货漏填单号", system_order_no="AJO-1",
                                      detail_ids=sorted([d1a, d1b]))
        await session.flush()
        row_v1 = await bill_of("AJWV1")
        got_a, got_b = await detail_of("AJD1A"), await detail_of("AJD1B")
        eq("AJ07 认领成功：状态=已恢复、运费=面单费+其它费用",
           (row_v1.status, str(row_v1.actual_freight)), ("restored", "10.00"))
        eq("AJ08 运单号写进了勾选的两条子订单行",
           (got_a.tracking_no, got_b.tracking_no), ("AJWV1", "AJWV1"))
        truthy("AJ09 留痕照写（批次+行ID+原值），但它是给人看的记录、不是撤销凭据",
               f"{_claim_marker_key(row_v1)}空；" in (got_a.update_remark or ""),
               f"update_remark={got_a.update_remark}")
        await expect("AJ10 已恢复行再带单号＝直接拒（这里没有「重新订正」了）", "已经是「已恢复」",
                     lambda: bills.restore_bill_rows([v1_id], remark="想换一行", system_order_no="AJO-1",
                                                     detail_ids=[d1b]))
        await expect("AJ11 已恢复行不带单号再「改对」也不给（终点，不是可反复的状态）",
                     "只有虚假/重复行可以删除或改对",
                     lambda: bills.restore_bill_rows([v1_id], remark="想再改一次"))
        await expect("AJ12 已恢复行不许删（§22.5 那条不扩的口子仍在）", "只有虚假/重复行可以删除",
                     lambda: bills.delete_bill_rows([v1_id]))

        # ══ C 组：确认批次，然后闸的顺序（§23.1 末段）══════════════════════════
        result = await bills.confirm_bill_batch(B1)
        await session.flush()
        shares = sorted((await session.execute(
            select(OrderDetailModel.source_detail_id, OrderDetailModel.freight_cost)
            .where(OrderDetailModel.source_detail_id.in_(["AJD1A", "AJD1B"]))
        )).all())
        eq("AJ13 确认后 10.00 按重量 1:3 摊进勾选的两条子订单",
           [(s, str(f)) for s, f in shares], [("AJD1A", "2.50"), ("AJD1B", "7.50")])
        head_total = str((await session.execute(select(OrderModel.freight_cost_amount).where(
            OrderModel.system_order_no == "AJO-1"))).scalar_one())
        truthy("AJ14 主订单运费合计跟着刷新", head_total == "10.00", f"实际合计={head_total}")
        truthy("AJ15 确认结果照常回写了这一票", "运费已回写子订单 1 票" in result.message, result.message)
        await expect("AJ16 已确认批次里的已恢复行：报「批次已确认，一律不动」（更硬的理由先赢）",
                     "批次已确认，一律不动",
                     lambda: bills.restore_bill_rows([v1_id], system_order_no="AJO-1", detail_ids=[d1a]))

        # ══ D 组：M2 那四种状态各就各位 ════════════════════════════════════════
        await expect("AJ17 已匹配行不能认领（报错只说「虚假」，不再捎上已恢复）", "只有虚假行可以认领改对",
                     lambda: bills.restore_bill_rows([wx_id], system_order_no="AJO-3", detail_ids=[d3a]))
        await expect("AJ18 重量省份异常行不能认领（它只提示核对）", "只有虚假行可以认领改对",
                     lambda: bills.restore_bill_rows([wz_id], system_order_no="AJO-3", detail_ids=[d3a]))
        await bills.restore_bill_rows([vd_id], remark="代发单号漏填", system_order_no="AJO-3", detail_ids=[d3a])
        await session.flush()
        await expect("AJ19 虚假行改对后再带单号＝拒绝；未确认批次里说的是「人工判定过就是终点」",
                     "已经是「已恢复」",
                     lambda: bills.restore_bill_rows([vd_id], system_order_no="AJO-2", detail_ids=[d2a]))
        n_dup = await bills.restore_bill_rows([dup_id], remark="重复行直接改对")
        await session.flush()
        eq("AJ20 重复行免认领照样能改对（本轮唯一保留的免填单号入口）",
           (n_dup, (await bill_of("AJWWX", "restored")).status), (1, "restored"))
        await expect("AJ21 从重复改来的已恢复行也不能再认领（先撞终点闸，撞不到「重复行」那句）",
                     "已经是「已恢复」",
                     lambda: bills.restore_bill_rows([dup_id], system_order_no="AJO-2", detail_ids=[d2a]))
        after = await snap_bills()
        eq("AJ22 一路拒下来账单侧什么都没动（状态/金额逐字对得上）",
           [(w, b, st, str(f)) for w, b, st, f, _, _ in after],
           [("AJWV1", v1_id, "restored", "10.00"), ("AJWVD", vd_id, "restored", "1.00"),
            ("AJWWX", wx_id, "matched", "1.00"), ("AJWWX", dup_id, "restored", "1.00"),
            ("AJWWZ", wz_id, "weight_province", "1.00")])
        eq("AJ23 订单侧也只有被认领的那几条带上单号，其余逐字未动",
           [(s, t) for s, t, _, _ in await snap_orders()],
           [("AJD1A", "AJWV1"), ("AJD1B", "AJWV1"), ("AJD2A", "AJWWX"),
            ("AJD2B", "AJWWZ"), ("AJD3A", "AJWVD")])

        await session.rollback()
        print()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:500]}")
    finally:
        await session.rollback()
        await session.close()

    async with async_db_session() as probe:
        left = (await probe.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no LIKE 'AJW%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order_detail WHERE source_detail_id LIKE 'AJD%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'AJO%') "
            "+ (SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no LIKE 'bill-ajtest%' OR batch_no LIKE 'order-2020%') "
            "+ (SELECT COUNT(*) FROM ecommerce_bill_parse_rule WHERE bill_type LIKE 'ajtest%') "
            "+ (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'AJTEST%')"))).scalar()
        (ok if left == 0 else bad)("AJ24 回滚干净（库里一行没留）", f"残留 {left} 行" if left else "0")

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
