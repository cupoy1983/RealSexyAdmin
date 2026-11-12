from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Security, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import (
    StoreBatchStatusSchema,
    StoreBatchTagSchema,
    StoreCreateSchema,
    StoreOutSchema,
    StoreQueryParam,
    StoreUpdateSchema,
    TagCreateSchema,
    TagOutSchema,
    TagQueryParam,
    TagUpdateSchema,
)
from .service import StoreService, TagService

StoreRouter = APIRouter(route_class=OperationLogRoute, prefix="/store", tags=["店铺管理"])
TagRouter = APIRouter(route_class=OperationLogRoute, prefix="/tag", tags=["负责人管理"])


# ── 店铺(Store) ──────────────────────────────────────────────────────────


@StoreRouter.get("/detail/{id}", summary="获取店铺详情", response_model=ResponseSchema[StoreOutSchema])
async def get_store_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="店铺ID", ge=1)],
) -> JSONResponse:
    result: StoreOutSchema = await StoreService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取店铺详情成功")


@StoreRouter.get("/list", summary="分页查询店铺", response_model=ResponseSchema[PageResultSchema[StoreOutSchema]])
async def get_store_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[StoreQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[StoreOutSchema] = await StoreService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询店铺列表成功")


@StoreRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建店铺", response_model=ResponseSchema[StoreOutSchema])
async def create_store_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[StoreCreateSchema, Body(description="创建参数")],
) -> JSONResponse:
    result: StoreOutSchema = await StoreService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建店铺成功")


@StoreRouter.put("/update/{id}", summary="修改店铺", response_model=ResponseSchema[StoreOutSchema])
async def update_store_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="店铺ID", ge=1)],
    data: Annotated[StoreUpdateSchema, Body(description="修改参数")],
) -> JSONResponse:
    result: StoreOutSchema = await StoreService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改店铺成功")


@StoreRouter.put("/batch/tags", summary="批量修改店铺负责人", response_model=ResponseSchema[None])
async def batch_set_store_tags_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[StoreBatchTagSchema, Body(description="批量修改负责人参数")],
) -> JSONResponse:
    await StoreService(auth, db).batch_set_tags(ids=data.ids, tag_ids=data.tag_ids)
    return SuccessResponse(msg="批量修改负责人成功")


@StoreRouter.put("/batch/status", summary="批量设置店铺状态", response_model=ResponseSchema[None])
async def batch_set_store_status_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[StoreBatchStatusSchema, Body(description="批量设置状态参数")],
) -> JSONResponse:
    await StoreService(auth, db).batch_set_status(ids=data.ids, status=data.status)
    return SuccessResponse(msg="批量设置状态成功")


@StoreRouter.delete("/delete", summary="删除店铺", response_model=ResponseSchema[None])
async def delete_store_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:store:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await StoreService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除店铺成功")


# ── 负责人(Tag) ──────────────────────────────────────────────────────────


@TagRouter.get("/detail/{id}", summary="获取负责人详情", response_model=ResponseSchema[TagOutSchema])
async def get_tag_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:tag:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="负责人ID", ge=1)],
) -> JSONResponse:
    result: TagOutSchema = await TagService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取负责人详情成功")


@TagRouter.get("/list", summary="分页查询负责人", response_model=ResponseSchema[PageResultSchema[TagOutSchema]])
async def get_tag_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:tag:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[TagQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[TagOutSchema] = await TagService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询负责人列表成功")


@TagRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建负责人", response_model=ResponseSchema[TagOutSchema])
async def create_tag_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:tag:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[TagCreateSchema, Body(description="创建参数")],
) -> JSONResponse:
    result: TagOutSchema = await TagService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建负责人成功")


@TagRouter.put("/update/{id}", summary="修改负责人", response_model=ResponseSchema[TagOutSchema])
async def update_tag_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:tag:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="负责人ID", ge=1)],
    data: Annotated[TagUpdateSchema, Body(description="修改参数")],
) -> JSONResponse:
    result: TagOutSchema = await TagService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改负责人成功")


@TagRouter.delete("/delete", summary="删除负责人", response_model=ResponseSchema[None])
async def delete_tag_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:tag:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await TagService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除负责人成功")
