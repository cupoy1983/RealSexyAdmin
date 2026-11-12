"""2026-11-02 AN 轮（§26.12 / ADR-0039 修订块）三道闸的回滚式验证。

编号前缀 AN，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AD/AE/AF/AG/AH/AI/AJ/AK/AL 不撞号。

一个事务跑完，结束显式 rollback；最后另开一个 session 证明探针数据一条没留下。

组：
AN1 尺子本体 `over_cent`（尾零放行、第 3 位非零才算超位）；
AN2 主表录入闸：新增/编辑两条路径都过、留空与 0 照旧合法、订单侧那一格不受影响；
AN3 导入必填闸（AN21/AN22）：原先静默跳行的两列改成带行号整份报错，重量那一格照旧不加闸；
    主编码与商品标题两格按 AN29 实测（参照文件 539/5567 行同时为空）保持可空；
    空格子的钱也过同一把尺（AN31），所以退款空着读出来是 `0.00` 不是 `0`；
    真实文件那三项（AN3-20/21）是这一轮的验收面：闸打在业务方自己的导出上就算错。
AN34 撤销 AN22 的「编码 `0` 算没填」，并给自动建档加一条例外（`ensure_store`：文件里店名是
    `-` 这类占位写法时不覆盖档案，平台照旧刷新）——那 9 行出库单要能进利润。
AN4 「是否ERP发货」三值白名单（AN27）+ 带括号的写法认得（AN30）+ 未发货仍不入库 + 汇总行整行豁免；
AN5 告警（AN20/AN25/AN28）：②③整条删除、只留①且原值直接比、文案指向快麦。
"""

import asyncio
import inspect
import io
import os
import sys
from decimal import Decimal
from pathlib import Path

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import delete, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.common.excel_head import aread_excel_with_header_scan  # noqa: E402
from app.plugin.module_ecommerce.common.numbers import over_cent  # noqa: E402
from app.plugin.module_ecommerce.order.importers.base import (  # noqa: E402
    RawOrderDetailRecord,
    RawOrderRecord,
)
from app.plugin.module_ecommerce.order.importers.excel_source import (  # noqa: E402
    ERP_SHIPPED_VALUE,
    H_IS_ERP_SHIPPED,
    UNSHIPPED_VALUE,
    ExcelOrderDataSource,
    H_SHOP_CODE,
    H_STORE_NAME,
    H_SYSTEM_ORDER_NO,
    _ERP_SHIPPED_VALUES,
    _HEADER_LOCATE_HEADERS,
    _normalize_shipped_flag,
)
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import (  # noqa: E402
    SkuCostCreateSchema,
    SkuCostUpdateSchema,
)
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.plugin.module_ecommerce.store.service import StoreService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

SHOP = "ANSHOP1"
STORE = "AN测试店铺"
PLATFORM = "AN淘宝"
MAIN = "NTESTMAIN"          # 主表成本 10.00
MAIN_OVER = "NTESTOVER"     # 存量超位探针：绕过服务层直接塞 3.1109
MAIN_WARN = "NTESTWARN"     # 告警端到端用：主表 3.11，快麦那一格 3.115
# 参照文件的真实路径含 Windows 用户名，不进 Git（shared-rules §3.4「本机绝对路径」）；没设就跳过那四项。
REAL_ORDER_FILE = os.environ.get("ECOM_EXPORT_ORDER_FILE", "")
HEADERS = [
    "序号", "订单商品明细ID", "系统订单号", "店铺编码", "店铺全称", "店铺平台", "订单状态",
    "主商家编码(款)", "规格商家编码(SKU)", "商品标题(款)", "订单买家已付金额", "平台实退金额",
    "运费收入", "系统实发商品数", "实退数量", "发货时间", "是否ERP发货", "快递单号",
    "重量kg(SKU)", "成本价(SKU)", "系统实发成本", "实退成本", "订单标签",
]
BASE: dict[str, object] = {
    "序号": "1", "订单商品明细ID": "NOD1", "系统订单号": "NORD-1", "店铺编码": SHOP,
    "店铺全称": STORE, "店铺平台": PLATFORM, "订单状态": "已完成", "主商家编码(款)": MAIN,
    "规格商家编码(SKU)": None, "商品标题(款)": "AN测试商品", "订单买家已付金额": "10.00",
    "平台实退金额": "0.00", "运费收入": "0.00", "系统实发商品数": 2, "实退数量": 1,
    "发货时间": "2021-10-05 10:00:00", "是否ERP发货": ERP_SHIPPED_VALUE, "快递单号": "NWB1",
    "重量kg(SKU)": "0.5", "成本价(SKU)": "10.00", "系统实发成本": "20.00", "实退成本": "10.00",
    "订单标签": None,
}

