import { request, type ExtendedRequestConfig } from "@utils";
import type { BatchConfirmResult } from "./batch";

const BILL_PARSE_RULE_API_PATH = "/ecommerce/bill-parse-rule";
const LOGISTICS_API_PATH = "/ecommerce/logistics";

// ── 账单解析规则(BillParseRule) ──────────────────────────────────────────

/** 账单 Excel 列映射：五项表头全部必填（ADR-0029，后端建档即校验）。
 *  字段留成可选是为了容纳「编辑中的半成品」与收紧之前建档的旧规则。 */
export interface BillColumnMapping {
  waybill_no?: string;
  bill_date?: string;
  bill_weight?: string;
  bill_other_fee?: string;
  receiver_province?: string | null;
}

export interface BillParseRulePageQuery extends PageQuery, UserByQueryParams {
  bill_type?: string;
  carrier_name?: string;
  status?: number;
}

export interface BillParseRuleTable extends BaseType {
  bill_type?: string;
  carrier_name?: string;
  column_mapping?: BillColumnMapping;
  waybill_fee?: string | number;
  status?: number;
}

export interface BillParseRuleForm extends BaseFormType {
  bill_type?: string;
  carrier_name?: string;
  column_mapping?: BillColumnMapping;
  /** 面单费：后端已改为必填（§25 AL1）——留空/null 直接 422，0 是合法值表示这家快递不收面单费。
   *  类型仍留可选，是为了容纳弹窗里「编辑中的空值」这一中间态；必填由表单规则 + 后端一起兜。 */
  waybill_fee?: string | number;
  status?: number;
}

