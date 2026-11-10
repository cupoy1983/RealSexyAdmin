"""BUG-09：离职时钉钉查无此人改为人工确认制，中途失败如实上报三名单。

所有钉钉外部调用都用假客户端，不触网、不发真实离职请求。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, CoreUserSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.modules.system.user.model import UserModel
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.account.service import DISMISS_CONFIRM_REQUIRED_CODE, DingtalkAccountService


class FakeDingtalkClient:
    def __init__(self, details: dict[str, dict | None], *, fail_on: set[str] | None = None) -> None:
        self.details = details
        self.fail_on = fail_on or set()
        self.dismissed: list[str] = []

    async def list_admin_userids(self) -> dict[int, list[str]]:
        return {1: ["main_admin"]}

    async def get_user_detail(self, *, userid: str) -> dict | None:
        return self.details.get(userid)

    async def dismiss_user(self, **kwargs: Any) -> None:
        userid = kwargs["userid"]
        if userid in self.fail_on:
            raise RuntimeError(f"钉钉离职接口异常: {userid}")
        self.dismissed.append(userid)


async def _seed_binding(db: AsyncSession, username: str, dingtalk_userid: str) -> tuple[int, int]:
    user = UserModel(username=username, name=username, password="x", status=0, is_deleted=False)
    db.add(user)
    await db.flush()
    binding = ExternalAccountBindingModel(
        system_type=SYSTEM_DINGTALK,
        oa_user_id=user.id,
        external_user_id=dingtalk_userid,
        mobile=f"137{user.id:08d}"[:11],
        group_id="G-BUG09",
        status=0,
        is_deleted=False,
    )
    db.add(binding)
    await db.flush()
    return user.id, binding.id


def _patch(monkeypatch: pytest.MonkeyPatch, client: FakeDingtalkClient) -> None:
    async def _noop_sync(self, *, oa_user_ids: list[int]) -> None:
        return None

    async def _reason(client: Any) -> str:
        return "R1"

    async def _operator(client: Any, *, userid: str, main_admin: str | None) -> str:
        return "operator"

    monkeypatch.setattr(DingtalkAccountService, "_require_configured", staticmethod(lambda: None))
    monkeypatch.setattr(DingtalkAccountService, "_client", lambda self: client)
    monkeypatch.setattr(DingtalkAccountService, "_pick_passive_reason_id", staticmethod(_reason))
    monkeypatch.setattr(DingtalkAccountService, "_resolve_opt_userid", staticmethod(_operator))
    monkeypatch.setattr(DingtalkAccountService, "_sync_attendance_before_dismiss", _noop_sync)


def _service(db: AsyncSession) -> DingtalkAccountService:
    return DingtalkAccountService(AuthSchema(user=CoreUserSchema(id=990901, is_superuser=True)), db)


async def test_missing_member_requires_confirmation(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """未确认时整批不动：返回确认码与名单，不办离职、不软删绑定。"""
    fake = FakeDingtalkClient({"ding_bug09_a": None})
    _patch(monkeypatch, fake)
    async with async_db_session() as db:
        user_id, binding_id = await _seed_binding(db, "bug09_missing_a", "ding_bug09_a")

        with pytest.raises(CustomException) as caught:
            await _service(db).dismiss_users([user_id])
        assert caught.value.code == DISMISS_CONFIRM_REQUIRED_CODE
        assert "ding_bug09_a" in caught.value.msg
        assert fake.dismissed == []

        binding = await db.get(ExternalAccountBindingModel, binding_id)
        assert binding is not None and not binding.is_deleted


async def test_forced_missing_deletes_oa_only(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """管理员确认后跳过钉钉、OA 照删：绑定软删、无钉钉离职动作、返回说明。"""
    fake = FakeDingtalkClient({"ding_bug09_b": None})
    _patch(monkeypatch, fake)
    async with async_db_session() as db:
        user_id, binding_id = await _seed_binding(db, "bug09_missing_b", "ding_bug09_b")

        # ADR-0016 12:41：dismiss_users 返回 (跳过说明, 结算结果)，第一个元素保持原语义。
        skipped, settlement = await _service(db).dismiss_users([user_id], force_missing=True)
        assert any("查无此人" in item for item in skipped)
        assert settlement.generated_count == 0
        assert fake.dismissed == []
        binding = await db.get(ExternalAccountBindingModel, binding_id)
        assert binding is not None and binding.is_deleted


async def test_midway_failure_reports_three_lists(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """第 2 人失败时如实返回已完成 / 失败 / 未执行，且不继续调用后面的人。"""
    fake = FakeDingtalkClient(
        {f"ding_bug09_{k}": {"userid": f"ding_bug09_{k}"} for k in "cde"},
        fail_on={"ding_bug09_d"},
    )
    _patch(monkeypatch, fake)
    async with async_db_session() as db:
        ids = []
        for key in "cde":
            user_id, _ = await _seed_binding(db, f"bug09_{key}", f"ding_bug09_{key}")
            ids.append(user_id)

        with pytest.raises(CustomException) as caught:
            await _service(db).dismiss_users(ids)
        msg = caught.value.msg
        assert "已完成" in msg and "失败" in msg and "未执行" in msg
        assert str(ids[0]) in msg and str(ids[2]) in msg
        assert fake.dismissed == ["ding_bug09_c"]


async def test_all_present_proceeds_normally(test_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDingtalkClient({"ding_bug09_f": {"userid": "ding_bug09_f"}})
    _patch(monkeypatch, fake)
    async with async_db_session() as db:
        user_id, binding_id = await _seed_binding(db, "bug09_f", "ding_bug09_f")
        skipped, _ = await _service(db).dismiss_users([user_id])
        assert skipped == []
        assert fake.dismissed == ["ding_bug09_f"]
        binding = await db.get(ExternalAccountBindingModel, binding_id)
        assert binding is not None and binding.is_deleted


def test_delete_route_exposes_force_missing(test_client: TestClient) -> None:
    """OpenAPI 里必须有 force_missing，否则前端确认后的重提会被参数校验挡掉。"""
    schema = test_client.app.openapi()
    paths = [path for path in schema["paths"] if path.endswith("/system/user/delete")]
    assert paths, "找不到删除用户路由"
    parameters = schema["paths"][paths[0]]["delete"].get("parameters", [])
    assert any(item.get("name") == "force_missing" for item in parameters)
