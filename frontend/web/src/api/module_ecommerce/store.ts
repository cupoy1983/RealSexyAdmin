import { request, type ExtendedRequestConfig } from "@utils";

const STORE_API_PATH = "/ecommerce/store";
const TAG_API_PATH = "/ecommerce/tag";

export interface TagSimple {
  id?: number;
  tag_name?: string;
}

// ── 店铺(Store) ──────────────────────────────────────────────────────────

export interface StorePageQuery extends PageQuery, UserByQueryParams {
  kuaimai_shop_id?: string;
  store_name?: string;
  platform?: string;
  status?: number;
}

export interface StoreTable extends BaseType {
  kuaimai_shop_id?: string;
  store_name?: string;
  platform?: string;
  status?: number;
  tags?: TagSimple[];
}

export interface StoreForm extends BaseFormType {
  kuaimai_shop_id?: string;
  store_name?: string;
  platform?: string;
  status?: number;
  tag_ids?: number[];
}

const StoreAPI = {
  listStore(query: StorePageQuery) {
    return request<ApiResponse<PageResult<StoreTable>>>({
      url: `${STORE_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailStore(id: number) {
    return request<ApiResponse<StoreTable>>({
      url: `${STORE_API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createStore(body: StoreForm) {
    return request<ApiResponse>({
      url: `${STORE_API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateStore(id: number, body: StoreForm) {
    return request<ApiResponse>({
      url: `${STORE_API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteStore(body: number[]) {
    return request<ApiResponse>({
      url: `${STORE_API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },

  // 批量改负责人：页面自己弹「批量修改负责人成功」，关掉拦截器默认 toast，免得提示连出两条（§18.2）
  batchSetTags(body: { ids: number[]; tag_ids: number[] }) {
    const config: ExtendedRequestConfig = {
      url: `${STORE_API_PATH}/batch/tags`,
      method: "put",
      data: body,
      showSuccessMessage: false,
    };
    return request<ApiResponse>(config);
  },

  // 批量设状态：页面自己弹「批量设置状态成功」，同上关掉拦截器默认 toast（§18.2）
  batchSetStatus(body: { ids: number[]; status: number }) {
    const config: ExtendedRequestConfig = {
      url: `${STORE_API_PATH}/batch/status`,
      method: "put",
      data: body,
      showSuccessMessage: false,
    };
    return request<ApiResponse>(config);
  },
};

export default StoreAPI;

// ── 负责人(Tag) ──────────────────────────────────────────────────────────

export interface TagPageQuery extends PageQuery, UserByQueryParams {
  tag_name?: string;
  status?: number;
}

export interface TagTable extends BaseType {
  tag_name?: string;
  description?: string;
  status?: number;
}

export interface TagForm extends BaseFormType {
  tag_name?: string;
  description?: string;
  status?: number;
}

export const TagAPI = {
  listTag(query: TagPageQuery) {
    return request<ApiResponse<PageResult<TagTable>>>({
      url: `${TAG_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detailTag(id: number) {
    return request<ApiResponse<TagTable>>({
      url: `${TAG_API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  createTag(body: TagForm) {
    return request<ApiResponse>({
      url: `${TAG_API_PATH}/create`,
      method: "post",
      data: body,
    });
  },

  updateTag(id: number, body: TagForm) {
    return request<ApiResponse>({
      url: `${TAG_API_PATH}/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deleteTag(body: number[]) {
    return request<ApiResponse>({
      url: `${TAG_API_PATH}/delete`,
      method: "delete",
      data: body,
    });
  },
};
