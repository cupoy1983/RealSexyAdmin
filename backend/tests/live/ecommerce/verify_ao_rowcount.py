"""AO 轮：订单更新「整单新建」的子订单行计入批次 row_count（§26.13 ③ 修复验证）。

每个场景单独开一个事务（复刻 db_getter 的 session.begin()），跑完强制 rollback；
收尾另开会话比对库内计数与批次计数，证明一条数据都没留下。

断言的是**增量**，不写死存量数字（AE 轮教训：锁死快照的脚本会自己变红）。
批次计数一律走原生 SQL 读——`EXPIRE_ON_COMMIT=False` 时 ORM 回读会拿到旧值。
已有订单的原始数据一律**照库里的值回传**，否则 pass1 的「数量不允许变更」会先中止，测不到第 2 遍。
"""

import asyncio
import dataclasses
import os
import sys
import traceback
import uuid
from decimal import Decimal  # noqa: F401

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault("ENVIRONMENT", "dev")

from sqlalchemy import func, select, text  # noqa: E402

from app.core.base_schema import AuthSchema, CoreUserSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402


def _load_all_models():
    import importlib
    import pkgutil

    for pkg_name in ("app.modules", "app.plugin"):
        pkg = importlib.import_module(pkg_name)
        for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".", onerror=lambda _n: None):
            if m.name.rsplit(".", 1)[-1] in ("model", "models"):
                try:
                    importlib.import_module(m.name)
                except Exception:
                    pass


_load_all_models()

from app.plugin.module_ecommerce.order.importers.base import (  # noqa: E402
    OrderDataSource,
    RawOrderDetailRecord,
    RawOrderRecord,
)
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402

AUTH = AuthSchema(user=CoreUserSchema(id=1, username="admin", name="AO 验证脚本", is_superuser=True))
SHIP_MONTH = "2026-09"
BATCH_NO = f"order-{SHIP_MONTH}"
DETAIL_FIELDS = {f.name for f in dataclasses.fields(RawOrderDetailRecord)}
RESULTS: list[tuple[str, bool]] = []


class Rollback(Exception):
    pass


class FakeSource(OrderDataSource):
    def __init__(self, raws):
        self.raws = raws

    async def fetch(self, **params):
        return self.raws


def fmt_dt(v):
    if isinstance(v, str) or v is None:
        return v
    return v.strftime("%Y-%m-%d %H:%M:%S")


def detail_from_db(d: dict, **over) -> RawOrderDetailRecord:
    """库里那一条子订单 → RawOrderDetailRecord：只取数据类认识的列，值原样回传（不触发 diff 中止）。"""
    base = {k: (fmt_dt(v) if k.endswith("_time") else v) for k, v in d.items() if k in DETAIL_FIELDS}
    base.update(over)
    return RawOrderDetailRecord(**base)


def brand_new_detail(ctx, **over) -> RawOrderDetailRecord:
    """造一条库里没有的子订单：形状抄靶子的第一条，明细ID 换成测试专用唯一值。"""
    src = ctx["details"][0]
    base = {k: v for k, v in src.items() if k in DETAIL_FIELDS}
    base["source_detail_id"] = f"AO-TEST-D-{uuid.uuid4().hex[:12]}"
    if not base.get("ship_time"):
        base["ship_time"] = next(d["ship_time"] for d in ctx["details"] if d["ship_time"])
    base.update(over)
    return RawOrderDetailRecord(**base)


async def batch_counters(session):
    row = (await session.execute(
        text("SELECT row_count, order_count, status FROM ecommerce_import_batch WHERE batch_no = :b"),
        {"b": BATCH_NO},
    )).first()
    return None if row is None else (row[0], row[1], row[2])


async def real_counts(session):
    orders = (await session.execute(
        select(func.count()).select_from(OrderModel).where(OrderModel.import_batch_id == BATCH_NO)
    )).scalar()
    details = (await session.execute(
        select(func.count())
        .select_from(OrderDetailModel)
        .join(OrderModel, OrderDetailModel.order_id == OrderModel.id)
        .where(OrderModel.import_batch_id == BATCH_NO)
    )).scalar()
    return orders, details


