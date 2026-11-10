from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, get_current_user, redis_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    BusinessCalendarSchema,
    DismissalContextQuerySchema,
    DismissalContextResultSchema,
    PerformanceConfirmResultSchema,
    PerformanceConfirmSchema,
    PerformanceEntryOutSchema,
    PerformanceEntrySaveSchema,
    PayslipOutSchema,
    SalaryProfileOutSchema,
    SalaryProfileUpdateSchema,
)
from .service import MANAGE_PERMISSION, SalaryService

SalaryRouter = APIRouter(route_class=OperationLogRoute, prefix="/salary", tags=["薪资绩效"])
QUERY_PERMISSION = "module_internal:salary-performance:query"
# 离职结算弹窗挂在用户管理页的删除链上，操作者持的是删用户权限，不是薪资管理权限。
DELETE_USER_PERMISSION = "module_system:user:delete"


@SalaryRouter.get("/payslip/page", summary="分页查询工资条", response_model=ResponseSchema[PageResultSchema[PayslipOutSchema]])
async def get_payslip_page_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([QUERY_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    month: Annotated[str | None, Query(min_length=7, max_length=7)] = None,
    name: Annotated[str | None, Query()] = None,
    oa_user_id: Annotated[int | None, Query(ge=1)] = None,
) -> dict:
    result = await SalaryService(auth, db).page_payslips(
        page_no=page.page_no,
        page_size=page.page_size,
        month=month,
        name=name,
        oa_user_id=oa_user_id,
    )
    return SuccessResponse(data=result, msg="查询工资条成功")


@SalaryRouter.get("/payslip/detail/{id}", summary="查询工资条详情", response_model=ResponseSchema[PayslipOutSchema])
async def get_payslip_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([QUERY_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(ge=1)],
) -> dict:
    result = await SalaryService(auth, db).get_payslip(id)
    return SuccessResponse(data=result, msg="查询工资条详情成功")


@SalaryRouter.post("/payslip/confirm/{id}", summary="确认工资条（永久锁定）", response_model=ResponseSchema[PayslipOutSchema])
async def confirm_payslip_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(ge=1)],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> dict:
    result = await SalaryService(auth, db, redis).confirm_payslip(id=id)
    return SuccessResponse(data=result, msg="工资条已确认并永久锁定")


@SalaryRouter.get("/profile/list", summary="查询员工薪资档案", response_model=ResponseSchema[list[SalaryProfileOutSchema]])
async def get_salary_profile_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    name: Annotated[str | None, Query()] = None,
) -> dict:
    result = await SalaryService(auth, db).list_profiles(name=name)
    return SuccessResponse(data=result, msg="查询薪资档案成功")


@SalaryRouter.post(
    "/profile/save/{oa_user_id}",
    summary="设置员工薪资档案（仅超管）",
    response_model=ResponseSchema[SalaryProfileOutSchema],
)
async def save_salary_profile_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    oa_user_id: Annotated[int, Path(ge=1)],
    data: Annotated[SalaryProfileUpdateSchema, Body()],
) -> dict:
    result = await SalaryService(auth, db).save_profile(oa_user_id=oa_user_id, data=data)
    return SuccessResponse(data=result, msg="薪资档案保存成功")


@SalaryRouter.get("/performance/list", summary="查询月度绩效登记", response_model=ResponseSchema[list[PerformanceEntryOutSchema]])
async def get_performance_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    month: Annotated[str, Query(min_length=7, max_length=7)],
    name: Annotated[str | None, Query()] = None,
) -> dict:
    result = await SalaryService(auth, db).list_performance(month=month, name=name)
    return SuccessResponse(data=result, msg="查询月度绩效成功")


@SalaryRouter.post("/performance/save", summary="登记月度绩效扣款", response_model=ResponseSchema[PerformanceEntryOutSchema])
async def save_performance_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[PerformanceEntrySaveSchema, Body()],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> dict:
    result = await SalaryService(auth, db, redis).save_performance(data)
    return SuccessResponse(data=result, msg="绩效扣款保存成功")


@SalaryRouter.post(
    "/performance/confirm",
    summary="确认或撤销个人绩效；全员确认后自动生成工资条",
    response_model=ResponseSchema[PerformanceConfirmResultSchema],
)
async def confirm_performance_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[PerformanceConfirmSchema, Body()],
) -> dict:
    result = await SalaryService(auth, db).confirm_performance(data)
    return SuccessResponse(data=result, msg=result.message)


# ── 服务端业务日历（ADR-0017）──────────────────────────────────
@SalaryRouter.get(
    "/business-calendar",
    summary="查询服务端业务日历",
    response_model=ResponseSchema[BusinessCalendarSchema],
)
async def get_business_calendar_controller(
    auth: Annotated[AuthSchema, Security(get_current_user)],
) -> dict:
    """两个 Tab 的默认月份与「3 日后开放」都由服务端给出，不用浏览器本地日期。"""
    result = SalaryService.business_calendar()
    return SuccessResponse(data=result, msg="查询业务日历成功")


# ── 离职结算单（ADR-0016）──────────────────────────────────────
@SalaryRouter.post(
    "/dismissal/context",
    summary="离职结算扣款上下文（只返回姓名/绩效基准，不做金额预览）",
    response_model=ResponseSchema[DismissalContextResultSchema],
)
async def dismissal_context_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([DELETE_USER_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[DismissalContextQuerySchema, Body()],
) -> dict:
    result = await SalaryService(auth, db).dismissal_context(oa_user_ids=data.oa_user_ids)
    return SuccessResponse(data=result, msg="查询离职结算扣款上下文成功")


@SalaryRouter.get(
    "/dismissal/list",
    summary="按月份查看离职结算单",
    response_model=ResponseSchema[list[PayslipOutSchema]],
)
async def dismissal_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission([MANAGE_PERMISSION]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    month: Annotated[str, Query(min_length=7, max_length=7, description="月份(YYYY-MM)")],
) -> dict:
    """仅当所选月份存在结算单时前端才显示「查看离职结算单」按钮；普通员工不可见。"""
    result = await SalaryService(auth, db).list_dismissal_payslips(month=month)
    return SuccessResponse(data=result, msg="查询离职结算单成功")
