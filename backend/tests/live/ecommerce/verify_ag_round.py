"""2026-10-29 AG 轮（§21 / ADR-0039「读文件即量化」）的回滚式验证。

编号前缀 AG，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AD/AE/AF 不撞号。

本轮的规矩只有一句：每个「文件格子 → 落库列」的数值，在**读那一格那一步**就量化到那一列的精度，
方向写死四舍五入；比较、求和、告警、落库只用这个值，导入与更新调同一个函数。
所以本轮唯一算「真做过」的证明是一条**不变式**（AG2）：同一份文件先导入、紧接着用同一份跑更新，
必须报「无变化」，且每个数值字段落库的数字与「只导入」时逐字相等。

各组要证的是：
AG0 尺子本体：精度取自列、方向是四舍五入（远离零），不是 Python 默认的凑偶；
AG1 订单解析层：重量整到 3 位、成本价整到 4 位、钱整到分，都在读那一格就发生；
AG2 幂等不变式：同一份文件「导入→更新」报无变化、数字逐字不变（AG1 那个重量 bug 的复发闸）；
AG3 _diff_detail 直调：生产者忘了量化也不许改写库里的数字（第二道闸）；
AG4 账单侧：账单行、合计行、批次备注、库里的行同一把尺；负数闸不被「-0.00」蒙过去；
AG5 摊分：份额方向写死，Σ份额 = 整票金额 这条不变式照旧成立；
AG6 尺子与库对齐探针：每个 DECIMAL 列都配了尺（没配就失败），MySQL 舍入方向实测就是四舍五入
    → 存量数字全是新尺子的不动点，不需要数据订正、不需要重跑导入。
"""

import asyncio
import io
import sys
from decimal import ROUND_HALF_EVEN, Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.model import ImportBatchModel  # noqa: E402
from app.plugin.module_ecommerce.common.numbers import (  # noqa: E402
    COST_QUANT,
    MONEY_QUANT,
    WEIGHT_QUANT,
    quantize_half_up,
    round_cent,
    round_cost,
    round_weight,
)
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel, CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.parsers.generic_parser import GenericExcelBillParser  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import BillColumnMapping, BillParseRuleCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.logistics.service import (  # noqa: E402
    BillParseRuleService, LogisticsService, _apportion)
from app.plugin.module_ecommerce.order.importers.base import RawOrderDetailRecord  # noqa: E402
from app.plugin.module_ecommerce.order.importers.excel_source import ExcelOrderDataSource  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

SHOP_ID = "AGSHOP1"
STORE_NAME = "AG测试店铺"
PLATFORM = "AG淘宝"
MAIN = "AGTESTMAIN"      # 主表成本 10.00
MAIN4 = "AGTESTCOST4"    # 主表成本 3.11（2 位，收列后主表只准到分），配快麦那侧的 5 位原值用
RULE_FAKE = "agtestfake"
MONTH_A = "2021-10"      # 幂等不变式（设计上不产生告警）
MONTH_B = "2021-09"      # 真该报告警的那一行
MONTH_C = "2021-08"      # 分销行
MONTH_BILL = "2022-07"

BILL_HEADERS = ["运单号", "账单日期", "账单重量", "其它费用", "收件省"]
ORDER_HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号", "重量kg(SKU)",
    "成本价(SKU)", "系统实发成本", "实退成本", "订单标签",
]

# 幂等不变式要比的子订单数值列，一个都不许漏（第 15 项是更新备注：写了变动就说明数字被动过）
SNAP_COLS = [
    "weight_kg", "cost_price", "excel_cost_price", "paid_amount", "refund_amount",
    "erp_shipped_cost", "erp_return_cost", "computed_product_cost", "computed_return_cost",
    "freight_cost", "ship_qty", "return_qty", "cost_missing", "distribution_order_type", "update_remark",
]
ORDER_SNAP_COLS = [
    "buyer_paid_amount", "refund_amount", "freight_income", "shipped_cost_amount",
    "return_cost_amount", "freight_cost_amount",
]

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
    def __init__(self, data: bytes, name: str = "ag.xlsx") -> None:
        self.data = data
        self.filename = name

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


