<!-- 考勤统计：展示钉钉智能考勤月报（结果由钉钉计算，本系统不重新统计） -->
<template>
  <ElCard class="fa-table-card">
    <!-- 回车必须能查询：@submit.prevent 只阻止默认提交，绑定 loadData 才会真的查 -->
    <ElForm class="fa-search-form" inline @submit.prevent="loadData">
      <ElFormItem label="月份">
        <ElDatePicker
          v-model="query.month"
          type="month"
          value-format="YYYY-MM"
          placeholder="统计月份"
          clearable
          style="width: 160px"
        />
      </ElFormItem>
      <ElFormItem v-if="canViewAll" label="姓名">
        <ElInput v-model="query.name" placeholder="姓名" clearable style="width: 160px" />
      </ElFormItem>
      <ElFormItem>
        <ElButton type="primary" :loading="loading" @click="loadData">查询</ElButton>
        <ElButton :loading="loading" @click="resetQuery">重置</ElButton>
      </ElFormItem>
      <!-- 公司休息日安排入口（ADR-0009）：拿到 rest 权限的人才能登记/删除 -->
      <ElFormItem>
        <ElButton v-hasPerm="['module_internal:salary-attendance:rest']" type="primary" plain @click="openRestDialog">
          休息日安排
        </ElButton>
      </ElFormItem>
      <!-- ADR-0018：考勤确认是整月工资条生成前置；确认后该月休息日不可再改 -->
      <ElFormItem v-if="syncState.month" :label="`${syncState.month} 考勤`">
        <ElTag :type="confirmTag.type">{{ confirmTag.text }}</ElTag>
        <ElButton
          v-if="syncState.can_confirm && !syncState.confirmed"
          v-hasPerm="['module_internal:salary-attendance:rest']"
          type="success"
          plain
          style="margin-left: 8px"
          :loading="confirming"
          @click="handleConfirmMonth"
        >
          确认考勤
        </ElButton>
        <ElButton
          v-else-if="syncState.can_confirm && syncState.confirmed"
          v-hasPerm="['module_internal:salary-attendance:rest']"
          type="warning"
          plain
          style="margin-left: 8px"
          :loading="confirming"
          @click="handleRevokeConfirm"
        >
          撤销确认
        </ElButton>
      </ElFormItem>
      <!-- 上个月没被拉过才显示：手动与每月 3 日 00:00 的自动任务互斥，谁先跑谁生效 -->
      <ElFormItem v-if="syncState.can_sync">
        <ElButton
          v-hasPerm="['module_internal:salary-attendance:sync']"
          type="warning"
          :loading="syncing"
          @click="handleSync"
        >
          拉取 {{ syncState.month }} 考勤数据
        </ElButton>
      </ElFormItem>
    </ElForm>

    <ElAlert
      type="info"
      :closable="false"
      show-icon
      title="考勤结果来自钉钉智能考勤报表；若通过「休息日安排」登记了公司休息日，系统会以钉钉月报为基准叠加休息日规则重算应出勤/休息/旷工。系统每月 3 日自动同步上个月，3 日后也可用按钮手工补拉，两者互斥。"
      style="margin-bottom: 12px"
    />

    <ElTable v-loading="loading" :data="rows" border size="small">
      <ElTableColumn prop="month" label="月份" width="100" />
      <ElTableColumn label="姓名" min-width="120">
        <template #default="{ row }">{{ row.user_name || row.name || row.oa_user_id }}</template>
      </ElTableColumn>
      <!-- ADR-0017：月报是历史事实，停用 / 离职员工照常显示，只补状态标记 -->
      <ElTableColumn label="员工状态" width="100">
        <template #default="{ row }">
          <ElTag :type="row.user_status_label === '已离职' ? 'info' : row.user_status_label === '已停用' ? 'warning' : 'success'">
            {{ row.user_status_label || "—" }}
          </ElTag>
        </template>
      </ElTableColumn>
      <ElTableColumn label="部门" min-width="140">
        <template #default="{ row }">{{ row.current_dept_name || row.dept_name || "-" }}</template>
      </ElTableColumn>
      <ElTableColumn prop="job_number" label="工号" width="110" />
      <ElTableColumn prop="group_name" label="考勤组" min-width="120" />
      <ElTableColumn prop="required_days" label="应出勤" width="90" />
      <ElTableColumn prop="actual_days" label="出勤" width="90" />
      <ElTableColumn prop="rest_days" label="休息" width="90" />
      <ElTableColumn prop="late_count" label="迟到次数" width="100" />
      <ElTableColumn prop="serious_late_count" label="严重迟到" width="100" />
      <ElTableColumn prop="early_leave_count" label="早退次数" width="100" />
      <ElTableColumn prop="missing_checkin_count" label="上班缺卡" width="100" />
      <ElTableColumn prop="missing_checkout_count" label="下班缺卡" width="100" />
      <ElTableColumn prop="absent_days" label="旷工天数" width="100" />
      <ElTableColumn prop="leave_personal" label="事假(天)" width="100" />
      <ElTableColumn prop="leave_sick" label="病假(天)" width="100" />
      <ElTableColumn prop="leave_annual" label="年假(天)" width="100" />
      <ElTableColumn prop="synced_at" label="同步时间" min-width="180" show-overflow-tooltip />
      <ElTableColumn label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <ElButton link type="primary" @click="openDaily(row)">查看明细</ElButton>
        </template>
      </ElTableColumn>
    </ElTable>

    <ElPagination
      class="fa-pagination"
      background
      layout="total, sizes, prev, pager, next"
      v-model:current-page="query.page_no"
      v-model:page-size="query.page_size"
      :total="total"
      :page-sizes="[10, 20, 50, 100]"
      @current-change="loadData"
      @size-change="loadData"
    />
  </ElCard>

  <!-- 日明细弹窗：一行 = 钉钉导出报表里的一个日期格 -->
  <ElDialog
    v-model="dailyVisible"
    :title="`考勤日明细 - ${dailyTitle}`"
    width="70%"
    destroy-on-close
    append-to-body
  >
    <ElTable v-loading="dailyLoading" :data="dailyRows" border size="small" max-height="480">
      <ElTableColumn prop="work_date" label="日期" width="120" />
      <ElTableColumn label="星期" width="90">
        <template #default="{ row }">{{ weekdayText(row.work_date) }}</template>
      </ElTableColumn>
      <ElTableColumn label="考勤结果" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="att-result" :class="`att-result--${resultCategory(row.effective_text || row.raw_text)}`">
            {{ row.effective_text || row.raw_text || "-" }}
          </span>
        </template>
      </ElTableColumn>
      <ElTableColumn prop="approve_text" label="关联的审批单" min-width="220" show-overflow-tooltip />
    </ElTable>
  </ElDialog>

  <!-- 休息日安排：选考勤组 + 多选日期，保存即重算；下方列表可删 -->
  <ElDialog v-model="restVisible" title="休息日安排" width="640px" destroy-on-close append-to-body>
    <!-- ADR-0018：上个月已确认 / 已生成工资条时提示锁定，其它月份仍可登记 -->
    <ElAlert
      v-if="!restEditable && restDisabledReason"
      type="error"
      :closable="false"
      show-icon
      :title="restDisabledReason"
      style="margin-bottom: 12px"
    />
    <ElAlert
      type="warning"
      :closable="false"
      show-icon
      title="登记后系统会按考勤组重新计算当月应出勤/休息/旷工；删除休息日会还原回钉钉原值。"
      style="margin-bottom: 12px"
    />
    <ElForm label-width="80px">
      <ElFormItem label="考勤组">
        <ElSelect v-model="restForm.group_id" placeholder="选择考勤组" style="width: 240px" @change="loadRestDays">
          <ElOption v-for="g in restGroups" :key="g.group_id" :value="g.group_id" :label="g.group_name || g.group_id" />
        </ElSelect>
      </ElFormItem>
      <ElFormItem label="休息日期">
        <ElDatePicker
          v-model="restForm.dates"
          type="dates"
          value-format="YYYY-MM-DD"
          placeholder="可点选多个日期"
          style="width: 100%"
        />
      </ElFormItem>
      <ElFormItem label="备注">
        <ElInput v-model="restForm.remark" placeholder="如：中秋补休" clearable />
      </ElFormItem>
      <ElFormItem>
        <ElButton type="primary" :loading="restSaving" @click="saveRestDays">保存并重算</ElButton>
      </ElFormItem>
    </ElForm>

    <ElDivider content-position="left">已安排的休息日</ElDivider>
    <ElTable v-loading="restLoading" :data="restDays" border size="small" max-height="300">
      <ElTableColumn prop="rest_date" label="日期" width="120" />
      <ElTableColumn prop="group_name" label="考勤组" min-width="120">
        <template #default="{ row }">{{ row.group_name || row.group_id }}</template>
      </ElTableColumn>
      <ElTableColumn prop="remark" label="备注" min-width="120" show-overflow-tooltip />
      <ElTableColumn label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <ElPopconfirm
            :disabled="isLockedMonth(row.month)"
            title="删除后会重算并还原该天考勤，确认删除？"
            @confirm="removeRestDay(row)"
          >
            <template #reference>
              <ElButton link type="danger" :disabled="isLockedMonth(row.month)">删除</ElButton>
            </template>
          </ElPopconfirm>
        </template>
      </ElTableColumn>
    </ElTable>
  </ElDialog>
