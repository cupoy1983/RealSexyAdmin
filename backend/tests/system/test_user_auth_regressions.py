"""BUG-08/11/12：Excel 导入规则、账号边界与数据权限回归测试（ADR-0013 口径）。

2026-11-10 清理：原「公开注册 / 第三方登录」两个回退善后用例已删除 —— 对应需求
已于 11-07 撤销，验证框架原版默认行为不属于本项目业务契约；前台注册与自助绑定
已移交外部开发者，不在本仓库跟进。其余 4 个用例均对应当前在用的用户管理规则。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.base_schema import AuthSchema, BatchSetAvailable, CoreUserSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.modules.system.dept.model import DeptModel
from app.modules.system.role.model import RoleModel
from app.modules.system.user.model import UserModel
from app.modules.system.user.schema import ResetPasswordSchema, UserCreateSchema
from app.modules.system.user.service import UserService


async def _ensure_role(db: AsyncSession, code: str, *, data_scope: int = 3, created_id: int | None = None) -> RoleModel:
    role = (await db.execute(select(RoleModel).where(RoleModel.code == code))).scalars().first()
    if role:
        return role
    role = RoleModel(name=f"测试角色{code}", code=code, data_scope=data_scope, status=0, created_id=created_id)
    db.add(role)
    await db.flush()
    return role


async def _ensure_dept(db: AsyncSession) -> DeptModel:
    dept = (await db.execute(select(DeptModel).where(DeptModel.code == "BUG_TEST_DEPT"))).scalars().first()
    if dept:
        return dept
    dept = DeptModel(name="缺陷测试部门", code="BUG_TEST_DEPT", status=0)
    db.add(dept)
    await db.flush()
    return dept


async def _create_user(
    db: AsyncSession,
    username: str,
    *,
    mobile: str | None = None,
    roles: list[RoleModel] | None = None,
    is_deleted: bool = False,
    deleted_id: int | None = None,
    wx_login: str | None = None,
) -> UserModel:
    user = UserModel(
        username=username,
        name=username,
        password="x",
        mobile=mobile,
        status=0,
        is_deleted=is_deleted,
        deleted_id=deleted_id,
        deleted_time=datetime.now(UTC) if is_deleted else None,
        wx_login=wx_login,
    )
    if roles:
        user.roles = roles
    db.add(user)
    await db.flush()
    return user


async def test_excel_import_role_and_mobile_rules(test_client: TestClient) -> None:
    async with async_db_session() as db:
        dept = await _ensure_dept(db)
        await _ensure_role(db, "USER")
        await _ensure_role(db, "ADMIN")
        service = UserService(AuthSchema(), db)

        base_row = {
            "dept_id": dept.id,
            "username": "bug03_import_no_mobile",
            "name": "无手机号",
            "email": None,
            "mobile": None,
            "gender": "2",
            "status": "正常",
            "role_name": "",
        }
        count, err = await service._process_import_row(2, base_row, False)
        assert count == 0 and "手机号不能为空" in (err or "")

        row = {**base_row, "username": "bug03_import_default_user", "mobile": "13900000001"}
        count, err = await service._process_import_row(3, row, False)
        assert (count, err) == (1, None), err
        created = (
            await db.execute(
                select(UserModel).options(selectinload(UserModel.roles)).where(UserModel.username == "bug03_import_default_user")
            )
        ).scalars().one()
        assert [r.code for r in created.roles] == ["USER"]

        row = {**base_row, "username": "bug03_import_admin_denied", "mobile": "13900000002", "role_name": "ADMIN"}
        count, err = await service._process_import_row(4, row, False)
        assert count == 0 and "无权指派管理员角色" in (err or "")

        # 更新已存在用户：手机号留空保留原值，导入不改角色
        update_row = {**base_row, "username": "bug03_import_default_user", "mobile": None, "role_name": "USER"}
        count, err = await service._process_import_row(5, update_row, True)
        assert (count, err) == (1, None), err
        await db.refresh(created)
        assert created.mobile == "13900000001"
        await db.commit()


async def test_non_superuser_cannot_control_admin_accounts(test_client: TestClient) -> None:
    async with async_db_session() as db:
        admin_role = await _ensure_role(db, "ADMIN")
        user_role = await _ensure_role(db, "USER")
        operator_role = await _ensure_role(db, "BUG08_OPERATOR", data_scope=3)
        operator = await _create_user(db, "bug08_operator", roles=[operator_role])
        target_admin = await _create_user(db, "bug08_target_admin", roles=[admin_role])
        target_user = await _create_user(db, "bug08_target_user", roles=[user_role])
        service = UserService(AuthSchema(user=CoreUserSchema(id=operator.id, is_superuser=False)), db)

        with pytest.raises(CustomException, match="无权编辑管理员账号"):
            await service.reset_password(ResetPasswordSchema(id=target_admin.id, password="Aa123456!"))
        with pytest.raises(CustomException, match="无权编辑管理员账号"):
            await service.set_available(BatchSetAvailable(ids=[target_admin.id], status=1))
        with pytest.raises(CustomException, match="仅超级管理员可删除"):
            await service.delete([target_admin.id])

        # 普通 USER 不受 ADMIN 保护影响
        await service.reset_password(ResetPasswordSchema(id=target_user.id, password="Aa123456!"))
        await service.set_available(BatchSetAvailable(ids=[target_user.id], status=1))
        await db.commit()


async def test_revive_clears_deleted_audit_and_detects_split_unique_keys(test_client: TestClient) -> None:
    async with async_db_session() as db:
        dept = await _ensure_dept(db)
        role = await _ensure_role(db, "USER")
        service = UserService(AuthSchema(), db)

        deleted = await _create_user(
            db, "bug12_revive", mobile="13900000003", is_deleted=True, deleted_id=123
        )
        data = UserCreateSchema(
            username="bug12_revive", password="Aa123456!", name="复活", mobile="13900000004", dept_id=dept.id, role_ids=[role.id]
        )
        revived = await service._revive_soft_deleted_user(data)
        assert revived is not None and revived.id == deleted.id
        await db.refresh(revived)
        assert revived.is_deleted is False and revived.deleted_id is None and revived.deleted_time is None

        # username 命中 A、mobile 命中 B 时必须明确报错，不得随便复活其中一条
        await _create_user(db, "bug12_split_a", mobile="13900000005", is_deleted=True)
        await _create_user(db, "bug12_split_b", mobile="13900000006", is_deleted=True)
        conflict = UserCreateSchema(
            username="bug12_split_a", password="Aa123456!", name="冲突", mobile="13900000006", dept_id=dept.id, role_ids=[role.id]
        )
        with pytest.raises(CustomException, match="多条已删除账号"):
            await service._revive_soft_deleted_user(conflict)
        await db.rollback()


async def test_role_precheck_and_write_share_data_scope(test_client: TestClient) -> None:
    """BUG-11：受限数据权限下，前置校验与写入对同一角色 ID 结果一致。"""
    async with async_db_session() as db:
        limited_role = await _ensure_role(db, "BUG11_LIMITED", data_scope=1)
        operator = await _create_user(db, "bug11_operator", roles=[limited_role])
        invisible_role = await _ensure_role(db, "USER", created_id=999999)
        service = UserService(AuthSchema(user=CoreUserSchema(id=operator.id, is_superuser=False)), db)
        # 「仅本人」数据权限下，UserModel 只能看到操作人自己
        target = operator

        # 前置校验不会因不可见角色误报「只能指派 USER」；写入端以同一口径明确报不存在
        await service._assert_operator_scope(role_ids=[invisible_role.id], is_superuser=False)
        with pytest.raises(CustomException, match="角色不存在"):
            await service._set_user_roles(user_ids=[target.id], role_ids=[invisible_role.id])
        await db.rollback()


