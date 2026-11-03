import { request } from "@utils";

// 薪资考勤页面下各 Tab 的后端接口地址。
// 按 RealSexyAdmin 的约定（参照 module_ecommerce/store.ts）：Tab 承载的业务各自
// 拥有扁平的接口路径，不嵌套在页面容器名下面，将来「薪资绩效」接入时直接加一行即可。
const ATTENDANCE_API_PATH = "/internal/attendance";

const AttendanceAPI = {
  page(query?: MonthlyReportPageQuery) {
    return request<ApiResponse<PageResult<MonthlyReportTable>>>({
      url: `${ATTENDANCE_API_PATH}/page`,
      method: "get",
      params: query,
    });
  },

  list(query?: MonthlyReportPageQuery) {
    return request<ApiResponse<MonthlyReportTable[]>>({
      url: `${ATTENDANCE_API_PATH}/list`,
      method: "get",
      params: query,
    });
  },

  detail(id: number) {
    return request<ApiResponse<MonthlyReportTable>>({
      url: `${ATTENDANCE_API_PATH}/detail/${id}`,
      method: "get",
    });
  },

  /** 某员工某月的日明细（报表「查看明细」弹窗用） */
  dailyList(params: { oa_user_id: number; month: string }) {
    return request<ApiResponse<DailyReportTable[]>>({
      url: `${ATTENDANCE_API_PATH}/daily-list`,
      method: "get",
      params,
    });
  },

  /** 上个月的同步状态，页面据此决定「手动拉取」按钮显不显示 */
  syncState() {
    return request<ApiResponse<MonthlyReportSyncState>>({
      url: `${ATTENDANCE_API_PATH}/sync-state`,
      method: "get",
    });
  },

  /**
   * 手动拉取上个月的考勤数据。
   * 与每月 7 号的自动任务互斥：谁先跑谁生效，拉过之后按钮/自动任务都不再重复拉。
   * 要打多次钉钉接口，人多时较慢，调用方要给 loading 并禁用按钮防止重复点击。
   */
  syncPreviousMonth() {
    return request<ApiResponse<MonthlyReportSyncResult>>({
      url: `${ATTENDANCE_API_PATH}/sync`,
      method: "post",
    });
  },

  /** 可安排休息日的考勤组下拉选项 */
  restDayGroups() {
    return request<ApiResponse<AttendanceGroupOption[]>>({
      url: `${ATTENDANCE_API_PATH}/rest-day/groups`,
      method: "get",
    });
  },

  /** 公司休息日列表（可按月份 / 考勤组过滤） */
  restDayList(params?: { month?: string; group_id?: string }) {
    return request<ApiResponse<RestDayTable[]>>({
      url: `${ATTENDANCE_API_PATH}/rest-day/list`,
      method: "get",
      params,
    });
  },

  /** 批量登记公司休息日。登记后后端立即重算受影响月份（ADR-0009）。 */
  restDayCreate(data: { group_id: string; dates: string[]; remark?: string }) {
    return request<ApiResponse<RestDayCreateResult>>({
      url: `${ATTENDANCE_API_PATH}/rest-day/create`,
      method: "post",
      data,
    });
  },

  /** 删除公司休息日。删除后立即反向重算、还原回钉钉原值。 */
  restDayDelete(ids: number[]) {
    return request<ApiResponse<{ deleted: number; months: string[] }>>({
      url: `${ATTENDANCE_API_PATH}/rest-day/delete`,
      method: "delete",
      data: ids,
    });
  },
};

export default AttendanceAPI;

export interface MonthlyReportTable {
  id?: number;
  oa_user_id?: number;
  month?: string;
  name?: string;
  user_name?: string;
  dept_name?: string;
  current_dept_name?: string;
  job_number?: string;
  group_name?: string;
  required_days?: number | null;
  actual_days?: number | null;
  rest_days?: number | null;
  late_count?: number | null;
  serious_late_count?: number | null;
  absent_late_days?: number | null;
  early_leave_count?: number | null;
  missing_checkin_count?: number | null;
  missing_checkout_count?: number | null;
  absent_days?: number | null;
  leave_personal?: number | null;
  leave_sick?: number | null;
  leave_annual?: number | null;
  synced_at?: string;
}

/** 日明细：一行 = 钉钉导出报表里的一个日期格 */
export interface DailyReportTable {
  id?: number;
  oa_user_id?: number;
  work_date?: string;
  month?: string;
  day_of_month?: number;
  group_id?: string;
  group_name?: string;
  raw_text?: string;
  approve_text?: string;
  /** 考勤结果展示值（叠加公司休息日），为空时前端回退显示 raw_text */
  effective_text?: string;
  synced_at?: string;
}

