from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from ..utils import audit_load_options
from .model import DailyReportModel, MonthlyReportModel, RestDayModel
from .schema import (
    DailyReportCreateSchema,
    DailyReportUpdateSchema,
    MonthlyReportCreateSchema,
    MonthlyReportUpdateSchema,
    RestDayCreateSchema,
    RestDayUpdateSchema,
)


class MonthlyReportCRUD(CRUDBase[MonthlyReportModel, MonthlyReportCreateSchema, MonthlyReportUpdateSchema]):
    """钉钉考勤月汇总数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=MonthlyReportModel, auth=auth, db=db)

    async def get_by_user_month(self, oa_user_id: int, month: str) -> MonthlyReportModel | None:
        """按用户 + 月份查月报"""
        return await self.get(oa_user_id=oa_user_id, month=month)

    async def list_by_month(self, month: str) -> list[MonthlyReportModel]:
        """取某月全部月报（重算用）。不带审计预加载：重算只读写统计列，不做 model_validate。"""
        rows = await self.db.execute(
            select(MonthlyReportModel).where(
                MonthlyReportModel.month == month,
                MonthlyReportModel.is_deleted == False,  # noqa: E712
                MonthlyReportModel.group_id.isnot(None),
            )
        )
        return list(rows.scalars().all())


class DailyReportCRUD(CRUDBase[DailyReportModel, DailyReportCreateSchema, DailyReportUpdateSchema]):
    """钉钉考勤日明细数据层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=DailyReportModel, auth=auth, db=db)

    async def list_by_user_month(self, *, oa_user_id: int, month: str) -> list[DailyReportModel]:
        """按用户 + 月份取全部日明细，按日期升序"""
        rows = await self.db.execute(
            select(DailyReportModel)
            .options(*audit_load_options(DailyReportModel))
            .where(
                DailyReportModel.oa_user_id == oa_user_id,
                DailyReportModel.month == month,
                DailyReportModel.is_deleted == False,  # noqa: E712
                # 没有考勤组的人不该有考勤明细，跟列表页的过滤是同一口径（ADR-0008）。
                # 这层是双保险：列表页把人藏住了，明细接口也得跟着藏住，
                # 否则会出现「列表看不见、接口直连能捞到」的口子。
                DailyReportModel.group_id.isnot(None),
            )
            .order_by(DailyReportModel.work_date.asc())
        )
        return list(rows.scalars().all())

    async def get_by_user_date(self, *, oa_user_id: int, work_date: date) -> DailyReportModel | None:
        """按用户 + 日期查单条日明细"""
        return await self.get(oa_user_id=oa_user_id, work_date=work_date)

    async def list_for_recalc(self, *, oa_user_id: int, month: str) -> list[DailyReportModel]:
        """重算用：取某用户某月全部日明细。不带审计预加载、不按考勤组过滤
        （重算需要休息日那天的原始行，哪怕它已被改成「公司休息」）。"""
        rows = await self.db.execute(
            select(DailyReportModel).where(
                DailyReportModel.oa_user_id == oa_user_id,
                DailyReportModel.month == month,
                DailyReportModel.is_deleted == False,  # noqa: E712
            )
        )
        return list(rows.scalars().all())


class RestDayCRUD(CRUDBase[RestDayModel, RestDayCreateSchema, RestDayUpdateSchema]):
    """公司休息日数据层

    休息日是纯配置，删除走**物理删**（`_purge_ungrouped_rows` 已有同样先例），
    所以 (group_id, rest_date) 唯一约束不会被软删残留卡住（ADR-0009）。
    """

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        super().__init__(model=RestDayModel, auth=auth, db=db)

    async def list_rest_days(self, *, month: str | None = None, group_id: str | None = None) -> list[RestDayModel]:
        """按月 / 考勤组筛休息日，按日期升序"""
        conditions = [RestDayModel.is_deleted == False]  # noqa: E712
        if month:
            conditions.append(RestDayModel.month == month)
        if group_id:
            conditions.append(RestDayModel.group_id == group_id)
        rows = await self.db.execute(
            select(RestDayModel)
            .options(*audit_load_options(RestDayModel))
            .where(*conditions)
            .order_by(RestDayModel.rest_date.asc())
        )
        return list(rows.scalars().all())

    async def get_by_group_date(self, *, group_id: str, rest_date: date) -> RestDayModel | None:
        """按考勤组 + 日期查单条休息日"""
        return await self.get(group_id=group_id, rest_date=rest_date)

    async def month_has_rest_day(self, month: str) -> bool:
        """某月是否存在公司休息日（同步后据此决定要不要重算）"""
        return await self.exists(month=month)

    async def physical_delete(self, ids: list[int]) -> list[RestDayModel]:
        """物理删除休息日，返回被删的行（调用方据此重算受影响月份）。

        先查出再删：删完就拿不到 month / group_id 了，而重算恰恰需要它们。
        """
        rows = await self.db.execute(
            select(RestDayModel).where(RestDayModel.id.in_(ids), RestDayModel.is_deleted == False)  # noqa: E712
        )
        objs = list(rows.scalars().all())
        if not objs:
            return []
        await self.db.execute(delete(RestDayModel).where(RestDayModel.id.in_([o.id for o in objs])))
        await self.db.flush()
        return objs
