"""2026-10-28 AC 轮（§17）改动的回滚式验证：一个事务跑完，结束显式 rollback，库里什么都不留。

编号前缀 AC，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB 不撞号。
AC24 用两份真实导出（桌面上的 12345.xlsx / 9月数据.xlsx）**只走解析层**：业务方明确过
12345.xlsx 只是新版式参考、不属于 9 月批次，所以全程不导入、不落库。

AC26 的「批次确认计数」一处没单独断言：它与主列表「只看缺账单」用的是同一把尺
（crud.missing_bill_conditions），AC26-3 已经覆盖那把尺子本身。

2026-10-30 AK 轮（§24.3 钉子 5）：AC26-3 / 3b / 4 原先调 `details_by_order(freight_missing=)`，
判据搬到了主订单列表的 EXISTS 上，三条断言改指 `OrderService.page(freight_missing=True)`——
它们断言的本来就是同一件事，搬家不许把断言弄丢。
"""

import asyncio
import io
import os
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.model import ImportBatchModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel, CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.parsers.generic_parser import GenericExcelBillParser  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import BillColumnMapping, BillParseRuleCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.logistics.service import BillParseRuleService, LogisticsService  # noqa: E402
from app.plugin.module_ecommerce.order.importers.excel_source import ExcelOrderDataSource  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import OrderQueryParam, SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []

SHOP_ID = "ACSHOP1"
MAIN = "ACTESTMAIN"
MAIN_NOCOST = "ACTESTNOCOST"
# 同月同类型只允许导入一次（common/import_batch create_batch 的硬闸），所以要导入三个批次的
# 验证就必须配三条规则——它们的列映射与面单费完全一样，只是 bill_type 不同。
RULE_OK = "acteston"
RULE_GAP = "actestgap"
RULE_MATCH = "actestmtch"
RULES = (RULE_OK, RULE_GAP, RULE_MATCH)
# 导出文件的真实路径含 Windows 用户名，不进 Git（shared-rules §3.4「本机绝对路径」）。
# 没设这两个环境变量就跑合成夹具那几项，真文件项自动 skip。
NEW_FILE = os.environ.get("ECOM_EXPORT_NEW_FILE", "")
OLD_FILE = os.environ.get("ECOM_EXPORT_OLD_FILE", "")

BILL_HEADERS = ["运单号", "账单日期", "账单重量", "其它费用", "收件省"]
ORDER_HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号", "重量kg(SKU)",
    "成本价(SKU)", "系统实发成本", "实退成本", "订单标签",
]


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


def skip(cid: str, detail: str) -> None:
    """断言的前置条件不成立（样本文件不在了），不计通过也不计失败，末尾单独报数。"""
    RESULTS.append(("SKIP", cid, detail))


async def raises(cid: str, coro, *, contains: str) -> None:
    try:
        value = await coro
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        (ok if contains in msg else bad)(cid, f"{type(e).__name__}：{msg[:160]}")
    else:
        bad(cid, f"没有报错，返回了 {str(value)[:160]}")


class BytesUpload:
    def __init__(self, data: bytes, name: str = "ac.xlsx") -> None:
        self.data = data
        self.filename = name

    async def read(self) -> bytes:
        return self.data

    async def close(self) -> None:
        return None


