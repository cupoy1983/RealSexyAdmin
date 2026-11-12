"""2026-10-27 AB 轮（§16）改动的回滚式验证：一个事务跑完，结束显式 rollback，库里什么都不留。

编号前缀 AB，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL 不撞号。
AB09 是取证项：tag_name 唯一索引取决于迁移有没有被用户应用，脚本只报现状不判对错。
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
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.model import ImportBatchModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel, CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.parsers.generic_parser import GenericExcelBillParser  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import (  # noqa: E402
    BillColumnMapping,
    BillParseRuleCreateSchema,
)
from app.plugin.module_ecommerce.logistics.service import BillParseRuleService, LogisticsService  # noqa: E402
from app.plugin.module_ecommerce.order.importers.base import RawOrderRecord  # noqa: E402,F401
from app.plugin.module_ecommerce.order.model import SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel, StoreTagModel, TagModel  # noqa: E402
from app.plugin.module_ecommerce.store.schema import (  # noqa: E402
    StoreCreateSchema,
    StoreUpdateSchema,
    TagCreateSchema,
)
from app.plugin.module_ecommerce.store.service import StoreService, TagService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []

TAG_OK = "AB测试甲"
SHOP_ID = "ABSHOP1"
RULE_ON = "abteston"
RULE_OFF = "abtestoff"
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
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}" if actual != expected else str(actual))


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


async def raises(cid: str, coro, *, contains: str) -> None:
    """断言报错且文案含关键片段（Pydantic 校验错也算拦住，但会标注异常类型）。"""
    try:
        value = await coro
    except CustomException as e:
        msg = str(e)
        (ok if contains in msg else bad)(cid, msg[:120])
    except Exception as e:  # noqa: BLE001
        bad(cid, f"抛的是 {type(e).__name__}（不是 CustomException）：{str(e)[:120]}")
    else:
        bad(cid, f"没有报错，返回了 {str(value)[:120]}")


class BytesUpload:
    def __init__(self, data: bytes, name: str = "ab.xlsx") -> None:
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


def order_row(idx: int, detail_id: str, paid: str, ship_month: str = "2021-05", main: str = "ABTESTMAIN") -> list:
    return [str(idx), f"ABOD{detail_id}", f"ABORD-{detail_id}", SHOP_ID, "AB测试店铺", "AB淘宝", "已完成",
            main, None, "AB测试商品", paid, "0.00", "0.00", 1, 0,
            f"{ship_month}-05 10:00:00", "ERP发货", f"ABWB{detail_id}", "0.5"]


def summary_row(paid: str) -> list:
    return ["汇总", None, None, None, None, None, None, None, None, None, paid, "0.00", "0.00",
            None, None, None, None, None, None]


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        stores, tags = StoreService(auth, session), TagService(auth, session)
        sku, orders = SkuCostService(auth, session), OrderService(auth, session)
        rules = BillParseRuleService(auth, session)
        logistics = LogisticsService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("ABTEST%")),
            delete(BillParseRuleModel).where(BillParseRuleModel.bill_type.like("abtest%")),
            delete(TagModel).where(TagModel.tag_name.like("AB测试%")),
            delete(StoreModel).where(StoreModel.kuaimai_shop_id.like("ABSHOP%")),
        ):
            await session.execute(stmt)
        await session.flush()

        # ══ AB2 / AB5：负责人与店铺的空格闸、硬删 ══════════════════════════
        t1 = await tags.create(TagCreateSchema(tag_name=f"  {TAG_OK}  "))
        await session.flush()
        stored = (await session.execute(
            text("SELECT tag_name FROM ecommerce_tag WHERE id=:i"), {"i": t1.id}
        )).scalar()
        eq("AB01 负责人名 trim 后入库", stored, TAG_OK)

        await raises("AB02 纯空格的负责人名被拒（空格不算字符串）",
                     tags.create(TagCreateSchema(tag_name="   ")), contains="不能为空")
        await raises("AB03 trim 之后同名算同一个人（§16.6 改口，旧 Z13 是「张三 」算另一个人）",
                     tags.create(TagCreateSchema(tag_name=f"{TAG_OK} ")), contains="负责人名称已存在")

        st = await stores.create(StoreCreateSchema(
            kuaimai_shop_id=f" {SHOP_ID} ", store_name=" AB测试店铺 ", platform=" AB淘宝 ", tag_ids=[t1.id]))
        await session.flush()
        row = (await session.execute(text(
            "SELECT kuaimai_shop_id, store_name, platform FROM ecommerce_store WHERE id=:i"), {"i": st.id}
        )).fetchone()
        eq("AB04 店铺三字段 trim 后入库", [row.kuaimai_shop_id, row.store_name, row.platform],
           [SHOP_ID, "AB测试店铺", "AB淘宝"])

        await raises("AB05 撞已有快麦店铺id 单独报错（不是当成新建）",
                     stores.create(StoreCreateSchema(
                         kuaimai_shop_id=f"{SHOP_ID} ", store_name="AB另一店", platform="AB淘宝")),
                     contains="已被其它店铺使用")
        await raises("AB06 店铺改名传纯空格被拒",
                     stores.update(st.id, StoreUpdateSchema(store_name="   ")), contains="不能为空")

        await raises("AB08 被店铺挂着的负责人仍禁删（Z12 回归）",
                     tags.delete([t1.id]), contains="仍被店铺挂着")

        t2 = await tags.create(TagCreateSchema(tag_name="AB测试乙"))
        await session.flush()
        await tags.delete([t2.id])
        await session.flush()
        left = (await session.execute(
            text("SELECT COUNT(*) FROM ecommerce_tag WHERE id=:i"), {"i": t2.id}
        )).scalar()
        eq("AB07 负责人删除是物理删（不留幽灵行）", left, 0)

        non_unique = (await session.execute(text(
            "SELECT NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ecommerce_tag' "
            "AND INDEX_NAME='ix_ecommerce_tag_tag_name'"))).scalar()
        truthy("AB09 [取证] tag_name 唯一索引在库里的实际状态（0=已应用迁移）",
               non_unique in (0, 1), f"NON_UNIQUE={non_unique}（None=索引不存在）")

        # ══ A1：成本主编码 ════════════════════════════════════════════════
        await raises("AB10 主商家编码纯空格被拒",
                     sku.create(SkuCostCreateSchema(main_sku_code="   ", product_name="AB测试商品",
                                                    cost_price=Decimal("1.00"))),
                     contains="主商家编码不能为空")
        nullish = (await session.execute(text(
            "SELECT COUNT(*) FROM ecommerce_sku_cost "
            "WHERE (main_sku_code IS NULL OR main_sku_code='') AND is_deleted=0"))).scalar()
        eq("AB11 没有主编码为空的成本行（查重没退化成「随便哪个主编码」）", nullish, 0)

        k = await sku.create(SkuCostCreateSchema(main_sku_code=" abtestmain ", product_name="AB测试商品",
                                                 cost_price=Decimal("1.00")))
        await session.flush()
        eq("AB12 主编码 trim + 大写入库", (await session.execute(
            text("SELECT main_sku_code FROM ecommerce_sku_cost WHERE id=:i"), {"i": k.id}
        )).scalar(), "ABTESTMAIN")

        # ══ AB4：账单负数闸 ═══════════════════════════════════════════════
        mapping = {"waybill_no": "运单号", "bill_date": "账单日期", "bill_weight": "账单重量",
                   "bill_other_fee": "其它费用", "receiver_province": "收件省"}
        parser = GenericExcelBillParser()
        await raises("AB13 账单重量为负 → 整份不落库并报行号",
                     parser.aparse(xlsx(BILL_HEADERS, [["ABWBX1", "2021-02-10", "-0.5", "0.00", "浙江"]]), mapping),
                     contains="账单重量不能为负")
        await raises("AB14 账单其它费用为负 → 整份不落库并报行号",
                     parser.aparse(xlsx(BILL_HEADERS, [
                         ["ABWBX1", "2021-02-10", "0.5", "0.00", "浙江"],
                         ["ABWBX2", "2021-02-10", "0.5", "-3.00", "浙江"]]), mapping),
                     contains="账单其它费用不能为负")
        good_rows = await parser.aparse(xlsx(BILL_HEADERS, [["ABWBX1", "2021-02-10", "0.5", "3.00", "浙江"]]), mapping)
        eq("AB15 正常账单行照样解析成功（回归）", len(good_rows), 1)

        # ══ AB3：停用规则的两个写入口 ═════════════════════════════════════
        col_map = BillColumnMapping(waybill_no="运单号", bill_date="账单日期", bill_weight="账单重量",
                                    bill_other_fee="其它费用", receiver_province="收件省")
        await rules.create(BillParseRuleCreateSchema(bill_type=RULE_ON, carrier_name="AB测试快递",
                                                     column_mapping=col_map, waybill_fee=Decimal("1.00")))
        await rules.create(BillParseRuleCreateSchema(bill_type=RULE_OFF, carrier_name="AB测试快递停用",
                                                     column_mapping=col_map, waybill_fee=Decimal("1.00"), status=1))

        await raises("AB16 停用中的类型不能导账单",
                     logistics.import_bill(file=BytesUpload(xlsx(
                         BILL_HEADERS, [["ABWBY1", "2021-02-10", "0.5", "0.00", "浙江"]])),
                         bill_type=RULE_OFF, bill_month="2021-02"),
                     contains="已停用")
        truthy("AB16b 停用导入没留下批次", (await session.execute(
            select(ImportBatchModel).where(ImportBatchModel.batch_no == f"bill-{RULE_OFF}-2021-02"))).scalar() is None,
            "库里出现了 bill-abtestoff-2021-02")

        await raises("AB17 停用中的类型不能声明零行批次",
                     logistics.declare_empty_batches("2021-09", [RULE_OFF]), contains="没有启用中的解析规则")
        decl = await logistics.declare_empty_batches("2021-08", [RULE_ON])
        truthy("AB18 启用中的类型声明成功（回归）", "abteston-2021-08" in decl, decl[:120])

        # ══ A6：计数接口认批次类别 ════════════════════════════════════════
        await raises("AB19 批次号不存在直接报错（不再回一串 0）",
                     logistics.bill_batch_counts("bill-abteston-2019-01"), contains="不存在")
        await orders.import_orders(ship_month="2021-05", file=BytesUpload(xlsx(
            ORDER_HEADERS, [order_row(1, "A", "10.00"), order_row(2, "B", "10.00"),
                            summary_row("20.00")])))
        await raises("AB20 传订单批次号报「不是账单批次」",
                     logistics.bill_batch_counts("order-2021-05"), contains="不是账单批次")

        # ══ AB1：改对与删除的状态闸（AI 轮删了「改回」入口，§22.5） ══════════
        # 虚假行改对现在必须先认领（§22.1），认领那套闸 AI 轮自己覆盖了；
        # 这里改挂一条**重复行**——它的运单号本来就查得到子订单，免认领，走的还是老那条翻状态的路。
        await orders.import_orders(ship_month="2021-03", file=BytesUpload(xlsx(
            ORDER_HEADERS, [order_row(9, "Z", "10.00", "2021-03"), summary_row("10.00")])))
        imp = await logistics.import_bill(file=BytesUpload(xlsx(
            BILL_HEADERS, [["ABWBFAKE1", "2021-03-10", "0.5", "2.00", "浙江"],
                           ["ABWBFAKE2", "2021-03-10", "0.5", "2.00", "浙江"],
                           ["ABWBZ", "2021-03-10", "0.5", "2.00", "浙江"],
                           ["ABWBZ", "2021-03-11", "0.5", "2.00", "浙江"]])),
            bill_type=RULE_ON, bill_month="2021-03")
        await session.flush()
        bill_rows = (await session.execute(
            select(CourierBillModel).where(CourierBillModel.import_batch_id == imp.batch_no)
            .order_by(CourierBillModel.id))).scalars().all()
        eq("AB20b 挂不上订单的两行判虚假，同票第二行判重复、第一行是原件",
           [r.status for r in bill_rows], ["fake", "fake", "matched", "duplicate"])
        fake1, _, original, dup = bill_rows

        await logistics.restore_bill_rows([dup.id], remark="AB测试改对")
        await session.flush()
        row_dup = (await session.execute(text(
            "SELECT status, actual_freight, anomaly_remark FROM ecommerce_courier_bill WHERE id=:i"), {"i": dup.id}
        )).fetchone()
        eq("AB21 改对：状态=已恢复、运费按面单费+其它费用算回", [row_dup.status, str(row_dup.actual_freight)],
           ["restored", "3.00"])
        truthy("AB21b 备注只往后拼「人工改对」，判重复那句旧文案没被抹掉",
               "人工改对：AB测试改对" in (row_dup.anomaly_remark or "") and "重复" in (row_dup.anomaly_remark or ""),
               str(row_dup.anomaly_remark)[:160])
        eq("AB21c 免认领改对不碰订单数据（原件行还是原件行）", original.status, "matched")

        await session.execute(text("UPDATE ecommerce_import_batch SET status='confirmed' WHERE batch_no=:b"),
                              {"b": imp.batch_no})
        await session.flush()
        await raises("AB24 批次确认后「改对」被拒（钱已写进子订单，只能改库）",
                     logistics.restore_bill_rows([fake1.id]), contains="批次已确认")
        await raises("AB24b 批次确认后删除同样被拒",
                     logistics.delete_bill_rows([fake1.id]), contains="批次已确认")
        await session.execute(text("UPDATE ecommerce_import_batch SET status='unconfirmed' WHERE batch_no=:b"),
                              {"b": imp.batch_no})
        await session.flush()

        await raises("AB28 已恢复行不许删（删除只认虚假/重复，回归）",
                     logistics.delete_bill_rows([dup.id]), contains="只有虚假/重复行")

        # ══ AB6：没有汇总行要把「没对账」说出来 ═══════════════════════════
        msg_no_summary = await orders.import_orders(ship_month="2021-06", file=BytesUpload(xlsx(
            ORDER_HEADERS, [order_row(1, "C", "10.00", "2021-06")])))
        truthy("AB26 无汇总行 → 结果文案提示未做金额校验和", "没读到「汇总」行" in msg_no_summary,
               msg_no_summary[:140])
        msg_with_summary = await orders.import_orders(ship_month="2021-07", file=BytesUpload(xlsx(
            ORDER_HEADERS, [order_row(1, "D", "10.00", "2021-07"), summary_row("10.00")])))
        truthy("AB27 有汇总行 → 不带提示（回归）", "没读到「汇总」行" not in msg_with_summary,
               msg_with_summary[:140])
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:260]}")
    finally:
        await session.rollback()
        await session.close()

    # 换一个会话确认库里真的没留东西
    async with async_db_session() as probe:
        residue = (await probe.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_tag WHERE tag_name LIKE 'AB测试%') "
            "+ (SELECT COUNT(*) FROM ecommerce_store WHERE kuaimai_shop_id LIKE 'ABSHOP%') "
            "+ (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'ABTEST%') "
            "+ (SELECT COUNT(*) FROM ecommerce_bill_parse_rule WHERE bill_type LIKE 'abtest%') "
            "+ (SELECT COUNT(*) FROM ecommerce_import_batch WHERE batch_no LIKE '%abtest%' OR batch_no LIKE '%2021-0%') "
            "+ (SELECT COUNT(*) FROM ecommerce_courier_bill WHERE waybill_no LIKE 'ABWB%') "
            "+ (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'ABORD-%')"))).scalar()
        truthy("AB30 回滚干净：库里没有残留", residue == 0, f"残留 {residue} 行")

    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid} {detail}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
