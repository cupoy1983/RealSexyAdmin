"""AB 轮的配套回归：确认批次这条「钱路」没被 _require_status_in / 认领改造打破。

回滚式：一个事务跑完，结束显式 rollback。
AI 轮（§22.5/§22.6）改动：虚假行改对要先认领，「改回」入口整条删除，删到空批次跟着消失。
"""

import asyncio
import io
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
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel, CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import BillColumnMapping, BillParseRuleCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.logistics.service import BillParseRuleService, LogisticsService  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []
RULE = "cvteston"
SHOP = "CVSHOP1"
MAIN = "CVTESTMAIN"
BILL_HEADERS = ["运单号", "账单日期", "账单重量", "其它费用", "收件省"]
ORDER_HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号", "重量kg(SKU)",
]


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


class BytesUpload:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.filename = "cv.xlsx"

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


def order_row(idx: int, detail_id: str, order_no: str, tracking: str, weight: str, ship_day: str) -> list:
    return [str(idx), f"CVOD{detail_id}", order_no, SHOP, "CV测试店铺", "CV淘宝", "已完成",
            MAIN, None, "CV测试商品", "10.00", "0.00", "0.00", 1, 0,
            f"{ship_day} 10:00:00", "ERP发货", tracking, weight]


def summary_row(paid: str) -> list:
    return ["汇总", None, None, None, None, None, None, None, None, None, paid, "0.00", "0.00",
            None, None, None, None, None, None]


