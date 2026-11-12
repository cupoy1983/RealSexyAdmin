from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Security
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute

from .schema import ImportBatchOutSchema, ImportBatchQueryParam
from .service import ImportBatchService

BatchRouter = APIRouter(route_class=OperationLogRoute, prefix="/batch", tags=["导入批次"])


@BatchRouter.get("/list", summary="分页查询导入批次", response_model=ResponseSchema[PageResultSchema[ImportBatchOutSchema]])
async def get_batch_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[ImportBatchQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[ImportBatchOutSchema] = await ImportBatchService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询导入批次列表成功")


@BatchRouter.get("/detail/{id}", summary="获取导入批次详情", response_model=ResponseSchema[ImportBatchOutSchema])
async def get_batch_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="批次ID", ge=1)],
) -> JSONResponse:
    result: ImportBatchOutSchema = await ImportBatchService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取导入批次详情成功")
