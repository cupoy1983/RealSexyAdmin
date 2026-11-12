"""AE 轮（批次必填 + 脏数据报警）后端验证 —— 全程在一条事务里写、结尾显式回滚，库里不留东西。

验四件事：
① 模型说 NOT NULL，数据库真的也 NOT NULL（迁移没应用上就等于没改）；
② NULL 批次号真的插不进去（不是只写在注释里）；
③ 挂不到批次的行会被门禁预检报出来，但**不影响** passed（孤儿本来就确认不了，不需要再加闸）；
④ 孤儿行按出账月份筛得到 —— 所以它在界面上必须有个红标说法，不能装成「未确认」。
"""

import asyncio
import sys

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.model import ImportBatchModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import CourierBillModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import CourierBillQueryParam  # noqa: E402
from app.plugin.module_ecommerce.logistics.service import LogisticsService  # noqa: E402
from app.plugin.module_ecommerce.order.model import OrderModel  # noqa: E402
from app.plugin.module_ecommerce.store.model import StoreModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

ORPHAN_BATCH = "bill-AEORPHAN-2026-09"
MONTH = "2026-09"

PASS = FAIL = 0


def check(tag: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {tag} {detail}")
    else:
        FAIL += 1
        print(f"FAIL {tag} {detail}")


def new_bill_row(batch_no: str | None) -> CourierBillModel:
    return CourierBillModel(
        import_batch_id=batch_no,
        bill_month=MONTH,
        bill_type="AE-TEST",
        waybill_no=f"AEORPHAN{batch_no}",
        status="fake",
    )


async def column_is_nullable(session, table: str) -> bool | None:
    """问数据库这一列到底可不可空（None 表示查不到这列）"""
    return (
        await session.execute(
            text(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = 'import_batch_id'"
            ),
            {"t": table},
        )
    ).scalar()


async def main() -> None:
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        bills = LogisticsService(auth, session)

        # ══ A01/A02：模型声明与数据库实际结构一致 ═══════════════════════════
        bill_col = CourierBillModel.__table__.columns["import_batch_id"]
        order_col = OrderModel.__table__.columns["import_batch_id"]
        check("A01 模型声明 NOT NULL（两张表都是）",
              bill_col.nullable is False and order_col.nullable is False,
              f"bill={bill_col.nullable} order={order_col.nullable}")

        db_bill = await column_is_nullable(session, "ecommerce_courier_bill")
        db_order = await column_is_nullable(session, "ecommerce_order")
        check("A02 数据库里也已经是 NOT NULL（迁移真的应用上了）",
              db_bill == "NO" and db_order == "NO",
              f"ecommerce_courier_bill={db_bill} ecommerce_order={db_order}")

        # ══ A03/A04：NULL 批次号插得进去吗（真闸）═══════════════════════════
        for tag, obj in (
            ("A03 账单行", new_bill_row(None)),
        ):
            try:
                async with session.begin_nested():
                    session.add(obj)
                    await session.flush()
                check(f"{tag} 插 NULL 批次被数据库拒掉", False, "居然插进去了，NOT NULL 没生效")
            except IntegrityError as exc:
                msg = str(exc.orig).split("\n")[0]
                check(f"{tag} 插 NULL 批次被数据库拒掉", "import_batch_id" in msg or "cannot be null" in msg, msg[:80])

        store_id = (await session.execute(select(StoreModel.id).limit(1))).scalar()
        try:
            async with session.begin_nested():
                session.add(
                    OrderModel(
                        system_order_no="AE-TEST-ORDER-0001",
                        store_id=store_id,
                        platform="AE测试",
                        order_status="已发货",
                        import_batch_id=None,
                    )
                )
                await session.flush()
            check("A04 主订单插 NULL 批次被数据库拒掉", False, "居然插进去了，NOT NULL 没生效")
        except IntegrityError as exc:
            msg = str(exc.orig).split("\n")[0]
            check("A04 主订单插 NULL 批次被数据库拒掉",
                  "import_batch_id" in msg or "cannot be null" in msg, msg[:80])

        # ══ A05：现状门禁预检 —— 干净数据下 ④ 应该是 0 ══════════════════════
        g0 = await bills.bill_gate_gaps(MONTH)
        check("A05 现状 2026-09 孤儿行数=0（脏数据报警不误报）",
              g0.orphan_bill_rows == 0, f"实际 {g0.orphan_bill_rows}")
        base_passed = g0.passed

        # 该月的真实行数会随用户测试变动，A08 只断言「多了一行」，不锁绝对值
        total_before = (await bills.courier_bill_page(
            page_no=1, page_size=1, search=CourierBillQueryParam(bill_month=MONTH)
        )).total

        # ══ A06/A07：造一行孤儿 —— 报警要响，但门禁判定不许变 ═══════════════
        async with session.begin_nested():
            session.add(new_bill_row(ORPHAN_BATCH))
            await session.flush()

        g1 = await bills.bill_gate_gaps(MONTH)
        check("A06 造出 1 行孤儿后，④ 报出 1 行",
              g1.orphan_bill_rows == 1, f"实际 {g1.orphan_bill_rows}")
        check("A07 孤儿不参与门禁判定（passed 与造孤儿前一致）",
              g1.passed == base_passed, f"造前 {base_passed} / 造后 {g1.passed}")

        r_by_month = await bills.courier_bill_page(
            page_no=1, page_size=1, search=CourierBillQueryParam(bill_month=MONTH)
        )
        check("A08 孤儿行算进「这一月」的行数里（不选批次也躲不掉）",
              r_by_month.total == total_before + 1, f"该月 {r_by_month.total} 行（造孤儿前 {total_before} + 孤儿 1）")

        r_both = await bills.courier_bill_page(
            page_no=1, page_size=1,
            search=CourierBillQueryParam(bill_month=MONTH, import_batch_id=ORPHAN_BATCH),
        )
        check("A08b 叠批次号也筛得到（接口层 eq 打在账单行自己身上，不 join 批次表）",
              r_both.total == 1, f"实际 {r_both.total} 行")
        check("A09 该孤儿号不在批次表里（下拉里选不到它，所以只能靠列上的红标）",
              (await session.execute(
                  select(ImportBatchModel).where(ImportBatchModel.batch_no == ORPHAN_BATCH)
              )).scalar() is None,
              f"批次表无 {ORPHAN_BATCH}")

        # ══ A10：批次被软删也算孤儿（join 条件带 is_deleted）═════════════════
        zt = (await session.execute(
            select(ImportBatchModel).where(ImportBatchModel.batch_no == "bill-ZT-Freight-2026-09")
        )).scalar_one()
        zt_rows = zt.row_count
        zt.is_deleted = True
        await session.flush()
        g2 = await bills.bill_gate_gaps(MONTH)
        check("A10 批次软删后也算挂不到批次（不只是物理删除才算）",
              g2.orphan_bill_rows == 1 + zt_rows, f"期望 1+{zt_rows}={1 + zt_rows}，实际 {g2.orphan_bill_rows}")
        zt.is_deleted = False
        await session.flush()

        # ══ A11：计数接口对孤儿批次仍然报错，不给出「0 行」的假平静 ══════════
        try:
            await bills.bill_batch_counts(ORPHAN_BATCH)
            check("A11 计数接口对孤儿批次报错（顶栏不会显示 0 行假象）", False, "没报错")
        except CustomException as exc:
            check("A11 计数接口对孤儿批次报错（顶栏不会显示 0 行假象）",
                  "不存在" in str(exc), str(exc)[:60])

        await session.rollback()
        print(f"\n合计 {PASS + FAIL} 项，FAIL {FAIL} 项")
    finally:
        await session.rollback()
        await session.close()


asyncio.run(main())