async def pick_target():
    """挑一张挂在目标批次、且至少一条子订单带发货时间的订单当靶子。"""
    async with async_db_session() as s:
        orders = (await s.execute(
            select(OrderModel).where(OrderModel.import_batch_id == BATCH_NO).order_by(OrderModel.id).limit(200)
        )).scalars().all()
        for o in orders:
            ds = (await s.execute(
                select(OrderDetailModel).where(OrderDetailModel.order_id == o.id).order_by(OrderDetailModel.id)
            )).scalars().all()
            if not ds:
                continue
            cols = {c.key for c in ds[0].__table__.columns}
            drows = [{k: (fmt_dt(v) if k.endswith("_time") else v) for k, v in ((k, getattr(d, k)) for k in cols)} for d in ds]
            if not any(d["ship_time"] for d in drows):
                continue
            store = await s.get(StoreModel, o.store_id)
            ocols = {c.key for c in o.__table__.columns}
            return {
                "order": {k: getattr(o, k) for k in ocols},
                "details": drows,
                "kuaimai_shop_id": store.kuaimai_shop_id if store else "0",
                "store_name": store.store_name if store else "测试店铺",
                "platform": store.platform if store else "快麦",
            }
        return {"error": "库里没有挂在 order-2026-09、有子订单且带发货时间的主订单"}


def raw_for_existing(ctx, extra_details=()) -> RawOrderRecord:
    """已有订单：把库里每一行原样回传（pass1 的「缺失/数量变更」两道闸才不中止）+ 可选的新行。"""
    details = [detail_from_db(d) for d in ctx["details"]]
    details.extend(extra_details)
    od = ctx["order"]
    return RawOrderRecord(
        system_order_no=od["system_order_no"],
        kuaimai_shop_id=ctx["kuaimai_shop_id"],
        store_name=ctx["store_name"],
        platform=ctx["platform"],
        order_status=od["order_status"],
        buyer_paid_amount=sum((d.paid_amount or Decimal("0") for d in details), Decimal("0")),
        refund_amount=sum((d.refund_amount or Decimal("0") for d in details), Decimal("0")),
        freight_income=od["freight_income"],
        province=od["province"],
        city=od["city"],
        system_remark=od["system_remark"],
        seller_remark=od["seller_remark"],
        is_shua_order=od["is_shua_order"],
        details=details,
    )


def raw_for_new_order(ctx, detail_count: int) -> RawOrderRecord:
    """整单新建：订单号与全部明细ID 都是测试专用的，成本/金额口径照靶子。"""
    details = [brand_new_detail(ctx) for _ in range(detail_count)]
    od = ctx["order"]
    return RawOrderRecord(
        system_order_no=f"AO-TEST-{uuid.uuid4().hex[:10]}",
        kuaimai_shop_id=ctx["kuaimai_shop_id"],
        store_name=ctx["store_name"],
        platform=ctx["platform"],
        order_status=od["order_status"],
        buyer_paid_amount=sum((d.paid_amount or Decimal("0") for d in details), Decimal("0")),
        refund_amount=sum((d.refund_amount or Decimal("0") for d in details), Decimal("0")),
        freight_income=od["freight_income"],
        province=od["province"],
        city=od["city"],
        system_remark=od["system_remark"],
        seller_remark=od["seller_remark"],
        is_shua_order=od["is_shua_order"],
        details=details,
    )


def check(label, expected, actual):
    passed = str(expected) == str(actual)
    RESULTS.append((label, passed))
    print(f"    {'✅' if passed else '❌'} {label}\n         期望: {expected}\n         实际: {actual}")


