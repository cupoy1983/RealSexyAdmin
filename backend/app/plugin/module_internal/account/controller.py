from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter, redis_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    DeptMappingCreateSchema,
    DeptMappingOutSchema,
    DeptMappingQueryParam,
    DeptMappingUpdateSchema,
    DingtalkAccountOutSchema,
    DingtalkAccountQueryParam,
    DingtalkManualMatchResultSchema,
    DingtalkManualMatchSchema,
)
from .service import DeptMappingService, DingtalkAccountService

AccountRouter = APIRouter(route_class=OperationLogRoute, prefix="/account", tags=["账号管理"])


@AccountRouter.get("/dept-mapping/page", summary="分页查询部门映射", response_model=ResponseSchema[PageResultSchema[DeptMappingOutSchema]])
async def get_dept_mapping_page_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[DeptMappingQueryParam, Query()] = None,
) -> dict:
    # 分页必须用 Depends() 包成子 dependant：FastAPI 只在「该 dependant 的 query 参数恰好只有 1 个
    # 且是 BaseModel」时才把模型展开成平铺字段，同级还有 Query 标量会让 search 恒为 None（筛选静默失效）。
    result = await DeptMappingService(auth, db).page(page_no=page.page_no, page_size=page.page_size, search=search)
    return SuccessResponse(data=result, msg="查询部门映射成功")


@AccountRouter.get("/dept-mapping/list", summary="查询部门映射列表", response_model=ResponseSchema[list[DeptMappingOutSchema]])
async def get_dept_mapping_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    search: Annotated[DeptMappingQueryParam, Query()] = None,
) -> dict:
    result = await DeptMappingService(auth, db).get_list(search=search)
    return SuccessResponse(data=result, msg="查询部门映射成功")


@AccountRouter.get("/dept-mapping/detail/{id}", summary="查询部门映射详情", response_model=ResponseSchema[DeptMappingOutSchema])
async def get_dept_mapping_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="映射ID")],
) -> dict:
    result = await DeptMappingService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="查询部门映射详情成功")


@AccountRouter.post("/dept-mapping/create", summary="新增部门映射", response_model=ResponseSchema[DeptMappingOutSchema])
async def create_dept_mapping_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[DeptMappingCreateSchema, Body()],
) -> dict:
    result = await DeptMappingService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="新增部门映射成功")


@AccountRouter.put("/dept-mapping/update/{id}", summary="修改部门映射", response_model=ResponseSchema[DeptMappingOutSchema])
async def update_dept_mapping_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[DeptMappingUpdateSchema, Body()],
    id: Annotated[int, Path(description="映射ID")],
) -> dict:
    result = await DeptMappingService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改部门映射成功")


@AccountRouter.delete("/dept-mapping/delete", summary="删除部门映射")
async def delete_dept_mapping_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="映射ID列表")],
) -> dict:
    await DeptMappingService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除部门映射成功")


@AccountRouter.get("/dingtalk/user/page", summary="分页查询钉钉账号", response_model=ResponseSchema[PageResultSchema[DingtalkAccountOutSchema]])
async def get_dingtalk_account_page_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    redis: Annotated[Redis, Depends(redis_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[DingtalkAccountQueryParam, Query()] = None,
) -> dict:
    # 同上：分页走 Depends() 子 dependant，否则 search 收不到值。
    result = await DingtalkAccountService(auth, db, redis).page_oa_users(page_no=page.page_no, page_size=page.page_size, search=search)
    return SuccessResponse(data=result, msg="查询钉钉账号成功")


@AccountRouter.post("/dingtalk/match", summary="钉钉账号手工匹配", response_model=ResponseSchema[DingtalkManualMatchResultSchema])
async def match_dingtalk_account_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_internal:account:match"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    redis: Annotated[Redis, Depends(redis_getter)],
    data: Annotated[DingtalkManualMatchSchema, Body()],
) -> dict:
    result = await DingtalkAccountService(auth, db, redis).manual_match(data=data)
    return SuccessResponse(data=result, msg="钉钉账号匹配完成")
