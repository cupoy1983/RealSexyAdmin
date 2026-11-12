"""2026-10-29 AF 轮（§20）改动的回滚式验证：一个事务跑完，结束显式 rollback，库里什么都不留。

编号前缀 AF，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AE 不撞号。

六件事各自要证的是：
AF1 缺失标记无条件求值，但只在值真的变了才写库（所以「无变化 N 条」不许归零）；
AF2 「没填」照旧是 None/0，「读不出来」一律带真实 Excel 行号报错；
AF3 店名/平台空着不再回落成 id/空串，自动建档写入前与手工建档同一次去空格；
AF5 四列快麦原值的钱两头都用四舍五入到分（业务方 2026-10-29 拍板=甲），不走见位进分；
AF7 定位不到表头行时说的是「找不到表头行」，不再是那句让人去文件里找不缺的列的「缺少必要的列」；
AF8 虚假行的原因只说事实。

AF5 要盯住的是三个数：`10.021` 存成 `10.02`（不是见位进分的 10.03）、`1.025` 存成 `10.03`
（不是 Python 默认凑偶的 10.02）、同一份文件跑第二次仍算「无变化」（不先量化就每轮刷一条备注）。
"""

import asyncio
import io
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.common.excel_head import read_excel_with_header_scan  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.model import ImportBatchModel  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.service import EMPTY_IMPORT_FILE_MSG  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel, CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.parsers.generic_parser import GenericExcelBillParser  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import BillColumnMapping, BillParseRuleCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.logistics.service import BillParseRuleService, LogisticsService  # noqa: E402
from app.plugin.module_ecommerce.order.importers.excel_source import ExcelOrderDataSource  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.plugin.module_ecommerce.store.service import StoreService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

SHOP_ID = "AFSHOP1"
STORE_NAME = "AF测试店铺"
PLATFORM = "AF淘宝"
MAIN = "AFTESTMAIN"
MAIN_NOCOST = "AFTESTNOCOST"
RULE_FAKE = "aftestfake"
MONTH_FLIP = "2021-11"
MONTH_BILL = "2022-06"
MONTH_AF5 = "2021-12"

BILL_HEADERS = ["运单号", "账单日期", "账单重量", "其它费用", "收件省"]
ORDER_HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号", "重量kg(SKU)",
    "成本价(SKU)", "系统实发成本", "实退成本", "订单标签",
]
PROBE = ["订单商品明细ID", "系统订单号"]

RESULTS: list[tuple[str, str, str]] = []


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


async def raises(cid: str, coro, *, contains: str, excludes: str = "") -> None:
    """断言「报的是这句话、而且不再报那句错方向的旧话」。"""
    try:
        value = await coro
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if contains not in msg:
            bad(cid, f"报错里没有「{contains}」：{type(exc).__name__}：{msg[:180]}")
        elif excludes and excludes in msg:
            bad(cid, f"报错里还留着旧那句「{excludes}」：{msg[:180]}")
        else:
            ok(cid, f"{type(exc).__name__}：{msg[:120]}")
    else:
        bad(cid, f"没有报错，返回了 {str(value)[:180]}")


class BytesUpload:
    def __init__(self, data: bytes, name: str = "af.xlsx") -> None:
        self.data = data
        self.filename = name

    async def read(self) -> bytes:
        return self.data

    async def close(self) -> None:
        return None


