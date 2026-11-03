"""考勤模块公共辅助 —— 跨表字典填充

供给 Service 层拼接响应时翻译外键名称为可读文案，避免页面端二次请求。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.modules.system.dept.model import DeptModel
from app.modules.system.user.model import UserModel


def audit_load_options(model: Any) -> tuple[Any, ...]:
    """审计字段关联（created_by / updated_by / deleted_by）的预加载选项。

    框架的 `BaseCRUD` 会自动 joinedload created_by / updated_by（`UserMixin` 注释里写明），
    但那只对走 CRUD 方法的查询生效；**原生 `select` 享受不到**。只要结果里有一行的
    `*_id` 非 NULL，输出 schema（`UserBySchema` 声明了 `*_by` 字段）做 `model_validate`
    时就会触发同步 IO → MissingGreenlet，页面直接报「组件渲染异常」。

    这个坑是**沉默的**：审计字段全为 NULL 的行根本不会触发懒加载，所以新表看起来一切正常，
    等到同步/编辑写入了 `updated_id` 才突然炸。

    参数:
    - model (Any): 继承了 `UserMixin` 的模型类

    返回:
    - tuple[Any, ...]: 直接展开给 `.options(*audit_load_options(XxxModel))`
    """
    return (
        joinedload(model.created_by),
        joinedload(model.updated_by),
        joinedload(model.deleted_by),
    )


async def fetch_dept_names(db: AsyncSession, dept_ids: list[int | None]) -> dict[int, str]:
    """批量取部门 id → 部门名映射"""
    ids = sorted({int(did) for did in dept_ids if did})
    if not ids:
        return {}
    rows = (await db.execute(select(DeptModel.id, DeptModel.name).where(DeptModel.id.in_(ids)))).all()
    return {row[0]: row[1] for row in rows}


async def fetch_user_names(db: AsyncSession, user_ids: list[int | None]) -> dict[int, str]:
    """批量取用户 id → 姓名映射"""
    ids = sorted({int(uid) for uid in user_ids if uid})
    if not ids:
        return {}
    rows = (await db.execute(select(UserModel.id, UserModel.name).where(UserModel.id.in_(ids)))).all()
    return {row[0]: row[1] for row in rows}


async def fetch_user_profiles(db: AsyncSession, user_ids: list[int | None]) -> dict[int, dict[str, Any]]:
    """批量取用户 id → {name, dept_id, dept_name}

    用 selectinload 预加载部门，避免逐个用户触发懒加载导致 N+1 查询。
    """
    ids = sorted({int(uid) for uid in user_ids if uid})
    if not ids:
        return {}
    stmt = select(UserModel).options(selectinload(UserModel.dept)).where(UserModel.id.in_(ids))
    users = (await db.execute(stmt)).scalars().all()
    return {
        user.id: {
            "name": user.name,
            "dept_id": user.dept_id,
            "dept_name": user.dept.name if user.dept else None,
        }
        for user in users
    }
