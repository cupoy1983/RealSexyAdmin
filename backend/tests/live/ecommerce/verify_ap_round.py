"""2026-11-08 AP 轮（§27 / ADR-0023 + ADR-0024 修订块）的回滚式验证。

编号前缀 AP，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AD/AE/AF/AG/AH/AI/AJ/AK/AL/AN/AO 不撞号。

一个事务跑完，结束显式 rollback；最后另开一个 session 证明真档案一格没被动过。

组：
AP1 建档例外的判据换成「店铺编码 0」（AP3=甲，撤销 AN34 那句「平台照常覆盖」）：
    编码 0 命中已有店 → 店名与平台两格都不动；编码非 0 → AN34 那份占位符词表照旧只管店名、平台照刷；
    新建路径一个字不改（AP5）。这一段直接拿库裡那家真的编码 0 的店跑——事务内改、结束回滚。
AP2 「NLPJ 就是 0 成本」落成库里的语义（AP1=甲）：主表 0.00 时取价拿到的是 0.00 而不是 None，
    子订单因此 cost_missing=False、computed_product_cost=0.00——这是「0 = 有意」在库里的第一例，
    同时锁住反面：NULL 仍然算缺失，订正只点 NLPJ，不许顺手把别的 NULL 刷平。
"""

import asyncio
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import delete, text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.order.importers.base import (  # noqa: E402
    RawOrderDetailRecord,
    RawOrderRecord,
)
from app.plugin.module_ecommerce.order.model import OrderDetailModel, OrderModel, SkuCostModel  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.plugin.module_ecommerce.store.service import StoreService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

ZERO_CODE = "0"          # 快麦给「出库单」留的固定编码（AP3 认的就是它）
OTHER_CODE = "APOTHER"   # 编码非 0：走 AN34 那份词表
NEW_CODE = "APNEW"       # 店不存在：走新建路径
MAIN_ZERO = "NAPZERO"    # 主表 0.00（有意0成本）
MAIN_NULL = "NAPNULL"    # 主表 NULL（缺失）
MAIN_NOTEXIST = "NAPNOTEXIST"  # 主表查无此行

