"""2026-10-24 编码语义落地后的功能验证：全程在事务里跑，结束强制 rollback，不留任何写入。"""

import asyncio
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text  # noqa: E402

from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.order.importers.base import normalize_spec_code  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.core.base_model import MappedBase  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)  # 与框架启动一致：引全模型，否则 relationship 里的 UserModel 解析不了

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


async def main() -> None:
    auth = AuthSchema()
    async with async_db_session() as db:
        try:
            svc = OrderService(auth, db)
            cost_svc = SkuCostService(auth, db)

            check("V1a 两码相同→NULL", normalize_spec_code("A", "A") is None, repr(normalize_spec_code("A", "A")))
            check("V1b 空串→NULL", normalize_spec_code("A", "") is None, repr(normalize_spec_code("A", "")))
            check("V1c 主编码空+规格有值→保留", normalize_spec_code(None, "B") == "B", repr(normalize_spec_code(None, "B")))
            check("V1d 两码不同→保留", normalize_spec_code("A", "B") == "B", repr(normalize_spec_code("A", "B")))

            # 存量不变式必须在任何测试写入之前读，否则会被本脚本自己的插入污染
            conflict = (await db.execute(text(
                "SELECT COUNT(*) FROM (SELECT main_sku_code FROM ecommerce_sku_cost WHERE is_deleted=0 "
                "AND main_sku_code IS NOT NULL GROUP BY main_sku_code "
                "HAVING SUM(sku_code IS NULL)>0 AND SUM(sku_code IS NOT NULL)>0) t"
            ))).fetchone()[0]
            check("V9 不存在商品级/规格级混挂的主编码", conflict == 0, f"冲突主编码 {conflict} 个")

            rows = (await db.execute(text(
                "SELECT main_sku_code, sku_code, cost_price FROM ecommerce_order_detail "
                "WHERE is_deleted=0 AND sku_code IS NOT NULL AND cost_price IS NOT NULL LIMIT 200"
            ))).all()
            cache: dict = {}
            bad = []
            for main, spec, stored in rows:
                got = await svc._lookup_sku_cost_price(main, spec, cache)
                if got != stored:
                    bad.append(f"{main}/{spec}: 主表算得 {got}，订正落库 {stored}")
            check("V2 规格级取价与订正结果一致(200行)", not bad, f"抽样 {len(rows)} 行，不符 {len(bad)} 行；{bad[:3]}")

            rows2 = (await db.execute(text(
                "SELECT main_sku_code, cost_price FROM ecommerce_order_detail "
                "WHERE is_deleted=0 AND sku_code IS NULL AND cost_price IS NOT NULL LIMIT 100"
            ))).all()
            bad2 = []
            for main, stored in rows2:
                got = await svc._lookup_sku_cost_price(main, None, {})
                if got != stored:
                    bad2.append(f"{main}: 主表算得 {got}，订正落库 {stored}")
            check("V3 商品级兜底取价与订正结果一致(100行)", not bad2, f"抽样 {len(rows2)} 行，不符 {len(bad2)} 行；{bad2[:3]}")

            main_with_spec = (await db.execute(text(
                "SELECT main_sku_code FROM ecommerce_sku_cost WHERE is_deleted=0 AND sku_code IS NOT NULL "
                "AND main_sku_code IS NOT NULL GROUP BY main_sku_code LIMIT 1"
            ))).fetchone()[0]
            await cost_svc.create(SkuCostCreateSchema(
                main_sku_code=main_with_spec, sku_code=None, product_name="兜底闸验证用",
                cost_price=Decimal("88.88"), cost_source="manual"))
            got = await svc._lookup_sku_cost_price(main_with_spec, None, {})
            check("V4 同主编码下有规格行→不兜底", got is None, f"主编码 {main_with_spec} 取到 {got}（期望 None）")

            main_no_spec = (await db.execute(text(
                "SELECT main_sku_code FROM ecommerce_sku_cost WHERE is_deleted=0 AND sku_code IS NULL LIMIT 1"
            ))).fetchone()[0]
            expect2 = (await db.execute(text(
                "SELECT cost_price FROM ecommerce_sku_cost WHERE is_deleted=0 AND sku_code IS NULL AND main_sku_code=:m"
            ), {"m": main_no_spec})).fetchone()[0]
            got2 = await svc._lookup_sku_cost_price(main_no_spec, None, {})
            check("V5 无规格行的主编码→按商品级行兜底", got2 == expect2, f"主编码 {main_no_spec} 取到 {got2}，主表 {expect2}")

            obj = await cost_svc.create(SkuCostCreateSchema(
                main_sku_code="ZZTEST-MAIN", sku_code="ZZTEST-MAIN", product_name="归一化验证",
                cost_price=Decimal("1.00"), cost_source="manual"))
            check("V6 两码相同创建→落库 sku_code=NULL", obj.sku_code is None, f"实得 sku_code={obj.sku_code!r}")

            try:
                await cost_svc.create(SkuCostCreateSchema(
                    main_sku_code="ZZTEST-MAIN", sku_code=None, product_name="重复商品级行",
                    cost_price=Decimal("2.00"), cost_source="manual"))
                check("V7 同主编码再建商品级行被拦", False, "竟然创建成功")
            except Exception as e:  # noqa: BLE001
                check("V7 同主编码再建商品级行被拦", "不分规格" in str(e), str(e))

            dup_sku = (await db.execute(text(
                "SELECT sku_code FROM ecommerce_sku_cost WHERE is_deleted=0 AND sku_code IS NOT NULL LIMIT 1"
            ))).fetchone()[0]
            try:
                await cost_svc.create(SkuCostCreateSchema(
                    main_sku_code="ZZTEST-OTHER", sku_code=dup_sku, product_name="重复规格码",
                    cost_price=Decimal("3.00"), cost_source="manual"))
                check("V8 规格编码重复被拦", False, f"规格码 {dup_sku} 竟然创建成功")
            except Exception as e:  # noqa: BLE001
                check("V8 规格编码重复被拦", "已存在" in str(e), str(e))

            leftover = (await db.execute(text(
                "SELECT COUNT(*) FROM ecommerce_order_detail "
                "WHERE is_deleted=0 AND (sku_code='' OR sku_code=main_sku_code)"
            ))).fetchone()[0]
            check("V10 明细无空串/同码残留", leftover == 0, f"残留 {leftover} 行")
        finally:
            await db.rollback()

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")
    print(f"合计 {passed}/{len(results)} 通过")


asyncio.run(main())
