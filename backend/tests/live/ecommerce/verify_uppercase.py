"""2026-10-25「大小写统一」验证（X 系列）。全程事务内跑，结束强制 rollback，不落任何写入。

覆盖：手工新增、Excel 源、快麦 API 源三个写入口；只差大小写的两码判为不分规格；
名称列不被大写化；跨大小写查重仍拦；混挂禁死与取价未被破坏。
"""

import asyncio
import io
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from openpyxl import Workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.order.importers.excel_source import (  # noqa: E402
    ExcelOrderDataSource,
    H_CITY,
    H_DETAIL_ID,
    H_ERP_RETURN_COST,
    H_ERP_SHIPPED_COST,
    H_FREIGHT_INCOME,
    H_IS_ERP_SHIPPED,
    H_MAIN_SKU_CODE,
    H_ORDER_STATUS,
    H_PAID_AMOUNT,
    H_PLATFORM,
    H_PRODUCT_NAME,
    H_PROVINCE,
    H_REFUND_AMOUNT,
    H_RETURN_QTY,
    H_SHIP_QTY,
    H_SHOP_CODE,
    H_SKU_CODE,
    H_SKU_NAME,
    H_STORE_NAME,
    H_SYSTEM_ORDER_NO,
)
from app.plugin.module_ecommerce.order.importers.kuaimai_api_source import KuaimaiApiOrderDataSource  # noqa: E402
from app.plugin.module_ecommerce.order.schema import SkuCostCreateSchema  # noqa: E402
from app.plugin.module_ecommerce.order.service import OrderService, SkuCostService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

M = "ZZ-CASE"
results: list[tuple[str, bool, str]] = []

_HEADERS = [
    H_DETAIL_ID, H_SYSTEM_ORDER_NO, H_SHOP_CODE, H_STORE_NAME, H_PLATFORM, H_ORDER_STATUS,
    H_PRODUCT_NAME, H_MAIN_SKU_CODE, H_SKU_CODE, H_SKU_NAME, H_PAID_AMOUNT, H_REFUND_AMOUNT,
    H_FREIGHT_INCOME, H_SHIP_QTY, H_RETURN_QTY, H_PROVINCE, H_CITY, H_ERP_SHIPPED_COST,
    H_ERP_RETURN_COST, H_IS_ERP_SHIPPED,
]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def excel_row(idx: int, main: str | None, spec: str | None, sku_name: str = "红色") -> dict:
    return {
        H_DETAIL_ID: f"{M}-D{idx}",
        H_SYSTEM_ORDER_NO: f"{M}-XL",
        H_SHOP_CODE: "990099",
        H_STORE_NAME: "大小写测试店",
        H_PLATFORM: "tb",
        H_ORDER_STATUS: "已发货",
        H_PRODUCT_NAME: "大小写测试商品",
        H_MAIN_SKU_CODE: main,
        H_SKU_CODE: spec,
        H_SKU_NAME: sku_name,
        H_PAID_AMOUNT: 10,
        H_REFUND_AMOUNT: 0,
        H_FREIGHT_INCOME: 0,
        H_SHIP_QTY: 1,
        H_RETURN_QTY: 0,
        H_PROVINCE: "浙江省",
        H_CITY: "杭州市",
        H_ERP_SHIPPED_COST: 0,
        H_ERP_RETURN_COST: 0,
        H_IS_ERP_SHIPPED: "ERP发货",
    }


