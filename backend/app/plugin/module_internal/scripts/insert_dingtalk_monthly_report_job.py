# -*- coding: utf-8 -*-
"""注入「钉钉考勤月报同步」系统定时任务节点（幂等）。

业务要求：每月 3 日同步上个月的钉钉智能考勤月报。同步逻辑预写在
app.plugin.module_internal.attendance.service.MonthlyReportService 里，
这里的代码块只负责调用它，不写任何业务规则。

默认执行时间：每月 3 日 06:00（cron: 秒 分 时 日 月 周）。
如需改时间，直接在系统「定时任务 → 节点」页面修改，不要再改这里。

用法（在 backend 目录下执行）：
    python app/plugin/module_internal/scripts/insert_dingtalk_monthly_report_job.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import false, select  # noqa: E402

from app.core.base_model import MappedBase  # noqa: E402
from app.core.database import async_db_session  # noqa: E402
from app.core.logger import logger  # noqa: E402
from app.modules.task.cronjob.node.model import NodeModel  # noqa: E402
from app.utils.import_util import ImportUtil  # noqa: E402

# 必须加载全部模型再建立 mapper
ImportUtil.find_models(MappedBase)

JOB_CODE = "dingtalk_monthly_report"
JOB_NAME = "钉钉考勤月报同步"
# cron 表达式：秒 分 时 日 月 周 → 每月 3 日 06:00:00
JOB_CRON = "0 0 0 3 * ?"

JOB_FUNC = '''def handler(**kwargs):
    """钉钉考勤月报同步：只调用预写的 Service，业务规则不在代码块里。"""
    import asyncio

    from redis.asyncio import Redis

    from app.config.setting import settings
    from app.core.ap_scheduler import SchedulerUtil
    from app.core.base_schema import AuthSchema
    from app.core.database import async_db_session
    from app.plugin.module_internal.attendance.service import MonthlyReportService

    async def _run() -> dict:
        # 定时任务跑在 APScheduler 的工作线程里，不能在线程间复用主事件循环的 Redis 客户端；
        # 这里在本线程的 asyncio.run 内部创建并关闭自己的客户端。
        redis = Redis(
            host=settings.REDIS_HOST,
            port=int(settings.REDIS_PORT),
            password=settings.REDIS_PASSWORD or None,
            db=int(settings.REDIS_DB_NAME),
            decode_responses=True,
        )
        try:
            async with async_db_session() as db, db.begin():
                service = MonthlyReportService(AuthSchema(), db, redis)
                result = await service.sync_previous_month()
                return result.model_dump()
        finally:
            await redis.aclose()

    _ = SchedulerUtil  # 保持导入：便于排查调度器运行状态

    return asyncio.run(_run())
'''


async def upsert_job_node() -> None:
    async with async_db_session() as session:
        try:
            node = (
                await session.execute(select(NodeModel).where(NodeModel.code == JOB_CODE, NodeModel.is_deleted == false()))
            ).scalars().first()

            if node:
                node.name = JOB_NAME
                node.func = JOB_FUNC
                node.trigger = "cron"
                node.trigger_args = JOB_CRON
                node.status = 0
                node.jobstore = "default"
                node.executor = "threadpool"
                node.coalesce = True
                node.max_instances = 1
                node.description = "每月7号同步上个月的钉钉智能考勤月报；具体执行时间可在节点页面调整"
                logger.info(f"已更新定时任务节点: {JOB_NAME}(code={JOB_CODE})")
            else:
                session.add(
                    NodeModel(
                        name=JOB_NAME,
                        code=JOB_CODE,
                        jobstore="default",
                        executor="threadpool",
                        trigger="cron",
                        trigger_args=JOB_CRON,
                        func=JOB_FUNC,
                        coalesce=True,
                        max_instances=1,
                        status=0,
                        description="每月7号同步上个月的钉钉智能考勤月报；具体执行时间可在节点页面调整",
                    )
                )
                logger.info(f"已创建定时任务节点: {JOB_NAME}(code={JOB_CODE})")

            await session.commit()
            logger.info("✅ 钉钉考勤月报定时任务节点写入完成；重启 backend 后由调度器自愈注册")
        except Exception as e:
            await session.rollback()
            logger.error(f"❌ 写入钉钉考勤月报定时任务节点失败，已回滚: {e!s}")
            raise


if __name__ == "__main__":
    asyncio.run(upsert_job_node())
