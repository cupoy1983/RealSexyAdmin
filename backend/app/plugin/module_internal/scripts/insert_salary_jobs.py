# -*- coding: utf-8 -*-
"""注入薪资绩效提醒任务（幂等），并停用旧版定时工资生成任务。

每月 3 日 12:00 起每天检查：上月绩效未设置完成就通过钉钉单聊提醒管理员。
工资条只由「全员绩效确认完成」触发，不再有定时或手动生成入口。

用法（backend 目录）：uv run python app/plugin/module_internal/scripts/insert_salary_jobs.py
写入后重启后端，由调度器自愈注册。cron 为「秒 分 时 日 月 周」。
"""

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
from app.modules.task.cronjob.node.model import NodeModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

ImportUtil.find_models(MappedBase)

JOB_CODE = "salary_performance_notify"
JOB_NAME = "薪资绩效钉钉提醒"
JOB_CRON = "0 0 12 * * ?"
JOB_FUNC = '''def handler(**kwargs):
    import asyncio
    from app.core.ap_scheduler import scheduler
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session
    from app.plugin.module_internal.salary.service import SalaryService

    async def _run() -> dict:
        async with async_db_session() as db, db.begin():
            result = await SalaryService(AuthSchema(), db).process_daily_notifications()
            return result

    # 2026-11-07：任务跑在 APScheduler 的 threadpool 线程，全局 async_engine 与
    # SchedulerUtil.redis_instance 属于 uvicorn 主事件循环。另起 asyncio.run() 会复用
    # 主循环的连接池，触发 "attached to a different loop"（每天 12:00 必崩）。
    # 把协程投递回主循环执行，资源归属才正确；result() 阻塞等待，异常照常冒泡给调度器。
    loop = scheduler._eventloop
    if loop is None or loop.is_closed():
        raise RuntimeError("调度器主事件循环不可用，任务未执行")
    return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=600)
'''


async def upsert_jobs() -> None:
    async with async_db_session() as session:
        try:
            node = (
                await session.execute(
                    select(NodeModel).where(NodeModel.code == JOB_CODE, NodeModel.is_deleted == False)  # noqa: E712
                )
            ).scalars().first()
            values = {
                "name": JOB_NAME,
                "func": JOB_FUNC,
                "trigger": "cron",
                "trigger_args": JOB_CRON,
                "status": 0,
                "jobstore": "default",
                "executor": "threadpool",
                "coalesce": True,
                "max_instances": 1,
                "description": "每月3日12:00起每天检查，未设置完上月绩效则钉钉私聊提醒管理员，并重试员工通知",
            }
            if node:
                for key, value in values.items():
                    setattr(node, key, value)
            else:
                session.add(NodeModel(code=JOB_CODE, **values))

            obsolete = (
                await session.execute(
                    select(NodeModel).where(
                        NodeModel.code == "salary_payslip_monthly_generate",
                        NodeModel.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().first()
            if obsolete:
                obsolete.status = 1
                obsolete.description = "已停用：工资条改为全员绩效确认后自动生成"
                logger.info("已停用旧版定时工资生成任务")
            await session.commit()
            logger.info("✅ 薪资绩效提醒任务更新完成；重启 backend 后由调度器自愈注册")
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(upsert_jobs())
