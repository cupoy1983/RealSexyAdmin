"""BUG-02：菜单迁移不得把旧考勤只读角色扩成管理权限；越权存量可审计清理。

复现原问题：角色只持有旧「考勤统计」只读菜单，旧版迁移会把 manage/sync/rest
等管理按钮一并授给它。修复后旧角色只迁移到「页面 + query」，管理权限仅
ADMIN/SUPER_ADMIN 或 --role 显式指定的角色持有。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import false, select, text

from app.core.database import async_db_session
from app.modules.system.menu.model import MenuModel
from app.modules.system.role.model import RoleModel
from app.plugin.module_internal.scripts.audit_internal_permissions import audit
from app.plugin.module_internal.scripts.sync_internal_menu import sync_menu_tree

OLD_ROLE_CODE = "BUG02_OLD"
MANAGE_PERMS = {
    "module_internal:account:create",
    "module_internal:account:update",
    "module_internal:account:delete",
    "module_internal:account:match",
    "module_internal:salary-attendance:manage",
    "module_internal:salary-attendance:sync",
    "module_internal:salary-attendance:rest",
    "module_internal:salary-performance:manage",
}
QUERY_PERMS = {
    "module_internal:account:query",
    "module_internal:salary-attendance:query",
    "module_internal:salary-performance:query",
}


def _run(coro):
    return asyncio.run(coro)


async def _ensure_menu(db, name: str, parent_id: int | None, menu_type: int, permission: str | None = None) -> MenuModel:
    stmt = select(MenuModel).where(
        MenuModel.name == name,
        MenuModel.is_deleted == false(),
        MenuModel.parent_id.is_(parent_id) if parent_id is None else MenuModel.parent_id == parent_id,
    )
    menu = (await db.execute(stmt)).scalars().first()
    if menu:
        return menu
    menu = MenuModel(name=name, type=menu_type, parent_id=parent_id, permission=permission)
    db.add(menu)
    await db.flush()
    return menu


async def _ensure_role(db, code: str) -> RoleModel:
    role = (await db.execute(select(RoleModel).where(RoleModel.code == code))).scalars().first()
    if role:
        return role
    role = RoleModel(name=code, code=code, data_scope=3, status=0)
    db.add(role)
    await db.flush()
    return role


async def _role_permissions(db, role_id: int) -> set[str]:
    rows = (
        await db.execute(
            text(
                "SELECT m.permission FROM sys_role_menus rm JOIN sys_menu m ON m.id = rm.menu_id "
                "WHERE rm.role_id = :rid AND m.permission IS NOT NULL"
            ),
            {"rid": role_id},
        )
    ).scalars().all()
    return {p for p in rows if p}


def test_old_role_migration_grants_only_readonly(test_client: TestClient, tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        # ── 布置旧结构：旧角色只持有「考勤统计」只读菜单 ──
        async with async_db_session() as db:
            root = await _ensure_menu(db, "内务中心", None, 1)
            old_dir = await _ensure_menu(db, "考勤管理", root.id, 1)
            old_page = await _ensure_menu(db, "考勤统计", old_dir.id, 2, "module_internal:monthly-report:query")
            old_role = await _ensure_role(db, OLD_ROLE_CODE)
            await _ensure_role(db, "ADMIN")
            await _ensure_role(db, "SUPER_ADMIN")
            await db.execute(
                text("INSERT INTO sys_role_menus (role_id, menu_id) VALUES (:rid, :mid)"),
                {"rid": old_role.id, "mid": old_page.id},
            )
            await db.commit()
            old_role_id = old_role.id
            old_page_id = old_page.id

        # ── 执行迁移 ──
        async with async_db_session() as db:
            await sync_menu_tree(db, [])
            await db.commit()

        async with async_db_session() as db:
            perms = await _role_permissions(db, old_role_id)
            # 旧角色只能拿到 query，不能有任何管理权限（原实现这里会拿到全部 manage）
            assert not (perms & MANAGE_PERMS), f"旧只读角色被扩权: {perms & MANAGE_PERMS}"
            assert QUERY_PERMS <= perms, f"旧角色查询权限迁移不完整: {perms}"

            # 旧菜单已软删、旧授权已清理
            old_menu = await db.get(MenuModel, old_page_id)
            assert old_menu.is_deleted is True

            # 超管线角色持有全部管理权限
            for code in ("ADMIN", "SUPER_ADMIN"):
                role = (await db.execute(select(RoleModel).where(RoleModel.code == code))).scalars().one()
                admin_perms = await _role_permissions(db, role.id)
                assert MANAGE_PERMS <= admin_perms, f"{code} 管理权限缺失: {MANAGE_PERMS - admin_perms}"

        # ── 重复执行迁移不得把已纠正的授权再加回去，也不得产生重复授权 ──
        async with async_db_session() as db:
            await sync_menu_tree(db, [])
            await db.commit()
        async with async_db_session() as db:
            perms = await _role_permissions(db, old_role_id)
            assert not (perms & MANAGE_PERMS)

        # ── 存量审计：人为制造一条越权授权，审计应能发现并在 --fix 时备份清理 ──
        async with async_db_session() as db:
            manage_menu = (
                await db.execute(
                    select(MenuModel).where(MenuModel.permission == "module_internal:salary-attendance:manage")
                )
            ).scalars().one()
            await db.execute(
                text("INSERT INTO sys_role_menus (role_id, menu_id) VALUES (:rid, :mid)"),
                {"rid": old_role_id, "mid": manage_menu.id},
            )
            await db.commit()

        assert await audit(fix=False) == 1, "只读审计应发现越权授权"
        monkeypatch.chdir(tmp_path)
        assert await audit(fix=True) == 0, "清理应成功"
        backups = list(tmp_path.glob("internal_permission_backup_*.sql"))
        assert backups, "清理前必须生成备份文件"
        assert "INSERT INTO sys_role_menus" in backups[0].read_text(encoding="utf-8")

        async with async_db_session() as db:
            perms = await _role_permissions(db, old_role_id)
            assert not (perms & MANAGE_PERMS), "越权管理授权应被清理"
            assert QUERY_PERMS <= perms, "清理不得误伤只读权限"

    _run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