async def main() -> None:
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        orders, sku = OrderService(auth, session), SkuCostService(auth, session)
        rules, logistics = BillParseRuleService(auth, session), LogisticsService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("CVTEST%")),
            delete(BillParseRuleModel).where(BillParseRuleModel.bill_type.like("cvtest%")),
        ):
            await session.execute(stmt)
        await session.flush()

        await sku.create(SkuCostCreateSchema(main_sku_code=MAIN, product_name="CV测试商品",
                                             cost_price=Decimal("2.00")))
        col_map = BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                    bill_other_fee="其它费用", receiver_province="收件省")
        await rules.create(BillParseRuleCreateSchema(bill_type=RULE, carrier_name="CV测试快递",
                                                     column_mapping=col_map, waybill_fee=Decimal("1.00")))

        # ══ 场景一：一票两子订单，重量 1:3 → 整票 10.00 摊 2.50 / 7.50 ══════
        await orders.import_orders(ship_month="2021-04", file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "A", "CVORD-1", "CVWBALL", "1.0", "2021-04-05"),
            order_row(2, "B", "CVORD-1", "CVWBALL", "3.0", "2021-04-05"),
            summary_row("20.00")])))
        await session.flush()

        imp1 = await logistics.import_bill(file=BytesUpload(xlsx(
            BILL_HEADERS, [["CVWBALL", "2021-04-11", "4.0", "9.00", "浙江"]])),
            bill_type=RULE, bill_month="2021-04")
        await session.flush()
        bill1 = (await session.execute(select(CourierBillModel).where(
            CourierBillModel.waybill_no == "CVWBALL"))).scalar_one()
        eq("CV01 一票命中两条子订单 → 已匹配（原件行）", bill1.status, "matched")
        truthy("CV02 账单店铺由摊分到的子订单带出", bill1.store_id is not None, f"store_id={bill1.store_id}")

        await logistics.confirm_bill_batch(imp1.batch_no)
        await session.flush()

        d1 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.tracking_no == "CVWBALL").order_by(OrderDetailModel.id))).scalars().all()
        eq("CV03 整票运费按重量摊到两条子订单", [str(d.freight_cost) for d in d1], ["2.50", "7.50"])
        eq("CV04 Σ摊分 = 整票金额（一分不丢）", str(sum((d.freight_cost or Decimal("0")) for d in d1) if d1 else Decimal("0")),
           "10.00")
        head1 = (await session.execute(select(OrderModel).where(OrderModel.system_order_no == "CVORD-1"))).scalar_one()
        eq("CV05 主订单运费合计回写", str(head1.freight_cost_amount), "10.00")

        # ══ 场景二：一票三子订单同权重，整票 1.00 → 余数 0.01 归权重最大那条 ══
        await orders.import_orders(ship_month="2021-05", file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "C", "CVORD-2", "CVWB3", "1.0", "2021-05-05"),
            order_row(2, "D", "CVORD-2", "CVWB3", "1.0", "2021-05-05"),
            order_row(3, "E", "CVORD-2", "CVWB3", "1.0", "2021-05-05"),
            summary_row("30.00")])))
        await session.flush()
        imp2 = await logistics.import_bill(file=BytesUpload(xlsx(
            BILL_HEADERS, [["CVWB3", "2021-05-12", "3.0", "0.00", "浙江"]])),
            bill_type=RULE, bill_month="2021-05")
        await session.flush()
        bill2 = (await session.execute(select(CourierBillModel).where(
            CourierBillModel.waybill_no == "CVWB3"))).scalar_one()
        eq("CV06 第二票也是原件行", bill2.status, "matched")

        await logistics.confirm_bill_batch(imp2.batch_no)
        await session.flush()
        d2 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.tracking_no == "CVWB3").order_by(OrderDetailModel.id))).scalars().all()
        shares2 = [str(d.freight_cost) for d in d2]
        truthy("CV07 余数归「权重最大」那条（同权重取先命中的一条），不是归收入最大的店",
               sum(Decimal(s) for s in shares2) == Decimal("1.00") and max(Decimal(s) for s in shares2) == Decimal(shares2[0]),
               f"摊分={shares2}")

        # ══ 场景三：虚假行删掉 → 批次跟着消失，这一票不该带钱进成本 ══════════
        await orders.import_orders(ship_month="2021-06", file=BytesUpload(xlsx(ORDER_HEADERS, [
            order_row(1, "F", "CVORD-3", "CVWBREV", "1.0", "2021-06-05"),
            summary_row("10.00")])))
        await session.flush()
        # 先把这票判成虚假：换个不存在的运单号
        imp3 = await logistics.import_bill(file=BytesUpload(xlsx(
            BILL_HEADERS, [["CVWBNOPE", "2021-06-13", "1.0", "5.00", "浙江"]])),
            bill_type=RULE, bill_month="2021-06")
        await session.flush()
        bill3 = (await session.execute(select(CourierBillModel).where(
            CourierBillModel.waybill_no == "CVWBNOPE"))).scalar_one()
        eq("CV08 挂不上订单 → 虚假", bill3.status, "fake")

        # AI 轮之后虚假行的界面出路只有两条：认领改对，或者删（§22.5 删了「改回」）。
        # 这一票压根不该算成本 → 删；删掉批次里最后一行，批次自己也跟着物理消失（§22.6）。
        msg3 = await logistics.delete_bill_rows([bill3.id])
        await session.flush()
        batch_left = (await session.execute(text(
            "SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no=:b"), {"b": imp3.batch_no})).scalar()
        bill_left = (await session.execute(text(
            "SELECT COUNT(*) FROM ecommerce_courier_bill WHERE id=:i"), {"i": bill3.id})).scalar()
        truthy("CV09 删到空：文案点名连带删除，批次与账单行（含软删）真的都不在了",
               "已无账单行" in msg3 and batch_left == 0 and bill_left == 0,
               f"文案={msg3[:90]}｜批次剩 {batch_left} 行｜账单行剩 {bill_left} 行")

        d3 = (await session.execute(select(OrderDetailModel).where(
            OrderDetailModel.tracking_no == "CVWBREV"))).scalar_one()
        truthy("CV10 这一票没有原件行留在批次里 → 该子订单运费仍是「还没定」", d3.freight_cost is None,
               f"freight_cost={d3.freight_cost}")
        head3 = (await session.execute(select(OrderModel).where(OrderModel.system_order_no == "CVORD-3"))).scalar_one()
        eq("CV11 主订单运费合计没被假钱污染", str(head3.freight_cost_amount), "0.00")
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:300]}")
    finally:
        await session.rollback()
        await session.close()

    async with async_db_session() as probe:
        left = (await probe.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no LIKE 'CVWB%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'CVORD-%') "
            "+ (SELECT COUNT(*) FROM ecommerce_bill_parse_rule WHERE bill_type LIKE 'cvtest%') "
            "+ (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'CVTEST%')"))).scalar()
        (ok if left == 0 else bad)("CV12 回滚干净", f"残留 {left} 行" if left else "0")

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
