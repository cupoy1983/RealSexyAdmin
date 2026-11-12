from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, Form, Path, Query, Security, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema, PageResultSchema, PaginationQueryParam
from app.core.dependencies import AuthPermission, db_getter
from app.core.router_class import OperationLogRoute
from app.plugin.module_ecommerce.common.import_batch.schema import (
    BatchConfirmResultSchema,
    BillBatchCountSchema,
    BillBatchDeclareSchema,
    BillGateGapSchema,
)

from .schema import (
    BillImportResultSchema,
    BillParseRuleCreateSchema,
    BillParseRuleOutSchema,
    BillParseRuleQueryParam,
    BillParseRuleUpdateSchema,
    CourierBillOutSchema,
    CourierBillQueryParam,
    CourierBillRestoreSchema,
)
from .service import BillParseRuleService, LogisticsService

BillParseRuleRouter = APIRouter(route_class=OperationLogRoute, prefix="/bill-parse-rule", tags=["账单解析规则"])
LogisticsRouter = APIRouter(route_class=OperationLogRoute, prefix="/logistics", tags=["快递账单"])


# ── 账单解析规则(BillParseRule) ──────────────────────────────────────────


@BillParseRuleRouter.get("/detail/{id}", summary="获取账单解析规则详情", response_model=ResponseSchema[BillParseRuleOutSchema])
async def get_bill_parse_rule_detail_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:bill_parse_rule:detail"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="规则ID", ge=1)],
) -> JSONResponse:
    result: BillParseRuleOutSchema = await BillParseRuleService(auth, db).detail(id=id)
    return SuccessResponse(data=result, msg="获取账单解析规则详情成功")


@BillParseRuleRouter.get("/list", summary="分页查询账单解析规则", response_model=ResponseSchema[PageResultSchema[BillParseRuleOutSchema]])
async def get_bill_parse_rule_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:bill_parse_rule:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[BillParseRuleQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[BillParseRuleOutSchema] = await BillParseRuleService(auth, db).page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询账单解析规则列表成功")


@BillParseRuleRouter.post(
    "/create", status_code=status.HTTP_201_CREATED, summary="创建账单解析规则", response_model=ResponseSchema[BillParseRuleOutSchema],
)
async def create_bill_parse_rule_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:bill_parse_rule:create"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BillParseRuleCreateSchema, Body(description="创建参数")],
) -> JSONResponse:
    result: BillParseRuleOutSchema = await BillParseRuleService(auth, db).create(data=data)
    return SuccessResponse(data=result, msg="创建账单解析规则成功")


@BillParseRuleRouter.put("/update/{id}", summary="修改账单解析规则", response_model=ResponseSchema[BillParseRuleOutSchema])
async def update_bill_parse_rule_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:bill_parse_rule:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    id: Annotated[int, Path(description="规则ID", ge=1)],
    data: Annotated[BillParseRuleUpdateSchema, Body(description="修改参数")],
) -> JSONResponse:
    result: BillParseRuleOutSchema = await BillParseRuleService(auth, db).update(id=id, data=data)
    return SuccessResponse(data=result, msg="修改账单解析规则成功")


@BillParseRuleRouter.delete("/delete", summary="删除账单解析规则", response_model=ResponseSchema[None])
async def delete_bill_parse_rule_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:bill_parse_rule:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="ID列表")],
) -> JSONResponse:
    await BillParseRuleService(auth, db).delete(ids=ids)
    return SuccessResponse(msg="删除账单解析规则成功")


# ── 快递账单(CourierBill) ────────────────────────────────────────────────


@LogisticsRouter.post("/bill/import", summary="导入快递账单并当场判定异常（§5.2）", response_model=ResponseSchema[BillImportResultSchema])
async def import_courier_bill_controller(
    file: Annotated[UploadFile, File(description="账单 Excel 文件")],
    bill_type: Annotated[str, Form(description="账单类型编码（对应解析规则）")],
    bill_month: Annotated[str, Form(description="出账月份(YYYY-MM)；可导入月份由前端限定为本月往前 3 个整月，接口只校验格式与不选未来月")],
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:logistics:import"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    result: BillImportResultSchema = await LogisticsService(auth, db).import_bill(
        file=file, bill_type=bill_type, bill_month=bill_month,
    )
    return SuccessResponse(data=result, msg=result.message)


