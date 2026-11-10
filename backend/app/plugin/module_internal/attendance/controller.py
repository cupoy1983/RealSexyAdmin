from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, redis_getter
from app.core.exceptions import CustomException
from app.core.router_class import OperationLogRoute

from .schema import (
    AttendanceGroupOptionSchema,
    DailyReportOutSchema,
    MonthConfirmSchema,
    MonthlyReportOutSchema,
    MonthlyReportQueryParam,
    MonthlyReportSyncResultSchema,
    MonthlyReportSyncStateSchema,
    RestDayCreateResultSchema,
    RestDayCreateSchema,
    RestDayOutSchema,
)
from .service import MonthlyReportService

AttendanceRouter = APIRouter(route_class=OperationLogRoute, prefix="/attendance", tags=["钉钉考勤"])


@AttendanceRouter.get("/page", summary="分页查询钉钉考勤月报", response_model=ResponseSchema[PageResultSchema[MonthlyReportOutSchema]])
async def get_monthly_report_page_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[MonthlyReportQueryParam, Query()] = None,
) -> dict:
    # 注意：分页必须用 Depends() 包成子 dependant，不能把 page_no/page_size 写成同签名里的
    # 标量 Query()。FastAPI 只在「该 dependant 的 query 参数恰好只有 1 个且是 BaseModel」时
    # 才把模型展开成平铺字段；一旦同级还有别的 Query 标量，search 就永远收不到值（恒为 None），
    # 表现为月份/姓名筛选全部失效且后端不报错。详见 _get_flat_fields_from_params。
    result = await MonthlyReportService(auth, db).page(page_no=page.page_no, page_size=page.page_size, search=search)
    return SuccessResponse(data=result, msg="查询钉钉考勤月报成功")


@AttendanceRouter.get("/list", summary="查询钉钉考勤月报列表", response_model=ResponseSchema[list[MonthlyReportOutSchema]])
async def get_monthly_report_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[MonthlyReportQueryParam, Query()] = None,
) -> dict:
    result = await MonthlyReportService(auth, db).get_list(search=search)
    return SuccessResponse(data=result, msg="查询钉钉考勤月报成功")


@AttendanceRouter.get(
    "/daily-list",
    summary="查询钉钉考勤日明细",
    response_model=ResponseSchema[list[DailyReportOutSchema]],
)
async def get_daily_report_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    oa_user_id: Annotated[int, Query(ge=1, description="OA用户ID")],
    month: Annotated[str, Query(min_length=7, max_length=7, description="月份(YYYY-MM)")],
) -> dict:
    result = await MonthlyReportService(auth, db).daily_list(oa_user_id=oa_user_id, month=month)
    return SuccessResponse(data=result, msg="查询钉钉考勤日明细成功")


@AttendanceRouter.get("/detail/{id}", summary="查询钉钉考勤月报详情", response_model=ResponseSchema[MonthlyReportOutSchema])
async def get_monthly_report_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="月报ID")],
) -> dict:
    result = await MonthlyReportService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="查询钉钉考勤月报详情成功")


@AttendanceRouter.get(
    "/sync-state",
    summary="查询上月钉钉考勤同步状态",
    response_model=ResponseSchema[MonthlyReportSyncStateSchema],
)
async def get_attendance_sync_state_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> dict:
    """页面据此决定「手动拉取」按钮显不显示。

    只查数据库、不打钉钉接口，页面每次加载都会调，所以要轻。
    """
    result = await MonthlyReportService(auth, db).previous_month_sync_state()
    return SuccessResponse(data=result, msg="查询考勤同步状态成功")


# ── 月度考勤确认（ADR-0018）：整月工资条生成前置 + 生成后锁考勤 ──────


@AttendanceRouter.post(
    "/month-confirm",
    summary="确认某月考勤（公司休息日已全部登记完毕）",
    response_model=ResponseSchema[None],
)
async def confirm_attendance_month_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:rest"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[MonthConfirmSchema, Body()],
) -> dict:
    """权限复用「休息日安排」：能改休息日的人才有资格确认考勤，超管直接放行。

    未确认的月份整月工资条一律不生成（`generation_blockers()` 第三道闸）。
    """
    await MonthlyReportService(auth, db).confirm_month(data)
    return SuccessResponse(msg=f"{data.month} 考勤已确认，可以生成该月工资条")


