# -*- coding: utf-8 -*-
"""审计并清理「内务中心管理权限」的越权授权（BUG-02 存量修复）。

背景：旧版 sync_internal_menu.py 曾把 manage/sync/rest 等管理按钮一并迁给
「曾持有旧考勤菜单的角色」。本脚本用于核查存量：

- 默认只读：列出所有非 ADMIN/SUPER_ADMIN 角色持有的内务中心管理权限；
- 加 --fix 才清理：先把受影响授权行备份成 SQL 文件，再逐条删除并提交。

用法（在 backend 目录下执行）：
    python app/plugin/module_internal/scripts/audit_internal_permissions.py
    python app/plugin/module_internal/scripts/audit_internal_permissions.py --fix
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import bindparam, text  # noqa: E402

from app.core.database import async_db_session  # noqa: E402
from app.core.logger import logger  # noqa: E402

MANAGE_PERMISSIONS = [
    "module_internal:account:create",
    "module_internal:account:update",
    "module_internal:account:delete",
    "module_internal:account:match",
    "module_internal:salary-attendance:manage",
    "module_internal:salary-attendance:sync",
    "module_internal:salary-attendance:rest",
    "module_internal:salary-performance:manage",
]
ALLOWED_ROLE_CODES = ("SUPER_ADMIN", "ADMIN")

_QUERY = text(
    """
    SELECT rm.role_id, r.code, r.name, rm.menu_id, m.permission
    FROM sys_role_menus rm
    JOIN sys_role r ON r.id = rm.role_id
    JOIN sys_menu m ON m.id = rm.menu_id
    WHERE m.permission IN :perms
      AND r.code NOT IN :allowed
      AND r.is_deleted = 0
    ORDER BY r.code, m.permission
    """.strip()
).bindparams(bindparam("perms", expanding=True), bindparam("allowed", expanding=True))


async def audit(fix: bool) -> int:
    async with async_db_session() as session:
        rows = (
            await session.execute(_QUERY, {"perms": MANAGE_PERMISSIONS, "allowed": list(ALLOWED_ROLE_CODES)})
        ).all()

        if not rows:
            logger.info("✅ 审计通过：没有非 ADMIN/SUPER_ADMIN 角色持有内务中心管理权限")
            return 0

        logger.warning(f"发现 {len(rows)} 条越权管理授权：")
        for role_id, code, name, menu_id, permission in rows:
            logger.warning(f"  角色[{code}]{name}(id={role_id}) -> {permission} (menu_id={menu_id})")

        if not fix:
            logger.info("以上为只读审计结果。确认无误后加 --fix 清理（清理前会自动备份）。")
            return 1

        backup_path = Path.cwd() / f"internal_permission_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        backup_path.write_text(
            "-- sys_role_menus 越权授权备份，如需还原直接执行以下 INSERT\n"
            + "\n".join(f"INSERT INTO sys_role_menus (role_id, menu_id) VALUES ({role_id}, {menu_id});" for role_id, _, _, menu_id, _ in rows)
            + "\n",
            encoding="utf-8",
        )
        logger.info(f"已备份 {len(rows)} 条授权到 {backup_path}")

        for role_id, _, _, menu_id, _ in rows:
            await session.execute(
                text("DELETE FROM sys_role_menus WHERE role_id = :rid AND menu_id = :mid"),
                {"rid": role_id, "mid": menu_id},
            )
        await session.commit()
        logger.info(f"✅ 已清理 {len(rows)} 条越权管理授权")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="审计/清理内务中心管理权限的越权授权")
    parser.add_argument("--fix", action="store_true", help="备份后删除越权授权；不加则只读审计")
    args = parser.parse_args()
    sys.exit(asyncio.run(audit(args.fix)))