export const BillParseRuleAPI = {
  listBillParseRule(query: BillParseRulePageQuery) {
    return request<ApiResponse<PageResult<BillParseRuleTable>>>({
      url: `${BILL_PARSE_RULE_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBillParseRule(id: number) {
    return request<ApiResponse<BillParseRuleTable>>({
      url: `${BILL_PARSE_RULE_API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createBillParseRule(body: BillParseRuleForm) {
    return request<ApiResponse>({
      url: `${BILL_PARSE_RULE_API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateBillParseRule(id: number, body: BillParseRuleForm) {
    return request<ApiResponse>({
      url: `${BILL_PARSE_RULE_API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteBillParseRule(body: number[]) {
    return request<ApiResponse>({
      url: `${BILL_PARSE_RULE_API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },
};

// ── 快递账单(CourierBill)：一票一行，判定与摊分结果都落在行上（ADR-0034） ──

/** 账单行状态：原件行=matched/weight_province/restored，虚假重复需人工处理（§5.2） */
export type BillRowStatus = "matched" | "fake" | "duplicate" | "weight_province" | "restored";

export const BILL_STATUS_OPTIONS: { label: string; value: BillRowStatus }[] = [
  { label: "已匹配", value: "matched" },
  { label: "重量省份异常", value: "weight_province" },
  { label: "已恢复", value: "restored" },
  { label: "虚假异常", value: "fake" },
  { label: "重复异常", value: "duplicate" },
];

export interface BillBatchCount {
  total: number;
  matched: number;
  duplicate: number;
  fake: number;
  weight_province: number;
  restored: number;
  /** 各票命中子订单的最早发货月 → 票数（按运单号去重，挂不上订单的票不计；现算不落库） */
  ship_month_distribution?: Record<string, number>;
}

/** 订单批次确认门禁的缺口预检：① 未确认的账单批次 ② 本月完全没批次的账单类型 ③ 启用中的解析规则为空 ④ 挂不到批次的脏数据行数（只报警，不进门禁判定） */
export interface BillGateGap {
  bill_month: string;
  unconfirmed_batches: string[];
  missing_types: string[];
  /** 一条启用中的账单解析规则都没有：账单是靠规则读进来的，空规则集等于没人核对（Z5） */
  no_active_rules: boolean;
  /** 该月带着批次号、批次表里却已没有这一批的账单行数。只有手工删过批次才会出现，是脏数据不是缺口（ADR-0033 2026-10-29） */
  orphan_bill_rows: number;
}

export interface BillImportResult {
  batch_no: string;
  row_count: number;
  matched: number;
  weight_province: number;
  duplicate: number;
  fake: number;
  message: string;
}

export interface CourierBillPageQuery extends PageQuery {
  import_batch_id?: string;
  bill_month?: string;
  bill_type?: string;
  waybill_no?: string;
  store_id?: number;
  status?: BillRowStatus;
}

export interface CourierBillTable extends BaseType {
  import_batch_id?: string | null;
  bill_month?: string;
  bill_date?: string | null;
  bill_type?: string;
  waybill_no?: string;
  bill_weight?: string | number | null;
  receiver_province?: string | null;
  waybill_fee?: string | number;
  bill_other_fee?: string | number;
  actual_freight?: string | number;
  store_id?: number | null;
  store_name?: string | null;
  status?: BillRowStatus;
  anomaly_remark?: string | null;
  system_order_nos?: string;
  detail_count?: number;
}

const LogisticsAPI = {
  /** 导入即判定：可导入月份窗口由前端限定（本月往前 3 个整月），接口只校验格式与不选未来月。
   *  结果文案由调用方用返回值弹一次，故关掉拦截器的默认 toast——否则成功提示会连出两条。 */
  importBill(formData: FormData) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/import`,
      method: "post",
      data: formData,
      headers: { "Content-Type": "multipart/form-data" },
      showSuccessMessage: false,
    };
    return request<ApiResponse<BillImportResult>>(config);
  },

  listCourierBill(query: CourierBillPageQuery) {
    return request<ApiResponse<PageResult<CourierBillTable>>>({
      url: `${LOGISTICS_API_PATH}/bill/list`,
      method: "get",
      params: query,
    });
  },

  billBatchCount(batchNo: string) {
    return request<ApiResponse<BillBatchCount>>({
      url: `${LOGISTICS_API_PATH}/bill/batch-count/${batchNo}`,
      method: "get",
    });
  },

  /**
   * 虚假/重复行人工改对后置「已恢复」，开始计入成本。
   * 虚假行必须带 system_order_no + detail_ids「认领订单」（§22.3）：把本票运单号订正进勾选的子订单行；
   * 重复行不许带单号。**一条账单行只有一次认领机会**：已恢复是终点，不能再改、不能再退回（§23）。
   */
  restoreCourierBill(body: {
    ids: number[];
    remark?: string;
    system_order_no?: string;
    detail_ids?: number[];
  }) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/restore`,
      method: "put",
      data: body,
      showSuccessMessage: false,
    };
    return request<ApiResponse<number>>(config);
  },

  /** 逻辑删除：只有未确认批次的虚假/重复行允许删；删到批次空了连批次一并物理删除（§22.6） */
  deleteCourierBill(body: number[]) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/delete`,
      method: "delete",
      data: body,
      showSuccessMessage: false,
    };
    return request<ApiResponse<string>>(config);
  },

  /** 确认账单批次：覆盖式回写运费成本，确认后不可变（ADR-0033/0034） */
  confirmBillBatch(batchNo: string) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/confirm/${batchNo}`,
      method: "post",
      showSuccessMessage: false,
    };
    return request<ApiResponse<BatchConfirmResult>>(config);
  },

  /** 作废未确认的账单批次：连账单行一起物理删除，同月同类型随即可重导（ADR-0033） */
  discardBillBatch(batchNo: string) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/batch-delete/${batchNo}`,
      method: "delete",
      showSuccessMessage: false,
    };
    return request<ApiResponse<string>>(config);
  },

  /** 声明本月某几家快递没有账单：生成零行未确认批次，订单批次门禁才认这个月齐了 */
  declareEmptyBatches(body: { bill_month: string; bill_types: string[] }) {
    const config: ExtendedRequestConfig = {
      url: `${LOGISTICS_API_PATH}/bill/declare`,
      method: "post",
      data: body,
      showSuccessMessage: false,
    };
    return request<ApiResponse<string>>(config);
  },

  /** 只读预检：确认订单批次前先看这个月的账单缺哪两份 */
  billGateGaps(billMonth: string) {
    return request<ApiResponse<BillGateGap>>({
      url: `${LOGISTICS_API_PATH}/bill/gate/${billMonth}`,
      method: "get",
    });
  },
};

export default LogisticsAPI;
