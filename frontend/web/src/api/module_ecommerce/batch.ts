import { request } from "@utils";

const BATCH_API_PATH = "/ecommerce/batch";

// ── 导入批次(ImportBatch)：订单导入与账单导入共用，确认状态即数据齐全度判据（ADR-0033） ──

export type BatchFileCategory = "order" | "bill";
export type BatchStatus = "unconfirmed" | "confirmed";

/** 批次确认结果（订单批次与账单批次共用端点出参，ADR-0033） */
export interface BatchConfirmResult {
  batch_no: string;
  message: string;
  warning?: string | null;
}

export interface ImportBatchPageQuery extends PageQuery, UserByQueryParams {
  batch_no?: string;
  file_category?: BatchFileCategory;
  sub_category?: string;
  batch_month?: string;
  status?: BatchStatus;
}

export interface ImportBatchTable extends BaseType {
  batch_no?: string;
  file_category?: BatchFileCategory;
  sub_category?: string | null;
  batch_month?: string;
  status?: BatchStatus;
  confirmed_time?: string | null;
  row_count?: number;
  order_count?: number;
  remark?: string | null;
}

const BatchAPI = {
  listBatch(query: ImportBatchPageQuery) {
    return request<ApiResponse<PageResult<ImportBatchTable>>>({
      url: `${BATCH_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailBatch(id: number) {
    return request<ApiResponse<ImportBatchTable>>({
      url: `${BATCH_API_PATH}/detail/${id}`,
      method: "get",
    });
  },
};

export default BatchAPI;