</template>

<script setup lang="ts">
import { ElMessage } from "element-plus";

import AttendanceAPI, {
  SalaryAPI,
  type AttendanceGroupOption,
  type BusinessCalendar,
  type DailyReportTable,
  type MonthlyReportSyncState,
  type MonthlyReportTable,
  type RestDayTable,
} from "@/api/module_internal/salary-attendance";
import { checkPerm } from "@/utils/checkPerm";

defineOptions({
  name: "MonthlyReportTab",
  inheritAttrs: false,
});

// ADR-0017：默认「上个月」取服务端业务日历，前端不再自己用浏览器本地日期推算。
const businessCalendar = ref<BusinessCalendar | null>(null);

const canViewAll = computed(() =>
  ["module_internal:salary-attendance:manage", "module_internal:salary-performance:manage"].some((permission) => checkPerm(permission)),
);
const loading = ref(false);
const rows = ref<MonthlyReportTable[]>([]);
const total = ref(0);
const query = ref({
  page_no: 1,
  page_size: 20,
  month: "",
  name: undefined as string | undefined,
});

async function loadBusinessCalendar() {
  try {
    const res = await SalaryAPI.businessCalendar();
    businessCalendar.value = res.data.data;
    query.value.month = businessCalendar.value.previous_month;
  } catch {
    businessCalendar.value = null;
    query.value.month = "";
  }
}

