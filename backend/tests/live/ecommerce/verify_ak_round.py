"""2026-10-30 AK 轮（§24）改动的回滚式验证：一个事务跑完，结束显式 rollback，库里什么都不留。

编号前缀 AK，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AD/AE/AF/AG/AI/AJ 不撞号。
「只看缺账单」的入口从展开区（子订单行级）搬到主订单列表（EXISTS），§24.3 的四条数据钉子逐条
断言：判据四个条件一个字没改、EXISTS 不翻倍、其它筛选照旧叠加、旧的两条路径（展开区勾选框 +
后端 freight_missing 参数）确实删干净了。第五条钉子（AC26 三条断言改指主列表）写在 AC 脚本里。
"""

import asyncio
import inspect
import sys
from datetime import datetime
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import delete, false, select, true  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.order.controller import get_order_detail_list_controller  # noqa: E402
from app.plugin.module_ecommerce.order.crud import OrderDetailCRUD  # noqa: E402
from app.plugin.module_ecommerce.order.importers.base import DISTRIBUTION_TYPE_NORMAL  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import OrderQueryParam  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []
AUTH = AuthSchema(permissions=["*:*:*"])

BATCH = "AKTESTBATCH"
SHIP_AT = datetime(2020, 5, 10, 12, 0, 0)


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


def missing_expr() -> object:
    """脚本自己手写的四条件，当 oracle 用——与 crud.missing_bill_conditions 对拍，不共用代码。"""
    return select(OrderDetailModel.id).where(
        OrderDetailModel.order_id == OrderModel.id,
        OrderDetailModel.is_deleted == false(),
        OrderDetailModel.is_erp_shipped == true(),
        OrderDetailModel.tracking_no.isnot(None),
        OrderDetailModel.freight_cost.is_(None),
        OrderDetailModel.distribution_order_type == DISTRIBUTION_TYPE_NORMAL,
    ).exists()


async def hit_orders(session, **extra) -> tuple[set[str], int]:
    """按 §24 的入口查主订单：走的就是界面那条 service.page，不抄近路。"""
    res = await OrderService(AUTH, session).page(
        page_no=1, page_size=500, search=OrderQueryParam(**extra)
    )
    return {r.system_order_no for r in res.items}, res.total


# 六类「不该把主订单带进来」的行，各一张单，一一对应 §24.3 钉子 2
EXCLUDED = {
    "AKORD-分销": "分销行（distribution_order_type≠0）不该带进来",
    "AKORD-未发货": "非ERP发货行不该带进来",
    "AKORD-无运单": "运单号为空的行不该带进来",
    "AKORD-运费置零": "运费已定（人工置 0）的行不该带进来",
    "AKORD-已有运费": "运费已定（>0）的行不该带进来",
    "AKORD-软删行": "软删掉的子订单行不该带进来",
}

