from datetime import UTC, date, datetime

from redis.asyncio.client import Redis
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ap_scheduler import SchedulerUtil
from app.core.logger import logger
from app.modules.system.menu.model import MenuModel
from app.modules.system.role.model import RoleMenusModel
from app.modules.system.user.model import UserModel, UserRolesModel
from app.plugin.infra.integration.dingtalk_client import DingtalkClient
from app.plugin.module_internal.account.model import ExternalAccountBindingModel
from app.plugin.module_internal.account.schema import SYSTEM_DINGTALK
from app.plugin.module_internal.attendance.model import MonthlyReportModel

from .model import PerformanceEntryModel, SalaryNotificationLogModel, SalaryProfileModel

ADMIN_REMINDER = "admin_reminder"
EMPLOYEE_REVIEW = "review"
PAYSLIP_PAID = "payslip_paid"
MANAGE_PERMISSION = "module_internal:salary-performance:manage"


def month_text(month: str) -> str:
    year, mon = month.split("-")
    return f"{year}年{int(mon)}月"


class SalaryNotificationService:
    def __init__(self, db: AsyncSession, redis: Redis | None = None) -> None:
        self.db = db
        self.redis = redis or SchedulerUtil.redis_instance

    async def _send(self, *, event_type: str, month: str, oa_user_id: int, content: str, once: bool) -> bool:
        log = (
            await self.db.execute(
                select(SalaryNotificationLogModel).where(
                    SalaryNotificationLogModel.event_type == event_type,
                    SalaryNotificationLogModel.month == month,
                    SalaryNotificationLogModel.oa_user_id == oa_user_id,
                )
            )
        ).scalars().first()
        if once and log and log.status == 1:
            return True
        now = datetime.now(UTC)
        if not log:
            log = SalaryNotificationLogModel(
                event_type=event_type,
                month=month,
                oa_user_id=oa_user_id,
                content=content,
                last_attempt_at=now,
            )
            self.db.add(log)
        log.content = content
        log.last_attempt_at = now
        try:
            binding = (
                await self.db.execute(
                    select(ExternalAccountBindingModel).where(
                        ExternalAccountBindingModel.oa_user_id == oa_user_id,
                        ExternalAccountBindingModel.system_type == SYSTEM_DINGTALK,
                        ExternalAccountBindingModel.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars().first()
            if not binding:
                raise RuntimeError("员工未绑定钉钉，无法发送")
            if self.redis is None:
                raise RuntimeError("Redis 不可用，无法获取钉钉凭证")
            await DingtalkClient(self.redis).send_ro_text_to_user(userid=binding.external_user_id, content=content)
            log.status = 1
            log.error = None
            log.sent_at = now
            await self.db.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            log.status = 0
            log.error = str(exc)[:2000]
            log.sent_at = None
            await self.db.flush()
            logger.warning(f"钉钉通知发送失败: type={event_type}, month={month}, user={oa_user_id}: {exc}")
            return False

    async def pending_setting_user_ids(self, month: str) -> list[int]:
        return list(
            (
                await self.db.execute(
                    select(MonthlyReportModel.oa_user_id)
                    .join(UserModel, UserModel.id == MonthlyReportModel.oa_user_id)
                    .join(
                        SalaryProfileModel,
                        and_(
                            SalaryProfileModel.oa_user_id == MonthlyReportModel.oa_user_id,
                            SalaryProfileModel.is_deleted == False,  # noqa: E712
                            SalaryProfileModel.performance_base > 0,
                        ),
                    )
                    .outerjoin(
                        PerformanceEntryModel,
                        and_(
                            PerformanceEntryModel.oa_user_id == MonthlyReportModel.oa_user_id,
                            PerformanceEntryModel.month == month,
                            PerformanceEntryModel.is_deleted == False,  # noqa: E712
                        ),
                    )
                    .where(
                        MonthlyReportModel.month == month,
                        MonthlyReportModel.group_id.isnot(None),
                        MonthlyReportModel.is_deleted == False,  # noqa: E712
                        # BUG-07：与绩效列表 / 确认进度 / 工资条生成保持同一人员口径。
                        # 少了这两个条件，停用或已离职员工会永远留在「待设置」名单里，
                        # 而管理页面又查不到他，N2 员工核对通知与整月流程会被永久卡住。
                        UserModel.is_deleted == False,  # noqa: E712
                        UserModel.status == 0,
                        PerformanceEntryModel.id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def _admin_ids(self) -> set[int]:
        super_ids = set(
            (
                await self.db.execute(
                    select(UserModel.id).where(
                        UserModel.is_superuser == True,  # noqa: E712
                        UserModel.is_deleted == False,  # noqa: E712
                        UserModel.status == 0,
                    )
                )
            )
            .scalars()
            .all()
        )
        manage_ids = set(
            (
                await self.db.execute(
                    select(UserRolesModel.user_id)
                    .join(RoleMenusModel, RoleMenusModel.role_id == UserRolesModel.role_id)
                    .join(MenuModel, MenuModel.id == RoleMenusModel.menu_id)
                    .join(UserModel, UserModel.id == UserRolesModel.user_id)
                    .where(
                        MenuModel.permission == MANAGE_PERMISSION,
                        MenuModel.is_deleted == False,  # noqa: E712
                        UserModel.is_deleted == False,  # noqa: E712
                        UserModel.status == 0,
                    )
                )
            )
            .scalars()
            .all()
        )
        return super_ids | manage_ids

    async def notify_admins_if_pending(self, month: str) -> dict[str, int]:
        if date.today().day < 3 or not await self.pending_setting_user_ids(month):
            return {"sent": 0, "failed": 0}
        users = (
            (
                await self.db.execute(
                    select(UserModel).where(UserModel.id.in_(await self._admin_ids())).order_by(UserModel.id)
                )
            )
            .scalars()
            .all()
        )
        result = {"sent": 0, "failed": 0}
        for user in users:
            content = (
                f"亲爱的管理员 {user.name}，请登录 RealSexyAdmin，设置 {month_text(month)} 员工绩效。"
                "设置完成后员工将收到考勤与绩效核对通知。麻烦了哦！"
            )
            ok = await self._send(event_type=ADMIN_REMINDER, month=month, oa_user_id=user.id, content=content, once=False)
            result["sent" if ok else "failed"] += 1
        return result

    async def notify_employees_for_review(self, month: str) -> dict[str, int]:
        if await self.pending_setting_user_ids(month):
            return {"sent": 0, "failed": 0}
        users = (
            (
                await self.db.execute(
                    select(UserModel)
                    .join(MonthlyReportModel, MonthlyReportModel.oa_user_id == UserModel.id)
                    .where(
                        MonthlyReportModel.month == month,
                        MonthlyReportModel.group_id.isnot(None),
                        MonthlyReportModel.is_deleted == False,  # noqa: E712
                        UserModel.is_deleted == False,  # noqa: E712
                        UserModel.status == 0,
                    )
                    .order_by(UserModel.id)
                )
            )
            .scalars()
            .all()
        )
        result = {"sent": 0, "failed": 0}
        for user in users:
            content = f"您好，{month_text(month)} 考勤信息已登记，请登录 RealSexyAdmin 核对。如有异议，请及时联系管理员。"
            ok = await self._send(event_type=EMPLOYEE_REVIEW, month=month, oa_user_id=user.id, content=content, once=True)
            result["sent" if ok else "failed"] += 1
        return result

    async def notify_payslip_paid(self, *, oa_user_id: int, month: str, name: str) -> bool:
        content = f"亲爱的 {name}，{month_text(month)} 工资条已确认并发放工资，请注意查收！"
        return await self._send(event_type=PAYSLIP_PAID, month=month, oa_user_id=oa_user_id, content=content, once=True)

    async def retry_failed_employee_notifications(self) -> dict[str, int]:
        logs = (
            (
                await self.db.execute(
                    select(SalaryNotificationLogModel).where(
                        SalaryNotificationLogModel.status == 0,
                        SalaryNotificationLogModel.event_type.in_([EMPLOYEE_REVIEW, PAYSLIP_PAID]),
                    )
                )
            )
            .scalars()
            .all()
        )
        result = {"sent": 0, "failed": 0}
        for log in logs:
            ok = await self._send(
                event_type=log.event_type,
                month=log.month,
                oa_user_id=log.oa_user_id,
                content=log.content,
                once=True,
            )
            result["sent" if ok else "failed"] += 1
        return result