def order_row(
    idx: int, detail_id: str, *, order_no: str | None = None, paid: object = "10.00",
    refund: object = "0.00", freight: object = "0.00",
    ship_month: str = MONTH_A, main: str = MAIN, ship_qty: object = 1, return_qty: object = 0,
    weight: object = "0.5", sku_cost: object = "10.00",
    erp_shipped_cost: object = "10.00", erp_return_cost: object = "0.00",
    tag: str | None = None,
) -> list:
    return [
        str(idx), f"AGOD{detail_id}", order_no or f"AGORD-{detail_id}", SHOP_ID, STORE_NAME, PLATFORM,
        "已完成", main, None, "AG测试商品", paid, refund, freight, ship_qty, return_qty,
        f"{ship_month}-05 10:00:00", "ERP发货", f"AGWB{detail_id}", weight,
        sku_cost, erp_shipped_cost, erp_return_cost, tag,
    ]


def parse_orders(rows: list[list]) -> object:
    return ExcelOrderDataSource().fetch(contents=xlsx(ORDER_HEADERS, rows))


async def snap_detail(session, detail_id: str) -> tuple[str, ...]:
    """按**新查询**取快照（EXPIRE_ON_COMMIT=False 时 ORM 对象上的值可能是旧的，一律走 select 现读）。"""
    row = (await session.execute(
        select(*(getattr(OrderDetailModel, n) for n in SNAP_COLS)).where(
            OrderDetailModel.source_detail_id == detail_id)
    )).one()
    return tuple("None" if v is None else str(v) for v in row)


async def snap_order(session, order_no: str) -> tuple[str, ...]:
    row = (await session.execute(
        select(*(getattr(OrderModel, n) for n in ORDER_SNAP_COLS)).where(
            OrderModel.system_order_no == order_no)
    )).one()
    return tuple("None" if v is None else str(v) for v in row)