RESULTS: list[tuple[str, str, str]] = []


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:  # noqa: ANN001
    (ok if actual == expected else bad)(cid, f"期望 {expected!r}，实际 {actual!r}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


def xlsx(rows: list[dict[str, object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        merged = {**BASE, **r}
        ws.append([merged[h] for h in HEADERS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def parse(rows: list[dict[str, object]]):
    return await ExcelOrderDataSource().fetch(contents=xlsx(rows))


async def parse_raises(cid: str, rows: list[dict[str, object]], *, contains: str) -> None:
    """这一份文件必须**整份报错**，且报错点名那一列（不再静默跳行）。"""
    try:
        orders = await parse(rows)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        ok(cid, f"{type(exc).__name__}：{msg[:120]}") if contains in msg else bad(
            cid, f"报错里没有「{contains}」：{msg[:180]}"
        )
        return
    bad(cid, f"竟然解析成功：{[o.system_order_no for o in orders]}")


async def parse_passes(cid: str, rows: list[dict[str, object]], *, detail: str = ""):
    try:
        orders = await parse(rows)
    except Exception as exc:  # noqa: BLE001
        bad(cid, f"应当通过却报错：{type(exc).__name__}：{str(exc)[:160]}")
        return None
    ok(cid, detail or "解析通过")
    return orders


def by_no(orders, no: str):  # noqa: ANN001, ANN201
    return next((o for o in orders or [] if o.system_order_no == no), None)


async def svc_raises(cid: str, coro, *, contains: str) -> None:  # noqa: ANN001
    try:
        value = await coro
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        ok(cid, f"{type(exc).__name__}：{msg[:130]}") if contains in msg else bad(
            cid, f"报错里没有「{contains}」：{msg[:180]}"
        )
    else:
        bad(cid, f"没有报错，返回了 {str(value)[:160]}")


def create_payload(cost, main: str = MAIN, ) -> SkuCostCreateSchema:  # noqa: ANN001
    return SkuCostCreateSchema(
        main_sku_code=main, sku_code=None, sku_name=None, product_name="AN 探针商品",
        cost_price=cost, cost_source="manual", remark=None,
    )


def raw_order(no: str) -> RawOrderRecord:
    return RawOrderRecord(
        system_order_no=no, kuaimai_shop_id=SHOP, store_name=STORE, platform=PLATFORM, order_status="已完成",
    )


def raw_detail(**kw) -> RawOrderDetailRecord:  # noqa: ANN001
    return RawOrderDetailRecord(source_detail_id="NODW", sku_code=None, product_name="AN", **kw)


def plain_detail(**kw) -> RawOrderDetailRecord:  # noqa: ANN001
    base = {
        "ship_qty": 2, "return_qty": 1, "paid_amount": Decimal("10.00"), "refund_amount": Decimal("0.00"),
        "freight_income": Decimal("0.00"), "weight_kg": Decimal("0.5"), "is_erp_shipped": True,
        "distribution_order_type": 0,
    }
    return raw_detail(**{**base, **kw})


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        sku_svc = SkuCostService(auth, session)
        order_svc = OrderService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("NTEST%")),
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("NOD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("NORD%")),
            delete(StoreModel).where(StoreModel.kuaimai_shop_id.like("AN34%")),
        ):
            await session.execute(stmt)
        await session.flush()

        # ── AN1 尺子本体 ──────────────────────────────────────────────
        truthy("AN1-1", over_cent(Decimal("3.115")), "3.115 算超位")
        truthy("AN1-2", over_cent(Decimal("0.0456")), "低价小件 0.0456 算超位")
        truthy("AN1-3", not over_cent(Decimal("3.110")), "尾零 3.110 不算超位（口径同 §25 第4条）")
        truthy("AN1-4", not over_cent(Decimal("3.11")), "3.11 不超位")
        truthy("AN1-5", not over_cent(Decimal("2")), "整数不超位")
        truthy("AN1-6", not over_cent(Decimal("0")), "0 不超位（有意0成本照旧合法）")
        truthy("AN1-7", over_cent(Decimal("3.1000001")), "第 7 位那点零头也算超位")

        # ── AN2 主表录入闸 ────────────────────────────────────────────
        created = await sku_svc.create(create_payload(Decimal("10.00")))
        eq("AN2-1", created.cost_price, Decimal("10.0000"))
        await svc_raises(
            "AN2-2", sku_svc.create(create_payload(Decimal("3.115"), "NTEST3")),
            contains="成本价最多写到分（2 位小数），收到 3.115",
        )
        tail = await sku_svc.create(create_payload(Decimal("10.100"), "NTESTTAIL"))
        eq("AN2-3", tail.cost_price, Decimal("10.1000"))
        nulled = await sku_svc.create(create_payload(None, "NTESTNULL"))
        truthy("AN2-4", nulled.cost_price is None, "留空=成本缺失（ADR-0023）照样建得出来")
        zeroed = await sku_svc.create(create_payload(Decimal("0"), "NTESTZERO"))
        eq("AN2-5", zeroed.cost_price, Decimal("0.0000"))

        await svc_raises(
            "AN2-6", sku_svc.update(created.id, SkuCostUpdateSchema(cost_price=Decimal("9.999"))),
            contains="更新失败，成本价最多写到分",
        )
        after = await sku_svc.update(created.id, SkuCostUpdateSchema(remark="只改备注"))
        eq("AN2-7", after.cost_price, Decimal("10.0000"))
        cleared = await sku_svc.update(created.id, SkuCostUpdateSchema(cost_price=None))
        truthy("AN2-8", cleared.cost_price is None, "显式 null 清空成本价没被精度闸挡住")

        # AN15 那 64 行**当年的样子**：绕过服务层硬塞一格 3.1109。2026-11-06 收列之后主表那一列是
        # Numeric(12,2)，MySQL 在写入时就把第 3 位舍掉了——「库里躺着 4 位主表价」这件事从此不可能，
        # 所以这一格现在实际存的是 3.11；下面 AN2-9 仍按**提交进来的数**报错，与列无关。
        legacy = SkuCostModel(
            main_sku_code=MAIN_OVER, sku_code=None, sku_name=None, product_name="AN 存量超位探针",
            cost_price=Decimal("3.1109"), cost_source="manual", remark=None,
        )
        session.add(legacy)
        await session.flush()
        await svc_raises(
            "AN2-9",
            sku_svc.update(legacy.id, SkuCostUpdateSchema(
                sku_name=None, product_name="AN 存量超位探针", cost_price=Decimal("3.1109"),
                cost_source="manual", remark="界面整表单提交",
            )),
            contains="收到 3.1109",
        )
        partial = await sku_svc.update(legacy.id, SkuCostUpdateSchema(remark="只改备注"))
        eq("AN2-10", partial.cost_price, Decimal("3.11"))

        # 订单侧那一格完全不受影响：快麦 4 位原值照存；主表那侧给的是 2 位的数——
        # 两侧刻度不同正是告警要提示去快麦改对的东西（AN28 原值直接比，不许先把快麦那侧整到 2 位）
        row_scale = await order_svc._build_detail_row(
            raw_order("NORD-SCALE"),
            plain_detail(main_sku_code=MAIN_OVER, excel_cost_price=Decimal("3.1109"),
                         ship_qty=1, return_qty=0),
            {}, [],
        )
        eq("AN2-11", row_scale["excel_cost_price"], Decimal("3.1109"))
        eq("AN2-12", row_scale["cost_price"], Decimal("3.11"))

        # ── AN3 导入必填闸 ────────────────────────────────────────────
        good = await parse_passes("AN3-1", [{}], detail="一份干净文件照常解析")
        eq("AN3-2", len(good or []), 1)
        for cid, header in (("AN3-3", "系统订单号"), ("AN3-4", "店铺编码"), ("AN3-5", "订单状态")):
            await parse_raises(cid, [{header: None}], contains=header)
        # AN29：这两格实测在参照文件里 539/5567 行同时为空（库里那 539 行就是它导进去的），
        # 是快麦「没录商家编码的商品」的稳定形态，所以**不设空值闸**——拦下来只会让整份正常月份进不来。
        no_main = await parse_passes("AN3-6", [{"主商家编码(款)": None, "商品标题(款)": None}],
                                     detail="主编码+标题同时空 → 照旧入库（9月文件那 539 行的样子）")
        truthy("AN3-6b", no_main[0].details[0].main_sku_code is None
               and no_main[0].details[0].product_name is None, "两格都落 None，没有静默丢行")
        # AN34（2026-11-03 业务方「改闸，店铺编码可以为0」）撤销 AN22：`0` 是快麦给「出库单」这类
        # 无源单据留的固定编码，他手工建一家 id=0 的店挂负责人，那些行照常进利润。空着仍然整份报错。
        zero_code = await parse_passes("AN3-8", [{"店铺编码": "0"}], detail="编码 0 现在解析得过（AN34 撤了 AN22）")
        eq("AN3-8b", zero_code[0].kuaimai_shop_id, "0")
        eq("AN3-8c", len(zero_code[0].details), 1)
        padded = await parse_passes("AN3-8d", [{"店铺编码": " 0 "}], detail="带首尾空格的 0 也认")
        eq("AN3-8e", padded[0].kuaimai_shop_id, "0")
        await parse_raises("AN3-9", [{"系统订单号": "   "}], contains="系统订单号不能为空")
        await parse_raises("AN3-10", [{"订单商品明细ID": None}], contains="订单商品明细ID不能为空")
        await parse_raises("AN3-11", [{"订单买家已付金额": ""}], contains="订单买家已付金额不能为空")
        paid_zero = await parse_passes("AN3-12", [{"订单买家已付金额": 0}], detail="0 合法")
        eq("AN3-13", str(paid_zero[0].details[0].paid_amount), "0.00")
        blanks = await parse_passes(
            "AN3-14", [{"平台实退金额": "", "运费收入": None, "重量kg(SKU)": None,
                        "规格商家编码(SKU)": None, "成本价(SKU)": None, "快递单号": None}],
            detail="退款/运费/重量/规格编码/成本价照旧可空",
        )
        eq("AN3-15", str(blanks[0].details[0].refund_amount), "0.00")
        truthy("AN3-16", blanks[0].details[0].weight_kg is None, "重量空 → None，没报错")
        truthy("AN3-17", blanks[0].details[0].excel_cost_price is None, "成本价(SKU) 空 → None（待补充成本）")
        scaled = await parse_passes("AN3-18", [{"成本价(SKU)": "3.11094"}], detail="快麦 5 位不拦")
        eq("AN3-19", scaled[0].details[0].excel_cost_price, Decimal("3.1109"))
        # 真实文件是这一轮的验收面：拿业务方给的那份（9月，5567 行）过一遍解析层。
        # 实测数据（只读探针量出来的，不是我猜的）：
        #   · 539 行「主商家编码(款)」与「商品标题(款)」同时为空 → AN29 把这两格退回可空；
        #   · 43 行「是否ERP发货」写的是 `[未发货]` → AN30 剥括号，否则这 43 行会混进库；
        #   · 9 行「店铺编码」填 `0`（订单 6005…462465，店名那格是字面 `-`）→ AN22 曾为它把
        #     整份文件拒在门外，2026-11-03 AN34 撤了这道闸：那 9 行是出库单，是一家真店铺，要进利润。
        real_file = Path(REAL_ORDER_FILE) if REAL_ORDER_FILE else None
        if real_file and real_file.exists():
            rows_real, _, _ = await aread_excel_with_header_scan(
                real_file.read_bytes(), _HEADER_LOCATE_HEADERS,
            )
            data_rows = [r for r in rows_real if str(r.get("序号") or "").strip() != "汇总"]
            zero_rows = [r for r in data_rows if str(r.get(H_SHOP_CODE) or "").strip() == "0"]
            eq("AN3-20 真实文件里编码 0 就是 9 行（AN33 实测的数）", len(zero_rows), 9)
            first_zero = zero_rows[0] if zero_rows else {}
            outbound_no = str(first_zero.get(H_SYSTEM_ORDER_NO) or "").strip()
            eq("AN3-20b 这 9 行挂在同一张主订单上", {str(r.get(H_SYSTEM_ORDER_NO) or "").strip() for r in zero_rows},
               {outbound_no})
            kept_zero = sum(
                1 for r in zero_rows
                if _normalize_shipped_flag(str(r.get(H_IS_ERP_SHIPPED) or "")) != UNSHIPPED_VALUE
            )
            orders_real: list = []
            try:
                orders_real = await ExcelOrderDataSource().fetch(contents=real_file.read_bytes())
                ok("AN3-20c 整份解析通过（AN34 撤闸之后，含校验和）")
            except Exception as exc:  # noqa: BLE001
                bad("AN3-20c", f"竟然还被拒：{type(exc).__name__}：{str(exc)[:200]}")
            outbound = by_no(orders_real, outbound_no)
            truthy("AN3-20d 出库单进了结果：编码 0、店名是文件里那个字面 `-`、子订单一条没少",
                   outbound is not None and outbound.kuaimai_shop_id == "0"
                   and outbound.store_name == str(first_zero.get(H_STORE_NAME) or "").strip()
                   and len(outbound.details) == kept_zero,
                   f"店名={getattr(outbound, 'store_name', None)!r} 明细={len(outbound.details) if outbound else 0}"
                   f"（文件里非未发货的 {kept_zero} 行）")
            flag_values = {
                str(r.get(H_IS_ERP_SHIPPED)).strip()
                for r in rows_real
                if str(r.get("序号") or "").strip() != "汇总"
            }
            unknown = {v for v in flag_values if v and _normalize_shipped_flag(v) not in _ERP_SHIPPED_VALUES}
            eq("AN3-21 真实文件「是否ERP发货」的写法全部在白名单内（含 [未发货] 这种带括号的）",
               sorted(unknown), [])
            truthy("AN3-21b 且这份文件确实给了 `[未发货]` 这一格（AN30 不是空穴来风）",
                   any(v.startswith("[") or v.startswith("【") for v in flag_values),
                   f"实际写法：{sorted(flag_values)[:6]}")
            unshipped_rows = [
                r for r in rows_real
                if _normalize_shipped_flag(str(r.get(H_IS_ERP_SHIPPED) or "")) == UNSHIPPED_VALUE
            ]
            truthy("AN3-21c 未发货行确实存在，改完闸之后它们走「不入库」而不是报错",
                   len(unshipped_rows) > 0, f"{len(unshipped_rows)} 行")
        else:
            ok("AN3-20", f"{REAL_ORDER_FILE or 'ECOM_EXPORT_ORDER_FILE 没设'} 不在，跳过真实文件那四项")

        # ── AN34 自动建档：编码 0 合法 + 占位符店名不覆盖档案 ────────────
        # 业务方 2026-11-03 那句「店铺名称为0的不进行覆盖」落在 `ensure_store` 的覆盖分支上。
        stores_svc = StoreService(auth, session)
        created_store = await stores_svc.ensure_store(
            kuaimai_shop_id="AN34A", store_name="出库单", platform="其它")
        await session.flush()
        eq("AN34-1 先手工建好的店（这里等价于按文件新建）名字落得住", created_store.store_name, "出库单")
        placeholdered = await stores_svc.ensure_store(
            kuaimai_shop_id="AN34A", store_name="-", platform="淘宝")
        await session.flush()
        eq("AN34-2 文件里店名是 `-` → 档案里的店名不动", placeholdered.store_name, "出库单")
        eq("AN34-3 平台照旧刷新（例外只圈店名）", placeholdered.platform, "淘宝")
        spaced = await stores_svc.ensure_store(kuaimai_shop_id="AN34A", store_name="  --  ", platform="淘宝")
        await session.flush()
        eq("AN34-4 带首尾空格的占位写法先 trim 再认，一样不覆盖", spaced.store_name, "出库单")
        renamed = await stores_svc.ensure_store(
            kuaimai_shop_id="AN34A", store_name="改个真名", platform="淘宝")
        await session.flush()
        eq("AN34-5 「不同即覆盖」这条没被例外吃掉：真名照改", renamed.store_name, "改个真名")
        zero_named = await stores_svc.ensure_store(kuaimai_shop_id="AN34A", store_name="0", platform="淘宝")
        await session.flush()
        eq("AN34-6 `0` 这类占位符也在词表里", zero_named.store_name, "改个真名")
        fresh = await stores_svc.ensure_store(kuaimai_shop_id="AN34B", store_name="-", platform="其它")
        await session.flush()
        eq("AN34-7 例外只管覆盖、不管新建：店还不存在时仍按文件建（所以顺序是先建店再导文件）",
           fresh.store_name, "-")
        again = await stores_svc.ensure_store(kuaimai_shop_id="AN34B", store_name="暂无", platform="其它")
        await session.flush()
        eq("AN34-8 第二次命中同一家店：门牌没开重，店名仍是文件里的 `-`", (again.id, again.store_name),
           (fresh.id, "-"))

        # ── AN4 是否ERP发货白名单 + 未发货仍不入库 + 汇总行豁免 ──────────
        await parse_raises("AN4-1", [{"是否ERP发货": None}], contains="是否ERP发货不能为空")
        await parse_raises("AN4-2", [{"是否ERP发货": "0"}], contains="只能是")
        await parse_raises("AN4-3", [{"是否ERP发货": "已发货"}], contains="未发货」之一")
        for cid, value in (("AN4-4", ERP_SHIPPED_VALUE), ("AN4-5", "其他ERP发货"), ("AN4-6", "未发货")):
            orders = await parse_passes(
                cid, [{}, {"是否ERP发货": value, "系统订单号": "NORD-T", "订单商品明细ID": "NOD-T"}],
                detail=f"「{value}」是合法写法",
            )
            tested = by_no(orders, "NORD-T")
            if value == "未发货":
                truthy(f"{cid}b", tested is None, "未发货那一行依旧不入库")
            else:
                truthy(
                    f"{cid}b", tested is not None and len(tested.details) == 1
                    and tested.details[0].is_erp_shipped is (value == ERP_SHIPPED_VALUE),
                    f"is_erp_shipped={value == 'ERP发货'}",
                )
        # AN30：快麦这一格有 43 行写的是带方括号的 `[未发货]`（真实文件实测）。以前按精确字符串比对，
        # 那 43 行既不报错也没被「未发货不入库」拦下——带着 is_erp_shipped=False 落了库，还参与了主订单金额。
        for cid, value in (("AN4-9", "[未发货]"), ("AN4-10", "【未发货】"), ("AN4-11", "[ERP发货]")):
            orders = await parse_passes(
                cid, [{}, {"是否ERP发货": value, "系统订单号": "NORD-B", "订单商品明细ID": "NOD-B"}],
                detail=f"「{value}」认得",
            )
            tested = by_no(orders, "NORD-B")
            if UNSHIPPED_VALUE in value:
                truthy(f"{cid}b", tested is None, f"{value} 按未发货处理 → 那一行不入库")
            else:
                truthy(f"{cid}b", tested is not None and tested.details[0].is_erp_shipped is True,
                       f"{value} 按 ERP 发货处理")
        await parse_raises("AN4-12", [{"是否ERP发货": "[已发货]"}], contains="读到「[已发货]」")

        summary = await parse_passes(
            "AN4-7",
            [{}, {"序号": "汇总", "系统订单号": None, "店铺编码": None, "订单状态": None,
                  "主商家编码(款)": None, "商品标题(款)": None, "是否ERP发货": None,
                  "订单商品明细ID": None, "订单买家已付金额": "10.00"}],
            detail="汇总行整行豁免：必填闸一个都没碰它，校验和照样对得上",
        )
        eq("AN4-8", len(summary or []), 1)

        # ── AN5 告警 ──────────────────────────────────────────────────
        params = list(inspect.signature(OrderService._collect_cost_warnings).parameters)
        eq("AN5-1", params, ["system_order_no", "d", "cost_price", "warnings"])

        w: list[str] = []
        OrderService._collect_cost_warnings(
            "NORD-W", raw_detail(main_sku_code=MAIN, excel_cost_price=Decimal("3.115")), Decimal("3.11"), w,
        )
        eq("AN5-2", len(w), 1)
        truthy("AN5-3", "请到快麦" in w[0] and "成本价(SKU) 3.115" in w[0], f"①文案指向快麦：{w[0]}")

        w = []
        OrderService._collect_cost_warnings(
            "NORD-W", raw_detail(main_sku_code=MAIN, excel_cost_price=Decimal("3.110")), Decimal("3.11"), w,
        )
        eq("AN5-4", len(w), 0)

        w = []
        OrderService._collect_cost_warnings(
            "NORD-W", raw_detail(
                main_sku_code=MAIN, excel_cost_price=Decimal("10.00"),
                erp_shipped_cost=Decimal("99.00"), erp_return_cost=Decimal("99.00"),
            ), Decimal("10.00"), w,
        )
        eq("AN5-5", len(w), 0)

        w = []
        OrderService._collect_cost_warnings(
            "NORD-W", raw_detail(main_sku_code=MAIN, excel_cost_price=Decimal("8.00")), None, w,
        )
        eq("AN5-6", len(w), 0)

        # 端到端：①报一条，②③那种差值（实发 99 vs 自算 20、实退 99 vs 自算 10）再无出口。
        # 主表这一格是 3.11、快麦那一格是 3.115 → 拿原值直接比（AN28），必报。
        await sku_svc.create(create_payload(Decimal("3.11"), MAIN_WARN))
        w = []
        row_e2e = await order_svc._build_detail_row(
            raw_order("NORD-E2E"),
            plain_detail(main_sku_code=MAIN_WARN, excel_cost_price=Decimal("3.115"),
                         erp_shipped_cost=Decimal("99.00"), erp_return_cost=Decimal("99.00")),
            {}, w,
        )
        eq("AN5-7", len(w), 1)
        truthy(
            "AN5-8",
            "成本价(SKU)" in w[0] and "系统实发成本" not in w[0] and "实退成本" not in w[0],
            w[0] if w else "一条告警都没有",
        )
        eq("AN5-9", row_e2e["computed_product_cost"], Decimal("6.22"))
        # 主表那个数**原样**进快照，不补零、不见位进分；收列后主表列 scale=2，读回来就是 "3.11"
        # （快照列仍 4 位，落库时 MySQL 自己写成 3.1100，是同一个数）。
        eq("AN5-9b", str(row_e2e["cost_price"]), "3.11")

        dist_w: list[str] = []
        row_dist = await order_svc._build_detail_row(
            raw_order("NORD-DIST"),
            plain_detail(main_sku_code=MAIN_WARN, excel_cost_price=Decimal("3.115"), ship_qty=1, return_qty=0,
                         erp_shipped_cost=Decimal("3.12"), erp_return_cost=None, distribution_order_type=1),
            {}, dist_w,
        )
        eq("AN5-10", len(dist_w), 0)
        eq("AN5-11", row_dist["cost_price"], Decimal("3.115"))

        await session.rollback()
    finally:
        await session.close()

    # 另开 session 证明回滚干净（EXPIRE_ON_COMMIT=False，一律走原生 SQL 现读）
    async with async_db_session() as check:
        left = (await check.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'NTEST%'),"
            "       (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'NORD%'),"
            "       (SELECT COUNT(*) FROM ecommerce_order_detail WHERE source_detail_id LIKE 'NOD%'),"
            "       (SELECT COUNT(*) FROM ecommerce_store WHERE kuaimai_shop_id LIKE 'AN34%')"
        ))).one()
    eq("AN6-1", left[0], 0)
    eq("AN6-2", left[1], 0)
    eq("AN6-3", left[2], 0)
    eq("AN6-4 回滚干净：AN34 那两家探针店铺也没留下", left[3], 0)

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid:10} {detail}")
    print(f"\n== AN 轮：{passed}/{len(RESULTS)} 通过 ==")
    if passed != len(RESULTS):
        sys.exit(1)


try:
    asyncio.run(main())
except Exception:  # noqa: BLE001 — 崩了也要把已收集的结果打出来
    print("\n-- 脚本中途崩溃，以下是崩溃前收集到的结果 --")
    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid:10} {detail}")
    raise
