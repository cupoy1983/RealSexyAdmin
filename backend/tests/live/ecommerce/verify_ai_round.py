"""AI 轮（认领订单＝把运单号订正回子订单行 / 「改回虚假」整条删除 / 删到空连带批次）后端验证。

全程在一条事务里写、结尾显式回滚，库里不留东西；预期失败的调用套在 SAVEPOINT 里，
和真实请求级事务（`app/core/dependencies.py` 的 `session.begin()`）同构——报错即整笔退回。

覆盖设计文档 §22.8 的 ①~⑦、§26.5 的乙（I 组）与 §26.6 的四条（K 组）：
① 认领写入后账单列表那两格立刻有内容；
② 四道闸逐条拦得住，而且**一条订单数据都不改**；
③ ~~重新订正~~（AJ 轮把这条功能整条删了，见 §23；改由 `verify_aj_round.py` 断言「已恢复行再进来直接被拒」）；
④ 认领完再确认批次，钱摊得下去（§22.1 那条死路真的通了）；
⑤ 已恢复行既改不回也删不掉；
⑥ 删到空连批次一起物理删除，`row_count` 与下拉一致；
⑦ ~~目标订单批次已确认即拒~~（§26.6 把这道闸整条删除，改由 K 组断言「已确认批次照样认领得动、且批次不被反确认」）；
⑧ §26.5 乙：同一个运单号已有原件行之后，同号第二张虚假行不许再认领（否则一份运费摊两遍）。
⑨ §26.6：认领的月份窗口锚在出账月、往前只放一档（一档内放行 / 两档外仍拒）；目标订单批次已确认不再是拒绝理由；
   跨月挂进去的那票在**确认账单**时钱真的摊进上月订单、并在那份账单批次备注里留「需重算利润」；勾选行报错点名到商品名而不是裸 id。

测试数据用 2019—2020 年那几个月：真实账期在 2026，历史月份既没订单批次也没账单批次。
"""

import asyncio
import io
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.service import ImportBatchService  # noqa: E402
from app.plugin.module_ecommerce.logistics import schema as logistics_schema  # noqa: E402
from app.plugin.module_ecommerce.logistics.controller import LogisticsRouter  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import (  # noqa: E402
    BillColumnMapping,
    BillParseRuleCreateSchema,
    CourierBillQueryParam,
)
from app.plugin.module_ecommerce.logistics.service import (  # noqa: E402
    BillParseRuleService,
    LogisticsService,
    _claim_marker_key,
)
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import OrderDetailOutSchema, SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []

RULE = "cvtestai"
SHOP = "AITSHOP"
MAIN = "AITESTMAIN"
M1, M2, M3, M4, M5 = "2019-05", "2019-06", "2019-07", "2019-08", "2019-09"
M6, M7 = "2019-10", "2019-11"
M8, M9, M10 = "2019-12", "2020-01", "2020-02"
B1, B2, B4, B5 = f"bill-{RULE}-{M1}", f"bill-{RULE}-{M2}", f"bill-{RULE}-{M4}", f"bill-{RULE}-{M5}"
B6, B7 = f"bill-{RULE}-{M6}", f"bill-{RULE}-{M7}"
B9, B10 = f"bill-{RULE}-{M9}", f"bill-{RULE}-{M10}"
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
        self.filename = "ait.xlsx"

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
    return [str(idx), did, order_no, SHOP, "AI测试店铺", "AI淘宝", "已完成",
            MAIN, None, "AI测试商品", "10.00", "0.00", "0.00", qty, 0,
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

    async def bill_by_id(bid: int) -> CourierBillModel:
        return (await session.execute(select(CourierBillModel).where(CourierBillModel.id == bid))).scalar_one()

    async def oid_of(order_no: str) -> int:
        return (await session.execute(select(OrderModel.id).where(
            OrderModel.system_order_no == order_no))).scalar_one()

    async def snap_orders() -> list:
        return sorted((await session.execute(
            select(OrderDetailModel.source_detail_id, OrderDetailModel.tracking_no,
                   OrderDetailModel.update_remark, OrderDetailModel.freight_cost)
            .where(OrderDetailModel.source_detail_id.like("AITD%"))
        )).all())

    async def snap_bills() -> list:
        return sorted((await session.execute(
            select(CourierBillModel.waybill_no, CourierBillModel.id, CourierBillModel.status,
                   CourierBillModel.actual_freight, CourierBillModel.anomaly_remark, CourierBillModel.is_deleted)
            .where(CourierBillModel.waybill_no.like("AIT%"))
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
        batch_svc = ImportBatchService(auth, session)

        # ══ A 组：「改回虚假」这条链路确实拆干净了（AI30=入口整条删掉）════════
        truthy("AI01 服务层没有 revert_bill_rows 了", not hasattr(LogisticsService, "revert_bill_rows"),
               "LogisticsService.revert_bill_rows 仍存在")
        truthy("AI02 路由里没有 PUT /bill/revert",
               not any("/bill/revert" in getattr(r, "path", "") for r in LogisticsRouter.routes),
               f"bill 路由={[r.path for r in LogisticsRouter.routes if '/bill/' in getattr(r, 'path', '')]}")
        truthy("AI03 请求模型 CourierBillRevertSchema 已删除",
               not hasattr(logistics_schema, "CourierBillRevertSchema"), "schema 里还 import 得到")
        sig = list(LogisticsService.restore_bill_rows.__annotations__)
        truthy("AI04 restore 入参带上了认领订单那两格",
               "system_order_no" in sig and "detail_ids" in sig, f"签名={sig}")
        eq("AI05 delete 现在返回一句话（级联删批次要讲给用户听）",
           LogisticsService.delete_bill_rows.__annotations__.get("return"), str)

        # ══ 建档：SKU 成本 + 解析规则 + 五个月订单 + 五个月账单 ═══════════════
        await sku.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="AI测试商品",
                                            cost_price=Decimal("2.00")))
        await rules.create(BillParseRuleCreateSchema(
            bill_type=RULE, carrier_name="AI测试快递",
            column_mapping=BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                             bill_other_fee="其它费用", receiver_province="收件省"),
            waybill_fee=Decimal("1.00")))

        await orders.import_orders(ship_month=M1, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD1A", "AITO-1", None, "1.0", "2019-05-05"),
            order_row(2, "AITD1B", "AITO-1", None, "3.0", "2019-05-05"),
            order_row(3, "AITD2A", "AITO-2", None, "2.0", "2019-05-06"),
            summary_row("30.00")])))
        await orders.import_orders(ship_month=M2, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD5A", "AITO-5", None, "1.0", "2019-06-05"),
            order_row(2, "AITD5B", "AITO-5", None, "1.0", "2019-06-05"),
            order_row(3, "AITD5C", "AITO-5", None, "1.0", "2019-06-05"),
            order_row(4, "AITD6A", "AITO-6", "AITWBX", "1.0", "2019-06-06"),
            order_row(5, "AITD7A", "AITO-7", None, "1.0", "2019-06-07"),
            summary_row("50.00")])))
        await orders.import_orders(ship_month=M3, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD8A", "AITO-8", None, "1.0", "2019-07-05"),
            summary_row("10.00")])))
        await orders.import_orders(ship_month=M5, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD9A", "AITO-9", None, "1.0", "2019-09-05"),
            order_row(2, "AITD9B", "AITO-9", None, "1.0", "2019-09-05"),
            summary_row("20.00")])))

        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITVB1", "2019-05-11", "4.0", "9.00"),
            bill_row("AITVB2", "2019-05-12", "2.0", "0.00")])), bill_type=RULE, bill_month=M1)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITWBX", "2019-06-11", "1.0", "0.00"),
            bill_row("AITWBX", "2019-06-11", "1.0", "0.00"),
            bill_row("AITG1", "2019-06-12", "1.0", "0.00"),
            bill_row("AITG2", "2019-06-12", "1.0", "0.00"),
            bill_row("AITG3", "2019-06-12", "1.0", "0.00"),
            bill_row("AITG4", "2019-06-13", "1.0", "0.00"),
            bill_row("AITG5", "2019-06-13", "1.0", "0.00"),
            bill_row("AITG6", "2019-06-14", "1.0", "0.00")])), bill_type=RULE, bill_month=M2)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITG7", "2019-07-11", "1.0", "0.00")])), bill_type=RULE, bill_month=M3)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITC1", "2019-08-11", "1.0", "0.00"),
            bill_row("AITC2", "2019-08-11", "1.0", "0.00"),
            bill_row("AITC3", "2019-08-12", "1.0", "0.00")])), bill_type=RULE, bill_month=M4)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITR1", "2019-09-11", "2.0", "0.00")])), bill_type=RULE, bill_month=M5)
        await session.flush()

        # 一次性把所有 id 取出来：后面的失败用例都在 SAVEPOINT 里跑，对象会被弄脏，只攥整数最稳。
        vb1_id, vb2_id = await bid_of("AITVB1"), await bid_of("AITVB2")
        dup_id, wbx_id = await bid_of("AITWBX", "duplicate"), await bid_of("AITWBX")
        g_ids = {n: await bid_of(f"AITG{n}") for n in range(1, 8)}
        c_ids = {n: await bid_of(f"AITC{n}") for n in range(1, 4)}
        r1_id = await bid_of("AITR1")
        d1a, d1b, d2a = await did_of("AITD1A"), await did_of("AITD1B"), await did_of("AITD2A")
        d5a, d5b, d5c = await did_of("AITD5A"), await did_of("AITD5B"), await did_of("AITD5C")
        d6a, d7a, d8a = await did_of("AITD6A"), await did_of("AITD7A"), await did_of("AITD8A")
        d9a, d9b = await did_of("AITD9A"), await did_of("AITD9B")

        truthy("AI06 前置数据：挂不上订单→虚假；同批第二张同单号→重复",
           ((await bill_of("AITVB1")).status, (await bill_of("AITVB2")).status,
            (await bill_of("AITWBX", "duplicate")).status) == ("fake", "fake", "duplicate"),
           f"VB1={vb1_id} VB2={vb2_id} WBX原件={wbx_id} WBX重复={dup_id}")

        # ══ B 组：虚假行不许「不认领就改对」（§22.1 那条死路的门）══════════════
        await expect("AI07 虚假行不填单号直接改对被拒", "还没认领订单",
                     lambda: bills.restore_bill_rows([vb1_id], remark="想直接改对"))
        eq("AI08 状态没被翻走", (await bill_of("AITVB1")).status, "fake")
        await expect("AI09 只给系统订单号、不勾行 = 半填，拒绝", "要一起给",
                     lambda: bills.restore_bill_rows([vb1_id], system_order_no="AITO-1"))
        await expect("AI10 只勾行、不给订单号 = 半填，拒绝", "要一起给",
                     lambda: bills.restore_bill_rows([vb1_id], detail_ids=[d1a]))
        await expect("AI11 一次给两票认领 = 拒绝", "一次只能给一票",
                     lambda: bills.restore_bill_rows([vb1_id, vb2_id], system_order_no="AITO-1", detail_ids=[d1a]))

        # ══ C 组：四道闸逐条拦下（§22.3 / §22.8② 与 ⑦）═══════════════════════
        await expect("AI12 闸①：系统订单号在订单表里查不到", "在订单表里查不到",
                     lambda: bills.restore_bill_rows([vb1_id], system_order_no="AITO-NONE", detail_ids=[d1a]))
        await expect("AI13 闸①：出账月往前**两**档仍拒（窗口只放一档，§26.6）", f"只许挂 order-{M4} 或 order-{M3}",
                     lambda: bills.restore_bill_rows([c_ids[1]], system_order_no="AITO-5", detail_ids=[d5a]))
        # 「未发货」行导入器根本不入库（excel_source 的既定规则），所以这里直接用 ORM 造一条未发货的行
        row5b = await detail_of("AITD5B")
        row5b.is_erp_shipped = False
        await session.flush()
        await expect("AI14 闸②：目标行没走 ERP 发货", "没走 ERP 发货",
                     lambda: bills.restore_bill_rows([g_ids[2]], system_order_no="AITO-5", detail_ids=[d5b]))
        row5c = await detail_of("AITD5C")
        row5c.distribution_order_type = 1
        await session.flush()
        await expect("AI15 闸②：目标行是分销订单", "分销单不参与账单匹配",
                     lambda: bills.restore_bill_rows([g_ids[3]], system_order_no="AITO-5", detail_ids=[d5c]))
        await expect("AI16 闸②：勾的行不属于这个主订单", "不存在，或不属于订单",
                     lambda: bills.restore_bill_rows([g_ids[4]], system_order_no="AITO-5", detail_ids=[d6a]))
        await expect("AI17 闸④：勾选行原单号正被别的原件行用着", "正被别的账单原件行用着",
                     lambda: bills.restore_bill_rows([g_ids[5]], system_order_no="AITO-6", detail_ids=[d6a]))
        row7a = await detail_of("AITD7A")
        row7a.weight_kg, row7a.ship_qty = None, 0
        await session.flush()
        await expect("AI18 闸：重量与实发件数全是 0（确认时同样会被拒，提前拦）", "重量与实发件数全是 0",
                     lambda: bills.restore_bill_rows([g_ids[6]], system_order_no="AITO-7", detail_ids=[d7a]))
        await batch_svc.mark_confirmed(await batch_svc.get_by_batch_no(f"order-{M3}"))
        # §26.6：「目标订单批次已确认」那道闸整条删除——上月末的货常在这个月才出账，而那时的
        # 订单批次早就确认完了；不放开它，往前那一档等于白放。
        await bills.restore_bill_rows([g_ids[7]], system_order_no="AITO-8", detail_ids=[d8a])
        await session.flush()
        eq("AI19 §26.6：目标订单批次已确认不再是拒绝理由，这一票照样认领得动",
           ((await bill_by_id(g_ids[7])).status, (await detail_of("AITD8A")).tracking_no), ("restored", "AITG7"))
        eq("AI20 认领之后订单批次仍是已确认（不反确认、批次状态一个字不动）",
           (await batch_svc.get_by_batch_no(f"order-{M3}")).status, "confirmed")
        await expect("AI21 已匹配的原件行不参与认领（它本来就挂得上）", "只有虚假行可以认领改对",
                     lambda: bills.restore_bill_rows([wbx_id], system_order_no="AITO-6", detail_ids=[d6a]))
        await expect("AI22 重复行填了单号反被拒：它不需要认领", "重复行",
                     lambda: bills.restore_bill_rows([dup_id], system_order_no="AITO-5", detail_ids=[d5a]))
        n_restored = await bills.restore_bill_rows([dup_id], remark="重复行直接改对")
        await session.flush()
        eq("AI23 重复行不填单号照样能改对（它的单号本来就查得到）",
           (n_restored, (await bill_by_id(dup_id)).status), (1, "restored"))

        # ══ D 组：认领主路径 + 列表那两格立刻有内容（§22.8①）═════════════════
        page_items = (await bills.courier_bill_page(
            1, 50, search=CourierBillQueryParam(import_batch_id=B1))).items
        before = next(i for i in page_items if i.waybill_no == "AITVB1")
        eq("AI24 认领前：列表「子订单数」=0（这一票谁也挂不上）",
           (before.detail_count, before.system_order_nos), (0, ""))

        await bills.restore_bill_rows([vb1_id], remark="发货漏填单号",
                                      system_order_no="AITO-1", detail_ids=sorted([d1a, d1b]))
        await session.flush()
        vb1 = await bill_of("AITVB1")
        got_a, got_b = await detail_of("AITD1A"), await detail_of("AITD1B")
        marker = _claim_marker_key(vb1)
        eq("AI25 认领＝把账单运单号写进勾选的子订单行（一票两包都写上）",
           (got_a.tracking_no, got_b.tracking_no), ("AITVB1", "AITVB1"))
        truthy("AI26 留痕精确到「批次 + 行ID」，原值记的是空",
               f"{marker}空；" in (got_a.update_remark or ""), f"update_remark={got_a.update_remark}")
        eq("AI27 认领当场一分钱不进订单（freight_cost 仍是 NULL）",
           (str(got_a.freight_cost), str(got_b.freight_cost)), ("None", "None"))
        eq("AI28 虚假行改对后账单侧存回面单费+其它费用", str(vb1.actual_freight), "10.00")
        truthy("AI29 账单异常原因写了认领凭据（订单号 + 承运商）",
               all(k in (vb1.anomaly_remark or "") for k in ("认领改对", "AITO-1", "AI测试快递")),
               f"anomaly_remark={vb1.anomaly_remark}")
        page_after = (await bills.courier_bill_page(
            1, 50, search=CourierBillQueryParam(import_batch_id=B1))).items
        row_after = next(i for i in page_after if i.waybill_no == "AITVB1")
        eq("AI30 认领后列表那两格立刻有内容（不用等确认批次）",
           (row_after.detail_count, row_after.system_order_nos), (2, "AITO-1"))
        await expect("AI31 已恢复行不许删（单行道，改对了就不给退）", "只有虚假/重复行可以删除",
                     lambda: bills.delete_bill_rows([vb1_id]))

        # ══ E 组（AJ 轮整组删除）：原「重新订正只退回带本票标记的行」§22.8③ ────
        # 功能没了：已恢复行不能再认领，也没有任何代码解析留痕文字。改由 verify_aj_round.py 断言。

        # ══ F 组：认领完再确认，钱摊得下去（§22.1 死路通了 / §22.8④）══════════
        await bills.restore_bill_rows([vb2_id], system_order_no="AITO-2", detail_ids=[d2a])
        await session.flush()
        await expect("AI39 虚假行没处理完 → 整个批次确认不了（这就是原来的死路）", "虚假/重复未处理",
                     lambda: bills.confirm_bill_batch(B4))
        result = await bills.confirm_bill_batch(B1)
        await session.flush()
        shares = (await session.execute(
            select(OrderDetailModel.source_detail_id, OrderDetailModel.freight_cost)
            .where(OrderDetailModel.source_detail_id.in_(["AITD1A", "AITD1B", "AITD2A", "AITD9A", "AITD9B"]))
            .order_by(OrderDetailModel.source_detail_id)
        )).all()
        eq("AI40 确认批次：10.00 按重量 1:3 摊给两包；1.00 给单独那票；未确认批次的钱没进来",
           [(s, str(f)) for s, f in shares],
           [("AITD1A", "2.50"), ("AITD1B", "7.50"), ("AITD2A", "1.00"),
            ("AITD9A", "None"), ("AITD9B", "None")])
        eq("AI41 Σ份额 = 整票金额（一分不丢）", str(sum((f or Decimal("0")) for _, f in shares)), "11.00")
        eq("AI42 主订单运费合计跟着刷新",
           (str((await session.execute(select(OrderModel.freight_cost_amount).where(
               OrderModel.system_order_no == "AITO-1"))).scalar_one()),
            str((await session.execute(select(OrderModel.freight_cost_amount).where(
                OrderModel.system_order_no == "AITO-2"))).scalar_one())),
           ("10.00", "1.00"))
        truthy("AI43 确认结果点名回写了 2 票", "运费已回写子订单 2 票" in result.message, result.message)
        await expect("AI44 批次确认后一律不动（再认领被拒）", "批次已确认，一律不动",
                     lambda: bills.restore_bill_rows([vb1_id], system_order_no="AITO-1", detail_ids=[d1a]))

        # ══ G 组：删到空 → 连批次一起物理删除 + row_count 跟着减（§22.6 / ⑥）══
        await expect("AI45 原件行（已匹配的）本来就不许删", "只有虚假/重复行可以删除",
                     lambda: bills.delete_bill_rows([wbx_id]))
        eq("AI46 前置：批次 bill-cvtestai-2019-08 有 3 行",
           (await batch_svc.get_by_batch_no(B4)).row_count, 3)
        msg = await bills.delete_bill_rows([c_ids[2], c_ids[3]])
        await session.flush()
        eq("AI47 删 2 行没删空：批次还在，row_count 减到 1（下拉那句「N 行」不能撒谎）",
           (await batch_svc.get_by_batch_no(B4)).row_count, 1)
        truthy("AI48 没删空时不级联、话术里也不提级联", "一并物理删除" not in msg, msg)
        eq("AI49 软删行此刻还在库里（is_deleted=1，没被提前物理删）",
           (await session.execute(text(
               "SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no IN ('AITC2','AITC3') AND is_deleted=1")
           )).scalar(), 2)
        msg2 = await bills.delete_bill_rows([c_ids[1]])
        await session.flush()
        truthy("AI50 删到空：批次自己跟着消失，并把这话说到界面返回里",
               "已无账单行" in msg2 and B4 in msg2, msg2)
        eq("AI51 批次记录是物理删除（不是软删）",
           (await session.execute(text(
               "SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no=:b"), {"b": B4})).scalar(), 0)
        eq("AI52 该批次名下账单行全物理清空（含之前的软删行，重导不撞 batch_no 唯一键）",
           (await session.execute(text(
               "SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no IN ('AITC1','AITC2','AITC3')"))).scalar(), 0)

        # ══ H 组：门禁预检口径 ════════════════════════════════════════════════
        gaps1 = await bills.bill_gate_gaps(M1)
        truthy("AI53 M1 已确认：这一家不再出现在门禁缺口里",
               B1 not in gaps1.unconfirmed_batches and RULE not in gaps1.missing_types,
               f"unconfirmed={gaps1.unconfirmed_batches} missing={gaps1.missing_types}")
        gaps2 = await bills.bill_gate_gaps(M2)
        truthy("AI54 M2 还没确认：批次仍算未确认缺口（改对重复行不等于确认）",
               B2 in gaps2.unconfirmed_batches, f"unconfirmed={gaps2.unconfirmed_batches}")

        # ══ I 组（§26.5 乙）：同号第二张虚假行不许再认领 ═══════════════════════
        # 「虚假 > 重复」这条优先级（ADR-0034）让同号的两张挂不上订单的行**各自标虚假**、
        # 彼此不判重复——这正是运费翻倍那条链的入口。第一张认领成功之后它就是原件行，
        # 第二张若也能认领，`_collect_waybill_groups` 把两张收进同一组、`total_amount` 成两份钱。
        await orders.import_orders(ship_month=M6, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD10A", "AITO-10", None, "1.0", "2019-10-05"),
            order_row(2, "AITD10B", "AITO-10", None, "1.0", "2019-10-05"),
            summary_row("20.00")])))
        await orders.import_orders(ship_month=M7, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD11A", "AITO-11", None, "1.0", "2019-11-05"),
            order_row(2, "AITD11B", "AITO-11", None, "1.0", "2019-11-05"),
            summary_row("20.00")])))
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITDW1", "2019-10-11", "2.0", "0.00")])), bill_type=RULE, bill_month=M6)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITDW1", "2019-11-11", "2.0", "0.00"),
            bill_row("AITDX2", "2019-11-12", "2.0", "0.00")])), bill_type=RULE, bill_month=M7)
        await session.flush()

        w1 = (await session.execute(
            select(CourierBillModel.id, CourierBillModel.status, CourierBillModel.import_batch_id)
            .where(CourierBillModel.waybill_no == "AITDW1").order_by(CourierBillModel.id)
        )).all()
        first_id, second_id = w1[0][0], w1[1][0]
        d10a, d11a, d11b = await did_of("AITD10A"), await did_of("AITD11A"), await did_of("AITD11B")
        eq("AI56 前置：同号两张分属两个月批次，各自标虚假、没互相判重复",
           [(s, b) for _, s, b in w1], [("fake", B6), ("fake", B7)])

        await bills.restore_bill_rows([first_id], remark="第一次认领",
                                      system_order_no="AITO-10", detail_ids=[d10a])
        await session.flush()
        eq("AI57 第一张认领成功（它从此是这一票唯一的原件行）",
           ((await bill_by_id(first_id)).status, (await detail_of("AITD10A")).tracking_no),
           ("restored", "AITDW1"))
        await expect("AI58 同号第二张再认领被拒，而且一条数据都不改（闸在写之前）", "这一票已经有原件行在挂着了",
                     lambda: bills.restore_bill_rows([second_id], system_order_no="AITO-11", detail_ids=[d11a]))
        msg_claim = ""
        try:
            async with session.begin_nested():
                await bills.restore_bill_rows([second_id], system_order_no="AITO-11", detail_ids=[d11a])
        except CustomException as exc:
            msg_claim = str(exc).replace("\n", " ")
        truthy("AI59 那句报错点名有主人的那张行（id + 批次），把人赶回那一张",
               all(k in msg_claim for k in (str(first_id), B6)), f"实际 {msg_claim[:220]}")
        eq("AI60 被拒之后第二张还是虚假、目标行也没被顺手写上单号",
           ((await bill_by_id(second_id)).status, (await detail_of("AITD11A")).tracking_no), ("fake", None))
        await bills.restore_bill_rows([await bid_of("AITDX2")], remark="另一票照常认领",
                                      system_order_no="AITO-11", detail_ids=[d11b])
        await session.flush()
        eq("AI61 不同单号的虚假行照常认领得动（这道闸没把认领整条堵死）",
           (await detail_of("AITD11B")).tracking_no, "AITDX2")
        msg_del = await bills.delete_bill_rows([second_id])
        await session.flush()
        truthy("AI62 §26.5 明说的代价兑现：那第二张在界面上仍叫虚假，出路是删，删得掉",
               "已删除 1 行" in msg_del, msg_del)

        # ══ K 组（§26.6）：往前一档 + 已确认批次放行 + 跨月那票的钱与提示行 ═════════
        # 业务节奏：「12 月底发的货进 1 月的账单，1 月的账单 2 月才拿到」——那时 order-2019-12
        # 早就确认完了。这一组把两条闸一起放开之后的整条链跑到底，钱的去处是它的正身断言。
        await orders.import_orders(ship_month=M8, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "AITD12A", "AITO-12", None, "1.0", "2019-12-28"),
            order_row(2, "AITD12B", "AITO-12", None, "3.0", "2019-12-28"),
            order_row(3, "AITD14A", "AITO-14", None, "1.0", "2019-12-30"),
            summary_row("30.00")])))
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITKW1", "2020-01-11", "4.0", "0.00"),
            bill_row("AITKW2", "2020-01-12", "1.0", "0.00")])), bill_type=RULE, bill_month=M9)
        await bills.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            bill_row("AITKW3", "2020-02-11", "1.0", "0.00")])), bill_type=RULE, bill_month=M10)
        await session.flush()
        kw1, kw2, kw3 = await bid_of("AITKW1"), await bid_of("AITKW2"), await bid_of("AITKW3")
        d12a, d12b, d14a = await did_of("AITD12A"), await did_of("AITD12B"), await did_of("AITD14A")
        eq("AI63 前置：1 月账单里这两票都判虚假（单号还没进订单，正是需要认领的那批）",
           [(await bill_by_id(kw1)).status, (await bill_by_id(kw2)).status], ["fake", "fake"])

        await bills.restore_bill_rows([kw1], remark="12月底发的票在1月账单里",
                                      system_order_no="AITO-12", detail_ids=[d12a, d12b])
        await session.flush()
        eq("AI64 往前一档放行：2020-01 的票挂进 order-2019-12（改之前必被「只许同月」拒）",
           ((await bill_by_id(kw1)).status, (await detail_of("AITD12A")).tracking_no,
            (await detail_of("AITD12B")).tracking_no), ("restored", "AITKW1", "AITKW1"))
        eq("AI65 跨月认领当场一分钱不进订单（钱只在确认账单那一刻摊下去）",
           [str((await detail_of(s)).freight_cost) for s in ("AITD12A", "AITD12B")], ["None", "None"])

        await batch_svc.mark_confirmed(await batch_svc.get_by_batch_no(f"order-{M8}"))
        await bills.restore_bill_rows([kw2], remark="同一批已确认订单里的另一票",
                                      system_order_no="AITO-14", detail_ids=[d14a])
        await session.flush()
        eq("AI66 往前一档 + 订单批次已确认：两条闸同时放开之后这一票也挂得进",
           ((await bill_by_id(kw2)).status, (await detail_of("AITD14A")).tracking_no), ("restored", "AITKW2"))
        await expect("AI67 窗口只有一档：2020-02 的票不接 order-2019-12，而且一条数据都不改",
                     f"只许挂 order-{M10} 或 order-{M9}",
                     lambda: bills.restore_bill_rows([kw3], system_order_no="AITO-14", detail_ids=[d14a]))

        result_k = await bills.confirm_bill_batch(B9)
        await session.flush()
        k_shares = (await session.execute(
            select(OrderDetailModel.source_detail_id, OrderDetailModel.freight_cost)
            .where(OrderDetailModel.source_detail_id.in_(["AITD12A", "AITD12B", "AITD14A"]))
            .order_by(OrderDetailModel.source_detail_id)
        )).all()
        eq("AI68 确认账单：运费摊进了已确认的上月订单（1.00 按 1:3 分两包、另 1.00 全给一票一包）",
           [(s, str(f)) for s, f in k_shares],
           [("AITD12A", "0.25"), ("AITD12B", "0.75"), ("AITD14A", "1.00")])
        truthy("AI69 触及已确认订单批次：提示行落在这份**账单**批次的备注里（只写提示，不自动重算）",
               f"改动了 order-{M8}，需重算利润" in ((await batch_svc.get_by_batch_no(B9)).remark or ""),
               f"remark={(await batch_svc.get_by_batch_no(B9)).remark}")
        truthy("AI70 确认结果把同一句话回给界面（操作员看得见哪个月被动过）",
               f"改动了 order-{M8}" in (result_k.warning or ""), f"warning={result_k.warning}")
        eq("AI71 摊分之后订单批次仍是已确认（本轮没有反确认、也没有把批次改回未确认）",
           (await batch_svc.get_by_batch_no(f"order-{M8}")).status, "confirmed")

        msg_word = ""
        try:
            async with session.begin_nested():
                await bills.restore_bill_rows([g_ids[2]], system_order_no="AITO-5", detail_ids=[d5b])
        except CustomException as exc:
            msg_word = str(exc).replace("\n", " ")
        truthy("AI72 未发货那条报错说人话：商品名／规格 · 实发件数（id），id 只留在括号里",
               all(k in msg_word for k in ("没走 ERP 发货", "AI测试商品", "不分规格", "实发 1 件", f"id {d5b}")),
               f"实际 {msg_word[:220]}")
        msg_dist = ""
        try:
            async with session.begin_nested():
                await bills.restore_bill_rows([g_ids[3]], system_order_no="AITO-5", detail_ids=[d5c])
        except CustomException as exc:
            msg_dist = str(exc).replace("\n", " ")
        truthy("AI73 分销那条也一样：不再只甩一排子订单行 id",
               all(k in msg_dist for k in ("分销单不参与账单匹配", "AI测试商品", f"id {d5c}")),
               f"实际 {msg_dist[:220]}")
        truthy("AI74 候选响应把分销类型带出来了（前端灰化勾选框的前置，§26.6 第 3 条）",
               OrderDetailOutSchema.model_validate(await detail_of("AITD12A")).model_dump()["distribution_order_type"] == 0,
               "序列化结果里 distribution_order_type 不是 0")

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
            "SELECT (SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no LIKE 'AIT%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order_detail WHERE source_detail_id LIKE 'AITD%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'AITO%') "
            "+ (SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no LIKE 'AIT%' OR batch_no LIKE '%2019-0%' OR batch_no LIKE '%2019-1%' OR batch_no LIKE '%2020-0%') "
            "+ (SELECT COUNT(*) FROM ecommerce_bill_parse_rule WHERE bill_type LIKE 'cvtestai%') "
            "+ (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'AITEST%')"))).scalar()
        (ok if left == 0 else bad)("AI55 回滚干净（库里一行没留）", f"残留 {left} 行" if left else "0")

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