def xlsx(headers: list[str], rows: list[list], *, title_row: str | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    if title_row:
        ws.append([title_row])
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def order_row(
    idx: int, detail_id: str, *, order_no: str | None = None, paid: str = "10.00",
    ship_month: str = "2021-11", main: str = MAIN, ship_qty: int = 1, return_qty: int = 0,
    shipped: bool = True, tracking: str | None = None, weight: str = "0.5",
    sku_cost: str | None = "10.00", erp_shipped_cost: str | None = "7.72",
    erp_return_cost: str | None = "0.00", tag: str | None = None,
) -> list:
    return [
        str(idx), f"ACOD{detail_id}", order_no or f"ACORD-{detail_id}", SHOP_ID, "AC测试店铺", "AC淘宝", "已完成",
        main, None, "AC测试商品", paid, "0.00", "0.00", ship_qty, return_qty,
        f"{ship_month}-05 10:00:00", "ERP发货" if shipped else "未发货",
        tracking if tracking is not None else f"ACWB{detail_id}", weight,
        sku_cost, erp_shipped_cost, erp_return_cost, tag,
    ]


async def read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return await asyncio.to_thread(f.read)


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        orders_svc, sku_svc = OrderService(auth, session), SkuCostService(auth, session)
        rules_svc, logistics = BillParseRuleService(auth, session), LogisticsService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("ACTEST%")),
            delete(BillParseRuleModel).where(BillParseRuleModel.bill_type.like("actest%")),
            delete(StoreModel).where(StoreModel.kuaimai_shop_id.like("ACSHOP%")),
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("ACOD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("ACORD-%")),
            delete(CourierBillModel).where(CourierBillModel.waybill_no.like("ACWB%")),
            delete(ImportBatchModel).where(ImportBatchModel.batch_no.like("%actest%")),
        ):
            await session.execute(stmt)
        await session.flush()

        # ══ AC24：表头行自动定位（两份真实导出，只解析不落库）══════════════
        if os.path.exists(NEW_FILE):
            new_src = ExcelOrderDataSource()
            new_raw = await new_src.fetch(contents=await read_file(NEW_FILE))
            new_details = [d for o in new_raw for d in o.details]
            truthy("AC24-01 新版式（标题行+表头行）解析成功，不再报「缺少必要的列」",
                   len(new_raw) > 0, f"{len(new_raw)} 张主订单")
            eq("AC24-02 明细数与上一轮实测一致", len(new_details), 143)
            eq("AC24-03 主订单数与上一轮实测一致", len(new_raw), 117)
            truthy("AC24-04 认到了「订单标签」列", new_src.order_tag_column_found is True, "order_tag_column_found=True")
            dist_rows = [d for d in new_details if d.distribution_order_type == 1]
            eq("AC16/AC23 判据命中 3 条（只认「已生成采购单」，不是「分销订单」四个字）", len(dist_rows), 3)
            eq("AC18 其余明细落 0（不是 None、不是布尔）",
               sum(1 for d in new_details if d.distribution_order_type == 0), len(new_details) - 3)
        else:
            # 样本文件不在了（桌面上只剩 Excel 的 ~$ 锁定文件）。143/117/47-vs-3 这些"实测数"
            # 上一轮已记进 §17.6，无法再用合成数据复现；这里改用带标题行的合成表守住"定位"本身。
            skip("AC24-01~04 + AC16/AC18/AC23 真实新版式实测",
                 f"{NEW_FILE or 'ECOM_EXPORT_NEW_FILE 没设'} 不存在，改用下面的合成标题行用例")
            synth_src = ExcelOrderDataSource()
            synth_raw = await synth_src.fetch(contents=xlsx(
                ORDER_HEADERS, [order_row(1, "T1", tag="拼多多分销订单,已生成采购单")],
                title_row="销售统计分析-按订单商品明细(系统)"))
            eq("AC24-01b 带标题行的订单表照样定位到表头（合成样本，替代真文件）", len(synth_raw), 1)
            truthy("AC24-04b 标题行版式仍认到「订单标签」列", synth_src.order_tag_column_found is True, "True")
            eq("AC16/AC23b 标题行版式仍判出分销（值=1）", synth_raw[0].details[0].distribution_order_type, 1)

        # AN22（2026-11-02）一度改了这份真文件的去留：它里面有 9 行「店铺编码」填的是 `0`
        # （订单 6005…462465，实测从第4490行起），那份文件当时整份被拒。
        # **2026-11-03 AN34 撤了这道闸**——`0` 是快麦给「出库单」留的固定编码，是一家真店铺，
        # 那 9 行要进利润。于是 AC24-05 重新回到它本来的意思：旧版式（第 1 行就是表头）的真文件照样解析得通。
        # AC8「没有标签列 → None」那条不变式仍由下面砍掉最后一列的合成文件守着。
        if os.path.exists(OLD_FILE):
            try:
                old_real = await ExcelOrderDataSource().fetch(contents=await read_file(OLD_FILE))
                ok("AC24-05 真旧文件（旧版式 + 编码 0 那 9 行出库单）解析通过（AN34 撤闸之后）",
                   f"{len(old_real)} 张主订单")
            except Exception as exc:  # noqa: BLE001
                bad("AC24-05", f"竟然报错：{type(exc).__name__}：{str(exc)[:160]}")
        else:
            skip("AC24-05 真旧文件", f"{OLD_FILE or 'ECOM_EXPORT_OLD_FILE 没设'} 不在")
        old_src = ExcelOrderDataSource()
        old_raw = await old_src.fetch(contents=xlsx(ORDER_HEADERS[:-1], [order_row(1, "OLD1")[:-1]]))
        truthy("AC24-05b 旧版式（没有「订单标签」列）照样解析", len(old_raw) > 0, f"{len(old_raw)} 张主订单")
        eq("AC8 旧版式没有标签列 → order_tag_column_found=False", old_src.order_tag_column_found, False)
        # 解析层的 None 是「这份文件没给标签」这个信号本身（不是"全是非分销"），
        # 更新路径靠它决定整列不碰（AC15）；变成 0 是落库那一步做的（下面 AC8-DB 断言）。
        eq("AC8/AC15 没有标签列 → 解析层的值是 None（无从判断，不是 0）",
           {d.distribution_order_type for o in old_raw for d in o.details}, {None})

        # ══ AC13：合计行不再让整份账单导不进来 ════════════════════════════
        col_map = BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                    bill_other_fee="其它费用", receiver_province="收件省")
        for bt in RULES:
            await rules_svc.create(BillParseRuleCreateSchema(bill_type=bt, carrier_name="AC测试快递",
                                                             column_mapping=col_map, waybill_fee=Decimal("1.00")))
        await session.flush()
        # 解析器拿的是规则里那份 JSON 列映射（dict），不是 Pydantic 对象——service 就是这么传的
        col_dict = col_map.model_dump()

        parser = GenericExcelBillParser()
        # 非账单行的行号语义：日期判定排在运单号之前，所以带日期没运单号的行仍然报运单号
        await raises("AC13-01 运单号为空但日期合法 → 照旧整份报错（不放过真缺陷）",
                     parser.aparse(xlsx(BILL_HEADERS, [["ACWBX1", "2021-12-10", "0.5", "2.00", "浙江"],
                                                       [None, "2021-12-10", "0.5", "2.00", "浙江"]]), col_dict),
                     contains="运单号不能为空")
        await raises("AC13-02 全部行都读不出账单日期 → 专属文案（不复用「上传文件为空」）",
                     parser.aparse(xlsx(BILL_HEADERS, [["ACWBX1", "合计", "1.0", "4.00", None],
                                                       ["ACWBX2", "", "1.0", "4.00", None]]), col_dict),
                     contains="账单日期全都读不出来")

        imp = await logistics.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            ["ACWBOK1", "2021-12-10", "0.5", "2.00", "浙江"],
            ["ACWBOK2", "2021-12-10", "0.5", "2.00", "浙江"],
            [None, None, "1.0", "4.00", None]])), bill_type=RULE_OK, bill_month="2021-12")
        await session.flush()
        eq("AC13-03 带合计行的账单导入成功（这就是用户报的那个 bug）", imp.row_count, 2)
        eq("AC13-04 批次 row_count 只数账单行，不含非账单行", (await session.execute(text(
            "SELECT row_count FROM ecommerce_import_batch WHERE batch_no=:b"), {"b": imp.batch_no})).scalar(), 2)
        remark = (await session.execute(text(
            "SELECT remark FROM ecommerce_import_batch WHERE batch_no=:b"), {"b": imp.batch_no})).scalar() or ""
        truthy("AC14 备注里逐行报了被摘掉的合计行", "非账单行 1 行：第3行(重量 1.000、费用 4.00)" in remark, remark[:200])
        truthy("AC14 备注含合计行三项核对结论", "合计行与逐行求和一致" in remark, remark[-160:])
        truthy("AC14 备注含匹配结果四态计数", "判定：已匹配" in remark, remark[-120:])
        truthy("AC12 对上了就不提示异常", "对不上" not in imp.message, imp.message[:160])

        bad_imp = await logistics.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            ["ACWBOK3", "2021-12-11", "0.5", "2.00", "浙江"],
            [None, None, "9.9", "99.00", None]])), bill_type=RULE_GAP, bill_month="2021-12")
        await session.flush()
        truthy("AC12 对不上不拒绝入库（1 行照样进）+ 当场提示", bad_imp.row_count == 1 and "对不上" in bad_imp.message,
               bad_imp.message[:200])

        # ══ AC24 账单侧：表头在第 2 行也能解析 ════════════════════════════
        titled = await parser.aparse(xlsx(BILL_HEADERS, [["ACWBOK4", "2021-12-12", "0.5", "2.00", "浙江"]],
                                         title_row="某某快递对账单"), col_dict)
        eq("AC24-06 账单侧同样自动定位表头（标题行不挡住）", len(titled), 1)

        # ══ AC19/21/27/28：分销行的成本例外 ═══════════════════════════════
        await sku_svc.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="AC测试商品",
                                                 cost_price=Decimal("10.00")))
        await session.flush()

        msg = await orders_svc.import_orders(ship_month="2021-11", file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "D1", tag="拼多多分销订单,已生成采购单", sku_cost="8.50",
                      erp_shipped_cost="7.72", erp_return_cost="0.00"),
            order_row(2, "D2", tag="预发货", erp_shipped_cost="99.00", erp_return_cost="0.00"),
            order_row(3, "D3", tag=None, main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None,
                      erp_return_cost=None, order_no="ACORD-D3"),
            order_row(4, "D4", order_no="ACORD-D3", tag="拼多多分销订单, 已生成采购单", main=MAIN_NOCOST,
                      sku_cost=None, erp_shipped_cost=None, erp_return_cost=None),
        ])))
        await session.flush()

        d1 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.source_detail_id == "ACODD1"))).scalars().one()
        d2 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.source_detail_id == "ACODD2"))).scalars().one()
        d4 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.source_detail_id == "ACODD4"))).scalars().one()

        eq("AC19 分销行发货成本取「系统实发成本」原值（不是 SKU 价 10.00×1）", str(d1.computed_product_cost), "7.72")
        eq("AC19/补充口径 分销行退货成本取「实退成本」原值", str(d1.computed_return_cost), "0.00")
        eq("AC18 分销标记落 1", d1.distribution_order_type, 1)
        eq("AC18 非分销标记落 0", d2.distribution_order_type, 0)
        # 主表价故意配成 10.00、Excel 的「成本价(SKU)」配成 8.50：落在 8.50 才证明这一行没去查主表
        eq("AC16 分销行的 cost_price 取 Excel「成本价(SKU)」原值（不是主表的 10.00）", str(d1.cost_price), "8.5000")
        eq("AC21 分销行不置 cost_missing（SKU 表查不到它是常态）", d4.cost_missing, False)
        eq("AC28 分销行原值空着 → 照原值落 0", str(d4.computed_product_cost), "0.00")
        eq("AC16 分销行「成本价(SKU)」也空着 → cost_price 落 NULL（不去主表补一个数）", d4.cost_price, None)
        eq("AC23 标签带空格也能命中（归一后子串包含）", d4.distribution_order_type, 1)
        truthy("AC27 分销行不跑差异告警（D1 原值 7.72 vs 主表 10.00 没报出来）",
               "ACODD1" not in msg, msg[:120])
        # AN20（2026-11-02 业务方确认）把告警②「系统实发成本 ≠ 自算发货成本」**整条删掉**：
        # D2 这一行 99.00 vs 自算 10.00 以前必报，现在不报——而它的「成本价(SKU)」=主表 10.00，
        # 告警①也没理由报。所以这一格**整条不该出现在结果里**，正是那条删除的证明。
        # ①本身还活着，端到端在 AN 轮 AN5-8 守着（主表 3.11 vs 文件 3.115）。
        truthy("AC27 非分销行的「实发成本 ≠ 自算」不再告警（AN20 整条删除告警②）",
               "ACODD2" not in msg, msg[-160:])
        missing_rows = (await session.execute(text(
            "SELECT source_detail_id FROM ecommerce_order_detail "
            "WHERE cost_missing=1 AND source_detail_id LIKE 'ACOD%'"))).scalars().all()
        # D3 是「非分销 + 主表查不到成本」，它进清单是对的；D4 同样查不到但它是分销行，不该进
        eq("AC21 只有非分销的缺成本行进「待补充成本」清单", list(missing_rows), ["ACODD3"])
        order_d3 = (await session.execute(select(OrderModel).where(
            OrderModel.system_order_no == "ACORD-D3"))).scalars().one()
        eq("§5.1 第10步 主订单求和把分销行的原值口径一起算进去（0.00 + 0.00）",
           str(order_d3.shipped_cost_amount), "0.00")

        # ══ AC8：缺标签列要在结果里说出来 ═════════════════════════════════
        msg_no_tag = await orders_svc.import_orders(ship_month="2021-12", file=BytesUpload(xlsx(
            ORDER_HEADERS[:-1], [order_row(1, "E1", ship_month="2021-12")])))
        await session.flush()
        truthy("AC8 旧版式（没有标签列）→ 明写一句", "没读到「订单标签」列，全部按非分销订单处理" in msg_no_tag,
               msg_no_tag[:160])
        truthy("AC8 带标签列时不提示", "没读到「订单标签」列" not in msg, msg[:120])
        eq("AC8 没有标签列时新行落库仍是 0（不是 NULL）", (await session.execute(text(
            "SELECT distribution_order_type FROM ecommerce_order_detail WHERE source_detail_id='ACODE1'"))).scalar(), 0)

        # ══ AC15：更新路径的「无标签列就不碰这一列」════════════════════════
        upd_no_tag = await orders_svc.update_orders(ship_month="2021-11", file=BytesUpload(xlsx(
            ORDER_HEADERS[:-1], [order_row(1, "D1", paid="10.00", sku_cost="8.50"),
                                 order_row(2, "D2", paid="10.00"),
                                 order_row(3, "D3", main=MAIN_NOCOST, sku_cost=None,
                                           erp_shipped_cost=None, erp_return_cost=None),
                                 order_row(4, "D4", order_no="ACORD-D3", main=MAIN_NOCOST, sku_cost=None,
                                           erp_shipped_cost=None, erp_return_cost=None)])))
        await session.flush()
        truthy("AC15 无标签列的更新不冲掉分销标记（D1 仍是 1）", upd_no_tag.ok, upd_no_tag.msg[:200])
        mark_and_price = (await session.execute(text(
            "SELECT distribution_order_type, cost_price FROM ecommerce_order_detail "
            "WHERE source_detail_id='ACODD1'"))).fetchone()
        eq("AC15 库里标记保持 1", mark_and_price[0], 1)
        eq("AC16 无标签列的更新照样按分销口径刷 cost_price（取文件里的 8.50，不查主表）",
           str(mark_and_price[1]), "8.5000")
        truthy("AC15 更新结果也提示这一轮没刷标记", "分销订单标记保持不变" in upd_no_tag.msg, upd_no_tag.msg[:200])

        upd_flip = await orders_svc.update_orders(ship_month="2021-11", file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "D1", tag="预发货", sku_cost="8.50", erp_shipped_cost="10.00", erp_return_cost="3.00"),
            order_row(2, "D2", tag="拼多多分销订单,已生成采购单", sku_cost="6.30",
                      erp_shipped_cost="7.72", erp_return_cost="0.00"),
            order_row(3, "D3", main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None, erp_return_cost=None),
            order_row(4, "D4", order_no="ACORD-D3", main=MAIN_NOCOST, sku_cost=None,
                      erp_shipped_cost=None, erp_return_cost=None)])))
        await session.flush()
        truthy("AC15 变动即应用（0→1、1→0 都跟着新标签走）", upd_flip.ok, upd_flip.msg[:200])
        flip = (await session.execute(text(
            "SELECT distribution_order_type, computed_product_cost, computed_return_cost, cost_price, "
            "cost_missing, update_remark FROM ecommerce_order_detail WHERE source_detail_id='ACODD2'"))).fetchone()
        eq("AC15 刷成分销后标记=1", flip[0], 1)
        eq("§5.1b 第5步 刷成分销后成本改取快麦原值", str(flip[1]), "7.72")
        eq("AC16 刷成分销后 cost_price 跟着文件的「成本价(SKU)」走（6.30，不是主表的 10.00）", str(flip[3]), "6.3000")
        truthy("AC15 变更写进更新备注（「分销订单 0→1」）", "分销订单 0→1" in (flip[5] or ""), str(flip[5])[:160])
        back = (await session.execute(text(
            "SELECT distribution_order_type, computed_product_cost, cost_price, cost_missing "
            "FROM ecommerce_order_detail WHERE source_detail_id='ACODD1'"))).fetchone()
        eq("AC15 刷回非分销后标记=0", back[0], 0)
        eq("§5.1b 第5步 刷回非分销后按主表重算（10.00×1）", str(back[1]), "10.00")
        eq("AC16 刷回非分销后 cost_price 改回主表价（10.00，不是文件里的 8.50）", str(back[2]), "10.0000")
        eq("§5.1b 第5步 刷回非分销后 cost_missing 重新按主表判定", back[3], False)

        # ══ AC26：分销行从运费这条线彻底摘出去 ════════════════════════════
        # 上一轮把 D1 刷回非分销、把 D2 刷成分销，所以这里 D2 才是那条"分销行"：
        # 两行都是 ERP发货、都带运单号、freight_cost 都还是 NULL，唯一区别就是标记。
        bill = await logistics.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            ["ACWBD1", "2021-12-13", "0.5", "2.00", "浙江"],
            ["ACWBD2", "2021-12-13", "0.5", "2.00", "浙江"]])), bill_type=RULE_MATCH, bill_month="2021-12")
        await session.flush()
        bill_rows = (await session.execute(select(CourierBillModel).where(
            CourierBillModel.import_batch_id == bill.batch_no).order_by(CourierBillModel.id))).scalars().all()
        by_no = {r.waybill_no: r for r in bill_rows}
        eq("AC26-1 分销行即使带运单号、即使 ERP发货，也挂不上子订单（判虚假）", by_no["ACWBD2"].status, "fake")
        eq("AC26-2 非分销行照旧匹配（回归）", by_no["ACWBD1"].status, "matched")
        eq("AC26-1b 虚假原因只说事实，不替业务方猜「所以这单是分销」（AF8 修订原断言；"
           "库里带写账单行时统一加的「第N行：」前缀，ACWBD2 是第 2 条数据行）",
           by_no["ACWBD2"].anomaly_remark, "第2行：无对应ERP发货子订单（匹配只看非分销的ERP发货子订单）")

        order_d1_id, order_d2_id = (await session.execute(text(
            "SELECT (SELECT id FROM ecommerce_order WHERE system_order_no='ACORD-D1'), "
            "(SELECT id FROM ecommerce_order WHERE system_order_no='ACORD-D2')"))).one()

        async def missing_hits(no: str) -> bool:
            """§24 的唯一入口：主订单列表勾「只看缺账单」，这张单在不在结果里。"""
            items = (await orders_svc.page(page_no=1, page_size=50,
                                           search=OrderQueryParam(system_order_no=no, freight_missing=True))).items
            return any(o.system_order_no == no for o in items)

        eq("AC26-3 分销行不把主订单带进「只看缺账单」（AK 轮改指主列表，断言不丢）",
           await missing_hits("ACORD-D2"), False)
        eq("AC26-3b 同条件的非分销行照样进「只看缺账单」（证明不是筛坏了）",
           await missing_hits("ACORD-D1"), True)
        eq("AC26-4 展开区不再带第二把尺：分销行照样查得到（只是不进缺账单）",
           len(await orders_svc.details_by_order(order_d2_id)), 1)

        await logistics.delete_bill_rows([by_no["ACWBD2"].id])
        await session.flush()
        await logistics.confirm_bill_batch(bill.batch_no)
        await session.flush()
        eq("AC26-5 批次确认后分销行的运费成本仍是 NULL（一分钱没摊给它）", (await session.execute(text(
            "SELECT freight_cost FROM ecommerce_order_detail WHERE source_detail_id='ACODD2'"))).scalar(), None)
        eq("AC26-6 同批非分销行拿到了运费（回归：摘掉一条不影响其余摊分）", (await session.execute(text(
            "SELECT freight_cost FROM ecommerce_order_detail WHERE source_detail_id='ACODD1'"))).scalar() is not None, True)
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:300]}")
    finally:
        await session.rollback()
        await session.close()

    async with async_db_session() as probe:
        residue = (await probe.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_store WHERE kuaimai_shop_id LIKE 'ACSHOP%') "
            "+ (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'ACTEST%') "
            "+ (SELECT COUNT(*) FROM ecommerce_bill_parse_rule WHERE bill_type LIKE 'actest%') "
            "+ (SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no LIKE '%actest%' "
            "   OR batch_no IN ('order-2021-11','order-2021-12','order-2021-10')) "
            "+ (SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no LIKE 'ACWB%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'ACORD-%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order_detail WHERE source_detail_id LIKE 'ACOD%')"))).scalar()
        truthy("AC30 回滚干净：库里没有残留", residue == 0, f"残留 {residue} 行")

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