export interface MonthlyReportPageQuery {
  page_no?: number;
  page_size?: number;
  month?: string;
  oa_user_id?: number;
  name?: string;
  dept_id?: number;
}

/** 上个月的考勤同步状态：can_sync 为 true 才显示「手动拉取」按钮 */
export interface MonthlyReportSyncState {
  month?: string;
  synced?: boolean;
  can_sync?: boolean;
}

/** 手动拉取上个月考勤的返回结果 */
export interface MonthlyReportSyncResult {
  month?: string;
  start_date?: string;
  end_date?: string;
  total?: number;
  success?: number;
  failed?: number;
  skipped?: number;
  messages?: string[];
}

/** 考勤组下拉选项 */
export interface AttendanceGroupOption {
  group_id: string;
  group_name?: string;
}

/** 公司休息日 */
export interface RestDayTable {
  id?: number;
  group_id?: string;
  group_name?: string;
  rest_date?: string;
  month?: string;
  remark?: string;
}

/** 休息日批量登记的返回结果 */
export interface RestDayCreateResult {
  created?: number;
  skipped_dates?: string[];
  months?: string[];
}

const SALARY_API_PATH = "/internal/salary";

/** 薪资绩效接口。普通员工只会拿到自己的工资条，管理接口由后端再次鉴权。 */
export const SalaryAPI = {
  payslipPage(params?: PayslipPageQuery) {
    return request<ApiResponse<PageResult<PayslipTable>>>({
      url: `${SALARY_API_PATH}/payslip/page`,
      method: "get",
      params,
    });
  },
  confirmPayslip(id: number) {
    return request<ApiResponse<PayslipTable>>({
      url: `${SALARY_API_PATH}/payslip/confirm/${id}`,
      method: "post",
    });
  },
  profileList(params?: { name?: string }) {
    return request<ApiResponse<SalaryProfile[]>>({
      url: `${SALARY_API_PATH}/profile/list`,
      method: "get",
      params,
    });
  },
  saveProfile(oaUserId: number, data: SalaryProfileForm) {
    return request<ApiResponse<SalaryProfile>>({
      url: `${SALARY_API_PATH}/profile/save/${oaUserId}`,
      method: "post",
      data,
    });
  },
  performanceList(params: { month: string; name?: string }) {
    return request<ApiResponse<PerformanceEntry[]>>({
      url: `${SALARY_API_PATH}/performance/list`,
      method: "get",
      params,
    });
  },
  savePerformance(data: PerformanceEntryForm) {
    return request<ApiResponse<PerformanceEntry>>({
      url: `${SALARY_API_PATH}/performance/save`,
      method: "post",
      data,
    });
  },
  confirmPerformance(data: PerformanceConfirmForm) {
    return request<ApiResponse<PerformanceConfirmResult>>({
      url: `${SALARY_API_PATH}/performance/confirm`,
      method: "post",
      data,
    });
  },
};

export interface PayslipPageQuery {
  page_no?: number;
  page_size?: number;
  month?: string;
  name?: string;
  oa_user_id?: number;
}

export interface PayslipTable {
  id?: number;
  oa_user_id?: number;
  month?: string;
  user_name?: string;
  dept_name?: string;
  group_name?: string;
  required_days?: number;
  actual_days?: number;
  missing_count?: number;
  base_salary?: number;
  full_attendance_bonus?: number;
  allowance?: number;
  performance_base?: number;
  performance_deduction?: number;
  prorated_base?: number;
  full_attendance_award?: number;
  performance_amount?: number;
  missing_deduction?: number;
  gross_amount?: number;
  net_amount?: number;
  generation_source?: string;
  generated_at?: string;
  status?: number;
  confirmed_at?: string;
}

export interface SalaryProfileForm {
  base_salary: number;
  full_attendance_bonus: number;
  allowance: number;
  performance_base: number;
}

export interface SalaryProfile extends SalaryProfileForm {
  id?: number;
  oa_user_id: number;
  user_name?: string;
  group_name?: string;
  has_profile: boolean;
}

export interface PerformanceEntry {
  id?: number;
  oa_user_id: number;
  user_name?: string;
  group_name?: string;
  month: string;
  performance_base: number;
  deduction: number;
  performance_amount: number;
  remark?: string;
  locked: boolean;
  attendance_ready: boolean;
  requires_performance: boolean;
  confirmed: boolean;
  confirmed_at?: string;
  confirmed_id?: number;
}

export interface PerformanceEntryForm {
  oa_user_id: number;
  month: string;
  deduction: number;
  remark?: string;
}

export interface PerformanceConfirmForm {
  oa_user_id: number;
  month: string;
  confirmed: boolean;
}

export interface PerformanceConfirmResult {
  month: string;
  confirmed_count: number;
  total_count: number;
  all_confirmed: boolean;
  payslips_generated: number;
  message: string;
}