@LogisticsRouter.get("/bill/list", summary="分页查询账单行（账单详情：一票一行）", response_model=ResponseSchema[PageResultSchema[CourierBillOutSchema]])
async def get_courier_bill_list_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:logistics:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[CourierBillQueryParam, Query()],
) -> JSONResponse:
    result: PageResultSchema[CourierBillOutSchema] = await LogisticsService(auth, db).courier_bill_page(
        page_no=page.page_no, page_size=page.page_size, search=search, order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="查询账单列表成功")


@LogisticsRouter.get(
    "/bill/batch-count/{batch_no}", summary="账单批次各状态行数（详情页顶部计数）", response_model=ResponseSchema[BillBatchCountSchema],
)
async def get_bill_batch_count_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:logistics:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    batch_no: Annotated[str, Path(description="批次号，如 bill-yto-2026-09")],
) -> JSONResponse:
    result: BillBatchCountSchema = await LogisticsService(auth, db).bill_batch_counts(batch_no)
    return SuccessResponse(data=result, msg="查询账单批次统计成功")


@LogisticsRouter.put("/bill/restore", summary="虚假/重复行人工改对后置为已恢复（虚假行须先认领订单）", response_model=ResponseSchema[int])
async def restore_courier_bill_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:logistics:update"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[CourierBillRestoreSchema, Body(description="账单行ID、说明与认领订单信息")],
) -> JSONResponse:
    result: int = await LogisticsService(auth, db).restore_bill_rows(
        ids=data.ids, remark=data.remark, system_order_no=data.system_order_no, detail_ids=data.detail_ids,
    )
    return SuccessResponse(data=result, msg=f"已恢复 {result} 行")


@LogisticsRouter.delete("/bill/delete", summary="逻辑删除虚假/重复账单行（仅未确认批次；删空则批次一并删除）", response_model=ResponseSchema[str])
async def delete_courier_bill_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:logistics:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    ids: Annotated[list[int], Body(description="账单行ID列表")],
) -> JSONResponse:
    result: str = await LogisticsService(auth, db).delete_bill_rows(ids)
    return SuccessResponse(data=result, msg="删除成功")


@LogisticsRouter.post(
    "/bill/confirm/{batch_no}", summary="确认账单批次并覆盖式回写运费成本", response_model=ResponseSchema[BatchConfirmResultSchema],
)
async def confirm_bill_batch_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:confirm"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    batch_no: Annotated[str, Path(description="账单批次号，如 bill-yto-2026-09")],
) -> JSONResponse:
    result: BatchConfirmResultSchema = await LogisticsService(auth, db).confirm_bill_batch(batch_no)
    return SuccessResponse(data=result, msg=result.message)


@LogisticsRouter.delete(
    "/bill/batch-delete/{batch_no}", summary="作废未确认的账单批次（连账单行一起物理删除）", response_model=ResponseSchema[str],
)
async def discard_bill_batch_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:delete"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    batch_no: Annotated[str, Path(description="账单批次号，如 bill-yto-2026-09")],
) -> JSONResponse:
    result: str = await LogisticsService(auth, db).discard_bill_batch(batch_no)
    return SuccessResponse(data=result, msg="账单批次已作废")


@LogisticsRouter.post("/bill/declare", summary="声明本月无账单（生成零行未确认批次）", response_model=ResponseSchema[str])
async def declare_empty_bill_batches_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:declare"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    data: Annotated[BillBatchDeclareSchema, Body(description="出账月份与要声明的账单类型")],
) -> JSONResponse:
    result: str = await LogisticsService(auth, db).declare_empty_batches(data.bill_month, data.bill_types)
    return SuccessResponse(data=result, msg="空账单批次声明成功")


@LogisticsRouter.get(
    "/bill/gate/{bill_month}", summary="订单批次确认门禁预检：本月账单缺哪两份（只读）", response_model=ResponseSchema[BillGateGapSchema],
)
async def get_bill_gate_gaps_controller(
    auth: Annotated[AuthSchema, Security(AuthPermission(["module_ecommerce:batch:query"]))],
    db: Annotated[AsyncSession, Depends(db_getter)],
    bill_month: Annotated[str, Path(description="出账月份(YYYY-MM)，与订单批次的发货月份同月比对")],
) -> JSONResponse:
    result: BillGateGapSchema = await LogisticsService(auth, db).bill_gate_gaps(bill_month)
    return SuccessResponse(data=result, msg="查询账单门禁缺口成功")