@AttendanceRouter.delete(
    "/month-confirm",
    summary="撤销某月考勤确认",
    response_model=ResponseSchema[None],
)
async def revoke_attendance_month_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:rest"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[MonthConfirmSchema, Body()],
) -> dict:
    """已生成正式工资条的月份不可撤销，避免绕过「生成后锁考勤」。"""
    await MonthlyReportService(auth, db).revoke_month_confirmation(data)
    return SuccessResponse(msg=f"{data.month} 考勤确认已撤销，可继续登记公司休息日")


@AttendanceRouter.post(
    "/sync",
    summary="手动拉取上月钉钉考勤月报",
    response_model=ResponseSchema[MonthlyReportSyncResultSchema],
)
async def sync_previous_month_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:sync"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> dict:
    """手动触发拉取上个月考勤。与每月 3 日 00:00 的自动任务互斥，谁先跑谁生效。

    后端再挡一次（按钮已经按状态隐藏，这里是防被绕过或直连接口重复拉）：
    同步是整批原子提交，任一人失败整批作废，所以重复拉同一月份没有意义。

    一次要打好几次钉钉接口，人数多时比较慢，前端按钮要 loading 并禁用防重复点。
    """
    service = MonthlyReportService(auth, db, redis)
    state = await service.previous_month_sync_state()
    if not state.can_sync:
        reason = "已同步过，无需重复拉取" if state.synced else "没有可拉取的人员（都没有考勤组）"
        raise CustomException(msg=f"{state.month} 考勤数据{reason}")
    result = await service.sync_previous_month()
    return SuccessResponse(data=result, msg=f"拉取 {result.month} 钉钉考勤月报成功")


# ── 公司休息日（ADR-0009）────────────────────────────────────


@AttendanceRouter.get(
    "/rest-day/groups",
    summary="查询可安排休息日的考勤组",
    response_model=ResponseSchema[list[AttendanceGroupOptionSchema]],
)
async def get_rest_day_groups_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> dict:
    result = await MonthlyReportService(auth, db).list_attendance_groups()
    return SuccessResponse(data=result, msg="查询考勤组成功")


@AttendanceRouter.get(
    "/rest-day/list",
    summary="查询公司休息日列表",
    response_model=ResponseSchema[list[RestDayOutSchema]],
)
async def get_rest_day_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    month: Annotated[str | None, Query(min_length=7, max_length=7, description="月份(YYYY-MM)")] = None,
    group_id: Annotated[str | None, Query(description="考勤组ID")] = None,
) -> dict:
    result = await MonthlyReportService(auth, db).list_rest_days(month=month, group_id=group_id)
    return SuccessResponse(data=result, msg="查询公司休息日成功")


@AttendanceRouter.post(
    "/rest-day/create",
    summary="批量登记公司休息日",
    response_model=ResponseSchema[RestDayCreateResultSchema],
)
async def create_rest_day_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:rest"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[RestDayCreateSchema, Body()],
) -> dict:
    """登记后立即重算受影响月份的月报与日明细（A2 立即生效）。整批在同一请求事务里，失败一起回滚。"""
    result = await MonthlyReportService(auth, db).create_rest_days(data)
    return SuccessResponse(data=result, msg=f"已登记 {result.created} 天休息日并重算")


@AttendanceRouter.delete("/rest-day/delete", summary="删除公司休息日")
async def delete_rest_day_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:salary-attendance:rest"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="休息日ID列表")],
) -> dict:
    """删除后立即反向重算，把数据还原回钉钉原值（A2 / A6）。"""
    result = await MonthlyReportService(auth, db).delete_rest_days(ids)
    return SuccessResponse(data=result, msg=f"已删除 {result['deleted']} 天休息日并重算")