RESULTS: list[tuple[str, str, str]] = []
PRE: dict[str, tuple] = {}   # 进事务前先读下的真档案原值，回滚之后拿它对照


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:  # noqa: ANN001
    (ok if actual == expected else bad)(cid, f"期望 {expected!r}，实际 {actual!r}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


def raw_order(no: str, shop: str = OTHER_CODE) -> RawOrderRecord:
    return RawOrderRecord(
        system_order_no=no, kuaimai_shop_id=shop, store_name="AP测试店铺", platform="AP淘宝", order_status="已完成",
    )


def detail_row(**kw) -> RawOrderDetailRecord:  # noqa: ANN001
    base: dict[str, object] = {
        "source_detail_id": "NAPD1", "sku_code": None, "product_name": "AP探针商品",
        "ship_qty": 400, "return_qty": 0, "paid_amount": Decimal("0.00"), "refund_amount": Decimal("0.00"),
        "freight_income": Decimal("0.00"), "weight_kg": None, "is_erp_shipped": True,
        "distribution_order_type": 0, "main_sku_code": MAIN_ZERO,
    }
    return RawOrderDetailRecord(**{**base, **kw})  # type: ignore[arg-type]


async def svc_raises(cid: str, coro, *, contains: str) -> None:  # noqa: ANN001
    try:
        value = await coro
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        (ok if contains in msg else bad)(cid, f"{type(exc).__name__}：{msg[:130]}" if contains in msg
                                          else f"报错里没有「{contains}」：{msg[:180]}")
    else:
        bad(cid, f"没有报错，返回了 {str(value)[:160]}")


async def db_row(session, shop_id: str):  # noqa: ANN001
    """事务内现读那一行（走原生 SQL，绕开 ORM 身份映射）：证明 UPDATE 真的没发出去。"""
    return (await session.execute(text(
        "SELECT store_name, platform, status FROM ecommerce_store WHERE kuaimai_shop_id = :c"
    ), {"c": shop_id})).one_or_none()


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        stores_svc = StoreService(auth, session)
        sku_svc = SkuCostService(auth, session)
        order_svc = OrderService(auth, session)

        for stmt in (
            delete(SkuCostModel).where(SkuCostModel.main_sku_code.like("NAP%")),
            delete(OrderDetailModel).where(OrderDetailModel.source_detail_id.like("NAPD%")),
            delete(OrderModel).where(OrderModel.system_order_no.like("NAPORD%")),
            delete(StoreModel).where(StoreModel.kuaimai_shop_id.in_([OTHER_CODE, NEW_CODE])),
        ):
            await session.execute(stmt)
        await session.flush()

        # ── AP1 建档例外：判据是「店铺编码 0」，不是文件那一格的写法 ────────
        real = await db_row(session, ZERO_CODE)
        truthy("AP1-0 库裡确实有编码 0 那家店（本段就拿它当被测对象，事务内改、结束回滚）",
               real is not None, f"现状 = {real}")
        PRE["row"] = real

        # ① 生产里真实会发生的那一次：9 月那份文件的编码 0 行，店名格是字面 `-`、平台格是 `快麦`
        hit = await stores_svc.ensure_store(kuaimai_shop_id=ZERO_CODE, store_name="-", platform="快麦")
        await session.flush()
        row = await db_row(session, ZERO_CODE)
        eq("AP1-1 命中已有店后一行都不写：店名仍是档案原值", row[0], real[0])
        eq("AP1-2 平台也仍是档案原值（AP3 撤销 AN34 那句「平台照常覆盖」）", row[1], real[1])
        eq("AP1-3 返回的就是那一家店，没开出第二个门牌", hit.store_name, real[0])

        # ② 模拟订正 SQL 跑完 + 人工停用的状态，再看例外扛不扛得住
        obj = await session.get(StoreModel, hit.id)
        obj.platform = "其它"
        obj.status = 1
        await session.flush()
        again = await stores_svc.ensure_store(kuaimai_shop_id=ZERO_CODE, store_name="随便起的真名", platform="拼多多")
        await session.flush()
        row = await db_row(session, ZERO_CODE)
        eq("AP1-4 编码 0 认的是编码不是写法：正常店名同样不覆盖", row[0], real[0])
        eq("AP1-5 平台不被文件里的有效值覆盖（人工改的「其它」守得住）", row[1], "其它")
        eq("AP1-6 停用不被顺手改回启用（AA8 那条边界仍在）", row[2], 1)
        eq("AP1-7 仍是那一家店", again.id, hit.id)

        # ③ 新建路径一个字不改（AP5「导入为-，我后面会手工修改」）
        fresh = await stores_svc.ensure_store(kuaimai_shop_id=NEW_CODE, store_name="-", platform="快麦")
        await session.flush()
        eq("AP1-8 例外只管覆盖不管新建：店不存在时照文件建，名字就是 `-`", fresh.store_name, "-")
        twice = await stores_svc.ensure_store(kuaimai_shop_id=NEW_CODE, store_name="暂无", platform="快麦")
        await session.flush()
        eq("AP1-9 第二次命中同一家店：门牌没开重（此时起两格都锁住了）", (twice.id, twice.store_name),
           (fresh.id, "-"))

        # ④ 编码非 0：AN34 那份词表照旧只管店名，平台照刷
        await stores_svc.ensure_store(kuaimai_shop_id=OTHER_CODE, store_name="AP别的店", platform="AP淘宝")
        await session.flush()
        placeholdered = await stores_svc.ensure_store(kuaimai_shop_id=OTHER_CODE, store_name="-", platform="京东")
        await session.flush()
        row = await db_row(session, OTHER_CODE)
        eq("AP1-10 编码非 0 + 占位符店名 → 店名不动（AN34 那条没被 AP3 吃掉）", row[0], "AP别的店")
        eq("AP1-11 编码非 0 时平台照旧刷新（例外只在编码 0 那一支）", row[1], "京东")
        spaced = await stores_svc.ensure_store(kuaimai_shop_id=OTHER_CODE, store_name="  暂无  ", platform="淘宝")
        await session.flush()
        row = await db_row(session, OTHER_CODE)
        eq("AP1-12 带首尾空格的占位写法先 trim 再认，一样不覆盖", row[0], "AP别的店")
        eq("AP1-13 trim 之后平台照常刷", row[1], "淘宝")
        renamed = await stores_svc.ensure_store(kuaimai_shop_id=OTHER_CODE, store_name="改个真名", platform="天猫")
        await session.flush()
        row = await db_row(session, OTHER_CODE)
        eq("AP1-14 「不同即覆盖」这条主线没被两条例外吃掉", (row[0], row[1]), ("改个真名", "天猫"))
        await svc_raises("AP1-15 纯空格店名仍算没填（AF3 那道写边界没松）",
                         stores_svc.ensure_store(kuaimai_shop_id=OTHER_CODE, store_name="   ", platform="天猫"),
                         contains="店铺名称不能为空")

        # ── AP2 「0 = 有意」第一次落到真实数据 ─────────────────────────
        zero_row = await sku_svc.create(SkuCostCreateSchema(
            main_sku_code=MAIN_ZERO, sku_code=None, sku_name=None, product_name="AP探针商品",
            cost_price=Decimal("0.00"), cost_source="manual", remark=None,
        ))
        eq("AP2-1 主表录入侧 0 照旧合法（AN13/AN25 拦的是小数第 3 位）", zero_row.cost_price, Decimal("0.0000"))
        await sku_svc.create(SkuCostCreateSchema(
            main_sku_code=MAIN_NULL, sku_code=None, sku_name=None, product_name="AP探针商品",
            cost_price=None, cost_source="manual", remark=None,
        ))

        eq("AP2-2 取价拿到的是 0.00 而不是 None（这就是「有意0成本」的全部含义）",
           await order_svc._lookup_sku_cost_price(MAIN_ZERO, None, {}), Decimal("0.00"))
        eq("AP2-3 反面还在：主表那格 NULL → 取价 None",
           await order_svc._lookup_sku_cost_price(MAIN_NULL, None, {}), None)
        eq("AP2-4 查无成本行 → 同样 None",
           await order_svc._lookup_sku_cost_price(MAIN_NOTEXIST, None, {}), None)

        row_zero = await order_svc._build_detail_row(
            raw_order("NAPORD-Z", ZERO_CODE), detail_row(main_sku_code=MAIN_ZERO, ship_qty=400), {}, [],
        )
        eq("AP2-5 子订单 cost_price 落 0.00", row_zero["cost_price"], Decimal("0.00"))
        eq("AP2-6 cost_missing 落 False（这一行退出「待补充成本」清单）", row_zero["cost_missing"], False)
        eq("AP2-7 0 × 400 仍然是 0.00：钱一个数字都不动", row_zero["computed_product_cost"], Decimal("0.00"))
        eq("AP2-8 退货侧同理", row_zero["computed_return_cost"], Decimal("0.00"))

        row_null = await order_svc._build_detail_row(
            raw_order("NAPORD-N", ZERO_CODE), detail_row(main_sku_code=MAIN_NULL, ship_qty=400), {}, [],
        )
        eq("AP2-9 对照：主表 NULL → 子订单 cost_price None", row_null["cost_price"], None)
        truthy("AP2-10 对照：这一行仍然算缺失（订正只点 NLPJ，不许把别的 NULL 刷平）",
               row_null["cost_missing"] is True, "NULL 仍然 → cost_missing=True")

        # ── AP2-11/12 存量那 9 行：快麦原值本来就给 0，与业务方口径对得上 ────
        real9 = (await session.execute(text(
            "SELECT COUNT(*), COALESCE(SUM(d.cost_missing), 0), COALESCE(SUM(d.excel_cost_price), 0) "
            "FROM ecommerce_order_detail d JOIN ecommerce_order o ON o.id = d.order_id "
            "JOIN ecommerce_store s ON s.id = o.store_id AND s.kuaimai_shop_id = '0'"
        ))).one()
        eq("AP2-11 编码 0 名下的子订单行仍是那 9 行", real9[0], 9)
        truthy("AP2-12 快麦那一格 9 行合计就是 0（「NLPJ 就是 0 成本」的实证）",
               Decimal(str(real9[2])) == Decimal("0"),
               f"excel_cost_price 合计 = {real9[2]}，cost_missing 合计 = {real9[1]}（订正 SQL 跑完应为 0）")
        nlpj_null = (await session.execute(text(
            "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code = 'NLPJ' AND cost_price IS NULL"
        ))).scalar()
        zero_total = (await session.execute(text(
            "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE cost_price = 0 AND main_sku_code NOT LIKE 'NAP%'"
        ))).scalar()
        print(f"    [信息] 主表 NLPJ 残留 NULL：{nlpj_null} 条（订正 SQL 跑前 13、跑后 0；本脚本只读，不动它）")
        print(f"    [信息] 主表 cost_price=0 的行数：{zero_total} 条（AP1 之前 0 条，SQL 跑完 13 条——"
              f"「有意0成本」这一类的第一例）")

        await session.rollback()
    finally:
        await session.close()

    # 另开 session：证明探针数据一条没留下、真档案那一格也没被本脚本改过
    async with async_db_session() as check:
        left = (await check.execute(text(
            "SELECT (SELECT COUNT(*) FROM ecommerce_store WHERE kuaimai_shop_id IN ('APOTHER','APNEW')),"
            "       (SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code LIKE 'NAP%'),"
            "       (SELECT COUNT(*) FROM ecommerce_order WHERE system_order_no LIKE 'NAPORD%'),"
            "       (SELECT store_name FROM ecommerce_store WHERE kuaimai_shop_id = '0'),"
            "       (SELECT platform FROM ecommerce_store WHERE kuaimai_shop_id = '0'),"
            "       (SELECT status FROM ecommerce_store WHERE kuaimai_shop_id = '0'),"
            "       (SELECT COUNT(*) FROM ecommerce_order_detail WHERE id BETWEEN 11923 AND 11931 AND cost_missing = 1)"
        ))).one()
    eq("AP3-1 探针店铺回滚干净", left[0], 0)
    eq("AP3-2 探针成本行回滚干净", left[1], 0)
    eq("AP3-3 探针订单回滚干净", left[2], 0)
    pre_name, pre_platform, pre_status = PRE["row"]
    truthy("AP3-4 真档案那家店的店名没被本脚本改掉（编码 0 例外生效）",
           left[3] == pre_name, f"当前店名 = {left[3]!r}，脚本进事务前读到的 = {pre_name!r}")
    truthy("AP3-5 真档案那家店的平台只可能来自订正 SQL，不可能是本脚本",
           left[4] in ("快麦", "其它"), f"当前平台 = {left[4]!r}（快麦=SQL 未跑，其它=SQL 已跑）")
    truthy("AP3-6 真档案那家店的停用状态没被本脚本留下", left[5] == pre_status,
           f"当前状态 = {left[5]}，脚本进事务前 = {pre_status}")
    truthy("AP3-7 那 9 行的 cost_missing 没被本脚本改过", left[6] in (0, 9),
           f"仍缺 {left[6]} 行（SQL 未跑=9，已跑=0）")

    passed = sum(1 for r in RESULTS if r[0] == "PASS")
    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid:58} {detail}")
    print(f"\n== AP 轮：{passed}/{len(RESULTS)} 通过 ==")
    if passed != len(RESULTS):
        sys.exit(1)


try:
    asyncio.run(main())
except Exception:  # noqa: BLE001 — 崩了也要把已收集的结果打出来
    print("\n-- 脚本中途崩溃，以下是崩溃前收集到的结果 --")
    for status, cid, detail in RESULTS:
        print(f"{status:4} {cid:58} {detail}")
    raise