def build_xlsx(rows: list[dict]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(_HEADERS)
    for r in rows:
        ws.append([r.get(h) for h in _HEADERS])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def read_codes(db, id_: int):
    return (await db.execute(text(
        "SELECT main_sku_code, sku_code, product_name, sku_name, cost_price FROM ecommerce_sku_cost WHERE id=:i"
    ), {"i": id_})).first()


async def main() -> None:
    auth = AuthSchema()
    async with async_db_session() as db:
        try:
            cost = SkuCostService(auth, db)
            order = OrderService(auth, db)

            # 存量基线自取，不写死 1744：锁死快照的脚本会随数据变化自己变红（AE 轮教训）
            base_live = (await db.execute(text(
                "SELECT COUNT(*) AS c FROM ecommerce_sku_cost WHERE is_deleted = 0 AND main_sku_code NOT LIKE 'ZZ-%'"
            ))).fetchone().c

            # ── X1 手工新增：小写编码落库变大写 ──
            c1 = await cost.create(SkuCostCreateSchema(
                main_sku_code="zz-case-a", sku_code="zz-case-a-red",
                product_name="大小写测试-规格", cost_price=Decimal("3.00"),
            ))
            await db.flush()
            row = await read_codes(db, c1.id)
            check("X1 新增小写两码→库里全大写", (row.main_sku_code, row.sku_code) == ("ZZ-CASE-A", "ZZ-CASE-A-RED"),
                  str((row.main_sku_code, row.sku_code)))

            # ── X2 带空格的编码：先 trim 再大写 ──
            c2 = await cost.create(SkuCostCreateSchema(
                main_sku_code="  zz-case-a-sp  ", sku_code=" zz-case-a-sp-x ",
                product_name="大小写测试-空格", cost_price=Decimal("4.00"),
            ))
            await db.flush()
            row = await read_codes(db, c2.id)
            check("X2 前后空格被去掉且转大写", (row.main_sku_code, row.sku_code) == ("ZZ-CASE-A-SP", "ZZ-CASE-A-SP-X"),
                  str((row.main_sku_code, row.sku_code)))

            # ── X3 只差大小写的两码 → 判为不分规格（规格格落 NULL） ──
            c3 = await cost.create(SkuCostCreateSchema(
                main_sku_code="zz-case-b", sku_code="ZZ-CASE-B",
                product_name="大小写测试-只差大小写", cost_price=Decimal("5.00"),
            ))
            await db.flush()
            row = await read_codes(db, c3.id)
            check("X3 只差大小写的两码→规格格自动清空", (row.main_sku_code, row.sku_code) == ("ZZ-CASE-B", None),
                  str((row.main_sku_code, row.sku_code)))

            # ── X4 跨大小写查重仍拦（同主编码再来一条商品级） ──
            try:
                await cost.create(SkuCostCreateSchema(
                    main_sku_code="ZZ-CASE-B", sku_code=None,
                    product_name="大小写测试-重复商品级", cost_price=Decimal("6.00"),
                ))
                check("X4 跨大小写的商品级重复被拦", False, "未抛错")
            except CustomException as e:
                check("X4 跨大小写的商品级重复被拦", "已有「不分规格」的成本记录" in e.msg, e.msg)

            # ── X5 名称列保持原样，不被大写化 ──
            row = await read_codes(db, c1.id)
            check("X5 商品名称未被大写化", row.product_name == "大小写测试-规格", str(row.product_name))
            c5 = await cost.create(SkuCostCreateSchema(
                main_sku_code="zz-case-e", sku_code="zz-case-e-m", sku_name="Red-L",
                product_name="mixedCase Name", cost_price=Decimal("7.00"),
            ))
            await db.flush()
            row = await read_codes(db, c5.id)
            check("X5b 规格名称/商品名称原样保留（含小写）",
                  row.sku_name == "Red-L" and row.product_name == "mixedCase Name",
                  f"{row.sku_name!r} / {row.product_name!r}")

            # ── X6 取价打通：库里大写、订单侧小写仍命中（数据库不区分） ──
            p1 = await order._lookup_sku_cost_price("ZZ-CASE-A", "ZZ-CASE-A-RED", {})
            p2 = await order._lookup_sku_cost_price("zz-case-a", "zz-case-a-red", {})
            p3 = await order._lookup_sku_cost_price("zz-case-b", None, {})
            check("X6a 大写键命中成本价", p1 == Decimal("3.0000"), str(p1))
            check("X6b 小写键同样命中（数据库不区分大小写）", p2 == Decimal("3.0000"), str(p2))
            check("X6c 不分规格行按主编码命中（小写键）", p3 == Decimal("5.0000"), str(p3))

            # ── X7 Excel 源：小写→大写、只差大小写→NULL、正常规格保留 ──
            rows = [
                excel_row(1, "zz-case-c", "ZZ-CASE-C"),      # 只差大小写 → NULL
                excel_row(2, "zz-case-c", "zz-case-c-red"),  # 正常两码 → 都大写
                excel_row(3, "zz-case-c", ""),               # 规格空 → NULL
                excel_row(4, "  zz-case-c  ", " zz-case-c "),  # 空格 + 只差大小写 → NULL
            ]
            parsed = await ExcelOrderDataSource().fetch(contents=build_xlsx(rows))
            got = [(d.main_sku_code, d.sku_code, d.sku_name) for o in parsed for d in o.details]
            check("X7a Excel源 只差大小写→NULL", got[0][:2] == ("ZZ-CASE-C", None), str(got[0]))
            check("X7b Excel源 小写两码→全大写", got[1][:2] == ("ZZ-CASE-C", "ZZ-CASE-C-RED"), str(got[1]))
            check("X7c Excel源 规格空→NULL", got[2][:2] == ("ZZ-CASE-C", None), str(got[2]))
            check("X7d Excel源 空格包裹的同码→NULL", got[3][:2] == ("ZZ-CASE-C", None), str(got[3]))
            check("X7e Excel源 规格名称不被大写化", all(g[2] == "红色" for g in got), str([g[2] for g in got]))

            # ── X8 快麦 API 源同规则 ──
            api = KuaimaiApiOrderDataSource._to_raw_order({
                "system_order_no": f"{M}-API", "kuaimai_shop_id": "990099", "store_name": "x", "platform": "tb",
                "details": [
                    {"source_detail_id": "A1", "main_sku_code": "zz-case-d", "sku_code": "ZZ-CASE-D", "product_name": "p"},
                    {"source_detail_id": "A2", "main_sku_code": "zz-case-d", "sku_code": "zz-case-d-m", "product_name": "p"},
                ],
            })
            api_codes = [(d.main_sku_code, d.sku_code) for d in api.details]
            check("X8 快麦API源同规则（大写 + 只差大小写归 NULL）",
                  api_codes == [("ZZ-CASE-D", None), ("ZZ-CASE-D", "ZZ-CASE-D-M")], str(api_codes))

            # ── X9 混挂禁死未被这次改动破坏 ──
            try:
                await cost.create(SkuCostCreateSchema(
                    main_sku_code="ZZ-CASE-A", sku_code=None,
                    product_name="大小写测试-想混挂", cost_price=Decimal("9.00"),
                ))
                check("X9 已有规格行时仍禁建商品级行", False, "被放行了")
            except CustomException as e:
                check("X9 已有规格行时仍禁建商品级行", "已有按规格录入" in e.msg, e.msg)

            # ── X10 本轮写入没污染存量（活行数与本轮开始前的基线一致、仍无只差大小写的行） ──
            live = (await db.execute(text(
                "SELECT COUNT(*) AS c FROM ecommerce_sku_cost WHERE is_deleted = 0 AND main_sku_code NOT LIKE 'ZZ-%'"
            ))).fetchone().c
            check("X10 存量活行数与基线一致", live == base_live, f"基线 {base_live}，实际 {live}")
        finally:
            await db.rollback()

    ok = sum(1 for _, p, _ in results if p)
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} | {name}{' | ' + detail if detail else ''}")
    print(f"合计 {ok}/{len(results)} 通过")


asyncio.run(main())
