"""2026-10-25 「混挂双向禁死」验证（§5.1 第7步）。全程事务内跑，结束强制 rollback。"""

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
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.order.crud import SkuCostCRUD  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

M = "ZZ-MIXBAN"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def expect_error(name: str, coro, keyword: str) -> None:
    """断言服务层按预期报错，且报错文案含指定关键词。"""
    try:
        await coro
        check(name, False, "未抛错，校验被放行")
    except CustomException as e:
        check(name, keyword in e.msg, f"实际文案：{e.msg}")
    except Exception as e:  # noqa: BLE001
        check(name, False, f"抛了非业务异常：{type(e).__name__}: {e}")


async def main() -> None:
    auth = AuthSchema()
    async with async_db_session() as db:
        try:
            svc = SkuCostService(auth, db)
            omd = M  # 缩写，便于阅读

            # 存量基线自取，不写死 1744：锁死快照的脚本会随数据变化自己变红（AE 轮教训）
            base_total = (await db.execute(text(
                "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE is_deleted = 0 AND main_sku_code NOT LIKE 'ZZ-MIXBAN%'"
            ))).fetchone()[0]

            # ── U1 主编码下已有规格行，再建商品级行 → 拦 ──
            spec1 = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{M}-A", sku_code=f"{M}-A-S1",
                product_name="混挂测试-规格商品", cost_price=Decimal("5.00"),
            ))
            await db.flush()
            await expect_error(
                "U1 已有规格行时禁建商品级行",
                svc.create(SkuCostCreateSchema(
                    main_sku_code=f"{M}-A", sku_code=None,
                    product_name="混挂测试-想建商品级", cost_price=Decimal("6.00"),
                )),
                "已有按规格录入",
            )

            # ── U2 主编码是商品级行，再建规格行 → 拦 ──
            await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{M}-B", sku_code=None,
                product_name="混挂测试-不分规格", cost_price=Decimal("7.00"),
            ))
            await db.flush()
            await expect_error(
                "U2 已有商品级行时禁建规格行",
                svc.create(SkuCostCreateSchema(
                    main_sku_code=f"{M}-B", sku_code=f"{M}-B-S1",
                    product_name="混挂测试-想建规格", cost_price=Decimal("8.00"),
                )),
                "已登记为「不分规格」",
            )

            # ── U3 同主编码下多规格行照常放行（新校验不能误伤正常多规格） ──
            spec2 = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{M}-A", sku_code=f"{M}-A-S2",
                product_name="混挂测试-第二个规格", cost_price=Decimal("9.00"),
            ))
            await db.flush()
            check("U3 同主编码多规格行放行", spec2.id > 0, f"id={spec2.id}")

            # ── U4 不同主编码的商品级行照常放行 ──
            other = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{M}-C", sku_code=None,
                product_name="混挂测试-另一款不分规格", cost_price=Decimal("1.00"),
            ))
            await db.flush()
            check("U4 不同主编码的商品级行放行", other.id > 0, f"id={other.id}")

            # ── U5/U6 原有两条查重不能被新校验顶掉 ──
            await expect_error(
                "U5 商品级行重复仍按原文案拦",
                svc.create(SkuCostCreateSchema(
                    main_sku_code=f"{M}-B", sku_code=None,
                    product_name="混挂测试-重复商品级", cost_price=Decimal("2.00"),
                )),
                "已有「不分规格」的成本记录",
            )
            await expect_error(
                "U6 规格编码重复仍按原文案拦",
                svc.create(SkuCostCreateSchema(
                    main_sku_code=f"{M}-A", sku_code=f"{M}-A-S1",
                    product_name="混挂测试-重复规格", cost_price=Decimal("3.00"),
                )),
                "该规格商家编码已存在成本记录",
            )

            # ── U7 绕过服务层直插的混挂存量数据，取价兜底闸仍不兜底 ──
            crud = SkuCostCRUD(auth, db)
            g_main = f"{M}-G"
            bypass_spec = await crud.create(data=SkuCostCreateSchema(
                main_sku_code=g_main, sku_code=f"{M}-G-S1",
                product_name="混挂绕过-规格行", cost_price=Decimal("10.00"), cost_source="manual",
            ))
            bypass_prod = await crud.create(data=SkuCostCreateSchema(
                main_sku_code=g_main, sku_code=None,
                product_name="混挂绕过-商品级行", cost_price=Decimal("20.00"), cost_source="manual",
            ))
            await db.flush()
            order_svc = OrderService(auth, db)
            gate_price = await order_svc._lookup_sku_cost_price(g_main, None, {})
            spec_price = await order_svc._lookup_sku_cost_price(g_main, f"{M}-G-S1", {})
            check("U7a 混挂下规格编码为NULL的取价不兜底(None)", gate_price is None, f"实际 {gate_price}")
            check("U7b 混挂下精确规格编码仍取到自身成本", spec_price == Decimal("10.0000"), f"实际 {spec_price}")
            check("U7c 绕过服务层确实插成了混挂(2行)",
                  (await db.execute(text("SELECT COUNT(*) FROM ecommerce_sku_cost WHERE main_sku_code=:m"),
                                    {"m": g_main})).fetchone()[0] == 2,
                  "商品级+规格级各一条")
            await db.execute(text("DELETE FROM ecommerce_sku_cost WHERE main_sku_code=:m"), {"m": g_main})
            await db.flush()

            # ── U8 存量数据零混挂（新校验的前提，也是订正是否干净的证据） ──
            mixed = (await db.execute(text(
                "SELECT main_sku_code FROM ecommerce_sku_cost "
                "WHERE is_deleted = 0 AND main_sku_code IS NOT NULL "
                "GROUP BY main_sku_code "
                "HAVING SUM(sku_code IS NULL) > 0 AND SUM(sku_code IS NOT NULL) > 0"
            ))).fetchall()
            check("U8 存量商品成本表零混挂主编码", len(mixed) == 0, f"混挂主编码：{[r.main_sku_code for r in mixed][:5]}")

            total = (await db.execute(text(
                "SELECT COUNT(*) FROM ecommerce_sku_cost WHERE is_deleted = 0 AND main_sku_code NOT LIKE 'ZZ-MIXBAN%'"
            ))).fetchone()[0]
            check("U9 存量行数与本轮开始前的基线一致", total == base_total, f"基线 {base_total}，实际 {total}")

            # ── U10 删掉规格行后，同一个主编码可以再建商品级行（禁死不是永久锁） ──
            await svc.delete([spec1.id, spec2.id])
            after = await svc.create(SkuCostCreateSchema(
                main_sku_code=f"{M}-A", sku_code=None,
                product_name="混挂测试-规格行清空后建商品级", cost_price=Decimal("11.00"),
            ))
            await db.flush()
            check("U10 删光规格行后允许建商品级行", after.id > 0, f"id={after.id}")
            check("U11 商品级行取价正常命中",
                  await order_svc._lookup_sku_cost_price(f"{M}-A", None, {}) == Decimal("11.0000"), "")

            # 中途回滚掉本测试建的行，保证最后 rollback 前的数据是自洽的（不留半截）
        finally:
            await db.rollback()

    ok = sum(1 for _, p, _ in results if p)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} | {name}{' | ' + detail if detail else ''}")
    print(f"合计 {ok}/{len(results)} 通过")


asyncio.run(main())