def xlsx(headers: list[str], rows: list[list], *, title_row: str | None = None,
         pre_rows: int = 0) -> bytes:
    """pre_rows 在表头**上面**垫若干填充行，用来造「表头落在扫描窗口之外」那份文件。"""
    wb = Workbook()
    ws = wb.active
    if title_row:
        ws.append([title_row])
    for n in range(pre_rows):
        ws.append([f"AF填充行{n}"])
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def order_row(
    idx: int, detail_id: str, *, order_no: str | None = None, paid: str = "10.00",
    refund: object = "0.00", freight: object = "0.00",
    ship_month: str = MONTH_FLIP, main: str = MAIN, ship_qty: object = 1, return_qty: object = 0,
    tracking: str | None = None, weight: object = "0.5", sku_cost: object = "10.00",
    erp_shipped_cost: object = "10.00", erp_return_cost: object = "0.00",
    tag: str | None = None, store_name: object = STORE_NAME, platform: object = PLATFORM,
) -> list:
    """一行子订单。数值格子故意做成任意类型：AF2/AF3 就靠往里塞「待补」「None」「   」来造脏格子。"""
    return [
        str(idx), f"AFOD{detail_id}", order_no or f"AFORD-{detail_id}", SHOP_ID, store_name, platform, "已完成",
        main, None, "AF测试商品", paid, refund, freight, ship_qty, return_qty,
        f"{ship_month}-05 10:00:00", "ERP发货",
        tracking if tracking is not None else f"AFWB{detail_id}", weight,
        sku_cost, erp_shipped_cost, erp_return_cost, tag,
    ]


def summary_row(paid: object, refund: object, freight: object) -> list:
    """文件末尾那行「汇总」：只有序号与三个钱格子会被读。"""
    row: list = [None] * len(ORDER_HEADERS)
    row[ORDER_HEADERS.index("序号")] = "汇总"
    row[ORDER_HEADERS.index("订单买家已付金额")] = paid
    row[ORDER_HEADERS.index("平台实退金额")] = refund
    row[ORDER_HEADERS.index("运费收入")] = freight
    return row


def parse_orders(rows: list[list], **kw) -> object:
    return ExcelOrderDataSource().fetch(contents=xlsx(ORDER_HEADERS, rows, **kw))


async def detail_cols(session, detail_id: str, columns: str) -> tuple:
    names = [c.strip() for c in columns.split(",")]
    row = (await session.execute(
        select(*(getattr(OrderDetailModel, n) for n in names)).where(
            OrderDetailModel.source_detail_id == detail_id)
    )).one()
    return tuple(row)