PLAN = [
    ("AKORD-M1", "广东", [("AKOD-M1A", {})]),
    ("AKORD-M2", "浙江", [("AKOD-M2A", {}), ("AKOD-M2B", {"remark": "[2020] 字段 a→b；"})]),
    ("AKORD-分销", "浙江", [("AKOD-D1", {"dist": 1})]),
    ("AKORD-未发货", "浙江", [("AKOD-N1", {"erp": False})]),
    ("AKORD-无运单", "浙江", [("AKOD-T1", {"tracking": None})]),
    ("AKORD-运费置零", "浙江", [("AKOD-Z1", {"freight": Decimal("0.00")})]),
    ("AKORD-已有运费", "浙江", [("AKOD-P1", {"freight": Decimal("5.50")})]),
    ("AKORD-软删行", "浙江", [("AKOD-S1", {"deleted": True})]),
]


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    ids: dict[str, int] = {}
    session = async_db_session()
    try:
        store_id = (await session.execute(select(StoreModel.id).order_by(StoreModel.id).limit(1))).scalar()
        truthy("AK0 夹具用现成店铺（不新建，免得碰店铺表的规矩）", store_id is not None, f"store_id={store_id}")
        if store_id is None:
            return

        for stmt in (
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("AKOD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("AKORD-%")),
        ):
            await session.execute(stmt)
        await session.flush()

        for no, province, _ in PLAN:
            o = OrderModel(
                system_order_no=no, store_id=store_id, platform="ak", order_status="已发货",
                province=province, city="AK市", import_batch_id=BATCH,
            )
            session.add(o)
            await session.flush()
            ids[no] = o.id
        for no, _, rows in PLAN:
            for sid, kw in rows:
                session.add(OrderDetailModel(
                    order_id=ids[no], source_detail_id=sid, product_name="AK商品", ship_qty=1,
                    is_erp_shipped=kw.get("erp", True), tracking_no=kw.get("tracking", "AKWB"),
                    freight_cost=kw.get("freight"),
                    distribution_order_type=kw.get("dist", DISTRIBUTION_TYPE_NORMAL),
                    ship_time=SHIP_AT, update_remark=kw.get("remark"),
                    is_deleted=kw.get("deleted", False),
                ))
        await session.flush()

        # ══ 钉子 1：筛出来的每张主订单确实含 ≥1 条缺账单子订单 ═══════════════
        hit, total = await hit_orders(session, system_order_no="AKORD-", freight_missing=True)
        oracle = set((await session.execute(
            select(OrderModel.system_order_no).where(
                OrderModel.system_order_no.like("AKORD-%"),
                OrderModel.is_deleted == false(),
                missing_expr(),
            )
        )).scalars())
        eq("AK1-1 命中的主订单 = {M1, M2}", hit, {"AKORD-M1", "AKORD-M2"})
        eq("AK1-2 与独立 oracle 逐单一致（不是各自凑巧）", hit, oracle)

        # ══ 钉子 2：四类不该进判据的行都不把主订单带进来 ═════════════════════
        for no, why in EXCLUDED.items():
            truthy(f"AK2 {why}", no not in hit, f"命中集合={sorted(hit)}")

        # ══ 钉子 3：一张单里多条缺账单行只出现一次（EXISTS 不是 JOIN）════════
        eq("AK3-1 AKORD-M2 有两条缺账单行，命中仍只 1 张", len(hit), 2)
        eq("AK3-2 分页 total 没被撑大", total, 2)

        # ══ 钉子 4：与其它条件叠加判据不变，勾掉即回到未筛状态 ═══════════════
        plain, plain_total = await hit_orders(session, system_order_no="AKORD-")
        eq("AK4-1 不勾时 8 张夹具单全在", plain, {p[0] for p in PLAN})
        eq("AK4-2 不勾时 total=8", plain_total, 8)
        eq("AK4-3 勾掉（freight_missing=False）与不传这个键等价",
           (await hit_orders(session, system_order_no="AKORD-", freight_missing=False))[0], plain)
        eq("AK5-1 叠加省份=浙江：只剩 AKORD-M2",
           (await hit_orders(session, system_order_no="AKORD-", province="浙江", freight_missing=True))[0],
           {"AKORD-M2"})
        eq("AK5-2 叠加店铺：判据不变",
           (await hit_orders(session, system_order_no="AKORD-", store_id=store_id, freight_missing=True))[0],
           {"AKORD-M1", "AKORD-M2"})
        eq("AK5-3 叠加发货时间区间（含）：判据不变",
           (await hit_orders(session, system_order_no="AKORD-", freight_missing=True,
                             ship_time=["2020-05-01 00:00:00", "2020-05-31 23:59:59"]))[0],
           {"AKORD-M1", "AKORD-M2"})
        eq("AK5-4 叠加发货时间区间（不含）：筛空而不是筛错",
           (await hit_orders(session, system_order_no="AKORD-", freight_missing=True,
                             ship_time=["2019-01-01 00:00:00", "2019-12-31 23:59:59"]))[0], set())
        eq("AK6 两个 EXISTS（更新状态 + 缺账单）叠加互不干扰：只有 AKORD-M2",
           (await hit_orders(session, system_order_no="AKORD-", update_status="updated",
                             freight_missing=True))[0], {"AKORD-M2"})

        # ══ 展开区看的是这一单的全部子订单；旧的第二把尺确实删了 ═════════════
        svc = OrderService(AUTH, session)
        eq("AK7-1 点开 AKORD-M2 看到全部 2 条子订单（缺不缺账单都在列里直接看）",
           len(await svc.details_by_order(ids["AKORD-M2"])), 2)
        eq("AK7-2 点开 AKORD-运费置零 照样看到 1 条（不缺账单也不被展开区筛掉）",
           len(await svc.details_by_order(ids["AKORD-运费置零"])), 1)
        sig_svc = inspect.signature(OrderService.details_by_order)
        truthy("AK8 service.details_by_order 的 freight_missing 形参已删除",
               "freight_missing" not in sig_svc.parameters, str(sig_svc))
        sig_ctl = inspect.signature(get_order_detail_list_controller)
        truthy("AK9 控制器 /detail/list/{order_id} 的 freight_missing 查询参数已删除",
               "freight_missing" not in sig_ctl.parameters, str(sig_ctl))

        # ══ 批次确认提示与主列表同一把尺（判据只住 crud 一处）═════════════════
        crud_cnt = await OrderDetailCRUD(AUTH, session).count(
            order_id=("in", list(ids.values())), missing_bill=True)
        oracle_cnt = (await session.execute(
            select(OrderDetailModel.id).where(
                OrderDetailModel.source_detail_id.like("AKOD%"),
                OrderDetailModel.is_deleted == false(),
                OrderDetailModel.is_erp_shipped == true(),
                OrderDetailModel.tracking_no.isnot(None),
                OrderDetailModel.freight_cost.is_(None),
                OrderDetailModel.distribution_order_type == DISTRIBUTION_TYPE_NORMAL,
            )
        )).all()
        eq("AK10-1 缺账单计数 = 独立 oracle（确认批次的软提示与主列表同尺）", crud_cnt, len(oracle_cnt))
        eq("AK10-2 夹具里缺账单行应为 3 条（M1A/M2A/M2B）", len(oracle_cnt), 3)
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:300]}")
    finally:
        await session.rollback()
        await session.close()

    async with async_db_session() as probe:
        o_left = (await probe.execute(
            select(OrderModel.id).where(OrderModel.system_order_no.like("AKORD-%"))
        )).all()
        truthy("AK11 回滚干净：主订单夹具零残留", len(o_left) == 0, f"残留 {len(o_left)} 张")
        d_left = (await probe.execute(
            select(OrderDetailModel.id).where(OrderDetailModel.source_detail_id.like("AKOD%"))
        )).all()
        truthy("AK11b 回滚干净：子订单夹具零残留", len(d_left) == 0, f"残留 {len(d_left)} 行")
        b_left = (await probe.execute(
            select(OrderModel.id).where(OrderModel.import_batch_id == BATCH)
        )).all()
        truthy("AK11c 夹具批次号没留下", len(b_left) == 0, f"残留 {len(b_left)} 张")
        real = (await probe.execute(select(OrderModel.id).where(OrderModel.is_deleted == false()))).all()
        truthy("AK12 真实主订单存量仍可查（夹具没弄脏库）", len(real) > 0, f"当前主订单 {len(real)} 张")

    for status, cid, msg in RESULTS:
        print(f"{status:4} {cid} {msg}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
