<!-- 账号管理：钉钉账号（可匹配）+ 快麦账号（预留空 Tab）+ 部门映射 -->
<template>
  <div class="fa-full-height">
    <ElCard class="fa-table-card">
      <ElTabs v-model="activeTab">
        <!-- 钉钉账号 -->
        <ElTabPane label="钉钉账号" name="dingtalk">
          <!-- 回车必须能查询：@submit.prevent 只阻止默认提交，绑定查询才生效 -->
          <ElForm class="fa-search-form" inline @submit.prevent="loadDingtalkUsers">
            <ElFormItem label="姓名/账号">
              <ElInput v-model="dingtalkQuery.name" placeholder="姓名或登录账号" clearable style="width: 180px" />
            </ElFormItem>
            <ElFormItem label="手机号">
              <ElInput v-model="dingtalkQuery.mobile" placeholder="手机号" clearable style="width: 160px" />
            </ElFormItem>
            <ElFormItem label="绑定状态">
              <ElSelect v-model="dingtalkQuery.bound" placeholder="全部" clearable style="width: 140px">
                <ElOption label="已绑定" :value="true" />
                <ElOption label="未绑定" :value="false" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem>
              <ElButton type="primary" :loading="dingtalkLoading" @click="loadDingtalkUsers">查询</ElButton>
              <ElButton :loading="dingtalkLoading" @click="resetDingtalkQuery">重置</ElButton>
            </ElFormItem>
          </ElForm>

          <ElTable v-loading="dingtalkLoading" :data="dingtalkRows" border size="small">
            <ElTableColumn prop="name" label="姓名" min-width="120" />
            <ElTableColumn prop="username" label="登录账号" min-width="140" />
            <ElTableColumn prop="mobile" label="手机号" min-width="130" />
            <ElTableColumn prop="dept_name" label="部门" min-width="140" />
            <ElTableColumn label="绑定状态" width="110">
              <template #default="{ row }">
                <ElTag :type="row.bound ? 'success' : 'info'">
                  {{ row.bound ? "已绑定" : "未绑定" }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="external_user_id" label="钉钉userId" min-width="180" show-overflow-tooltip />
            <ElTableColumn prop="external_name" label="钉钉姓名" min-width="120" />
            <ElTableColumn label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <ElButton
                  v-hasPerm="[`${PERM}:match`]"
                  link
                  type="primary"
                  :disabled="!row.can_match"
                  :title="row.match_message || '按手机号匹配钉钉成员'"
                  @click="handleMatch(row)"
                >
                  匹配
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>

          <ElPagination
            class="fa-pagination"
            background
            layout="total, sizes, prev, pager, next"
            v-model:current-page="dingtalkQuery.page_no"
            v-model:page-size="dingtalkQuery.page_size"
            :total="dingtalkTotal"
            :page-sizes="[10, 20, 50, 100]"
            @current-change="loadDingtalkUsers"
            @size-change="loadDingtalkUsers"
          />
        </ElTabPane>

        <!-- 快麦账号：预留，不接业务 -->
        <ElTabPane label="快麦账号" name="kuaimai">
          <ElEmpty description="快麦账号暂未接入，仅预留扩展位" />
        </ElTabPane>

        <!-- 部门映射 -->
        <ElTabPane label="部门映射" name="dept">
          <div class="fa-tab-toolbar">
            <ElButton v-hasPerm="[`${PERM}:create`]" type="primary" @click="openDeptCreate">新增</ElButton>
            <ElButton :loading="deptLoading" @click="loadDeptMappings">刷新</ElButton>
          </div>

          <ElTable v-loading="deptLoading" :data="deptRows" border size="small">
            <ElTableColumn prop="oa_dept_name" label="OA部门" min-width="160" />
            <ElTableColumn prop="dingtalk_dept_id" label="钉钉部门ID" min-width="160" />
            <ElTableColumn prop="dingtalk_dept_name" label="钉钉部门名称" min-width="180" />
            <ElTableColumn label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <ElButton v-hasPerm="[`${PERM}:update`]" link type="primary" @click="openDeptEdit(row)">编辑</ElButton>
                <ElButton v-hasPerm="[`${PERM}:delete`]" link type="danger" @click="handleDeptDelete(row)">删除</ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElCard>

    <ElDialog v-model="deptDialogVisible" :title="deptDialogTitle" width="520px" append-to-body>
      <ElForm ref="deptFormRef" :model="deptForm" :rules="deptRules" label-width="120px">
        <ElFormItem label="OA部门" prop="oa_dept_id">
          <ElSelect v-model="deptForm.oa_dept_id" filterable placeholder="请选择OA部门" style="width: 100%">
            <ElOption v-for="item in deptOptions" :key="item.id" :label="item.name" :value="item.id" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="钉钉部门ID" prop="dingtalk_dept_id">
          <ElInput v-model="deptForm.dingtalk_dept_id" placeholder="钉钉通讯录部门ID" clearable />
        </ElFormItem>
        <ElFormItem label="钉钉部门名称">
          <ElInput v-model="deptForm.dingtalk_dept_name" placeholder="便于识别，可留空" clearable />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="deptDialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="deptSubmitting" @click="submitDeptForm">保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<script setup lang="ts">
import AccountAPI, {
  type DeptMappingForm,
  type DeptMappingTable,
  type DingtalkAccountTable,
} from "@/api/module_internal/account";
import DeptAPI, { type DeptTable } from "@/api/module_system/dept";
import { ElMessage, ElMessageBox } from "element-plus";

defineOptions({
  name: "InternalAccount",
  inheritAttrs: false,
});

const PERM = "module_internal:account";

const activeTab = ref("dingtalk");

/* ── 钉钉账号 ─────────────────────────────────────────── */
const dingtalkLoading = ref(false);
const dingtalkRows = ref<DingtalkAccountTable[]>([]);
const dingtalkTotal = ref(0);
const dingtalkQuery = ref({
  page_no: 1,
  page_size: 20,
  name: undefined as string | undefined,
  mobile: undefined as string | undefined,
  bound: undefined as boolean | undefined,
});

async function loadDingtalkUsers() {
  dingtalkLoading.value = true;
  try {
    const res = await AccountAPI.dingtalkUserPage({
      page_no: dingtalkQuery.value.page_no,
      page_size: dingtalkQuery.value.page_size,
      name: dingtalkQuery.value.name || undefined,
      mobile: dingtalkQuery.value.mobile || undefined,
      bound: dingtalkQuery.value.bound,
    });
    dingtalkRows.value = res.data.data.items || [];
    dingtalkTotal.value = res.data.data.total || 0;
  } finally {
    dingtalkLoading.value = false;
  }
}

function resetDingtalkQuery() {
  dingtalkQuery.value.name = undefined;
  dingtalkQuery.value.mobile = undefined;
  dingtalkQuery.value.bound = undefined;
  dingtalkQuery.value.page_no = 1;
  loadDingtalkUsers();
}

async function handleMatch(row: DingtalkAccountTable) {
  if (!row.oa_user_id) return;
  try {
    await ElMessageBox.confirm(
      `将按手机号 ${row.mobile || "-"} 绑定钉钉成员。绑定后不允许修改或解除，确认继续？`,
      "钉钉账号匹配",
      { type: "warning" }
    );
  } catch {
    return;
  }

  try {
    const res = await AccountAPI.dingtalkMatch({ oa_user_id: row.oa_user_id });
    ElMessage.success(res.data.msg || "钉钉账号匹配完成");
    await loadDingtalkUsers();
  } catch {
    // 接口已统一提示错误
  }
}

/* ── 部门映射 ─────────────────────────────────────────── */
const deptLoading = ref(false);
const deptRows = ref<DeptMappingTable[]>([]);
const deptOptions = ref<{ id: number; name: string }[]>([]);
const deptDialogVisible = ref(false);
const deptDialogTitle = ref("新增部门映射");
const deptSubmitting = ref(false);
const deptEditingId = ref<number | undefined>(undefined);
const deptFormRef = ref();
const deptForm = ref<DeptMappingForm>({
  oa_dept_id: 0,
  dingtalk_dept_id: "",
  dingtalk_dept_name: "",
  status: 0,
});

const deptRules = {
  oa_dept_id: [{ required: true, message: "请选择OA部门", trigger: "change" }],
  dingtalk_dept_id: [{ required: true, message: "请填写钉钉部门ID", trigger: "blur" }],
};

function flattenDepts(list: DeptTable[]): { id: number; name: string }[] {
  const result: { id: number; name: string }[] = [];
  const walk = (items: DeptTable[]) => {
    for (const item of items) {
      if (item.id) result.push({ id: item.id, name: item.name || `部门${item.id}` });
      if (item.children?.length) walk(item.children);
    }
  };
  walk(list || []);
  return result;
}

async function loadDeptOptions() {
  if (deptOptions.value.length) return;
  const res = await DeptAPI.listDept({ status: 0 });
  deptOptions.value = flattenDepts((res.data.data || []) as DeptTable[]);
}

async function loadDeptMappings() {
  deptLoading.value = true;
  try {
    const res = await AccountAPI.deptMappingList();
    deptRows.value = res.data.data || [];
  } finally {
    deptLoading.value = false;
  }
}

async function openDeptCreate() {
  await loadDeptOptions();
  deptEditingId.value = undefined;
  deptDialogTitle.value = "新增部门映射";
  deptForm.value = { oa_dept_id: 0, dingtalk_dept_id: "", dingtalk_dept_name: "", status: 0 };
  deptDialogVisible.value = true;
}

async function openDeptEdit(row: DeptMappingTable) {
  await loadDeptOptions();
  deptEditingId.value = row.id;
  deptDialogTitle.value = "修改部门映射";
  deptForm.value = {
    oa_dept_id: row.oa_dept_id || 0,
    dingtalk_dept_id: row.dingtalk_dept_id || "",
    dingtalk_dept_name: row.dingtalk_dept_name || "",
    status: row.status ?? 0,
  };
  deptDialogVisible.value = true;
}

async function submitDeptForm() {
  if (!deptFormRef.value) return;
  try {
    await deptFormRef.value.validate();
  } catch {
    return;
  }

  deptSubmitting.value = true;
  try {
    if (deptEditingId.value) {
      await AccountAPI.deptMappingUpdate(deptEditingId.value, deptForm.value);
    } else {
      await AccountAPI.deptMappingCreate(deptForm.value);
    }
    ElMessage.success("保存成功");
    deptDialogVisible.value = false;
    await loadDeptMappings();
  } finally {
    deptSubmitting.value = false;
  }
}

async function handleDeptDelete(row: DeptMappingTable) {
  if (!row.id) return;
  try {
    await ElMessageBox.confirm(`确认删除「${row.oa_dept_name || row.oa_dept_id}」的部门映射？`, "删除确认", {
      type: "warning",
    });
  } catch {
    return;
  }
  await AccountAPI.deptMappingDelete([row.id]);
  ElMessage.success("删除成功");
  await loadDeptMappings();
}

onMounted(async () => {
  await loadDingtalkUsers();
  await loadDeptMappings();
});
</script>

<style scoped>
.fa-search-form {
  margin-bottom: 12px;
}

.fa-tab-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.fa-pagination {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