async def order_cols(session, order_no: str, columns: str) -> tuple:
    names = [c.strip() for c in columns.split(",")]
    row = (await session.execute(
        select(*(getattr(OrderModel, n) for n in names)).where(OrderModel.system_order_no == order_no)
    )).one()
    return tuple(row)


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        orders_svc, sku_svc = OrderService(auth, session), SkuCostService(auth, session)
        rules_svc, logistics = BillParseRuleService(auth, session), LogisticsService(auth, session)
        stores_svc = StoreService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("AFTEST%")),
            delete(BillParseRuleModel).where(BillParseRuleModel.bill_type.like("aftest%")),
            delete(StoreModel).where(StoreModel.kuaimai_shop_id.like("AFSHOP%")),
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("AFOD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("AFORD-%")),
            delete(CourierBillModel).where(CourierBillModel.waybill_no.like("AFWB%")),
            delete(ImportBatchModel).where(ImportBatchModel.batch_no.like("%aftest%")),
        ):
            await session.execute(stmt)
        await session.flush()

        col_map = BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                    bill_other_fee="其它费用", receiver_province="收件省")
        await rules_svc.create(BillParseRuleCreateSchema(bill_type=RULE_FAKE, carrier_name="AF测试快递",
                                                         column_mapping=col_map, waybill_fee=Decimal("1.00")))
        await session.flush()
        col_dict = col_map.model_dump()
        parser = GenericExcelBillParser()

        # ══ AF7：定位不到表头行时，报错要说「定位不到」══════════════════════
        junk = xlsx(["报表标题", "说明"], [["x", "y"]])
        await raises("AF7-01 订单侧探针列名一个都不在 → 报「找不到表头行」",
                     ExcelOrderDataSource().fetch(contents=junk),
                     contains="找不到同时含", excludes="缺少必要的列")
        await raises("AF7-02 那句话点名两个探针列名与硬编码的位置（改列名要改代码得说清）",
                     ExcelOrderDataSource().fetch(contents=junk), contains="excel_source.py")
        await raises("AF7-03 表头在第 11 行（超出扫描窗口）也算定位不到",
                     ExcelOrderDataSource().fetch(contents=xlsx(
                         ORDER_HEADERS, [order_row(1, "W1")], pre_rows=10)),
                     contains="找不到同时含")
        await raises("AF7-04 定位到了但真少一列 → 才说「缺少必要的列」（ZS08 回归）",
                     ExcelOrderDataSource().fetch(contents=xlsx(
                         [h for h in ORDER_HEADERS if h != "主商家编码(款)"], [order_row(1, "W2")])),
                     contains="主商家编码(款)")
        await raises("AF7-05 只有表头没有数据行 → 仍先报「文件为空」（不拿「找不到表头」吓人）",
                     ExcelOrderDataSource().fetch(contents=xlsx(ORDER_HEADERS, [])),
                     contains=EMPTY_IMPORT_FILE_MSG)

        bad_map = {**col_dict, "waybill_no": "运单号码", "bill_date": "结算日期"}
        one_bill = xlsx(BILL_HEADERS, [["AFWBX1", "2022-06-10", "0.5", "2.00", "浙江"]])
        await raises("AF7-06 账单侧规则列名配错 → 报「找不到表头行」并指向解析规则管理",
                     parser.aparse(one_bill, bad_map),
                     contains="解析规则管理", excludes="账单文件缺少必要的列")
        await raises("AF7-07 那句话把规则里实际配的名字带出来（不然还得去后台翻）",
                     parser.aparse(one_bill, bad_map), contains="运单号码")
        await raises("AF7-08 规则本身缺项先报缺项（旧结构规则不许被「找不到表头」盖过去）",
                     parser.aparse(one_bill, {k: v for k, v in col_dict.items() if k != "receiver_province"}),
                     contains="缺少必要的列映射配置")
        await raises("AF7-09 定位到了但文件真少一列 → 才说「账单文件缺少必要的列」（回归）",
                     parser.aparse(xlsx([h for h in BILL_HEADERS if h != "收件省"],
                                        [["AFWBX2", "2022-06-10", "0.5", "2.00"]]), col_dict),
                     contains="账单文件缺少必要的列")
        await raises("AF7-10 空账单文件仍先报「账单文件为空」（顺序与订单侧一致）",
                     parser.aparse(xlsx(BILL_HEADERS, []), col_dict), contains="账单文件为空")
        titled = xlsx(ORDER_HEADERS, [order_row(1, "W3")], title_row="报表标题")
        eq("AF7-11 定位成功时表头行号仍是 2（报错用的行号没跑偏）",
           read_excel_with_header_scan(titled, PROBE)[1], 2)
        eq("AF7-12 定位成功时第三个返回值是 True（正常文件不受影响）",
           read_excel_with_header_scan(titled, PROBE)[2], True)
        eq("AF7-13 定位失败时第三个返回值是 False（调用方据此说话）",
           read_excel_with_header_scan(xlsx(ORDER_HEADERS, [order_row(1, "W4")], pre_rows=10), PROBE)[2], False)

        # ══ AF2：没填照旧是空，读不出来带真实行号 ═══════════════════════════
        raw = (await parse_orders([order_row(1, "P1", weight="-", sku_cost="--",
                                            erp_shipped_cost="无", erp_return_cost="暂无",
                                            ship_qty="-", return_qty="/")]))[0].details[0]
        eq("AF2-01 重量格子写「-」→ 仍是 None（没填，不是 0）", raw.weight_kg, None)
        eq("AF2-02 成本价格子写「--」→ 仍是 None", raw.excel_cost_price, None)
        eq("AF2-03 实发成本写「无」→ 仍是 None", raw.erp_shipped_cost, None)
        eq("AF2-04 实退成本写「暂无」→ 仍是 None", raw.erp_return_cost, None)
        eq("AF2-05 数量格子写占位符 → 按 0 算（快麦无值即无数量）", (raw.ship_qty, raw.return_qty), (0, 0))

        await raises("AF2-06 成本价格子写「待补」→ 报「不是合法的数值」",
                     parse_orders([order_row(1, "P2", sku_cost="待补")]),
                     contains="成本价(SKU) 不是合法的数值")
        await raises("AF2-07 那句话带真实行号与「整份不落库」（以前是静默变 NULL）",
                     parse_orders([order_row(1, "P2", sku_cost="待补")]),
                     contains="第2行: 成本价(SKU)")
        await raises("AF2-08 带标题行时行号按真实 Excel 行算（第3行不是第2行）",
                     parse_orders([order_row(1, "P3", weight="45,6kg")],
                                  title_row="销售统计分析-按订单商品明细(系统)"),
                     contains="第3行: 重量kg(SKU)")
        await raises("AF2-09 实发数量写「一件」→ 「不是合法的整数」",
                     parse_orders([order_row(1, "P4", ship_qty="一件")]),
                     contains="系统实发商品数 不是合法的整数")
        await raises("AF2-10 实退成本写「见售后」→ 带行号报错（这一列以前静默变 None）",
                     parse_orders([order_row(1, "P5", erp_return_cost="见售后")]),
                     contains="第2行: 实退成本")
        await raises("AF2-11 报错里把读到的原文带出来（不然不知道哪格长什么样）",
                     parse_orders([order_row(1, "P6", sku_cost="面议")]),
                     contains="读到「面议」")

        # ══ AF3：店名/平台空着不再回落成 id/空串 ════════════════════════════
        await raises("AF3-01 店铺全称空着 → 带行号报错（以前会造一家名叫 AFSHOP1 的店）",
                     parse_orders([order_row(1, "S1", store_name=None)]),
                     contains="店铺全称不能为空", excludes="")
        await raises("AF3-02 那句话把快麦店铺id 一起报出来（不然不知道哪家店）",
                     parse_orders([order_row(1, "S1", store_name=None)]), contains="AFSHOP1")
        await raises("AF3-03 店铺平台空着 → 带行号报错（以前传空串，按平台筛就漏它）",
                     parse_orders([order_row(1, "S2", platform=None)]), contains="店铺平台不能为空")
        await raises("AF3-04 纯空格也算空（与手工建档同一把尺）",
                     parse_orders([order_row(1, "S3", platform="   ")]), contains="店铺平台不能为空")
        await raises("AF3-05 带标题行时那句拒绝用的也是真实行号（第3行不是第2行）",
                     parse_orders([order_row(1, "S4", platform=None)], title_row="报表标题"),
                     contains="第3行: 店铺平台不能为空")

        await sku_svc.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="AF测试商品",
                                                 cost_price=Decimal("10.00")))
        await session.flush()
        await orders_svc.import_orders(ship_month=MONTH_FLIP, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "F1", main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None,
                      erp_return_cost=None, tag="拼多多分销订单,已生成采购单"),
            order_row(2, "F2", tag="预发货"),
            order_row(3, "F3", main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None,
                      erp_return_cost=None, tag="预发货"),
        ])))
        await session.flush()
        stored = (await session.execute(select(StoreModel).where(
            StoreModel.kuaimai_shop_id == SHOP_ID))).scalars().one()
        eq("AF3-06 自动建档落的是 Excel 里的店铺全称（不是快麦编码）", stored.store_name, STORE_NAME)
        eq("AF3-07 平台落的也是 Excel 原值（不是空串）", stored.platform, PLATFORM)

        store = await stores_svc.ensure_store(kuaimai_shop_id="AFSHOP2", store_name="  AF带空格店  ",
                                              platform=" AF拼多多 ")
        await session.flush()
        eq("AF3-08 ensure_store 写入前 trim 店名（与 _trim_required 同一把尺）", store.store_name, "AF带空格店")
        eq("AF3-09 ensure_store 写入前 trim 平台", store.platform, "AF拼多多")
        again = await stores_svc.ensure_store(kuaimai_shop_id="AFSHOP2", store_name="AF带空格店",
                                              platform="AF拼多多")
        eq("AF3-10 trim 后再比较：trim 过的同一家店不会被开成两个门牌", again.id, store.id)
        eq("AF3-11 「不同即覆盖」保留（ADR-0024 没改这条）", again.store_name, "AF带空格店")
        for cid, shop, name, platform in (
            ("AF3-12 ensure_store 对纯空格的店名直接拒绝", "AFSHOP3", "   ", "AF淘宝"),
            ("AF3-13 ensure_store 对空平台直接拒绝（不许留空串）", "AFSHOP4", "AF第四店", ""),
        ):
            try:
                await stores_svc.ensure_store(kuaimai_shop_id=shop, store_name=name, platform=platform)
                bad(cid, "没报错")
            except CustomException as exc:
                ok(cid, str(exc)[:60])

        # ══ AF1：缺失标记无条件求值，但只在值变了才写库 ═════════════════════
        before = await detail_cols(session, "AFODF1", "distribution_order_type, cost_missing, cost_price")
        eq("AF1-00 翻转前：分销行为 1、不进待补充清单（AC21 口径回归）", before[:2], (1, False))
        eq("AF1-0b 翻转前 cost_price 是 NULL（AF1 要治的就是这一格）", before[2], None)

        upd = await orders_svc.update_orders(ship_month=MONTH_FLIP, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "F1", main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None,
                      erp_return_cost=None, tag="预发货"),
            order_row(2, "F2", tag="预发货"),
            order_row(3, "F3", main=MAIN_NOCOST, sku_cost=None, erp_shipped_cost=None,
                      erp_return_cost=None, tag="预发货"),
        ])))
        await session.flush()
        after = await detail_cols(session, "AFODF1", "distribution_order_type, cost_missing, computed_product_cost")
        truthy("AF1-01 更新成功（不是整批中止）", upd.ok, upd.msg[:160])
        eq("AF1-02 刷回非分销后标记=0", after[0], 0)
        eq("AF1-03 库里 NULL、主表也查不到 → cost_missing 刷成 True（以前整段被跳过）", after[1], True)
        eq("AF1-04 这行仍按 0 成本进利润（数字没变，变的是它现在进得了清单）", str(after[2]), "0.00")
        missing_list = (await session.execute(select(OrderDetailModel.source_detail_id).where(
            OrderDetailModel.cost_missing.is_(True),
            OrderDetailModel.source_detail_id.like("AFOD%")).order_by(OrderDetailModel.source_detail_id)
        )).scalars().all()
        eq("AF1-05 翻转那条现在进「待补充成本」清单了（清单就是按 cost_missing 筛的）",
           list(missing_list), ["AFODF1", "AFODF3"])
        truthy("AF1-06 无条件求值不许把每一行都判成有变动：F2/F3 仍算无变化",
               "另有 2 单无变化" in upd.msg, upd.msg[-200:])
        eq("AF1-07 成本价没变的 F2 照旧 cost_missing=False（不无故刷成新标记）",
           (await detail_cols(session, "AFODF2", "cost_missing"))[0], False)

        # ══ AF8：虚假行的原因只说事实 ══════════════════════════════════════
        bill = await logistics.import_bill(file=BytesUpload(xlsx(BILL_HEADERS, [
            ["AFWBNOMATCH", "2022-06-11", "0.5", "2.00", "浙江"]])),
            bill_type=RULE_FAKE, bill_month=MONTH_BILL)
        await session.flush()
        fake = (await session.execute(select(CourierBillModel).where(
            CourierBillModel.waybill_no == "AFWBNOMATCH"))).scalars().one()
        eq("AF8-01 挂不上子订单 → 判虚假（回归）", fake.status, "fake")
        eq("AF8-02 原因只说「匹配只看非分销的ERP发货子订单」，不替业务方猜这单是分销"
           "（库里带写账单行时统一加的「第N行：」前缀，§20.6）",
           fake.anomaly_remark, "第1行：无对应ERP发货子订单（匹配只看非分销的ERP发货子订单）")
        truthy("AF8-03 库里这句话不再留着旧的「（分销订单不参与匹配）」尾巴",
               "（分销订单不参与匹配）" not in (fake.anomaly_remark or ""), str(fake.anomaly_remark)[:120])
        batch_remark = (await session.execute(select(ImportBatchModel.remark).where(
            ImportBatchModel.batch_no == bill.batch_no))).scalar() or ""
        truthy("AF8-04 改文案不影响批次对账备注里的虚假计数那句话", "虚假 1" in batch_remark, batch_remark[-160:])

        # ══ AF5=甲：四列快麦原值的钱，两条路径都用四舍五入到分 ═══════════════
        # 三个数盯死这把尺的方向：10.021→10.02（不是见位进分的 10.03）、1.025→1.03（不是凑偶的 1.02）、
        # 同一份文件跑第二次仍算「无变化」（不先量化就每轮刷一条备注）。
        async def one(**kw):  # noqa: ANN001, ANN202
            return (await parse_orders([order_row(1, "Q1", **kw)]))[0].details[0]

        eq("AF5-01 买家已付 10.021 → 10.02（四舍五入，不走进位进分）", str((await one(paid="10.021")).paid_amount), "10.02")
        eq("AF5-02 买家已付 1.025 → 1.03（方向写死：Python 默认凑偶会给 1.02）", str((await one(paid="1.025")).paid_amount), "1.03")
        eq("AF5-03 平台实退 0.004 → 0.00", str((await one(refund="0.004")).refund_amount), "0.00")
        eq("AF5-04 运费收入 0.006 → 0.01（订单级金额搭同一把尺，Z2 才成立）", str((await one(freight="0.006")).freight_income), "0.01")
        eq("AF5-05 系统实发成本 7.721 → 7.72（不进分）", str((await one(erp_shipped_cost="7.721")).erp_shipped_cost), "7.72")
        eq("AF5-06 实退成本 0.004 → 0.00", str((await one(erp_return_cost="0.004")).erp_return_cost), "0.00")
        eq("AF5-07 两格空着仍是 None（整分没把「没填」吃成 0.00）",
           ((await one(erp_shipped_cost=None)).erp_shipped_cost, (await one(erp_return_cost=None)).erp_return_cost),
           (None, None))
        eq("AF5-08 成本价(SKU) 3.1109 原样保留 4 位（§15.1 不适用的那一列）", str((await one(sku_cost="3.1109")).excel_cost_price), "3.1109")
        eq("AF5-09 重量 0.5004 → 0.500（AG 轮修订原断言：ADR-0039 之后每一格都在读取那一步"
           "量到**自己那一列**的精度，重量是 3 位，不再是原样透传）",
           str((await one(weight="0.5004")).weight_kg), "0.500")

        af5_rows = [
            order_row(1, "G1", order_no="AFORD-G", paid="10.004", freight="0.004", ship_month=MONTH_AF5),
            order_row(2, "G2", order_no="AFORD-G", paid="10.004", freight="0.004", ship_month=MONTH_AF5),
            order_row(3, "G3", order_no="AFORD-H", main=MAIN_NOCOST, sku_cost="3.1109",
                      erp_shipped_cost="7.721", erp_return_cost="0.004",
                      tag="拼多多分销订单,已生成采购单", ship_month=MONTH_AF5),
            order_row(4, "J1", order_no="AFORD-J", paid="10.029", ship_month=MONTH_AF5),
        ]
        af5_bytes = xlsx(ORDER_HEADERS, af5_rows)
        await orders_svc.import_orders(ship_month=MONTH_AF5, file=BytesUpload(af5_bytes))
        await session.flush()
        paid_pair = (await session.execute(
            select(OrderDetailModel.paid_amount).where(
                OrderDetailModel.source_detail_id.in_(["AFODG1", "AFODG2"])).order_by(
                OrderDetailModel.source_detail_id))).scalars().all()
        eq("AF5-10 每条子订单行各 10.00（两行都先整分再落库）", [str(x) for x in paid_pair], ["10.00", "10.00"])
        eq("AF5-11 主订单已付 = Σ 两行 = 20.00（旧口径先加 20.008 交给 MySQL → 20.01，Z2 破）",
           str((await order_cols(session, "AFORD-G", "buyer_paid_amount"))[0]), "20.00")
        eq("AF5-12 主订单运费收入同理是 0.00（旧口径 0.008 → 0.01）",
           str((await order_cols(session, "AFORD-G", "freight_income"))[0]), "0.00")
        dist5 = await detail_cols(session, "AFODG3", "erp_shipped_cost, computed_product_cost, "
                                                    "erp_return_cost, computed_return_cost, cost_price")
        eq("AF5-13 分销行：原值与 computed_* 逐字相等（都是 7.72）", str(dist5[0]), str(dist5[1]))
        eq("AF5-14 分销行：退货成本那一对也逐字相等（都是 0.00）", str(dist5[2]), str(dist5[3]))
        eq("AF5-15 分销行 7.721 落库是 7.72（不是见位进分的 7.73）", str(dist5[0]), "7.72")
        eq("AF5-16 分销行成本价 4 位原值进得了库", str(dist5[4]), "3.1109")
        eq("AF5-17 库里 10.029 已按四舍五入存成 10.03（更新路径要拿它当旧值比）",
           str((await detail_cols(session, "AFODJ1", "paid_amount"))[0]), "10.03")

        upd5 = await orders_svc.update_orders(ship_month=MONTH_AF5, file=BytesUpload(af5_bytes))
        await session.flush()
        truthy("AF5-18 同一份文件跑第二次：3 单（4 条子订单行）全算无变化（不先量化就每轮刷一条备注）",
               "另有 3 单无变化" in upd5.msg and upd5.ok, upd5.msg[-200:])
        remarks = (await session.execute(select(OrderDetailModel.update_remark).where(
            OrderDetailModel.source_detail_id.in_(["AFODG1", "AFODG2", "AFODG3", "AFODJ1"])))).scalars().all()
        truthy("AF5-19 重跑没留下任何「已付金额」伪变动痕迹",
               not any("已付金额" in (r or "") for r in remarks), str(remarks)[:200])

        upd5b = await orders_svc.update_orders(ship_month=MONTH_AF5, file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(4, "J1", order_no="AFORD-J", paid="10.021", ship_month=MONTH_AF5)])))
        await session.flush()
        truthy("AF5-20 更新能改这一格（不是整批中止）", upd5b.ok, upd5b.msg[:160])
        eq("AF5-21 更新侧也用四舍五入：10.021 刷成 10.02，不是见位进分的 10.03",
           str((await detail_cols(session, "AFODJ1", "paid_amount"))[0]), "10.02")
        remark_j1 = (await detail_cols(session, "AFODJ1", "update_remark"))[0] or ""
        truthy("AF5-22 备注里写的是两个分精度值（不拿带第 3 位的原始值吓人）",
               "已付金额 10.03→10.02" in remark_j1, remark_j1[:200])

        # 汇总行两侧同尺（§20.4 ④）：快麦自己的汇总也带第 3 位时不该误报；但它按进位报上来时必须拦
        eq("AF5-23 数据行 10.004 + 汇总行也写 10.004 → 两侧同尺，导入通过",
           str((await parse_orders([order_row(1, "K1", paid="10.004"), summary_row("10.004", "0.00", "0.00")]))[0]
               .buyer_paid_amount), "10.00")
        await raises("AF5-24 快麦按它自己的加法式把汇总报成 20.01 → 报「汇总不一致」摆上台面（记着的代价）",
                     parse_orders([order_row(1, "K2", order_no="AFORD-K", paid="10.004"),
                                   order_row(2, "K3", order_no="AFORD-K", paid="10.004"),
                                   summary_row("20.01", "0.00", "0.00")]),
                     contains="与「汇总」行不一致")
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:400]}")
    finally:
        await session.rollback()
        await session.close()

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    fails = sum(1 for s, _, _ in RESULTS if s == "FAIL")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {fails} 项")


asyncio.run(main())
