"""BUG-01：部分人员预同步不得被误判为全公司已完成同步。

两代修复都要覆盖：
1. 旧实现「存在任意一条月报」就算拉完 → 离职预同步只写一人即可误判；
2. 人数核对（月报行数 ≥ 应到人数）仍有漏洞：离职员工的历史月报会顶替
   新进考勤组但还没同步的人。最终口径是「应到人员名单 ⊆ 当月有月报人员名单」。

测试库是 session 级共享 SQLite，别的文件也会造绑定，所以这里用
「查全库应到名单 → 按需补月报」的方式自适应，不假设库里只有本文件的用户。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.attendance.model import MonthlyReportModel
from app.plugin.module_internal.attendance.service import MonthlyReportService

MONTH = "2026-05"


def _run(coro):
    return asyncio.run(coro)


async def _seed_user(db, username: str) -> UserModel:
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    return user


async def _seed_binding(db, user: UserModel, external_user_id: str, group_id: str | None = "G1") -> ExternalAccountBindingModel:
    binding = ExternalAccountBindingModel(
        system_type=SYSTEM_DINGTALK,
        oa_user_id=user.id,
        external_user_id=external_user_id,
        mobile=f"138{user.id:08d}"[:11],
        group_id=group_id,
        status=0,
        is_deleted=False,
    )
    db.add(binding)
    await db.flush()
    return binding


async def _expected_ids(db) -> set[int]:
    """与 month_synced 完全同口径的应到人员名单。"""
    active = select(UserModel.id).where(UserModel.is_deleted == False, UserModel.status == 0)  # noqa: E712
    rows = (
        await db.execute(
            select(ExternalAccountBindingModel.oa_user_id).where(
                ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                ExternalAccountBindingModel.oa_user_id.isnot(None),
                ExternalAccountBindingModel.oa_user_id.in_(active),
                ExternalAccountBindingModel.group_id.isnot(None),
                ExternalAccountBindingModel.group_id != "",
            )
        )
    ).scalars().all()
    return set(rows)


async def _fill_reports(db, month: str, user_ids: set[int]) -> None:
    for uid in user_ids:
        exists = (
            await db.execute(
                select(MonthlyReportModel).where(
                    MonthlyReportModel.oa_user_id == uid,
                    MonthlyReportModel.month == month,
                )
            )
        ).scalars().first()
        if not exists:
            db.add(MonthlyReportModel(oa_user_id=uid, month=month, group_id="G1", status=0, is_deleted=False))
    await db.flush()


async def _month_synced(month: str = MONTH) -> bool:
    async with async_db_session() as db:
        return await MonthlyReportService(AuthSchema(), db).month_synced(month)


def test_month_synced_requires_every_expected_user(test_client: TestClient) -> None:
    """只同步一个人不算完成；全员都到齐才算完成。"""

    async def scenario() -> None:
        async with async_db_session() as db:
            user_a = await _seed_user(db, "bug01_set_a")
            user_b = await _seed_user(db, "bug01_set_b")
            await _seed_binding(db, user_a, "bug01_set_ding_a")
            await _seed_binding(db, user_b, "bug01_set_ding_b")
            # 离职预同步只写 A 的月报（user_ids 部分同步）
            await _fill_reports(db, MONTH, {user_a.id})
            await db.commit()

        assert await _month_synced() is False, "部分人员预同步被误判为全量完成"

        async with async_db_session() as db:
            await _fill_reports(db, MONTH, await _expected_ids(db))
            await db.commit()
        assert await _month_synced() is True, "全员到齐后应判定为已完成"

    _run(scenario())


def test_historical_report_cannot_substitute_new_member(test_client: TestClient) -> None:
    """复核反例：B 离职 + C 新进组，B 的历史月报不得顶替 C 让系统判定已完成。"""

    async def scenario() -> None:
        # 基线：全库应到人员都有当月月报
        async with async_db_session() as db:
            await _fill_reports(db, MONTH, await _expected_ids(db))
            await db.commit()
        assert await _month_synced() is True

        # B 的绑定软删（历史月报保留），C 新进考勤组但还没同步
        async with async_db_session() as db:
            binding_b = (
                await db.execute(
                    select(ExternalAccountBindingModel).where(
                        ExternalAccountBindingModel.external_user_id == "bug01_set_ding_b"
                    )
                )
            ).scalars().one()
            binding_b.is_deleted = True
            user_c = await _seed_user(db, "bug01_set_c")
            await _seed_binding(db, user_c, "bug01_set_ding_c")
            await db.commit()

        assert await _month_synced() is False, "旧实现的数量核对会在这里误判为已同步"

        async with async_db_session() as db:
            user_c = (await db.execute(select(UserModel).where(UserModel.username == "bug01_set_c"))).scalars().one()
            await _fill_reports(db, MONTH, {user_c.id})
            await db.commit()
        assert await _month_synced() is True, "补上新人月报后应恢复已完成"

    _run(scenario())


def test_empty_month_is_not_synced(test_client: TestClient) -> None:
    """完全没有月报的月份，无论有没有绑定都不得判定为已同步。"""
    assert _run(_month_synced("2030-01")) is False


def test_month_with_only_ungrouped_binding_is_not_synced(test_client: TestClient) -> None:
    """应参与人数只算「有考勤组」的绑定；无考勤组绑定不计入，也不得误判。"""

    async def scenario() -> None:
        async with async_db_session() as db:
            user = await _seed_user(db, "bug01_user_ungrouped")
            await _seed_binding(db, user, "bug01_ding_ungrouped", group_id=None)
            await db.commit()
        assert await _month_synced("2030-02") is False

    _run(scenario())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
