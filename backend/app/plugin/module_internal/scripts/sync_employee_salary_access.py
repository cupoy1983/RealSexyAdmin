# -*- coding: utf-8 -*-
"""给普通员工（USER）授予薪资考勤页面和两个只读权限，不授予任何管理按钮。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.logger import logger  # noqa: E402
from app.modules.system.menu.model import MenuModel  # noqa: E402
from app.plugin.module_internal.scripts.sync_internal_menu import grant_roles  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

QUERY_PERMISSIONS = [
    "module_internal:salary-attendance:query",
    "module_internal:salary-performance:query",
]


async def main() -> None:
    async with async_db_session() as session:
        try:
            page = (
                await session.execute(
                    select(MenuModel).where(
                        MenuModel.route_path == "salary-attendance",
                        MenuModel.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().first()
            if not page:
                raise RuntimeError("薪资考勤页面不存在，请先执行 sync_internal_menu.py")
            buttons = (
                (
                    await session.execute(
                        select(MenuModel).where(
                            MenuModel.parent_id == page.id,
                            MenuModel.permission.in_(QUERY_PERMISSIONS),
                            MenuModel.is_deleted == False,  # noqa: E712
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(buttons) != 2:
                raise RuntimeError("两个只读权限节点不完整，请先执行 sync_internal_menu.py")
            menu_ids = [page.id, *(button.id for button in buttons)]
            if page.parent_id:
                menu_ids.append(page.parent_id)
            await grant_roles(session, ["USER"], menu_ids)
            await session.commit()
            logger.info("✅ USER 已获得自己的考勤与工资条查询权限，未授予管理按钮")
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(main())