async function loadData() {
  loading.value = true;
  try {
    const res = await AttendanceAPI.page({
      page_no: query.value.page_no,
      page_size: query.value.page_size,
      month: query.value.month || undefined,
      name: query.value.name || undefined,
    });
    rows.value = res.data.data.items || [];
    total.value = res.data.data.total || 0;
  } finally {
    loading.value = false;
  }
}

function resetQuery() {
  query.value.month = businessCalendar.value?.previous_month || "";
  query.value.name = undefined;
  query.value.page_no = 1;
  loadData();
}

// ── 日明细弹窗 ──────────────────────────────────────────────
const dailyVisible = ref(false);
const dailyLoading = ref(false);
const dailyRows = ref<DailyReportTable[]>([]);
const dailyTitle = ref("");

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

function weekdayText(date?: string): string {
  if (!date) return "-";
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return "-";
  return `周${WEEKDAYS[parsed.getDay()]}`;
}

// 考勤结果配色（ADR-0009 A8）：假期黄、休息绿（含公司休息）、旷工红；正常/缺卡/外勤不上色。
type ResultCategory = "rest" | "absent" | "leave" | "none";
function resultCategory(text?: string): ResultCategory {
  const t = (text || "").trim();
  if (!t) return "none";
  if (t.includes("休息")) return "rest"; // 「公司休息」与钉钉「休息」同色绿
  if (t.includes("旷工")) return "absent";
  if (t.includes("假")) return "leave"; // 事假 / 病假 / 年假
  return "none";
}

async function openDaily(row: MonthlyReportTable) {
  if (!row.oa_user_id || !row.month) return;
  dailyTitle.value = `${row.user_name || row.name || row.oa_user_id}（${row.month}）`;
  dailyVisible.value = true;
  dailyLoading.value = true;
  try {
    const res = await AttendanceAPI.dailyList({ oa_user_id: row.oa_user_id, month: row.month });
    dailyRows.value = res.data.data || [];
  } finally {
    dailyLoading.value = false;
  }
}

// ── 手动拉取 ──────────────────────────────────────────────
// 与每月 3 日 00:00 的自动任务互斥：谁先跑谁生效，所以拉过之后按钮就自动消失。
const syncState = ref<MonthlyReportSyncState>({});
const syncing = ref(false);

async function loadSyncState() {
  try {
    const res = await AttendanceAPI.syncState();
    syncState.value = res.data.data || {};
  } catch {
    // 状态查不到就不显示按钮，不影响列表本身的查询
    syncState.value = {};
  }
}

// ── 月度考勤确认（ADR-0018）──────────────────────────────────
type TagType = "success" | "warning" | "info" | "danger" | "primary";
const confirming = ref(false);

const confirmTag = computed<{ type: TagType; text: string }>(() => {
  if (!syncState.value.can_confirm) return { type: "info", text: "考勤已锁定" };
  if (syncState.value.confirmed) return { type: "success", text: "已确认" };
  return { type: "warning", text: "未确认" };
});

const restEditable = computed(() => syncState.value.rest_editable === true);

const restDisabledReason = computed(() => {
  const state = syncState.value;
  if (!state.month) return "";
  if (!state.synced) return `${state.month} 考勤尚未全量同步，暂不能登记该月休息日`;
  if (!state.can_confirm) return `${state.month} 已生成正式工资条，考勤与公司休息日已锁定，不能修改`;
  if (state.confirmed) return `${state.month} 考勤已确认，如需修改请先撤销确认`;
  return "";
});