async def run_scenario(title, ctx, make_raws, expect_row_delta, expect_order_delta, expect_msg_has=None):
    print(f"\n{'=' * 78}\n▶ {title}\n{'=' * 78}")
    async with async_db_session() as session:
        try:
            async with session.begin():
                b_before = await batch_counters(session)
                r_before = await real_counts(session)
                res = await OrderService(AUTH, session).update_orders(
                    ship_month=SHIP_MONTH, data_source=FakeSource(make_raws())
                )
                if not res.ok:
                    print(f"  ‼ 更新被中止：{res.msg.splitlines()[0]}")
                    RESULTS.append((title + " —— 应成功应用", False))
                    raise Rollback
                print(f"  返回 ok=True | {res.msg.splitlines()[0]}")
                b_after = await batch_counters(session)
                r_after = await real_counts(session)
                check("批次 row_count 增量", expect_row_delta, b_after[0] - b_before[0])
                check("批次 order_count 增量", expect_order_delta, b_after[1] - b_before[1])
                if expect_msg_has:
                    check(f"结果文案含「{expect_msg_has}」", True, expect_msg_has in res.msg)
                # 计数器必须与真实行数一起动，否则修好的只是数字
                check("真实子订单行增量 = 批次 row_count 增量", b_after[0] - b_before[0], r_after[1] - r_before[1])
                check("真实主订单增量 = 批次 order_count 增量", b_after[1] - b_before[1], r_after[0] - r_before[0])
                raise Rollback
        except Rollback:
            print("  ↩ 本场景已回滚")
        except Exception:
            print("  ‼ 场景异常（同样随事务回滚）:")
            traceback.print_exc()
            RESULTS.append((title + " —— 执行异常", False))


async def main():
    async with async_db_session() as s:
        base_batch = await batch_counters(s)
        base_real = await real_counts(s)
    if base_batch is None:
        print(f"❌ 批次 {BATCH_NO} 不存在，脚本不跑")
        return
    print(
        f"【基线】批次 {BATCH_NO}：row_count={base_batch[0]} order_count={base_batch[1]} status={base_batch[2]}"
        f" ｜ 真实：{base_real[0]} 张主订单 / {base_real[1]} 行子订单"
    )

    ctx = await pick_target()
    if "error" in ctx:
        print(f"❌ {ctx['error']}")
        return
    print(f"【靶子】订单 {ctx['order']['system_order_no']}（库里 {len(ctx['details'])} 行）")

    # AO1 只有整单新建（1 单 2 行）——修复前这一档 row_count 增量是 0
    await run_scenario(
        "AO1 整单新建 1 单 / 2 行 → row_count 必须 +2",
        ctx,
        lambda: [raw_for_new_order(ctx, 2)],
        2,
        1,
        "新建订单 1 单（含 2 条子订单",
    )

    # AO2 已有订单 +1 行，另整单新建 1 单 3 行 → +4（修复前只有 +1）
    await run_scenario(
        "AO2 已有订单 +1 行、另整单新建 1 单 3 行 → row_count 必须 +4",
        ctx,
        lambda: [
            raw_for_existing(ctx, [brand_new_detail(ctx)]),
            raw_for_new_order(ctx, 3),
        ],
        4,
        1,
        "新建订单 1 单（含 3 条子订单",
    )

    # AO3 只有已有订单新增 1 行（防回归：这条口径本来就是对的）
    await run_scenario(
        "AO3 只有已有订单新增 1 行 → row_count +1（本来就该对）",
        ctx,
        lambda: [raw_for_existing(ctx, [brand_new_detail(ctx)])],
        1,
        0,
    )

    # AO4 只有整单新建 1 单 1 行 → +1/+1（最小粒度，确认不是「按单算了一行」）
    await run_scenario(
        "AO4 整单新建 1 单 / 1 行 → row_count +1",
        ctx,
        lambda: [raw_for_new_order(ctx, 1)],
        1,
        1,
    )

    # AO5 回滚不留痕
    async with async_db_session() as s:
        after_batch = await batch_counters(s)
        after_real = await real_counts(s)
        leftovers = (await s.execute(
            select(func.count()).select_from(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("AO-TEST-%"))
        )).scalar()
        leftover_orders = (await s.execute(
            select(func.count()).select_from(OrderModel).where(OrderModel.system_order_no.like("AO-TEST-%"))
        )).scalar()
    check("回滚后批次计数回到基线", base_batch, after_batch)
    check("回滚后真实计数回到基线", base_real, after_real)
    check("回滚后无 AO-TEST 子订单残留", 0, leftovers)
    check("回滚后无 AO-TEST 主订单残留", 0, leftover_orders)

    ok = sum(1 for _, p in RESULTS if p)
    print(f"\n{'=' * 78}\n合计 {len(RESULTS)} 条断言，通过 {ok}，FAIL {len(RESULTS) - ok}")
    for label, p in RESULTS:
        if not p:
            print(f"  ❌ {label}")


if __name__ == "__main__":
    asyncio.run(main())
