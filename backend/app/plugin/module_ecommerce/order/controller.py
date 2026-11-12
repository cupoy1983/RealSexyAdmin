from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, Security, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ErrorResponse, ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute
from app.plugin.module_ecommerce.common.import_batch.schema import BatchConfirmResultSchema

from .schema import (
    OrderDetailOutSchema,
    OrderOutSchema,
    OrderQueryParam,
    ScamApprovalDetailSchema,
    ScamReconciliationResultSchema,
    SkuCostCreateSchema,
    SkuCostOutSchema,
    SkuCostQueryParam,
    SkuCostUpdateSchema,
)
from .service import OrderService, OrderUpdateResult, SkuCostService

OrderRouter = APIRouter(route_class=OperationLogRoute, prefix="/order", tags=["订单与商品成本"])
SkuCostRouter = APIRouter(route_class=OperationLogRoute, prefix="/sku-cost", tags=["商品成本"])


# ── 订单(Order) ──────────────────────────────────────────────────────────


@OrderRouter.get(
    "/detail/list/{order_id}",
    summary="获取订单的子订单列表（列表页行展开懒加载）",
    response_model=ResponseSchema[list[OrderDetailOutSchema]],
)
async def get_order_detail_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    order_id: Annotated[int, Path(description="主订单ID", ge=1)],
) -> JSONResponse:
    result: list[OrderDetailOutSchema] = await OrderService(auth, db).details_by_order(order_id=order_id)
    return SuccessResponse(data=result, msg="获取子订单列表成功")


@OrderRouter.get("/list", summary="分页查询订单", response_model=ResponseSchema[PageResultSchema[OrderOutSchema]])
async def get_order_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[OrderQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[OrderOutSchema] = await OrderService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询订单列表成功")


@OrderRouter.post("/import", summary="导入订单Excel（第一期默认数据源，ADR-0027）", response_model=ResponseSchema[str])
async def import_order_controller(
    file: Annotated[UploadFile, File(description="订单 Excel 文件")],
    ship_month: Annotated[str, Form(description="发货月份(YYYY-MM)，即本次导入批次")],
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:import"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result: str = await OrderService(auth, db).import_orders(ship_month=ship_month, file=file)
    return SuccessResponse(data=result, msg="导入订单成功")


@OrderRouter.post("/update", summary="更新订单Excel（增量比对+更新备注留痕，ADR-0032）", response_model=ResponseSchema[str])
async def update_order_controller(
    file: Annotated[UploadFile, File(description="订单 Excel 文件")],
    ship_month: Annotated[str, Form(description="发货月份(YYYY-MM)，即本次更新批次")],
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result: OrderUpdateResult = await OrderService(auth, db).update_orders(ship_month=ship_month, file=file)
    if not result.ok:
        # 中止不能抛异常：请求级事务回滚会连中止备注一起丢掉，改用错误响应（事务正常提交）。
        # HTTP 状态仍用 200，靠业务码表达错误：前端 http 拦截器对 HTTP 400 会无条件弹 toast，
        # 而 200+业务错误码可通过 showErrorMessage:false 抑制，改由页面用 ElMessageBox 展示整段中止清单。
        return ErrorResponse(data=result.msg, msg=result.msg, status_code=status.HTTP_200_OK)
    return SuccessResponse(data=result.msg, msg="更新订单成功")


@OrderRouter.post(
    "/confirm/{batch_no}",
    summary="确认订单批次（门禁：同月账单批次须全部已确认，ADR-0033）",
    response_model=ResponseSchema[BatchConfirmResultSchema],
)
async def confirm_order_batch_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:confirm"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    batch_no: Annotated[str, Path(description="订单批次号(order-YYYY-MM)")],
) -> JSONResponse:
    result: BatchConfirmResultSchema = await OrderService(auth, db).confirm_order_batch(batch_no=batch_no)
    return SuccessResponse(data=result, msg="确认订单批次成功")


@OrderRouter.get(
    "/missing-cost/list",
    summary="缺失成本待补充清单",
    response_model=ResponseSchema[PageResultSchema[OrderDetailOutSchema]],
)
async def get_order_missing_cost_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
) -> JSONResponse:
    result: PageResultSchema[OrderDetailOutSchema] = await OrderService(auth, db).missing_cost_list(
        page_no=page.page_no, page_size=page.page_size, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询缺失成本清单成功")


@OrderRouter.post(
    "/scam-reconciliation/run",
    summary="刷单订单与销量权重审批明细逐笔核对（ADR-0028）",
    response_model=ResponseSchema[list[ScamReconciliationResultSchema]],
)
async def run_scam_reconciliation_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:order:scam_reconciliation"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ship_month: Annotated[str, Body(description="发货月份(YYYY-MM)")],
    approval_details: Annotated[
        list[ScamApprovalDetailSchema], Body(description="本月销量权重审批明细（正向/负向混合，已从钉钉拉取并解析）")
    ],
) -> JSONResponse:
    result: list[ScamReconciliationResultSchema] = await OrderService(auth, db).reconcile_scam_orders(
        ship_month=ship_month, approval_details=approval_details,
    )
    return SuccessResponse(data=result, msg="刷单核对完成")


# ── 商品成本主表(SkuCost) ────────────────────────────────────────────────


@SkuCostRouter.get("/detail/{id}", summary="获取商品成本详情", response_model=ResponseSchema[SkuCostOutSchema])
async def get_sku_cost_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:sku_cost:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="商品成本ID", ge=1)],
) -> JSONResponse:
    result: SkuCostOutSchema = await SkuCostService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取商品成本详情成功")


@SkuCostRouter.get("/list", summary="分页查询商品成本", response_model=ResponseSchema[PageResultSchema[SkuCostOutSchema]])
async def get_sku_cost_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:sku_cost:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[SkuCostQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[SkuCostOutSchema] = await SkuCostService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询商品成本列表成功")


@SkuCostRouter.post("/create", status_code=status.HTTP_201_CREATED, summary="创建商品成本", response_model=ResponseSchema[SkuCostOutSchema])
async def create_sku_cost_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:sku_cost:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[SkuCostCreateSchema, Body(description="创建参数")],
) -> JSONResponse:
    result: SkuCostOutSchema = await SkuCostService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建商品成本成功")


@SkuCostRouter.put("/update/{id}", summary="修改商品成本", response_model=ResponseSchema[SkuCostOutSchema])
async def update_sku_cost_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:sku_cost:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="商品成本ID", ge=1)],
    data: Annotated[SkuCostUpdateSchema, Body(description="修改参数")],
) -> JSONResponse:
    result: SkuCostOutSchema = await SkuCostService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改商品成本成功")


@SkuCostRouter.delete("/delete", summary="删除商品成本", response_model=ResponseSchema[None])
async def delete_sku_cost_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:sku_cost:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await SkuCostService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除商品成本成功")
