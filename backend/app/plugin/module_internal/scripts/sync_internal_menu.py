# -*- coding: utf-8 -*-
"""把「内务中心」菜单树同步到「账号管理 + 薪资考勤」结构。

业务结构：
    内务中心
    ├── 账号管理（页内含 钉钉账号 / 快麦账号 / 部门映射 三个 Tab）
    └── 薪资考勤（页内含 考勤统计 / 薪资绩效 两个 Tab，薪资绩效预留占位）

    旧版的「考勤管理」是 type=1 目录、下面挂「考勤统计」子菜单；
    新版的「薪资考勤」降为 type=2 页面，统计内容改由页内 Tab 承载。
    对应的后端模块在 app/plugin/module_internal/ 下：Internal 域由
    core/discover.py 按 module_xxx → /xxx 动态注册，不需要在 routers.py 里挂载。

行为：
    - 幂等：以 (name, parent_id) 判断，已存在则更新；
    - 旧菜单做软删除，并清理 sys_role_menus 授权，避免残留无效权限；
    - 原来持有旧菜单的角色只迁移到等价的「页面 + 查询」权限，
      绝不借机扩成管理权限（manage/sync/rest、账号增删改）；
    - 管理权限只授予 ADMIN / SUPER_ADMIN，或用 --role 显式指定的角色；
    - 加 --role 可为指定角色额外授权（追加，不整体替换）。

用法（在 backend 目录下执行）：
    python app/plugin/module_internal/scripts/sync_internal_menu.py
    python app/plugin/module_internal/scripts/sync_internal_menu.py --role SUPER_ADMIN --role ADMIN
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import false, select, text, update  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.logger import logger  # noqa: E402
from app.modules.system.menu.model import MenuModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

# 必须加载全部模型再建立 mapper（MenuModel.roles 的 secondary 指向 sys_role_menus）
ImportUtil.find_models(MappedBase)

# 需要清理的旧页面（含其按钮子节点）
OLD_PAGES = ["班次管理", "排班管理", "考勤记录", "请假登记", "考勤统计", "钉钉账号映射"]

# 「考勤管理」目录 → 「薪资考勤」页面的迁移名
ATTENDANCE_DIR_OLD_NAME = "考勤管理"
ATTENDANCE_DIR_NEW_NAME = "薪资考勤"

# 管理权限默认只授予这两个角色；其他角色要拿管理权限必须用 --role 显式指定
DEFAULT_MANAGE_ROLE_CODES = ["SUPER_ADMIN", "ADMIN"]

# 新页面定义: (名称, 路由名, 路径, 组件目录, 图标, 顺序, 按钮[(标签, 权限字符串)])
# 按钮权限写全串：薪资绩效是页面内 Tab 的预留权限，不跟随页面的资源段命名。
NEW_PAGES = [
    (
        "账号管理",
        "InternalAccount",
        "account",
        "module_internal/account/index",
        "ri:contacts-line",
        1,
        [
            ("查询", "module_internal:account:query"),
            ("新增", "module_internal:account:create"),
            ("编辑", "module_internal:account:update"),
            ("删除", "module_internal:account:delete"),
            ("匹配", "module_internal:account:match"),
        ],
    ),
    (
        "薪资考勤",
        "InternalSalaryAttendance",
        "salary-attendance",
        "module_internal/salary-attendance/index",
        "ri:calendar-check-line",
        2,
        [
            ("查询", "module_internal:salary-attendance:query"),
            # 全员考勤数据范围：普通员工只有上面的 query，后端会强制只看自己。
            ("全员考勤", "module_internal:salary-attendance:manage"),
            # 手工拉取上个月考勤。与每月 3 日 00:00 的自动任务互斥，拿到这个按钮的人才能触发；
            # 没拿到的人仍然能看列表，只是没有那个按钮（后端接口也按同一个权限串拦）。
            ("手动拉取", "module_internal:salary-attendance:sync"),
            # 公司休息日安排（ADR-0009）：登记/删除休息日后会重算当月考勤，是写操作，
            # 单独一个权限点，不跟只读的 query 混在一起。
            ("休息日安排", "module_internal:salary-attendance:rest"),
            # 普通员工只有 query，只能看自己的工资条；manage 才能看全员、登记绩效和补生成。
            ("薪资绩效", "module_internal:salary-performance:query"),
            ("薪资管理", "module_internal:salary-performance:manage"),
        ],
    ),
]


async def find_or_create(session, *, name: str, route_path: str | None, parent_id: int | None, defaults: dict) -> tuple[MenuModel, bool]:
    """按 (name, parent_id) 查找菜单，不存在则创建。返回 (菜单对象, 是否新建)"""
    stmt = select(MenuModel).where(
        MenuModel.name == name,
        MenuModel.is_deleted == false(),
        MenuModel.parent_id.is_(parent_id) if parent_id is None else MenuModel.parent_id == parent_id,
    )
    exists = (await session.execute(stmt)).scalars().first()
    if exists:
        for key, value in defaults.items():
            setattr(exists, key, value)
        await session.flush()
        return exists, False

    menu = MenuModel(name=name, route_path=route_path, parent_id=parent_id, **defaults)
    session.add(menu)
    await session.flush()
    return menu, True


async def migrate_attendance_dir(session, root_id: int) -> int | None:
    """把旧「考勤管理」目录就地降级成「薪资考勤」页面（幂等）。

    旧结构是 type=1 目录 + redirect 到子菜单；新结构是 type=2 页面，
    内容由页内 Tab 承载。这里只改字段、不新建节点，
    保证重复执行不会产出第二个同名项。

    返回该节点的 id（旧菜单的子节点挂在它下面，清理时要用到）。
    """
    stmt = select(MenuModel).where(
        MenuModel.parent_id == root_id,
        MenuModel.name.in_([ATTENDANCE_DIR_OLD_NAME, ATTENDANCE_DIR_NEW_NAME]),
        MenuModel.is_deleted == false(),
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None

    # 万一两个名字同时存在（历史脏数据），保留 id 最小的，其余软删除
    rows.sort(key=lambda m: m.id)
    keeper, duplicates = rows[0], rows[1:]

    for menu in duplicates:
        await session.execute(text("DELETE FROM sys_role_menus WHERE menu_id = :mid"), {"mid": menu.id})
    if duplicates:
        dup_ids = [m.id for m in duplicates]
        await session.execute(
            update(MenuModel).where(MenuModel.id.in_(dup_ids)).values(is_deleted=True, deleted_time=datetime.now(UTC))
        )
        logger.warning(f"发现 {len(dup_ids)} 个重复的考勤/薪资菜单节点，已保留 id={keeper.id} 并软删除其余")

    keeper.name = ATTENDANCE_DIR_NEW_NAME
    keeper.title = ATTENDANCE_DIR_NEW_NAME
    keeper.type = 2  # 目录 → 页面
    keeper.route_path = "salary-attendance"
    keeper.route_name = "InternalSalaryAttendance"
    keeper.component_path = "module_internal/salary-attendance/index"
    keeper.permission = "module_internal:salary-attendance:query"
    keeper.redirect = None  # 页面不再向下跳转，内容由页内 Tab 切换
    keeper.always_show = False
    keeper.description = "薪资与考勤：考勤统计、薪资绩效"
    await session.flush()
    logger.info(f"已把菜单节点 id={keeper.id} 迁移为「{ATTENDANCE_DIR_NEW_NAME}」页面")
    return keeper.id


async def remove_old_menus(session, root_id: int) -> list[int]:
    """软删除旧页面及其按钮，清理角色授权；返回曾经拥有这些菜单的角色ID"""
    old_rows = (
        await session.execute(
            select(MenuModel).where(
                MenuModel.name.in_(OLD_PAGES),
                MenuModel.parent_id == root_id,
                MenuModel.is_deleted == false(),
            )
        )
    ).scalars().all()
    if not old_rows:
        return []

    old_ids = [m.id for m in old_rows]
    child_rows = (
        await session.execute(select(MenuModel).where(MenuModel.parent_id.in_(old_ids), MenuModel.is_deleted == false()))
    ).scalars().all()
    all_ids = old_ids + [m.id for m in child_rows]

    role_ids: set[int] = set()
    for menu_id in all_ids:
        rows = (await session.execute(text("SELECT role_id FROM sys_role_menus WHERE menu_id = :mid"), {"mid": menu_id})).scalars().all()
        role_ids.update(int(rid) for rid in rows if rid)
        await session.execute(text("DELETE FROM sys_role_menus WHERE menu_id = :mid"), {"mid": menu_id})

    await session.execute(
        update(MenuModel).where(MenuModel.id.in_(all_ids)).values(is_deleted=True, deleted_time=datetime.now(UTC))
    )
    await session.flush()
    logger.info(f"已清理旧菜单 {len(all_ids)} 个节点（含按钮）: {', '.join(OLD_PAGES)}")
    return sorted(role_ids)


async def sync_menu_tree(session, role_codes: list[str]) -> dict:
    stats = {"created": 0, "skipped": 0}

    root, created = await find_or_create(
        session,
        name="内务中心",
        route_path="/internal",
        parent_id=None,
        defaults={
            "type": 1,
            "icon": "ri:briefcase-4-line",
            "order": 7,
            "route_name": "Internal",
            "redirect": "/internal/account",
            "title": "内务中心",
            "always_show": True,
            "description": "内部行政事务",
            "scope": "web",
        },
    )
    stats["created" if created else "skipped"] += 1

    # 先迁移旧目录，后面 find_or_create 才能命中同一个节点而不是再建一个
    attendance_id = await migrate_attendance_dir(session, root_id=root.id)
    # 旧页面挂在「考勤管理/薪资考勤」这个节点下，不是挂在根节点下
    old_role_ids = await remove_old_menus(session, root_id=attendance_id) if attendance_id else []

    menu_ids = [root.id]
    # 只读清单：根目录 + 页面 + query 按钮。旧角色只能迁到这一层（BUG-02）
    readonly_menu_ids = [root.id]
    for page_name, route_name, path, component, icon, order, buttons in NEW_PAGES:
        page, created = await find_or_create(
            session,
            name=page_name,
            route_path=path,
            parent_id=root.id,
            defaults={
                "type": 2,
                "icon": icon,
                "order": order,
                "permission": buttons[0][1] if buttons else None,
                "route_name": route_name,
                "component_path": component,
                "title": page_name,
                "description": page_name,
                "always_show": False,
                "scope": "web",
            },
        )
        stats["created" if created else "skipped"] += 1
        menu_ids.append(page.id)
        readonly_menu_ids.append(page.id)

        for btn_order, (btn_label, btn_permission) in enumerate(buttons, start=1):
            btn, btn_created = await find_or_create(
                session,
                name=btn_label,
                route_path=None,
                parent_id=page.id,
                defaults={
                    "type": 3,
                    "order": btn_order,
                    "permission": btn_permission,
                    "title": btn_label,
                    "description": btn_label,
                    "scope": "web",
                },
            )
            stats["created" if btn_created else "skipped"] += 1
            # 按钮也要进授权清单：薪资绩效这类权限只在按钮上，漏了就没有角色能拿到它
            menu_ids.append(btn.id)
            if btn_permission.endswith(":query"):
                readonly_menu_ids.append(btn.id)

    # 原来有旧内务中心菜单的角色，只迁移到等价的「页面 + 查询」权限，避免权限丢失；
    # 管理权限不在迁移范围内，防止旧考勤只读角色被扩成全员考勤/绩效/休息日管理员。
    if old_role_ids:
        await grant_role_ids(session, old_role_ids, readonly_menu_ids)
    # 管理权限只给超管线角色和 --role 显式指定的角色（含页面与全部按钮）
    manage_role_codes = list(dict.fromkeys([*DEFAULT_MANAGE_ROLE_CODES, *role_codes]))
    await grant_roles(session, manage_role_codes, menu_ids)

    return stats


async def grant_role_ids(session, role_ids: list[int], menu_ids: list[int]) -> None:
    """按角色ID批量授权（保留旧菜单原有的角色范围）"""
    for role_id in role_ids:
        inserted = 0
        for menu_id in menu_ids:
            exists = (
                await session.execute(
                    text("SELECT 1 FROM sys_role_menus WHERE role_id = :rid AND menu_id = :mid"),
                    {"rid": role_id, "mid": menu_id},
                )
            ).scalar()
            if exists:
                continue
            await session.execute(
                text("INSERT INTO sys_role_menus (role_id, menu_id) VALUES (:rid, :mid)"),
                {"rid": role_id, "mid": menu_id},
            )
            inserted += 1
        if inserted:
            logger.info(f"角色ID {role_id} 重新授权新菜单 {inserted} 条")


async def grant_roles(session, role_codes: list[str], menu_ids: list[int]) -> None:
    """把菜单授权给指定角色（sys_role_menus 关联表）"""
    for code in role_codes:
        role_id = (
            await session.execute(text("SELECT id FROM sys_role WHERE code = :code AND is_deleted = 0"), {"code": code})
        ).scalar()
        if not role_id:
            logger.warning(f"角色不存在，跳过授权: {code}")
            continue
        inserted = 0
        for menu_id in menu_ids:
            exists = (
                await session.execute(
                    text("SELECT 1 FROM sys_role_menus WHERE role_id = :rid AND menu_id = :mid"),
                    {"rid": role_id, "mid": menu_id},
                )
            ).scalar()
            if exists:
                continue
            await session.execute(
                text("INSERT INTO sys_role_menus (role_id, menu_id) VALUES (:rid, :mid)"),
                {"rid": role_id, "mid": menu_id},
            )
            inserted += 1
        logger.info(f"角色 {code} 新增菜单授权 {inserted} 条")


async def main(role_codes: list[str]) -> int:
    async with async_db_session() as session:
        try:
            stats = await sync_menu_tree(session, role_codes)
            await session.commit()
            logger.info(f"✅ 内务中心菜单同步完成: 新建 {stats['created']} 个节点，跳过 {stats['skipped']} 个已存在节点")
        except Exception as e:
            await session.rollback()
            logger.error(f"❌ 内务中心菜单同步失败，已回滚: {e!s}")
            raise
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="同步内务中心菜单到账号管理 + 薪资考勤结构")
    parser.add_argument("--role", action="append", default=[], help="按角色编码授权菜单，可重复传入（如 SUPER_ADMIN）")
    args = parser.parse_args()

    sys.exit(asyncio.run(main(args.role)))