/** sync-state 只描述上个月；其它月份仍可登记，最终由后端按日期所属月份逐个硬拦 */
function isLockedMonth(month?: string) {
  return !!month && month === syncState.value.month && !restEditable.value;
}

async function handleConfirmMonth() {
  if (!syncState.value.month) return;
  confirming.value = true;
  try {
    await AttendanceAPI.confirmMonth(syncState.value.month);
    await loadSyncState();
  } finally {
    confirming.value = false;
  }
}

async function handleRevokeConfirm() {
  if (!syncState.value.month) return;
  confirming.value = true;
  try {
    await AttendanceAPI.revokeMonthConfirm(syncState.value.month);
    await loadSyncState();
  } finally {
    confirming.value = false;
  }
}

async function handleSync() {
  syncing.value = true;
  try {
    const res = await AttendanceAPI.syncPreviousMonth();
    const data = res.data.data;
    ElMessage.success(`已拉取 ${data?.month}：成功 ${data?.success ?? 0} 人，无考勤组跳过 ${data?.skipped ?? 0} 人`);
    // 同一个月拉过就不能再拉，按钮要跟着消失；列表也要刷出新数据
    await loadSyncState();
    await loadData();
  } finally {
    syncing.value = false;
  }
}

onMounted(async () => {
  await loadBusinessCalendar();
  loadSyncState();
  await loadData();
});

// ── 休息日安排（ADR-0009）──────────────────────────────────
const restVisible = ref(false);
const restGroups = ref<AttendanceGroupOption[]>([]);
const restDays = ref<RestDayTable[]>([]);
const restLoading = ref(false);
const restSaving = ref(false);
const restForm = ref<{ group_id?: string; dates: string[]; remark?: string }>({ dates: [] });

async function openRestDialog() {
  restVisible.value = true;
  restForm.value = { dates: [] };
  restDays.value = [];
  try {
    const res = await AttendanceAPI.restDayGroups();
    restGroups.value = res.data.data || [];
    if (restGroups.value.length) {
      restForm.value.group_id = restGroups.value[0]?.group_id;
      await loadRestDays();
    }
  } catch {
    // 考勤组拉不到就只显示空表单，不阻断
  }
}

async function loadRestDays() {
  if (!restForm.value.group_id) {
    restDays.value = [];
    return;
  }
  restLoading.value = true;
  try {
    const res = await AttendanceAPI.restDayList({ group_id: restForm.value.group_id });
    restDays.value = res.data.data || [];
  } finally {
    restLoading.value = false;
  }
}

async function saveRestDays() {
  if (!restForm.value.group_id) {
    ElMessage.warning("请选择考勤组");
    return;
  }
  if (!restForm.value.dates?.length) {
    ElMessage.warning("请选择休息日期");
    return;
  }
  // ADR-0018：上个月已确认 / 已生成工资条时，前端先拦一次；其它月份照常提交，
  // 最终由后端按每个日期所属月份逐个硬拦（前端只知道上个月的状态）。
  if (restForm.value.dates.some((d) => isLockedMonth(d.slice(0, 7)))) {
    ElMessage.warning(restDisabledReason.value);
    return;
  }
  restSaving.value = true;
  try {
    const res = await AttendanceAPI.restDayCreate({
      group_id: restForm.value.group_id,
      dates: restForm.value.dates,
      remark: restForm.value.remark || undefined,
    });
    const data = res.data.data;
    const skipped = data?.skipped_dates?.length ? `，${data.skipped_dates.length} 天已存在跳过` : "";
    ElMessage.success(`已登记 ${data?.created ?? 0} 天休息日并重算${skipped}`);
    restForm.value.dates = [];
    await loadRestDays();
    await loadData(); // 月报数字变了，刷新列表
  } finally {
    restSaving.value = false;
  }
}

async function removeRestDay(row: RestDayTable) {
  if (!row.id || isLockedMonth(row.month)) return;
  await AttendanceAPI.restDayDelete([row.id]);
  ElMessage.success("已删除并重算");
  await loadRestDays();
  await loadData();
}
</script>

<style scoped>
.fa-search-form {
  margin-bottom: 12px;
}

.fa-pagination {
  margin-top: 12px;
  justify-content: flex-end;
}

/* 考勤结果配色（ADR-0009 A8）：假期黄、休息绿、旷工红；正常/缺卡/外勤不上色 */
.att-result {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  line-height: 1.6;
}

.att-result--rest {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.att-result--absent {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.att-result--leave {
  color: var(--el-color-warning);
  background: var(--el-color-warning-light-9);
}

.att-result--none {
  padding: 0;
  background: transparent;
}
</style>
