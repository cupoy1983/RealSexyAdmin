import { request, type ExtendedRequestConfig } from "@utils";
import type { BatchConfirmResult } from "./batch";

const ORDER_API_PATH = "/ecommerce/order";
const SKU_COST_API_PATH = "/ecommerce/sku-cost";

// ── 订单(Order) ──────────────────────────────────────────────────────────

export interface OrderDetailRow {
  id?: number;
  order_id?: number;
  source_detail_id?: string;
  platform_order_no?: string;
  order_type?: string;
  detail_status?: string;
  sku_code?: string;
  main_sku_code?: string;
  sku_name?: string;
  product_name?: string;
  weight_kg?: string | number | null;
  cost_price?: string | number | null;
  ship_qty?: number;
  return_qty?: number;
  computed_product_cost?: string | number;
  computed_return_cost?: string | number;
  cost_missing?: boolean;
  paid_amount?: string | number;
  refund_amount?: string | number;
  warehouse_name?: string;
  courier_template?: string;
  tracking_no?: string;
  is_erp_shipped?: boolean;
  /** 分销订单类型：0=非分销，非 0=分销（认领弹窗靠它灰化勾选框，ADR-0036 / §26.6 第 3 条闸） */
  distribution_order_type?: number;
  /** 运费成本：null=还没定(缺账单) 0=有意按0 >0=账单实额（ADR-0034） */
  freight_cost?: string | number | null;
  pay_time?: string;
  ship_time?: string;
  finish_time?: string;
  after_sales_no?: string;
  excel_cost_price?: string | number | null;
  erp_shipped_cost?: string | number | null;
  erp_return_cost?: string | number | null;
  update_remark?: string | null;
}

export interface OrderPageQuery extends PageQuery {
  system_order_no?: string;
  store_id?: number;
  platform?: string;
  refund_status?: string;
  shua_status?: string;
  province?: string;
  city?: string;
  update_status?: string;
  ship_time?: string[];
  /** 只看缺账单：主订单下至少有一条缺账单子订单（§24，唯一入口在订单列表搜索栏） */
  freight_missing?: boolean;
}

export interface OrderTable extends BaseType {
  system_order_no?: string;
  store_id?: number;
  store_name?: string;
  platform?: string;
  order_status?: string;
  buyer_paid_amount?: string | number;
  refund_amount?: string | number;
  refund_status?: string;
  shipped_cost_amount?: string | number;
  return_cost_amount?: string | number;
  freight_income?: string | number;
  freight_cost_amount?: string | number;
  province?: string;
  city?: string;
  system_remark?: string;
  seller_remark?: string;
  is_shua_order?: boolean;
  shua_status?: string;
  data_source?: string;
  import_batch_id?: string;
}

export interface ScamApprovalDetail {
  process_instance_id: string;
  platform_order_no: string;
  amount: string | number;
}

export interface ScamReconciliationResult {
  order_id: number;
  system_order_no: string;
  platform_order_no: string;
  order_amount: string | number;
  positive_approval_amount?: string | number | null;
  negative_approval_amount?: string | number | null;
  match_status: string;
  reason?: string | null;
}

const OrderAPI = {
  listOrder(query: OrderPageQuery) {
    return request<ApiResponse<PageResult<OrderTable>>>({
      url: `${ORDER_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  /** 展开某个主订单时取它的全部子订单；「缺账单」筛选不住在这里，只在主列表搜索栏（§24） */
  listOrderDetails(orderId: number) {
    return request<ApiResponse<OrderDetailRow[]>>({
      url: `${ORDER_API_PATH}/detail/list/${orderId}`,
      method: "get",
    });
  },

  /** 确认订单批次：门禁要求严格同月的账单批次全部已确认（ADR-0033） */
  // 确认订单批次：页面用 showUpdateResult 弹带数字的汇总与告警，此处关掉拦截器默认 toast（§18.2）
  confirmOrderBatch(batchNo: string) {
    const config: ExtendedRequestConfig = {
      url: `${ORDER_API_PATH}/confirm/${batchNo}`,
      method: "post",
      showSuccessMessage: false,
    };
    return request<ApiResponse<BatchConfirmResult>>(config);
  },

  // 导入订单：汇总清单由调用方用返回值弹一次，关掉拦截器默认 toast，免得成功提示连出两条
  importOrder(formData: FormData) {
    const config: ExtendedRequestConfig = {
      url: `${ORDER_API_PATH}/import`,
      method: "post",
      data: formData,
      headers: { "Content-Type": "multipart/form-data" },
      showSuccessMessage: false,
    };
    return request<ApiResponse<string>>(config);
  },

  // 更新订单：中止/成功都用页面级 ElMessageBox 展示整段清单，故关掉拦截器默认的 toast
  updateOrder(formData: FormData) {
    const config: ExtendedRequestConfig = {
      url: `${ORDER_API_PATH}/update`,
      method: "post",
      data: formData,
      headers: { "Content-Type": "multipart/form-data" },
      showErrorMessage: false,
      showSuccessMessage: false,
    };
    return request<ApiResponse<string>>(config);
  },

  missingCostList(query: PageQuery) {
    return request<ApiResponse<PageResult<OrderDetailRow>>>({
      url: `${ORDER_API_PATH}/missing-cost/list`,
      method: "get",
      params: query,
    });
  },

  runScamReconciliation(body: { ship_month: string; approval_details: ScamApprovalDetail[] }) {
    return request<ApiResponse<ScamReconciliationResult[]>>({
      url: `${ORDER_API_PATH}/scam-reconciliation/run`,
      method: "post",
      data: body,
    });
  },
};

export default OrderAPI;

// ── 商品成本主表(SkuCost) ────────────────────────────────────────────────

export interface SkuCostPageQuery extends PageQuery, UserByQueryParams {
  main_sku_code?: string;
  sku_code?: string;
  product_name?: string;
  cost_source?: string;
  cost_missing?: boolean;
}

export interface SkuCostTable extends BaseType {
  sku_code?: string;
  main_sku_code?: string;
  sku_name?: string;
  product_name?: string;
  cost_price?: string | number | null;
  cost_source?: string;
  remark?: string;
}

export interface SkuCostForm extends BaseFormType {
  sku_code?: string;
  main_sku_code?: string;
  sku_name?: string;
  product_name?: string;
  cost_price?: string | number | null;
  cost_source?: string;
  remark?: string;
}

export const SkuCostAPI = {
  listSkuCost(query: SkuCostPageQuery) {
    return request<ApiResponse<PageResult<SkuCostTable>>>({
      url: `${SKU_COST_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailSkuCost(id: number) {
    return request<ApiResponse<SkuCostTable>>({
      url: `${SKU_COST_API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createSkuCost(body: SkuCostForm) {
    return request<ApiResponse>({
      url: `${SKU_COST_API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateSkuCost(id: number, body: SkuCostForm) {
    return request<ApiResponse>({
      url: `${SKU_COST_API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteSkuCost(body: number[]) {
    return request<ApiResponse>({
      url: `${SKU_COST_API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },
};
