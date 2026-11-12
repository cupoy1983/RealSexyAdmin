"""2026-10-30 AL 轮（§25）改动的回滚式验证：面单费改必填 + 上限 100 + 两位精度拒收。

编号前缀 AL，与既往 Z/AA/X/W/M/N/P/Q/V/ST/OD/BL/AB/AC/AD/AE/AF/AG/AH/AI/AJ/AK 不撞号。
一个事务跑完，结束显式 rollback；§25.3 第六条钉子要求跑完之后存量六条规则与 2010 行账单
的面单费快照、Σactual_freight 一字不变——所以脚本前后各取一次基线对拍。
"""

import asyncio
import sys
from decimal import Decimal

from pathlib import Path

# backend/ 相对本文件定位，换 clone、换机器都一样能 import 到 app
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.base_schema import AuthSchema  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.plugin.module_ecommerce.logistics.model import BillParseRuleModel  # noqa: E402
from app.plugin.module_ecommerce.logistics.schema import (  # noqa: E402
    BillParseRuleCreateSchema,
    BillParseRuleUpdateSchema,
)
from app.plugin.module_ecommerce.logistics.service import BillParseRuleService  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

RESULTS: list[tuple[str, str, str]] = []
AUTH = AuthSchema(permissions=["*:*:*"])

PROBE_TYPE = "AL-PROBE"
MAPPING = {
    "waybill_no": "运单号",
    "bill_date": "日期",
    "bill_weight": "重量",
    "bill_other_fee": "其它费用",
    "receiver_province": "省份",
}


def ok(cid: str, detail: str = "") -> None:
    RESULTS.append(("PASS", cid, detail))


def bad(cid: str, detail: str) -> None:
    RESULTS.append(("FAIL", cid, detail))


def eq(cid: str, actual, expected) -> None:
    (ok if actual == expected else bad)(cid, f"期望 {expected}，实际 {actual}")


def truthy(cid: str, cond: bool, detail: str) -> None:
    (ok if cond else bad)(cid, detail)


def rejected(cid: str, schema, payload, why: str) -> None:
    """断言这个载荷过不了参数校验层（AL16=甲：只靠声明式 Field，报错文案允许是英文）。"""
    try:
        obj = schema.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        ok(cid, f"{why} → 拒绝（{first.get('type')}）")
        return
    bad(cid, f"{why} 竟然通过了，值 {obj.waybill_fee!r}")


def accepted(cid: str, schema, payload, expect: Decimal) -> None:
    try:
        obj = schema.model_validate(payload)
    except ValidationError as exc:
        bad(cid, f"应当通过却报错：{exc.errors()[0].get('type')}")
        return
    eq(cid, obj.waybill_fee, expect)


def create_payload(fee) -> dict:
    body = {"bill_type": PROBE_TYPE, "carrier_name": "AL 探针", "column_mapping": MAPPING, "status": 0}
    if fee is not _OMIT:
        body["waybill_fee"] = fee
    return body


class _Omit:
    pass


_OMIT = _Omit()


async def baseline() -> tuple[dict, list, Decimal, int]:
    """库里的现状：规则值、按类型的快照合计、Σactual_freight、账单行数。"""
    async with async_db_session() as s:
        rules = {
            r[0]: Decimal(str(r[1]))
            for r in (
                await s.execute(
                    text("SELECT bill_type, waybill_fee FROM ecommerce_bill_parse_rule WHERE bill_type <> :t"),
                    {"t": PROBE_TYPE},
                )
            ).all()
        }
        per_type = (
            await s.execute(
                text(
                    "SELECT bill_type, COUNT(*), SUM(waybill_fee) FROM ecommerce_courier_bill "
                    "WHERE is_deleted = 0 GROUP BY bill_type ORDER BY bill_type"
                )
            )
        ).all()
        total = (
            await s.execute(text("SELECT COALESCE(SUM(actual_freight), 0) FROM ecommerce_courier_bill WHERE is_deleted = 0"))
        ).scalar()
        rows = (await s.execute(text("SELECT COUNT(*) FROM ecommerce_courier_bill WHERE is_deleted = 0"))).scalar()
    return rules, list(per_type), Decimal(str(total)), int(rows)