def bare(**kw) -> OrderDetailModel:  # noqa: ANN001, ANN201 —— 内存对象，不进 session
    row = OrderDetailModel()
    kw.setdefault("paid_amount", Decimal("0.00"))
    kw.setdefault("refund_amount", Decimal("0.00"))
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def raw_detail(**kw) -> RawOrderDetailRecord:  # noqa: ANN001, ANN201
    return RawOrderDetailRecord(source_detail_id="AGX", sku_code=None, product_name="AG", **kw)


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        orders_svc, sku_svc = OrderService(auth, session), SkuCostService(auth, session)
        rules_svc, logistics = BillParseRuleService(auth, session), LogisticsService(auth, session)
        parser = GenericExcelBillParser()
        col_map = BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                    bill_other_fee="其它费用", receiver_province="收件省")
        col_dict = col_map.model_dump()

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("AGTEST%")),
            delete(BillParseRuleModel).where(BillParseRuleModel.bill_type.like("agtest%")),
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("AGOD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("AGORD-%")),
            delete(CourierBillModel).where(CourierBillModel.waybill_no.like("AGWB%")),
            delete(ImportBatchModel).where(ImportBatchModel.batch_no.like("%agtest%")),
        ):
            await session.execute(stmt)
        await session.flush()

        # ══ AG0：尺子本体——精度取自列、方向四舍五入（远离零），不是 Python 默认凑偶 ══
        # 每条都配一个「默认凑偶会给什么」的对照值，不然读者看不出这条断言在拦什么。
        ruler_cases = (
            (round_weight, WEIGHT_QUANT, "0.0025", "0.003", "0.002", "重量 0.0025 → 0.003"
             "（凑偶给 0.002，那正是本轮改掉存量数字的那条路）"),
            (round_weight, WEIGHT_QUANT, "0.0165", "0.017", "0.016", "重量 0.0165 → 0.017"),
            (round_weight, WEIGHT_QUANT, "0.3105", "0.311", "0.310", "重量 0.3105 → 0.311"),
            (round_weight, WEIGHT_QUANT, "0.0045", "0.005", "0.004", "重量 0.0045 → 0.005"),
            (round_weight, WEIGHT_QUANT, "-0.0025", "-0.003", "-0.002",
             "重量 -0.0025 → -0.003：远离零，不是朝零"),
            (round_cost, COST_QUANT, "3.11085", "3.1109", "3.1108", "成本价 3.11085 → 3.1109"),
            (round_cost, COST_QUANT, "3.11105", "3.1111", "3.1110", "成本价 3.11105 → 3.1111"),
            (round_cent, MONEY_QUANT, "1.025", "1.03", "1.02", "钱 1.025 → 1.03（AF5 回归）"),
            (round_cent, MONEY_QUANT, "0.125", "0.13", "0.12", "钱 0.125 → 0.13（摊分单份那一格）"),
            (round_cent, MONEY_QUANT, "-1.025", "-1.03", "-1.02", "钱 -1.025 → -1.03"),
        )
        for n, (fn, quant, src, expect, even, label) in enumerate(ruler_cases, start=1):
            got = str(fn(Decimal(src)))
            eq(f"AG0-{n:02d} {label}（默认凑偶给 {even}）", got, expect)
            eq(f"AG0-{n:02d}b 对照：Python 默认 quantize 确实给 {even}（这条断言不是同义反复）",
               str(Decimal(src).quantize(quant, rounding=ROUND_HALF_EVEN)), even)

        eq("AG0-11 已落在格子上的值不动（0.5 → 0.500 是补位不是改数）", str(round_weight(Decimal("0.5"))), "0.500")
        eq("AG0-12 三把精度就是三列的 scale：分 / 千克后三位 / 成本价第四位",
           (str(MONEY_QUANT), str(WEIGHT_QUANT), str(COST_QUANT)), ("0.01", "0.001", "0.0001"))
        eq("AG0-13 量化幂等（量两次 = 量一次，重跑导入不会一路进位）",
           str(round_weight(round_weight(Decimal("0.0025")))), str(round_weight(Decimal("0.0025"))))

        # ══ AG1：订单侧「读那一格那一步」就量化 ══════════════════════════════
        async def one(**kw):  # noqa: ANN001, ANN202
            return (await parse_orders([order_row(1, "Q1", **kw)]))[0].details[0]

        eq("AG1-01 重量 0.0025 读出来就是 0.003（以前带 4 位往下走，落库才由 MySQL 舍）",
           str((await one(weight="0.0025")).weight_kg), "0.003")
        eq("AG1-02 重量 0.0165 → 0.017", str((await one(weight="0.0165")).weight_kg), "0.017")
        eq("AG1-03 成本价 3.11094 → 3.1109（整到列的 4 位，不是整到分）",
           str((await one(sku_cost="3.11094")).excel_cost_price), "3.1109")
        eq("AG1-04 系统实发成本 7.721 → 7.72（原值列不管见位进分）",
           str((await one(erp_shipped_cost="7.721")).erp_shipped_cost), "7.72")
        eq("AG1-05 实退成本 0.004 → 0.00", str((await one(erp_return_cost="0.004")).erp_return_cost), "0.00")
        eq("AG1-06 买家已付 10.021 → 10.02（AF5 那把尺没被本轮改动带走）",
           str((await one(paid="10.021")).paid_amount), "10.02")
        eq("AG1-07 运费收入 0.006 → 0.01", str((await one(freight="0.006")).freight_income), "0.01")
        eq("AG1-08 空着的两格仍是 None（整位没把「没填」吃成 0）",
           ((await one(weight=None)).weight_kg, (await one(sku_cost=None)).excel_cost_price), (None, None))
        eq("AG1-09 已经正好落在格子上的值不动（0.003 → 0.003）", str((await one(weight="0.003")).weight_kg), "0.003")

        # ══ 主数据：10.00 与 3.11 两档成本 ═══════════════════════════════════
        # 主表那一列 2026-11-06 收成 Numeric(12,2)（§26.13 第 1 条），3.1109 这种值**建不进主表了**：
        # 列 scale=2，而且 AN16 那道录入闸会先把第 3 位非零挡回来。于是「两侧同尺」这件事换了个形状，
        # 而不是消失了——快麦那一格仍按它落库的列存 4 位，主表只有 2 位，比的是两个**原始价**：
        # 「文件 3.11094 vs 主表 3.11」= 真不一致，必须报（AN28 甲：先把快麦那侧整到 2 位再比，
        # 等于把要提示「去快麦改对」的那一半抹掉）；「文件 3.110 vs 主表 3.11」= 同一个数，不报
        # （`Decimal` 相等不看尾零）。原先那条「3.11094 对 3.1109 不报」的断言随收列作废，改指的就是这两格。
        await sku_svc.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="AG测试商品",
                                                cost_price=Decimal("10.00")))
        session.add(SkuCostModel(main_sku_code=MAIN4, sku_code=None, sku_name=None,
                                 product_name="AG两位商品", cost_price=Decimal("3.11"),
                                 cost_source="manual", remark=None))
        await session.flush()

        # ══ AG2：幂等不变式（本轮唯一算「真做过」的证明）══════════════════════
        file_a = xlsx(ORDER_HEADERS, [
            order_row(1, "I1", order_no="AGORD-IDEM", weight="0.0025", paid="10.004", ship_month=MONTH_A),
            order_row(2, "I2", order_no="AGORD-IDEM", weight="0.0165", paid="10.004", ship_month=MONTH_A),
            order_row(3, "I3", order_no="AGORD-IDEM", main=MAIN4, weight="0.3105", sku_cost="3.11094",
                      erp_shipped_cost="3.12", ship_month=MONTH_A),
        ])
        msg_a = await orders_svc.import_orders(ship_month=MONTH_A, file=BytesUpload(file_a))
        await session.flush()
        truthy("AG2-01 导入成功", "成功导入" in msg_a, msg_a[:160])
        truthy("AG2-02 文件 5 位 3.11094 对 2 位主表 3.11 → **现在必须报**（收列后是真不一致，AN28 原值直接比）",
               "成本价(SKU) 3.1109 与系统成本价 3.11 不一致" in msg_a, msg_a[-220:])
        weights_a = {d: (await snap_detail(session, d))[0] for d in ("AGODI1", "AGODI2", "AGODI3")}
        eq("AG2-03 落库的重量是量好的 3 位：I1=0.003、I2=0.017、I3=0.311（不是文件里的 4 位）",
           tuple(weights_a[d] for d in ("AGODI1", "AGODI2", "AGODI3")),
           ("0.003", "0.017", "0.311"))
        eq("AG2-04 成本价那一列量到 4 位（I3 excel_cost_price=3.1109）",
           (await snap_detail(session, "AGODI3"))[2], "3.1109")
        eq("AG2-05 主订单 = Σ**已量化**的子订单行：10.004+10.004+10.00 → 30.00（先求和再舍给 30.01）",
           (await snap_order(session, "AGORD-IDEM"))[0], "30.00")

        before = {d: await snap_detail(session, d) for d in ("AGODI1", "AGODI2", "AGODI3")}
        order_before = await snap_order(session, "AGORD-IDEM")

        upd_a = await orders_svc.update_orders(ship_month=MONTH_A, file=BytesUpload(file_a))
        await session.flush()
        truthy("AG2-06 同一份文件跑更新成功（不是整批中止）", upd_a.ok, upd_a.msg[:160])
        truthy("AG2-07 三条子订单全算「无变化」（不先量化就每轮刷一条备注）",
               "另有 1 单无变化" in upd_a.msg and "成功更新 0 单" in upd_a.msg, upd_a.msg[-220:])
        after = {d: await snap_detail(session, d) for d in before}
        for n, d in enumerate(before, start=8):
            eq(f"AG2-0{n} {d} 的 15 个数值列逐字未变（含重量/成本价/两条派生成本/更新备注）",
               after[d], before[d])
        eq("AG2-11 主订单六个聚合数逐字未变", await snap_order(session, "AGORD-IDEM"), order_before)
        eq("AG2-12 更新备注一条都没写（写了就说明有数值被改写过）",
           sorted({after[d][14] for d in after}), ["None"])
        truthy("AG2-13 结果里没有「重量kg」变动那句话（AG1 那个 bug 的直接指纹）",
               "重量kg" not in upd_a.msg, upd_a.msg[-220:])
        truthy("AG2-14 结果里没有「已付金额」变动那句话", "已付金额" not in upd_a.msg, upd_a.msg[-220:])

        upd_a2 = await orders_svc.update_orders(ship_month=MONTH_A, file=BytesUpload(file_a))
        await session.flush()
        after3 = {d: await snap_detail(session, d) for d in before}
        truthy("AG2-15 第三遍更新仍报「无变化」、数字仍逐字相等（不变式不是只有第二遍成立）",
               "另有 1 单无变化" in upd_a2.msg and all(after3[d] == before[d] for d in before),
               upd_a2.msg[-200:])

        # ══ AG2b：真该报的照报、不该报的别报（不许把沉默当成「修好了」）════════
        file_b = xlsx(ORDER_HEADERS, [
            order_row(1, "W1", order_no="AGORD-WARN", main=MAIN4, sku_cost="3.11095",
                      weight="0.0025", erp_shipped_cost="3.12", ship_month=MONTH_B),
            order_row(2, "W2", order_no="AGORD-WARN", main=MAIN4, sku_cost="3.110",
                      weight="0.0025", erp_shipped_cost="3.12", ship_month=MONTH_B),
        ])
        msg_b = await orders_svc.import_orders(ship_month=MONTH_B, file=BytesUpload(file_b))
        await session.flush()
        truthy("AG2-16 3.11095 报、尾零 3.110 不报：主表 2 位 vs 快麦 4 位，只报那一格真的不同的（AG 的老断言随收列改指这里）",
               "成本价(SKU) 3.1110 与系统成本价 3.11 不一致" in msg_b and msg_b.count("与系统成本价") == 1,
               msg_b[-260:])
        truthy("AG2-17 那句话里的文件价是量过的 3.1110，不是 5 位原值（告警报的就是库里那个数）",
               "3.11095" not in msg_b, msg_b[-260:])
        upd_b = await orders_svc.update_orders(ship_month=MONTH_B, file=BytesUpload(file_b))
        await session.flush()
        truthy("AG2-18 这一行同样「导入→更新」无变化", "另有 1 单无变化" in upd_b.msg, upd_b.msg[-200:])

        # ══ AG2c：分销行（三个数取 Excel 原值，走的是同一条尺子）════════════════
        file_c = xlsx(ORDER_HEADERS, [
            order_row(1, "D1", order_no="AGORD-DIST", main=MAIN4, weight="0.0165", sku_cost="3.11094",
                      erp_shipped_cost="7.721", erp_return_cost="0.004",
                      tag="拼多多分销订单,已生成采购单", ship_month=MONTH_C),
        ])
        msg_c = await orders_svc.import_orders(ship_month=MONTH_C, file=BytesUpload(file_c))
        await session.flush()
        d1 = await snap_detail(session, "AGODD1")
        truthy("AG2-19 分销行不报成本告警（原值列与派生成本两侧同尺）",
               "成本不一致告警" not in msg_c, msg_c[-220:])
        eq("AG2-20 分销行重量量到 3 位（0.0165 → 0.017）", d1[0], "0.017")
        eq("AG2-21 分销行成本价取 Excel 值并量到 4 位（3.11094 → 3.1109）", d1[1], "3.1109")
        eq("AG2-22 分销行实发成本量到分（7.721 → 7.72）", d1[5], "7.72")
        eq("AG2-23 派生成本 = 量好的原值再走见位进分：7.72（不是拿 7.721 进出来的 7.73）", d1[7], "7.72")
        upd_c = await orders_svc.update_orders(ship_month=MONTH_C, file=BytesUpload(file_c))
        await session.flush()
        truthy("AG2-24 分销行也「导入→更新」无变化", "另有 1 单无变化" in upd_c.msg, upd_c.msg[-200:])
        eq("AG2-25 分销行数值逐字未变", await snap_detail(session, "AGODD1"), d1)

        # ══ AG3：_diff_detail 直调——生产者忘了量化也不许改写库里的数字 ═══════════
        # AG1 已经把源值量好了，这一组是第二道闸：假想一个没量的生产者（快麦 API 接入、
        # 以后新增的数据源），比较这一步用的仍是同一把尺，库里的数字不会被挪走。
        changes, data = OrderService._diff_detail(
            bare(weight_kg=Decimal("0.003"), paid_amount=Decimal("10.02")),
            raw_detail(weight_kg=Decimal("0.0025"), paid_amount=Decimal("10.021")), "ts")
        eq("AG3-01 库里 0.003 / 文件 0.0025（未量化的生产者）→ 无变化"
           "（旧代码在这里判成变动，并把库里的 0.003 改写成 0.002）", (changes, data), ([], {}))
        changes, data = OrderService._diff_detail(
            bare(weight_kg=Decimal("0.003")), raw_detail(weight_kg=Decimal("0.0045")), "ts")
        eq("AG3-02 真改了重量才报，且写回的是量好的 0.005（不是 0.0045 原样落库）",
           (len(changes), str(data["weight_kg"])), (1, "0.005"))
        truthy("AG3-03 备注里写的是量过的两个数（0.003→0.005），不是 4 位原值",
               "重量kg 0.003→0.005" in changes[0], changes[0])
        changes, data = OrderService._diff_detail(
            bare(paid_amount=Decimal("10.02")), raw_detail(paid_amount=Decimal("10.015")), "ts")
        eq("AG3-04 钱那一格同样：库里 10.02 / 文件 10.015（量后 10.02）→ 无变化", (changes, data), ([], {}))
        changes, _ = OrderService._diff_detail(bare(weight_kg=None),
                                               raw_detail(weight_kg=Decimal("0.0025")), "ts")
        eq("AG3-05 库里空着、文件填了 → 仍算变动（同尺没把「补齐」这条路堵掉）", len(changes), 1)
        changes, _ = OrderService._diff_detail(bare(weight_kg=Decimal("0.003")),
                                               raw_detail(weight_kg=None), "ts")
        eq("AG3-06 库里填着、文件空了 → 仍算变动（None 不参与量化，也不会报错）", len(changes), 1)

        # ══ AG4：账单侧同一把尺 ══════════════════════════════════════════════
        parsed = await parser.aparse(
            xlsx(BILL_HEADERS, [["AGWBQ1", "2022-07-10", "0.0025", "1.005", "浙江"]]), col_dict)
        eq("AG4-01 账单重量 0.0025 解析出来就是 0.003（以前内存里带原精度、落库由 MySQL 舍，"
           "批次备注与库里的行各说一套）", str(parsed[0].bill_weight), "0.003")
        eq("AG4-02 其它费用 1.005 → 1.01（钱整到分，方向与订单侧同一个）", str(parsed[0].bill_other_fee), "1.01")
        eq("AG4-03 空重量仍是 None（「没填」与 0.000 分得开，重量异常判定靠的就是这个）",
           str((await parser.aparse(xlsx(BILL_HEADERS, [["AGWBQ2", "2022-07-10", "-", "2.00", "浙江"]]),
                                    col_dict))[0].bill_weight), "None")
        await raises("AG4-04 负重量 -0.0004 量后是 -0.000：它不 < 0，闸判的是符号，脏值不许静默收成 0",
                     parser.aparse(xlsx(BILL_HEADERS, [["AGWBQ3", "2022-07-10", "-0.0004", "2.00", "浙江"]]),
                                   col_dict),
                     contains="账单重量不能为负")
        await raises("AG4-05 其它费用 -0.001 量后是 -0.00，「假值」会被 or Decimal(\"0.00\") 换成正 0.00"
                     "——本轮把负数闸挪到兜底之前",
                     parser.aparse(xlsx(BILL_HEADERS, [["AGWBQ4", "2022-07-10", "0.5", "-0.001", "浙江"]]),
                                   col_dict),
                     contains="账单其它费用不能为负")
        await raises("AG4-06 正常的负费用照样拦（AB 轮那条闸没被改动带松）",
                     parser.aparse(xlsx(BILL_HEADERS, [["AGWBQ5", "2022-07-10", "0.5", "-2.00", "浙江"]]),
                                   col_dict),
                     contains="账单其它费用不能为负")

        await rules_svc.create(BillParseRuleCreateSchema(bill_type=RULE_FAKE, carrier_name="AG测试快递",
                                                         column_mapping=col_map, waybill_fee=Decimal("1.00")))
        await session.flush()
        bill_bytes = xlsx(BILL_HEADERS, [
            ["AGWBR1", "2022-07-12", "0.0025", "1.005", "浙江"],
            ["AGWBR2", "2022-07-12", "0.0025", "1.005", "浙江"],
            ["AGWBTOT", "合计", "0.006", "2.02", ""],
        ])
        bill_imp = await logistics.import_bill(file=BytesUpload(bill_bytes), bill_type=RULE_FAKE,
                                               bill_month=MONTH_BILL)
        await session.flush()
        remark = (await session.execute(select(ImportBatchModel.remark).where(
            ImportBatchModel.batch_no == bill_imp.batch_no))).scalar() or ""
        eq("AG4-07 库里的账单行重量是量好的 0.003", str((await session.execute(
            select(CourierBillModel.bill_weight).where(CourierBillModel.waybill_no == "AGWBR1"))).scalar()),
           "0.003")
        eq("AG4-08 库里的账单行费用是量好的 1.01", str((await session.execute(
            select(CourierBillModel.bill_other_fee).where(CourierBillModel.waybill_no == "AGWBR1"))).scalar()),
           "1.01")
        truthy("AG4-09 备注里的 Σ重量 0.006kg 就是库里两行加出来的（以前报 0.005，对不上库）",
               "Σ账单重量 0.006kg" in remark, remark[-260:])
        truthy("AG4-10 备注里的 Σ费用 2.02元 同理（以前用未量的 1.005×2 加出 2.01）",
               "Σ其它费用 2.02元" in remark, remark[-260:])
        truthy("AG4-11 合计行（快递写的 0.006 / 2.02）与逐行求和**判为一致**："
               "两侧同尺之后不再有纯舍入差造成的假「对不上」",
               "合计行与逐行求和一致" in remark and "不一致" not in remark, remark[-300:])
        truthy("AG4-12 反推整票运费那句话也是量过的数（2.02 + 1.00×2 = 4.02）",
               "反推整票运费合计 4.02元" in remark, remark[-300:])

        # ══ AG5：摊分——方向写死，但 Σ份额 = 整票金额 这条不变式不动 ═══════════
        eq("AG5-01 0.25 摊给两条等重子订单 → [0.12, 0.13]（凑偶会给 [0.13, 0.12]：哪一条拿那一分不同）",
           [str(x) for x in _apportion(Decimal("0.25"), [Decimal("1"), Decimal("1")])], ["0.12", "0.13"])
        for total, keys in (
            (Decimal("0.10"), [Decimal("1"), Decimal("1"), Decimal("1")]),
            (Decimal("10.00"), [Decimal("0.003"), Decimal("0.017")]),
            (Decimal("99.99"), [Decimal("0.5"), Decimal("0.5"), Decimal("0.5")]),
            (Decimal("1.00"), [Decimal("0.311"), Decimal("0.003"), Decimal("0.017")]),
            (Decimal("0.25"), [Decimal("1"), Decimal("1")]),
        ):
            shares = _apportion(total, keys)
            eq(f"AG5-02 Σ份额 = 整票金额（{total} 摊 {len(keys)} 条，余数归权重最大那条）",
               str(sum(shares, Decimal("0"))), str(round_cent(total)))
        eq("AG5-03 权重全 0 → 份额全 0（不除零、不瞎分）",
           [str(x) for x in _apportion(Decimal("5.00"), [Decimal("0"), Decimal("0")])], ["0", "0"])
        eq("AG5-04 量好的重量当权重：0.003 : 0.017 摊 10.00 → 1.50 + 8.50",
           [str(x) for x in _apportion(Decimal("10.00"), [Decimal("0.003"), Decimal("0.017")])],
           ["1.50", "8.50"])
        eq("AG5-05 每一条份额都在分格上（见位进分不参与摊分，摊出来就是四舍五入的份额 + 一条余数）",
           all(str(s) == str(round_cent(s)) for s in _apportion(Decimal("99.99"),
                                                                [Decimal("0.5"), Decimal("0.5"),
                                                                 Decimal("0.5")])), True)

        # ══ AG6：尺子与库对齐 + MySQL 方向实测（存量数字是不是不动点）════════════
        expected_scale = {
            ("ecommerce_order", "buyer_paid_amount"): 2, ("ecommerce_order", "refund_amount"): 2,
            ("ecommerce_order", "shipped_cost_amount"): 2, ("ecommerce_order", "return_cost_amount"): 2,
            ("ecommerce_order", "freight_income"): 2, ("ecommerce_order", "freight_cost_amount"): 2,
            ("ecommerce_order_detail", "weight_kg"): 3, ("ecommerce_order_detail", "cost_price"): 4,
            ("ecommerce_order_detail", "excel_cost_price"): 4,
            ("ecommerce_order_detail", "computed_product_cost"): 2,
            ("ecommerce_order_detail", "computed_return_cost"): 2,
            ("ecommerce_order_detail", "paid_amount"): 2, ("ecommerce_order_detail", "refund_amount"): 2,
            ("ecommerce_order_detail", "freight_cost"): 2,
            ("ecommerce_order_detail", "erp_shipped_cost"): 2,
            ("ecommerce_order_detail", "erp_return_cost"): 2,
            ("ecommerce_sku_cost", "cost_price"): 2,
            ("ecommerce_courier_bill", "bill_weight"): 3, ("ecommerce_courier_bill", "bill_other_fee"): 2,
            ("ecommerce_courier_bill", "waybill_fee"): 2, ("ecommerce_courier_bill", "actual_freight"): 2,
            ("ecommerce_bill_parse_rule", "waybill_fee"): 2,
        }
        cols = (await session.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, NUMERIC_SCALE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE 'ecommerce%' AND DATA_TYPE = 'decimal' "
            "ORDER BY TABLE_NAME, COLUMN_NAME"
        ))).all()
        seen = {(str(t), str(c)): int(s) for t, c, s in cols}
        unlabeled = [f"{t}.{c}(scale={s})" for (t, c), s in seen.items() if (t, c) not in expected_scale]
        truthy(f"AG6-01 库里每一个 DECIMAL 列都在「尺子清单」里（新增数值列没配尺就失败，共 {len(seen)} 列）",
               not unlabeled, f"未配尺的列：{unlabeled}")
        mismatched = [f"{t}.{c}：库 scale={seen[(t, c)]}、代码 quant={scale}"
                      for (t, c), scale in expected_scale.items()
                      if (t, c) in seen and seen[(t, c)] != scale]
        eq("AG6-02 代码里的三把精度与列的 scale 逐列相等（尺子量的就是那一格）", mismatched, [])
        ghost = [f"{t}.{c}" for (t, c) in expected_scale if (t, c) not in seen]
        eq("AG6-03 清单里没有已不存在的列（列被删掉要同步清单，不许留幽灵）", ghost, [])

        # MySQL 的 DECIMAL 舍入方向实测：与本轮尺子一致 → 「存量数字一个都不变」是量出来的，
        # 不是形容词（存量都是 MySQL 当年舍出来的值）。用 CAST 保证喂进去的是精确 DECIMAL 不是二进制浮点。
        places = {MONEY_QUANT: 2, WEIGHT_QUANT: 3, COST_QUANT: 4}
        for probe, quant in (("1.025", MONEY_QUANT), ("-1.025", MONEY_QUANT),
                             ("0.0025", WEIGHT_QUANT), ("0.0045", WEIGHT_QUANT),
                             ("3.11085", COST_QUANT)):
            db_round = (await session.execute(text(
                f"SELECT ROUND(CAST('{probe}' AS DECIMAL(12, 6)), {places[quant]})"))).scalar()
            ours = quantize_half_up(Decimal(probe), quant)
            even = Decimal(probe).quantize(quant, rounding=ROUND_HALF_EVEN)
            eq(f"AG6-04 MySQL ROUND({probe}) = 本尺子 {ours}（存量数字都是本尺子的不动点）",
               str(Decimal(str(db_round))), str(ours))
            truthy(f"AG6-05 同一格 MySQL 也不给 Python 默认的 {even}（两套并存这件事在本轮之前就不该有）",
               str(Decimal(str(db_round))) != str(even), f"MySQL {db_round} / 默认 {even} / 本轮 {ours}")

        scanned, offenders = 0, []
        for table, column, quant in (
            ("ecommerce_order_detail", "weight_kg", WEIGHT_QUANT),
            ("ecommerce_order_detail", "cost_price", COST_QUANT),
            ("ecommerce_order_detail", "excel_cost_price", COST_QUANT),
            ("ecommerce_order_detail", "paid_amount", MONEY_QUANT),
            ("ecommerce_order_detail", "erp_shipped_cost", MONEY_QUANT),
            ("ecommerce_sku_cost", "cost_price", MONEY_QUANT),  # 2026-11-06 收列：主表 2 位，订单侧那两列照旧 4 位
            ("ecommerce_courier_bill", "bill_weight", WEIGHT_QUANT),
        ):
            for v in (await session.execute(text(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
            ))).scalars().all():
                scanned += 1
                dec = Decimal(str(v))
                if quantize_half_up(dec, quant) != dec:
                    offenders.append(f"{table}.{column}={v}")
        eq(f"AG6-06 真实库存量 {scanned} 个数值全是新尺子的不动点（不需要数据订正、不需要重跑导入）",
           offenders[:20], [])
    finally:
        await session.rollback()
        await session.close()

    passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    print(f"\n===== AG 轮：{passed}/{len(RESULTS)} 通过 =====")
    for status, cid, detail in RESULTS:
        print(f"{status} {cid} :: {detail}")
    fails = [c for s, c, _ in RESULTS if s == "FAIL"]
    print(f"\n失败 {len(fails)} 项：{fails}")


if __name__ == "__main__":
    asyncio.run(main())
