"""2026-10-25 两项修复的验证：成本价显式清空 + 商品成本硬删。全程事务内跑，结束强制 rollback。"""

import asyncio
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema, SkuCostUpdateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

results: list[tuple[str, bool, str]] = []
MARK = "ZZ-HARDDEL-TEST"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def fetch(db, id_: int):
    return (await db.execute(text(
        "SELECT cost_price, product_name, is_deleted FROM ecommerce_sku_cost WHERE id=:i"
    ), {"i": id_})).first()


async def count_by_code(db, code: str) -> int:
    return (await db.execute(text(
        "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE sku_code=:c"
    ), {"c": code})).fetchone()[0]


async def main() -> None:
    auth = AuthSchema()
    async with async_db_session() as db:
        try:
            svc = SkuCostService(auth, db)

            # ── 准备一条规格级成本行 ──
            created = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{MARK}-MAIN", sku_code=f"{MARK}-SPEC1",
                product_name="硬删测试商品", cost_price=Decimal("9.99"),
            ))
            oid = created.id
            await db.flush()
            check("T1 测试行已建(成本 9.99)", (await fetch(db, oid)).cost_price == Decimal("9.99"),
                  str((await fetch(db, oid)).cost_price))

            # ── 修复二：清空成本价 ──
            await svc.update(oid, SkuCostUpdateSchema(cost_price=None))
            await db.flush()
            row = await fetch(db, oid)
            check("T2 显式 null 清空成本价生效(NULL)", row.cost_price is None, f"实际 {row.cost_price}")

            # 重新填一个 0：0 是有意 0 成本，不能被当成缺失
            await svc.update(oid, SkuCostUpdateSchema(cost_price=Decimal("0")))
            await db.flush()
            check("T3 可再写回 0(有意0成本)", (await fetch(db, oid)).cost_price == Decimal("0.0000"),
                  str((await fetch(db, oid)).cost_price))

            # PATCH 语义没被破坏：不带 cost_price 的更新不能把成本抹掉
            await svc.update(oid, SkuCostUpdateSchema(product_name=f"{MARK}-改名"))
            await db.flush()
            row = await fetch(db, oid)
            check("T4 未提交成本价时保持原值", row.cost_price == Decimal("0.0000") and row.product_name == f"{MARK}-改名",
                  f"cost={row.cost_price} name={row.product_name}")

            # 备注清空走同一条显式 null 通道
            await svc.update(oid, SkuCostUpdateSchema(remark="一句话备注"))
            await db.flush()
            await svc.update(oid, SkuCostUpdateSchema(remark=None))
            await db.flush()
            remark = (await db.execute(text("SELECT remark FROM ecommerce_sku_cost WHERE id=:i"), {"i": oid})).fetchone().remark
            check("T5 备注显式 null 也落库", remark is None, f"实际 {remark!r}")

            # 前端把 undefined 规范成 null 后不会再丢键
            payload = SkuCostUpdateSchema.model_validate({"cost_price": None})
            check("T6 前端 null 载荷被认成已设置", "cost_price" in payload.model_dump(exclude_unset=True),
                  str(payload.model_dump(exclude_unset=True)))
            payload2 = SkuCostUpdateSchema.model_validate({"product_name": "x"})
            check("T7 未提交的键不进入更新字典", "cost_price" not in payload2.model_dump(exclude_unset=True),
                  str(payload2.model_dump(exclude_unset=True)))

            # ── 修复一：硬删 ──
            await svc.delete([oid])
            await db.flush()
            check("T8 删除后表里没有该行(含软删)", await fetch(db, oid) is None, str(await fetch(db, oid)))

            # 同一个规格编码可以重新录入 —— 这正是之前 1062 的场景
            again = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{MARK}-MAIN", sku_code=f"{MARK}-SPEC1",
                product_name="硬删测试商品重建", cost_price=Decimal("1.00"),
            ))
            await db.flush()
            check("T9 删除后同一规格编码可重新录入",
                  again.id != oid and await count_by_code(db, f"{MARK}-SPEC1") == 1,
                  f"新 id={again.id} 同码行数={await count_by_code(db, f'{MARK}-SPEC1')}")

            # 商品级行（sku_code 为 NULL）删除后可重建，服务层去重靠活行
            prod = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{MARK}-MAIN2", sku_code=None,
                product_name="不分规格测试", cost_price=Decimal("2.00"),
            ))
            await svc.delete([prod.id])
            await db.flush()
            rebuilt = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{MARK}-MAIN2", sku_code=None,
                product_name="不分规格测试重建", cost_price=Decimal("3.00"),
            ))
            await db.flush()
            check("T10 商品级行删除后可重建", rebuilt.product_name == "不分规格测试重建", f"id={rebuilt.id}")

            # 批量删除也是硬删
            b1 = await svc.create(SkuCostCreateSchema(main_sku_code=f"{MARK}-B1", sku_code=f"{MARK}-B1S", product_name="批量1"))
            b2 = await svc.create(SkuCostCreateSchema(main_sku_code=f"{MARK}-B2", sku_code=f"{MARK}-B2S", product_name="批量2"))
            await db.flush()
            await svc.delete([b1.id, b2.id])
            await db.flush()
            check("T11 批量删除也是物理删除", await fetch(db, b1.id) is None and await fetch(db, b2.id) is None, "")

            # 删除不存在的 id 仍然报错，且不牵连合法 id（校验在 DELETE 语句之前，无需回滚）
            try:
                await svc.delete([rebuilt.id, 999999999])
                check("T12 含不存在 id 时整批拒绝", False, "没有抛错")
            except Exception as e:  # noqa: BLE001
                still = await fetch(db, rebuilt.id)
                check("T12 含不存在 id 时整批拒绝", "不存在" in str(e) and still is not None,
                      f"{e!s} 行还在={still is not None}")

            # 空 ids 仍拒绝
            try:
                await svc.delete([])
                check("T13 空 ids 被拒", False, "没有抛错")
            except Exception as e:  # noqa: BLE001
                check("T13 空 ids 被拒", "不能为空" in str(e), str(e))

            # 存量事实：整表没有软删幽灵行
            ghosts = (await db.execute(text(
                "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE is_deleted=1"
            ))).fetchone()[0]
            total = (await db.execute(text(
                "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE is_deleted=0"
            ))).fetchone()[0]
            check("T14 存量无软删幽灵行", ghosts == 0, f"幽灵 {ghosts} 条 / 活行 {total} 条")

            # 订单明细表不依赖成本行的外键，硬删不会牵连订单
            fks = (await db.execute(text(
                "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE REFERENCED_TABLE_NAME='ecommerce_sku_cost' AND TABLE_SCHEMA=DATABASE()"
            ))).fetchone()[0]
            check("T15 没有外键引用成本表", fks == 0, f"引用列 {fks} 个")
        finally:
            await db.rollback()

    ok = 0
    for name, passed, detail in results:
        ok += 1 if passed else 0
        print(f"{'PASS' if passed else 'FAIL'}  {name}  {detail if not passed or detail else ''}")
    print(f"合计 {ok}/{len(results)} 通过")


asyncio.run(main())