async def main() -> None:
    before_rules, before_per_type, before_total, before_rows = await baseline()
    truthy("AL0 基线：规则不为空且没有探针类型", len(before_rules) >= 1 and PROBE_TYPE not in before_rules, f"现有规则 {sorted(before_rules)}")

    session = async_db_session()
    try:
        # ── 钉子 1：创建侧五种非法输入都拒绝 ────────────────────────────
        rejected("AL1-1 创建不带面单费 → 拒绝", BillParseRuleCreateSchema, create_payload(_OMIT), "缺字段")
        rejected("AL1-2 创建面单费为 null → 拒绝", BillParseRuleCreateSchema, create_payload(None), "null")
        rejected("AL1-3 创建面单费 -0.01 → 拒绝", BillParseRuleCreateSchema, create_payload(Decimal("-0.01")), "负数")
        rejected("AL1-4 创建面单费 100.01 → 拒绝", BillParseRuleCreateSchema, create_payload(Decimal("100.01")), "超上限")
        rejected("AL1-5 创建面单费 1.654 → 拒绝", BillParseRuleCreateSchema, create_payload(Decimal("1.654")), "三位小数")
        rejected("AL1-6 创建面单费英文字母 → 拒绝", BillParseRuleCreateSchema, create_payload("abc"), "非数字")

        # ── 钉子 2：0 / 0.00 / 100 / 1.65 通过，且落库与提交一致 ────────
        accepted("AL2-1 创建 fee=0 通过", BillParseRuleCreateSchema, create_payload(Decimal("0")), Decimal("0"))
        accepted("AL2-2 创建 fee=0.00 通过", BillParseRuleCreateSchema, create_payload(Decimal("0.00")), Decimal("0.00"))
        accepted("AL2-3 创建 fee=100 通过（含 100）", BillParseRuleCreateSchema, create_payload(Decimal("100")), Decimal("100"))
        accepted("AL2-4 创建 fee=1.65 通过", BillParseRuleCreateSchema, create_payload(Decimal("1.65")), Decimal("1.65"))
        accepted("AL2-5 创建 fee=0 的字符串也通过", BillParseRuleCreateSchema, create_payload("0"), Decimal("0"))

        created = await BillParseRuleService(AUTH, session).create(
            data=BillParseRuleCreateSchema.model_validate(create_payload(Decimal("1.65")))
        )
        rule_id = created.id
        landed = (await session.execute(
            text("SELECT waybill_fee FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id}
        )).scalar()
        eq("AL2-6 创建落库值 = 提交值 1.65（走 service，不抄近路）", Decimal(str(landed)), Decimal("1.65"))

        zero_landed = await BillParseRuleService(AUTH, session).update(
            id=rule_id, data=BillParseRuleUpdateSchema.model_validate({"waybill_fee": Decimal("0")})
        )
        zero_row = (await session.execute(
            text("SELECT waybill_fee FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id}
        )).scalar()
        eq("AL2-7 更新传 0 真的落 0（0 不许被当成「不改」吞掉）", Decimal(str(zero_row)), Decimal("0.00"))
        eq("AL2-8 更新返回值也报 0", Decimal(str(zero_landed.waybill_fee)), Decimal("0.00"))

        # ── 钉子 3：更新侧 null / 缺字段都拒绝，且老值一字不变 ──────────
        rejected("AL3-1 更新传 null → 拒绝", BillParseRuleUpdateSchema, {"waybill_fee": None}, "null")
        rejected("AL3-2 更新不带面单费 → 拒绝", BillParseRuleUpdateSchema, {"carrier_name": "只改名字"}, "缺字段")
        rejected("AL3-3 更新传 -0.01 → 拒绝", BillParseRuleUpdateSchema, {"waybill_fee": Decimal("-0.01")}, "负数")
        rejected("AL3-4 更新传 100.01 → 拒绝", BillParseRuleUpdateSchema, {"waybill_fee": Decimal("100.01")}, "超上限")
        rejected("AL3-5 更新传 1.654 → 拒绝", BillParseRuleUpdateSchema, {"waybill_fee": Decimal("1.654")}, "三位小数")
        after_failed = (await session.execute(
            text("SELECT waybill_fee FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id}
        )).scalar()
        eq("AL3-6 五条非法更新后老值仍是 0.00（AL8 的钉子：没有「清空=不改」后门）", Decimal(str(after_failed)), Decimal("0.00"))

        # ── 钉子 4：尾零行为（实测：decimal_places 放行，落库仍是两位） ──
        accepted("AL4-1 创建 fee=1.650 → 按实测放行", BillParseRuleCreateSchema, create_payload(Decimal("1.650")), Decimal("1.650"))
        accepted("AL4-2 更新 fee=1.650 → 按实测放行", BillParseRuleUpdateSchema, {"waybill_fee": Decimal("1.650")}, Decimal("1.650"))
        tail = await BillParseRuleService(AUTH, session).update(
            id=rule_id, data=BillParseRuleUpdateSchema.model_validate({"waybill_fee": Decimal("1.650")})
        )
        tail_row = (await session.execute(
            text("SELECT waybill_fee FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id}
        )).scalar()
        eq("AL4-3 尾零 1.650 落库为 1.65（列是 Numeric(10,2)，不多不少）", Decimal(str(tail_row)), Decimal("1.65"))
        eq("AL4-4 回读值 1.65", Decimal(str(tail.waybill_fee)), Decimal("1.65"))

        # ── 钉子 5：停用状态一样吃必填 ────────────────────────────────
        await session.execute(
            text("UPDATE ecommerce_bill_parse_rule SET status = 1 WHERE id = :i"), {"i": rule_id}
        )
        disabled = (await session.execute(
            text("SELECT status FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id}
        )).scalar()
        eq("AL5-1 探针规则已置停用", int(disabled), 1)
        rejected("AL5-2 停用规则的更新不带面单费 → 拒绝", BillParseRuleUpdateSchema, {"status": 1}, "缺字段")
        rejected("AL5-3 停用规则的更新传 null → 拒绝", BillParseRuleUpdateSchema, {"status": 1, "waybill_fee": None}, "null")
        accepted("AL5-4 停用规则的更新带 0 → 通过", BillParseRuleUpdateSchema, {"status": 1, "waybill_fee": Decimal("0")}, Decimal("0"))

        # ── 顺手确认：这一列仍是 NOT NULL、无服务端默认值（AL1b=甲 没改表结构） ──
        # default=0 住在 ORM 模型里（model.py:45），不是 MySQL 的 server default：库里 COLUMN_DEFAULT 为空。
        # 现在参数层每次都给值，这条默认值只是「裸 INSERT 漏列」时的最后一道，靠 NOT NULL 兜。
        col = (await session.execute(
            text("SELECT IS_NULLABLE, COLUMN_DEFAULT, NUMERIC_SCALE FROM information_schema.COLUMNS "
                 "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ecommerce_bill_parse_rule' AND COLUMN_NAME = 'waybill_fee'")
        )).first()
        truthy(
            "AL6-1 表结构未动：列仍 NOT NULL、两位小数、无 server default",
            str(col[0]) == "NO" and col[1] is None and int(col[2]) == 2,
            f"实际 {col}",
        )

        # 探针规则不留库（回滚兜底，先显式删一次）
        await session.execute(text("DELETE FROM ecommerce_bill_parse_rule WHERE id = :i"), {"i": rule_id})
        await session.flush()
    except Exception as e:  # noqa: BLE001
        bad("ABORT", f"脚本中断：{type(e).__name__}: {str(e)[:400]}")
    finally:
        await session.rollback()
        await session.close()

    # ── 钉子 6：回滚干净 + 存量数字一字未动 ────────────────────────────
    after_rules, after_per_type, after_total, after_rows = await baseline()
    eq("AL7-1 探针规则零残留", PROBE_TYPE in after_rules, False)
    eq("AL7-2 六条规则的现值一字不变", after_rules, before_rules)
    eq("AL7-3 各类型账单行的面单费快照合计一字不变", after_per_type, before_per_type)
    eq("AL7-4 Σactual_freight 一字不变（那 619.00 一分钱没被碰）", after_total, before_total)
    eq("AL7-5 账单行数一字不变", after_rows, before_rows)

    # 快照与规则仍逐类一致（原件行不变式的外层）
    for bill_type, cnt, fee_sum in after_per_type:
        rule_fee = after_rules.get(bill_type)
        if rule_fee is None:
            bad(f"AL8-1 {bill_type} 挂不到规则", f"账单里有 {cnt} 行，规则表却没有这个类型")
            continue
        eq(f"AL8-2 {bill_type} 面单费快照合计 = 行数 × 规则现值", Decimal(str(fee_sum)), (rule_fee * int(cnt)).quantize(Decimal("0.01")))

    for status, cid, msg in RESULTS:
        print(f"{status:4} {cid} {msg}")
    print(f"\n合计 {len(RESULTS)} 项，FAIL {sum(1 for s, _, _ in RESULTS if s == 'FAIL')} 项")


asyncio.run(main())
