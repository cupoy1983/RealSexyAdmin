import { request } from "@utils";

const API_PATH = "/internal/account";

const AccountAPI = {
  /** 部门映射：分页 */
  deptMappingPage(query?: DeptMappingPageQuery) {
    return request<ApiResponse<PageResult<DeptMappingTable>>>({
      url: `${API_PATH}/dept-mapping/page`,
      method: "get",
      params: query,
    });
  },

  /** 部门映射：列表 */
  deptMappingList(query?: DeptMappingPageQuery) {
    return request<ApiResponse<DeptMappingTable[]>>({
      url: `${API_PATH}/dept-mapping/list`,
      method: "get",
      params: query,
    });
  },

  deptMappingDetail(id: number) {
    return request<ApiResponse<DeptMappingTable>>({
      url: `${API_PATH}/dept-mapping/detail/${id}`,
      method: "get",
    });
  },

  deptMappingCreate(body: DeptMappingForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/dept-mapping/create`,
      method: "post",
      data: body,
    });
  },

  deptMappingUpdate(id: number, body: DeptMappingForm) {
    return request<ApiResponse>({
      url: `${API_PATH}/dept-mapping/update/${id}`,
      method: "put",
      data: body,
    });
  },

  deptMappingDelete(body: number[]) {
    return request<ApiResponse>({
      url: `${API_PATH}/dept-mapping/delete`,
      method: "delete",
      data: body,
    });
  },

  /** 钉钉账号 Tab：以 OA 用户为主体分页，带绑定状态 */
  dingtalkUserPage(query?: DingtalkAccountPageQuery) {
    return request<ApiResponse<PageResult<DingtalkAccountTable>>>({
      url: `${API_PATH}/dingtalk/user/page`,
      method: "get",
      params: query,
    });
  },

  /** 钉钉账号手工匹配（未绑定时可用一次，绑定后不允许再匹配） */
  dingtalkMatch(body: { oa_user_id: number }) {
    return request<ApiResponse<DingtalkMatchResult>>({
      url: `${API_PATH}/dingtalk/match`,
      method: "post",
      data: body,
    });
  },
};

export default AccountAPI;

export interface DeptMappingTable {
  id?: number;
  oa_dept_id?: number;
  oa_dept_name?: string;
  dingtalk_dept_id?: string;
  dingtalk_dept_name?: string;
  status?: number;
}

export interface DeptMappingForm {
  oa_dept_id: number;
  dingtalk_dept_id: string;
  dingtalk_dept_name?: string;
  status?: number;
}

export interface DeptMappingPageQuery {
  page_no?: number;
  page_size?: number;
  oa_dept_id?: number;
  dingtalk_dept_id?: string;
  status?: number;
}

export interface DingtalkAccountTable {
  oa_user_id?: number;
  username?: string;
  name?: string;
  mobile?: string;
  dept_id?: number;
  dept_name?: string;
  user_status?: number;
  bound?: boolean;
  binding_id?: number;
  external_user_id?: string;
  external_name?: string;
  bound_time?: string;
  can_match?: boolean;
  match_message?: string;
}

export interface DingtalkAccountPageQuery {
  page_no?: number;
  page_size?: number;
  name?: string;
  mobile?: string;
  dept_id?: number;
  bound?: boolean;
}

export interface DingtalkMatchResult {
  oa_user_id?: number;
  external_user_id?: string;
  created?: boolean;
  message?: string;
}
