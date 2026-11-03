<template>
  <ElCard class="fa-table-card">
    <ElAlert
      v-if="canManage"
      type="info"
      :closable="false"
      show-icon
      title="每月 3 日拉取上月考勤，12:00 起未设置完绩效就每天通过钉钉提醒。设置完成后通知员工核对，管理员确认后生成工资条，超管确认后通知员工查收。"
      style="margin-bottom: 12px"
    />

    <ElTabs v-model="activeTab" @tab-change="handleTabChange">
      <ElTabPane label="工资条" name="payslips">
        <ElForm inline class="fa-search-form" @submit.prevent>
          <ElFormItem label="月份">
            <ElDatePicker v-model="query.month" type="month" value-format="YYYY-MM" placeholder="全部月份" clearable />
          </ElFormItem>
          <ElFormItem v-if="canManage" label="姓名">
            <ElInput v-model="query.name" placeholder="员工姓名" clearable style="width: 150px" @keyup.enter="loadPayslips" />
          </ElFormItem>
          <ElFormItem>
            <ElButton type="primary" :loading="payslipLoading" @click="loadPayslips">查询</ElButton>
            <ElButton @click="resetPayslipQuery">重置</ElButton>
          </ElFormItem>
        </ElForm>

        <ElTable v-loading="payslipLoading" :data="payslipRows" border size="small">
          <ElTableColumn prop="month" label="月份" width="90" fixed />
          <ElTableColumn v-if="canManage" prop="user_name" label="姓名" width="100" fixed />
          <ElTableColumn prop="required_days" label="应出勤" width="80" />
          <ElTableColumn prop="actual_days" label="实出勤" width="80" />
          <ElTableColumn label="基础工资" width="105" align="right">
            <template #default="{ row }">{{ money(row.base_salary) }}</template>
          </ElTableColumn>
          <ElTableColumn label="折算工资" width="105" align="right">
            <template #default="{ row }">{{ money(row.prorated_base) }}</template>
          </ElTableColumn>
          <ElTableColumn label="全勤奖" width="95" align="right">
            <template #default="{ row }">{{ money(row.full_attendance_award) }}</template>
          </ElTableColumn>
          <ElTableColumn label="补贴" width="95" align="right">
            <template #default="{ row }">{{ money(row.allowance) }}</template>
          </ElTableColumn>
          <ElTableColumn label="实际绩效" width="100" align="right">
            <template #default="{ row }">{{ money(row.performance_amount) }}</template>
          </ElTableColumn>
          <ElTableColumn prop="missing_count" label="缺卡次数" width="85" />
          <ElTableColumn label="缺卡扣款" width="100" align="right">
            <template #default="{ row }">-{{ money(row.missing_deduction) }}</template>
          </ElTableColumn>
          <ElTableColumn label="实发工资" width="115" align="right" fixed="right">
            <template #default="{ row }"><strong>{{ money(row.net_amount) }}</strong></template>
          </ElTableColumn>
          <ElTableColumn label="状态" width="90" fixed="right">
            <template #default="{ row }">
              <ElTag :type="row.status === 1 ? 'success' : 'warning'">{{ row.status === 1 ? "已确认" : "未确认" }}</ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn v-if="isSuperuser" label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <ElButton v-if="row.status === 0" link type="primary" @click="handleConfirm(row)">确认</ElButton>
            </template>
          </ElTableColumn>
        </ElTable>
        <ElPagination
          v-model:current-page="query.page_no"
          v-model:page-size="query.page_size"
          class="fa-pagination"
          background
          layout="total, sizes, prev, pager, next"
          :total="payslipTotal"
          @size-change="loadPayslips"
          @current-change="loadPayslips"
        />
      </ElTabPane>
	  
	  <ElTabPane v-if="canManage" label="绩效登记" name="performance">
        <ElForm inline class="fa-search-form" @submit.prevent>
          <ElFormItem label="月份"><ElDatePicker v-model="performanceMonth" type="month" value-format="YYYY-MM" :clearable="false" @change="loadPerformance" /></ElFormItem>
          <ElFormItem label="姓名"><ElInput v-model="performanceName" clearable style="width: 150px" /></ElFormItem>
          <ElFormItem><ElButton type="primary" :loading="performanceLoading" @click="loadPerformance">查询</ElButton></ElFormItem>
        </ElForm>
        <ElAlert
          v-if="!performancePeriodOpen"
          type="info"
          :closable="false"
          title="每月 3 日 00:00 后才能设置和确认上个月绩效。"
          style="margin-bottom: 12px"
        />
        <ElAlert
          v-if="performanceRows.length > 0 && !performanceRows[0]?.attendance_ready"
          type="warning"
          :closable="false"
          title="该月考勤尚未同步。提交绩效设置时系统会自动拉取上月考勤，成功后再保存。"
          style="margin-bottom: 12px"
        />
        <ElTable v-loading="performanceLoading" :data="performanceRows" border size="small">
          <ElTableColumn prop="user_name" label="姓名" width="120" />
          <ElTableColumn prop="group_name" label="考勤组" min-width="130" />
          <ElTableColumn label="绩效基准" width="120" align="right"><template #default="{ row }">{{ money(row.performance_base) }}</template></ElTableColumn>
          <ElTableColumn label="绩效扣款" width="120" align="right"><template #default="{ row }">{{ money(row.deduction) }}</template></ElTableColumn>
          <ElTableColumn label="实际绩效" width="120" align="right"><template #default="{ row }">{{ money(row.performance_amount) }}</template></ElTableColumn>
          <ElTableColumn prop="remark" label="扣款说明" min-width="180" show-overflow-tooltip />
          <ElTableColumn label="状态" width="100">
            <template #default="{ row }">
              <ElTag :type="row.locked ? 'info' : row.confirmed ? 'success' : 'warning'">
                {{ !row.requires_performance ? "无绩效" : row.locked ? "已生成" : row.confirmed ? "已确认" : "待确认" }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <template v-if="!row.locked && row.requires_performance">
                <ElButton v-if="!row.confirmed" link type="primary" :disabled="!performancePeriodOpen" @click="openPerformanceDialog(row as PerformanceEntry)">登记</ElButton>
                <ElButton link :type="row.confirmed ? 'danger' : 'success'" :disabled="!row.attendance_ready || !performancePeriodOpen" @click="handlePerformanceConfirm(row as PerformanceEntry)">
                  {{ row.confirmed ? "撤销确认" : "确认" }}
                </ElButton>
              </template>
            </template>
          </ElTableColumn>
        </ElTable>
      </ElTabPane>

      <ElTabPane v-if="canManage" label="薪资档案" name="profiles">
        <ElForm inline class="fa-search-form" @submit.prevent>
          <ElFormItem label="姓名"><ElInput v-model="profileName" clearable style="width: 160px" /></ElFormItem>
          <ElFormItem><ElButton type="primary" :loading="profileLoading" @click="loadProfiles">查询</ElButton></ElFormItem>
        </ElForm>
        <ElTable v-loading="profileLoading" :data="profileRows" border size="small">
          <ElTableColumn prop="user_name" label="姓名" width="120" />
          <ElTableColumn prop="group_name" label="考勤组" min-width="130" />
          <ElTableColumn label="基础工资" width="120" align="right"><template #default="{ row }">{{ money(row.base_salary) }}</template></ElTableColumn>
          <ElTableColumn label="全勤奖" width="110" align="right"><template #default="{ row }">{{ money(row.full_attendance_bonus) }}</template></ElTableColumn>
          <ElTableColumn label="固定补贴" width="110" align="right"><template #default="{ row }">{{ money(row.allowance) }}</template></ElTableColumn>
          <ElTableColumn label="绩效基准" width="110" align="right"><template #default="{ row }">{{ money(row.performance_base) }}</template></ElTableColumn>
          <ElTableColumn label="档案状态" width="100"><template #default="{ row }"><ElTag :type="row.has_profile ? 'success' : 'info'">{{ row.has_profile ? "已设置" : "未设置" }}</ElTag></template></ElTableColumn>
          <ElTableColumn v-if="isSuperuser" label="操作" width="90" fixed="right">
            <template #default="{ row }"><ElButton link type="primary" @click="openProfileDialog(row as SalaryProfile)">设置</ElButton></template>
          </ElTableColumn>
        </ElTable>
      </ElTabPane>
      
    </ElTabs>
  </ElCard>

  <ElDialog v-model="profileDialogVisible" title="设置薪资档案" width="480px" destroy-on-close>
    <ElForm label-width="100px">
      <ElFormItem label="员工"><ElInput :model-value="editingProfileName" disabled /></ElFormItem>
      <ElFormItem label="基础工资"><ElInputNumber v-model="profileForm.base_salary" :min="0" :precision="2" :step="100" style="width: 100%" /></ElFormItem>
      <ElFormItem label="全勤奖"><ElInputNumber v-model="profileForm.full_attendance_bonus" :min="0" :precision="2" :step="50" style="width: 100%" /></ElFormItem>
      <ElFormItem label="固定补贴"><ElInputNumber v-model="profileForm.allowance" :min="0" :precision="2" :step="50" style="width: 100%" /></ElFormItem>
      <ElFormItem label="绩效基准"><ElInputNumber v-model="profileForm.performance_base" :min="0" :precision="2" :step="100" style="width: 100%" /></ElFormItem>
    </ElForm>
    <template #footer>
      <ElButton @click="profileDialogVisible = false">取消</ElButton>
      <ElButton type="primary" :loading="savingProfile" @click="saveProfile">保存</ElButton>
    </template>
  </ElDialog>

  <ElDialog v-model="performanceDialogVisible" title="登记绩效扣款" width="480px" destroy-on-close>
    <ElAlert type="warning" :closable="false" title="工资条生成后不能再修改绩效扣款" style="margin-bottom: 16px" />
    <ElForm label-width="100px">
      <ElFormItem label="员工"><ElInput :model-value="editingPerformanceName" disabled /></ElFormItem>
      <ElFormItem label="绩效基准"><span>{{ money(performanceForm.performance_base) }} 元</span></ElFormItem>
      <ElFormItem label="绩效扣款"><ElInputNumber v-model="performanceForm.deduction" :min="0" :precision="2" :step="100" style="width: 100%" /></ElFormItem>
      <ElFormItem label="实际绩效"><strong>{{ money(performancePreview) }} 元</strong></ElFormItem>
      <ElFormItem label="扣款说明"><ElInput v-model="performanceForm.remark" type="textarea" :rows="3" maxlength="255" show-word-limit /></ElFormItem>
    </ElForm>
    <template #footer>
      <ElButton @click="performanceDialogVisible = false">取消</ElButton>
      <ElButton type="primary" :loading="savingPerformance" @click="savePerformance">保存</ElButton>
    </template>
  </ElDialog>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";

import { SalaryAPI } from "@/api/module_internal/salary-attendance";
import type {
  PerformanceEntry,
  PayslipTable,
  SalaryProfile,
} from "@/api/module_internal/salary-attendance";
import { useUserStore } from "@stores";
import { checkPerm } from "@/utils/checkPerm";

defineOptions({ name: "SalaryPerformanceTab", inheritAttrs: false });

const userStore = useUserStore();
const canManage = computed(() => checkPerm("module_internal:salary-performance:manage"));
const isSuperuser = computed(() => Boolean(userStore.basicInfo?.is_superuser));
const performancePeriodOpen = computed(() => new Date().getDate() >= 3);
const activeTab = ref("payslips");

const query = ref({ page_no: 1, page_size: 20, month: "", name: "" });
const payslipRows = ref<PayslipTable[]>([]);
const payslipTotal = ref(0);
const payslipLoading = ref(false);

const profileRows = ref<SalaryProfile[]>([]);
const profileName = ref("");
const profileLoading = ref(false);
const profileDialogVisible = ref(false);
const savingProfile = ref(false);
const editingProfileId = ref(0);
const editingProfileName = ref("");
const profileForm = ref({ base_salary: 0, full_attendance_bonus: 0, allowance: 0, performance_base: 0 });

const performanceRows = ref<PerformanceEntry[]>([]);
const performanceName = ref("");
const performanceLoading = ref(false);
const performanceDialogVisible = ref(false);
const savingPerformance = ref(false);
const editingPerformanceName = ref("");
const performanceForm = ref({ oa_user_id: 0, month: "", performance_base: 0, deduction: 0, remark: "" });

function previousMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth()).padStart(2, "0")}`;
}
const performanceMonth = ref(previousMonth());
const performancePreview = computed(() => Math.max(performanceForm.value.performance_base - performanceForm.value.deduction, 0));

function money(value?: number): string {
  return Number(value ?? 0).toFixed(2);
}

async function loadPayslips() {
  payslipLoading.value = true;
  try {
    const response = await SalaryAPI.payslipPage({
      ...query.value,
      month: query.value.month || undefined,
      name: query.value.name || undefined,
    });
    payslipRows.value = response.data.data.items;
    payslipTotal.value = response.data.data.total;
  } finally {
    payslipLoading.value = false;
  }
}

function resetPayslipQuery() {
  query.value = { page_no: 1, page_size: 20, month: "", name: "" };
  loadPayslips();
}

async function handleConfirm(row: PayslipTable) {
  await ElMessageBox.confirm(
    `确认 ${row.user_name || "该员工"} ${row.month} 工资条？确认后永久锁定，任何人都不能撤销或修改。`,
    "不可逆操作",
    { type: "warning", confirmButtonText: "确认并永久锁定", cancelButtonText: "取消" },
  );
  if (!row.id) return;
  await SalaryAPI.confirmPayslip(row.id);
  ElMessage.success("工资条已确认并永久锁定");
  await loadPayslips();
}

async function loadProfiles() {
  if (!canManage.value) return;
  profileLoading.value = true;
  try {
    const response = await SalaryAPI.profileList({ name: profileName.value || undefined });
    profileRows.value = response.data.data;
  } finally {
    profileLoading.value = false;
  }
}

function openProfileDialog(row: SalaryProfile) {
  editingProfileId.value = row.oa_user_id;
  editingProfileName.value = row.user_name || String(row.oa_user_id);
  profileForm.value = {
    base_salary: row.base_salary,
    full_attendance_bonus: row.full_attendance_bonus,
    allowance: row.allowance,
    performance_base: row.performance_base,
  };
  profileDialogVisible.value = true;
}

async function saveProfile() {
  savingProfile.value = true;
  try {
    await SalaryAPI.saveProfile(editingProfileId.value, profileForm.value);
    ElMessage.success("薪资档案保存成功");
    profileDialogVisible.value = false;
    await loadProfiles();
  } finally {
    savingProfile.value = false;
  }
}

async function loadPerformance() {
  if (!canManage.value || !performanceMonth.value) return;
  performanceLoading.value = true;
  try {
    const response = await SalaryAPI.performanceList({ month: performanceMonth.value, name: performanceName.value || undefined });
    performanceRows.value = response.data.data;
  } finally {
    performanceLoading.value = false;
  }
}

function openPerformanceDialog(row: PerformanceEntry) {
  editingPerformanceName.value = row.user_name || String(row.oa_user_id);
  performanceForm.value = {
    oa_user_id: row.oa_user_id,
    month: row.month,
    performance_base: row.performance_base,
    deduction: row.deduction,
    remark: row.remark || "",
  };
  performanceDialogVisible.value = true;
}

async function savePerformance() {
  savingPerformance.value = true;
  try {
    await SalaryAPI.savePerformance({
      oa_user_id: performanceForm.value.oa_user_id,
      month: performanceForm.value.month,
      deduction: performanceForm.value.deduction,
      remark: performanceForm.value.remark || undefined,
    });
    ElMessage.success("绩效扣款保存成功");
    performanceDialogVisible.value = false;
    await loadPerformance();
  } finally {
    savingPerformance.value = false;
  }
}

async function handlePerformanceConfirm(row: PerformanceEntry) {
  const revoking = row.confirmed;
  await ElMessageBox.confirm(
    revoking
      ? `确认撤销 ${row.user_name || "该员工"} 的绩效确认？撤销后需要重新确认才能生成工资条。`
      : `确认 ${row.user_name || "该员工"} 的绩效？如果这是最后一名待确认员工，系统将立即自动生成整月工资条。`,
    revoking ? "撤销绩效确认" : "确认绩效",
    { type: "warning", confirmButtonText: revoking ? "撤销确认" : "确认", cancelButtonText: "取消" },
  );
  const response = await SalaryAPI.confirmPerformance({
    oa_user_id: row.oa_user_id,
    month: row.month,
    confirmed: !revoking,
  });
  ElMessage.success(response.data.data.message);
  await loadPerformance();
  if (response.data.data.payslips_generated > 0) {
    await loadPayslips();
  }
}

function handleTabChange(name: string | number) {
  if (name === "profiles") loadProfiles();
  if (name === "performance") loadPerformance();
}

onMounted(() => {
  loadPayslips();
});
</script>

<style scoped>
.fa-search-form { margin-bottom: 12px; }
.fa-pagination { margin-top: 12px; justify-content: flex-end; }
</style>
