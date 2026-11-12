"""AE 轮（搜索栏改造）机制验证 —— 全程只读：只做分页查询，结束显式回滚，库里不留任何东西。

要验的是「月份 → 批次下拉」这套界面设计在后端现有能力上能不能真的跑起来，
以及我上一轮那句凭读码得出的断言（不用新增接口）到底成不成立。
"""

import asyncio
import sys

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.exceptions import CustomException  # noqa: E402
from app.plugin.module_ecommerce.common.import_batch.schema import (  # noqa: E402
    ImportBatchQueryParam,
)
from app.plugin.module_ecommerce.common.import_batch.service import (  # noqa: E402
    ImportBatchService,
)
from app.plugin.module_ecommerce.logistics.schema import (  # noqa: E402
    BillParseRuleQueryParam,
    CourierBillQueryParam,
)
from app.plugin.module_ecommerce.logistics.service import (  # noqa: E402
    BillParseRuleService,
    LogisticsService,
)
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

PASS = FAIL = 0


def check(tag: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {tag} {detail}")
    else:
        FAIL += 1
        print(f"FAIL {tag} {detail}")


async def main() -> None:
    auth = AuthSchema(permissions=["*:*:*"])
    session = async_db_session()
    try:
        batches = ImportBatchService(auth, session)
        bills = LogisticsService(auth, session)
        rules = BillParseRuleService(auth, session)

        # ══ M01/M02：批次下拉的数据源（现有 /batch/list 的分页方法）══════════
        # 数字一律不写死：用户随时会重导、删行、确认，锁存量数字的回归第二天就红，红得还不是代码。
        r1 = await batches.page(
            page_no=1, page_size=50,
            search=ImportBatchQueryParam(file_category="bill", batch_month="2026-09"),
        )
        nos = sorted(i.batch_no for i in r1.items)
        check("M01 按「出账月份」查得到当月账单批次（下拉不用新接口）",
              r1.total == len(nos) > 0 and all(n.endswith("-2026-09") for n in nos),
              f"命中 {r1.total} 条 -> {nos}")
        check("M01b 只出账单批次、不混进订单批次",
              all(n.startswith("bill-") for n in nos), str(nos))

        r2 = await batches.page(
            page_no=1, page_size=50,
            search=ImportBatchQueryParam(file_category="bill", batch_month="2026-09", status="unconfirmed"),
        )
        unconf = sorted(i.batch_no for i in r2.items)
        status_of = {i.batch_no: i.status for i in r1.items}
        excluded = [n for n in nos if n not in unconf]
        check("M02 再按「是否确认」收窄只剩未确认（AE11② 甲可行）",
              all(i.status == "unconfirmed" for i in r2.items)
              and set(unconf) <= set(nos)
              and all(status_of[n] == "confirmed" for n in excluded),
              f"未确认 {unconf}｜被滤掉的 {excluded} 状态 {[status_of[n] for n in excluded]}")

        # ══ M03/M04/M05：账单行的三种筛法，决定 AE10 / AE15 的取舍 ═══════════
        biggest = max(r1.items, key=lambda i: i.row_count)
        r3 = await bills.courier_bill_page(
            page_no=1, page_size=1, search=CourierBillQueryParam(import_batch_id=biggest.batch_no),
        )
        check("M03 选中批次 = 该批次全部行（与批次下拉那句「N 行」一致，AE19 的口径）",
              r3.total == biggest.row_count,
              f"批次 {biggest.batch_no}：筛出 {r3.total} 行 vs row_count={biggest.row_count}")

        per_batch = {
            i.batch_no: (await session.execute(
                text("SELECT COUNT(*) FROM ecommerce_courier_bill "
                     "WHERE import_batch_id=:b AND is_deleted=0"), {"b": i.batch_no}
            )).scalar()
            for i in r1.items
        }
        r4 = await bills.courier_bill_page(
            page_no=1, page_size=1, search=CourierBillQueryParam(bill_month="2026-09"),
        )
        check("M04 只选出账月份、不选批次也能筛（AE15 甲技术成立：该月各家批次之和=该月总行数）",
              r4.total == sum(per_batch.values()),
              f"该月 {r4.total} 行 vs 各批次之和 {sum(per_batch.values())}（差额=孤儿行）{per_batch}")

        r5 = await bills.courier_bill_page(
            page_no=1, page_size=1, search=CourierBillQueryParam(bill_type=biggest.sub_category),
        )
        same_type = (await session.execute(
            text("SELECT COUNT(*) FROM ecommerce_courier_bill WHERE bill_type=:t AND is_deleted=0"),
            {"t": biggest.sub_category},
        )).scalar()
        check("M05 跨月只看一家公司照样筛得动（去掉公司框不丢能力）",
              r5.total == same_type >= r3.total,
              f"按 bill_type={biggest.sub_category} 筛出 {r5.total} 行 vs 账单表同码 {same_type} 行"
              f"（该公司当月这一批 {r3.total} 行）")

        status_counts = {}
        for st in ("matched", "weight_province", "duplicate", "fake", "restored"):
            status_counts[st] = (await bills.courier_bill_page(
                page_no=1, page_size=1,
                search=CourierBillQueryParam(import_batch_id=biggest.batch_no, status=st),
            )).total
        check("M06 批次 + 行状态可叠加，五种状态加起来=该批次行数（没有筛不到的状态）",
              sum(status_counts.values()) == biggest.row_count,
              f"{status_counts} vs {biggest.row_count}")

        # ══ M07：下拉要显示的公司名从哪来（解析规则表，不用新接口）════════════
        rr = await rules.page(page_no=1, page_size=50, search=BillParseRuleQueryParam())
        named = {i.bill_type: i.carrier_name for i in rr.items}
        check("M07 每条批次都能按 bill_type 反查到快递公司名称（下拉 label 有出处）",
              all(named.get(code) for code in ("YT-Normal", "YT-Bulk", "ZT-Freight")),
              f"可用名称 -> { {k: named.get(k) for k in ('YT-Normal', 'YT-Bulk', 'ZT-Freight')} }")

        # ══ M08：库里有没有「零行的声明批次」——决定 AE17 有没有真实数据可依 ═══
        zero = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM ecommerce_import_batch "
                    "WHERE is_deleted = 0 AND file_category = 'bill' AND row_count = 0"
                )
            )
        ).scalar()
        check("M08 当前零行声明批次数量（AE17 的现状）", True, f"{zero} 个")

        # ══ M09：用户报的那个错确实来自「批次查不到」，不是数据问题 ═══════════
        try:
            await bills.bill_batch_counts("bill-NOT-EXIST-2026-09")
            check("M09 计数接口对不存在的批次仍然报错（AE13 甲的静默口子）", False, "没报错，预期会抛")
        except CustomException as exc:
            check("M09 计数接口对不存在的批次仍然报错（AE13 甲的静默口子）",
                  "不存在" in str(exc.__dict__.get("msg", "")) or "批次" in str(exc), str(exc)[:60])

        await session.rollback()
        print(f"\n合计 {PASS + FAIL} 项，FAIL {FAIL} 项")
    finally:
        await session.rollback()
        await session.close()


asyncio.run(main())
