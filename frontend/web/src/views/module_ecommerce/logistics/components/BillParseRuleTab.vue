<!-- 账单解析规则管理（ADR-0029：独立表，按bill_type分发列映射配置） -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="searchItems"
      :rules="searchBarRules"
      :is-expand="false"
      :show-expand="true"
      :show-reset="true"
      :show-search="true"
      :disabled-search="false"
      :default-expanded="false"
      include-audit
      @search="handleSearchBarSearch"
      @reset="onResetSearch"
    />

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <FaTableHeaderLeft
            :remove-ids="selectedIds"
            :perm-create="['module_ecommerce:bill_parse_rule:create']"
            :perm-delete="['module_ecommerce:bill_parse_rule:delete']"
            :delete-loading="batchDeleting"
            :create-loading="createLoading"
            @add="handleAdd"
            @delete="handleBatchDelete"
          />
        </template>
      </FaTableHeader>

      <FaTable
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @selection-change="onTableSelectionChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <FaDialog
      v-model="dialogVisible.visible"
      :title="dialogVisible.title"
      width="580px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
      @confirm="handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions :column="1" :data="detailFormData" :items="detailItems" max-height="70vh">
          <template #column_mapping="{ row }">
            <div>运单号表头：{{ (row as BillParseRuleTable)?.column_mapping?.waybill_no }}</div>
            <div>账单日期表头：{{ (row as BillParseRuleTable)?.column_mapping?.bill_date }}</div>
            <div>账单重量表头：{{ (row as BillParseRuleTable)?.column_mapping?.bill_weight }}</div>
            <div>
              账单其它费用表头：{{ (row as BillParseRuleTable)?.column_mapping?.bill_other_fee }}
            </div>
            <div>
              收件省表头：{{
                (row as BillParseRuleTable)?.column_mapping?.receiver_province ??
                "（旧规则未配，需重配）"
              }}
            </div>
          </template>
        </FaDescriptions>
      </template>
      <template v-else>
        <FaForm
          scrollbar
          max-height="70vh"
          :key="formRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="dialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="140"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        >
          <!-- 面单费自己画：要在格子下面常驻一句说明，说明 0 的含义（§25 AL17） -->
          <template #waybill_fee>
            <ElInputNumber
              v-model="waybillFee"
              :min="0"
              :max="100"
              :step="0.01"
              :precision="2"
              controls-position="right"
              placeholder="没有面单费就填 0"
            />
            <div class="mt-1 text-xs leading-5 text-(--el-text-color-secondary)">
              面单费必填：没有面单费就填 0，0 表示这家快递不收面单费。留空、负数、超过 100
              或小数超过两位都存不进去。
            </div>
          </template>
        </FaForm>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import {
  BillParseRuleAPI,
  type BillColumnMapping,
  type BillParseRuleForm,
  type BillParseRuleTable,
} from "@/api/module_ecommerce/logistics";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";

defineOptions({
  name: "EcommerceBillParseRuleTab",
  inheritAttrs: false,
});

const emit = defineEmits<{ changed: [] }>();

type SearchForm = {
  bill_type?: string;
  carrier_name?: string;
  status?: number;
  created_id?: number;
  updated_id?: number;
  created_time?: string[];
  updated_time?: string[];
};
const searchForm = ref<SearchForm>({});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const STATUS_OPTIONS = [
  { label: "正常", value: 0 },
  { label: "停用", value: 1 },
];

// 表头五项全部必填（ADR-0029）：缺哪项就在列表里点名，免得等导入账单时才暴雷
const MAPPING_LABELS: { key: keyof BillColumnMapping; label: string }[] = [
  { key: "waybill_no", label: "运单号" },
  { key: "bill_date", label: "账单日期" },
  { key: "bill_weight", label: "账单重量" },
  { key: "bill_other_fee", label: "账单其它费用" },
  { key: "receiver_province", label: "收件省" },
];

function mappingHint(row: BillParseRuleTable): string {
  const mapping = row.column_mapping ?? {};
  const missing = MAPPING_LABELS.filter((h) => !mapping[h.key]).map((h) => h.label);
  if (missing.length) return `缺表头：${missing.join("、")}`;
  return "已配齐";
}

const searchItems = computed<SearchFormItem[]>(() => [
  { label: "账单类型编码", key: "bill_type", type: "input", clearable: true, span: 6 },
  { label: "快递公司", key: "carrier_name", type: "input", clearable: true, span: 6 },
  {
    label: "状态",
    key: "status",
    type: "select",
    props: { options: STATUS_OPTIONS, clearable: true },
    span: 6,
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedRows, selectedIds, batchDeleting, onTableSelectionChange } =
  useTableSelection<BillParseRuleTable>();
const createLoading = ref(false);

const {
  columns,
  columnChecks,
  data,
  loading,
  pagination,
  getData,
  replaceSearchParams,
  resetSearchParams,
  handleSizeChange,
  handleCurrentChange,
  refreshData,
  refreshCreate,
  refreshUpdate,
  refreshRemove,
} = useTable({
  core: {
    apiFn: BillParseRuleAPI.listBillParseRule,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<BillParseRuleTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "bill_type", label: "账单类型编码", minWidth: 120, showOverflowTooltip: true },
      { prop: "carrier_name", label: "快递公司", minWidth: 120, showOverflowTooltip: true },
      {
        prop: "column_mapping",
        label: "表头配置",
        minWidth: 200,
        showOverflowTooltip: true,
        formatter: (row: BillParseRuleTable) => mappingHint(row),
      },
      { prop: "waybill_fee", label: "面单费", width: 100 },
      {
        prop: "status",
        label: "状态",
        width: 88,
        status: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } },
      },
      {
        prop: "operation",
        label: "操作",
        width: 180,
        fixed: "right",
        align: "center",
        formatter: (row: BillParseRuleTable) => formatOperationCell(row),
      },
    ]),
  },
});

async function handleSearchBarSearch(params: SearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({ ...params });
  await getData();
}

async function onResetSearch() {
  searchForm.value = {};
  await resetSearchParams();
}

const detailFormData = ref<BillParseRuleTable>({});
const detailItems: import("@/components/display/fa-descriptions/index.vue").DescriptionsItem[] = [
  { label: "账单类型编码", prop: "bill_type" },
  { label: "快递公司", prop: "carrier_name" },
  { label: "面单费", prop: "waybill_fee" },
  { label: "列映射配置", prop: "column_mapping", slot: "column_mapping" },
  {
    label: "状态",
    prop: "status",
    tag: { map: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } } },
  },
];

type RuleFormModel = BillParseRuleForm & {
  waybill_no_header?: string;
  bill_date_header?: string;
  bill_weight_header?: string;
  bill_other_fee_header?: string;
  receiver_province_header?: string;
};

const formData = ref<RuleFormModel>({});
// 面单费初值留空（§25）：原先写死 0，等于替用户回答「这家快递不收面单费」，而库里 0 和漏配长得一样。
const initialFormData: RuleFormModel = {
  bill_type: undefined,
  carrier_name: undefined,
  waybill_fee: undefined,
  status: 0,
  waybill_no_header: undefined,
  bill_date_header: undefined,
  bill_weight_header: undefined,
  bill_other_fee_header: undefined,
  receiver_province_header: undefined,
};

// 接口给的面单费可能是 "1.65" 这种字符串，而 ElInputNumber 只收 number；这一层把两边对上。
// 清空时数字框会给 null，统一折成 undefined——必填规则才认得「没填」这件事。
const waybillFee = computed<number | undefined>({
  get: () => {
    const raw = formData.value.waybill_fee;
    if (raw == null || raw === "") return undefined;
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  },
  set: (v) => {
    formData.value.waybill_fee = v ?? undefined;
  },
});

const { dialogVisible } = useCrudDialog();

const rules = reactive({
  bill_type: [{ required: true, message: "请输入账单类型编码", trigger: "blur" }],
  carrier_name: [{ required: true, message: "请输入快递公司名称", trigger: "blur" }],
  waybill_fee: [{ required: true, message: "面单费必须给一个值，确实没有就填 0", trigger: "blur" }],
  waybill_no_header: [{ required: true, message: "请输入运单号对应的表头名称", trigger: "blur" }],
  bill_date_header: [{ required: true, message: "请输入账单日期对应的表头名称", trigger: "blur" }],
  bill_weight_header: [
    { required: true, message: "请输入账单重量对应的表头名称", trigger: "blur" },
  ],
  bill_other_fee_header: [
    { required: true, message: "请输入账单其它费用对应的表头名称", trigger: "blur" },
  ],
  receiver_province_header: [
    { required: true, message: "请输入收件省对应的表头名称", trigger: "blur" },
  ],
});

const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const formRenderKey = ref(0);

function toApiPayload(m: RuleFormModel): BillParseRuleForm {
  return {
    id: m.id,
    bill_type: m.bill_type,
    carrier_name: m.carrier_name,
    waybill_fee: m.waybill_fee,
    status: m.status,
    column_mapping: {
      waybill_no: m.waybill_no_header,
      bill_date: m.bill_date_header,
      bill_weight: m.bill_weight_header,
      bill_other_fee: m.bill_other_fee_header,
      receiver_province: m.receiver_province_header,
    },
  };
}

function fromApiRecord(t: BillParseRuleTable): RuleFormModel {
  return {
    id: t.id,
    bill_type: t.bill_type,
    carrier_name: t.carrier_name,
    waybill_fee: t.waybill_fee,
    status: t.status,
    waybill_no_header: t.column_mapping?.waybill_no,
    bill_date_header: t.column_mapping?.bill_date,
    bill_weight_header: t.column_mapping?.bill_weight,
    bill_other_fee_header: t.column_mapping?.bill_other_fee,
    receiver_province_header: t.column_mapping?.receiver_province ?? undefined,
  };
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<RuleFormModel>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey,
    detailApi: async (id: number) => {
      const res = await BillParseRuleAPI.detailBillParseRule(id);
      return { ...res, data: { ...res.data, data: fromApiRecord(res.data.data) } };
    },
    createApi: (body: RuleFormModel) => BillParseRuleAPI.createBillParseRule(toApiPayload(body)),
    updateApi: (id: number, body: RuleFormModel) =>
      BillParseRuleAPI.updateBillParseRule(id, toApiPayload(body)),
    titles: { create: "新增账单解析规则", update: "修改账单解析规则", detail: "账单解析规则详情" },
    detailFormData,
    onCreateSuccess: async () => {
      await refreshCreate();
      emit("changed");
    },
    onUpdateSuccess: async () => {
      await refreshUpdate();
      emit("changed");
    },
  });

async function handleAdd() {
  createLoading.value = true;
  try {
    await handleOpenDialog("create");
  } finally {
    createLoading.value = false;
  }
}

const dialogFormItems = computed<FormItem[]>(() => [
  {
    label: "账单类型编码",
    key: "bill_type",
    type: "input",
    span: 24,
    props: {
      placeholder: "如 sf / yunda / zto",
      maxlength: 32,
      disabled: dialogVisible.type === "update",
    },
  },
  {
    label: "快递公司",
    key: "carrier_name",
    type: "input",
    span: 24,
    props: { placeholder: "请输入快递公司名称", maxlength: 64 },
  },
  {
    label: "面单费",
    key: "waybill_fee",
    type: "number",
    span: 24,
    // 控件与说明文字由模板里的 #waybill_fee 插槽自己渲染（要在输入框下面留一句说明）
  },
  {
    label: "运单号列表头",
    key: "waybill_no_header",
    type: "input",
    span: 24,
    props: { placeholder: "账单Excel里运单号那一列的表头文字" },
  },
  {
    label: "账单日期列表头",
    key: "bill_date_header",
    type: "input",
    span: 24,
    props: { placeholder: "账单日期列（判定账单日期是否落在所选出账月）" },
  },
  {
    label: "账单重量列表头",
    key: "bill_weight_header",
    type: "input",
    span: 24,
    props: { placeholder: "账单重量列（重量异常的输入）" },
  },
  {
    label: "其它费用列表头",
    key: "bill_other_fee_header",
    type: "input",
    span: 24,
    props: { placeholder: "账单Excel里其它费用那一列的表头文字" },
  },
  {
    label: "收件省列表头",
    key: "receiver_province_header",
    type: "input",
    span: 24,
    props: { placeholder: "收件省列（省份异常的输入，五项表头全部必填）" },
  },
  {
    key: "status",
    label: "状态",
    type: "radiogroup",
    span: 24,
    props: {
      options: [
        { label: "正常", value: 0 },
        { label: "停用", value: 1 },
      ],
    },
  },
]);

function buildRowActions(row: BillParseRuleTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_ecommerce:bill_parse_rule:detail",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      icon: "ri:edit-2-line",
      perm: "module_ecommerce:bill_parse_rule:update",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      icon: "ri:delete-bin-4-line",
      perm: "module_ecommerce:bill_parse_rule:delete",
      run: () => {
        if (row.id != null) deleteRow(row.id, row.bill_type ?? "");
      },
    },
  ];
}

function formatOperationCell(row: BillParseRuleTable) {
  return renderTableOperationCell(buildRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteRow(id: number, name: string) {
  try {
    await confirmDelete(
      `确定删除「${name}」的解析规则吗？规则是物理删除、不可恢复：已导入的账单行不受影响，但这个快递类型以后再也接不进账单，除非重新建档。`
    );
    await BillParseRuleAPI.deleteBillParseRule([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
    emit("changed");
  } catch {
    // 用户取消
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  const names = selectedRows.value.map((r) => String(r.bill_type ?? r.id));
  const detail = `${names.slice(0, 5).join("、")}${names.length > 5 ? `…等${ids.length}条` : ""}`;
  try {
    await confirmDelete(
      `确定删除选中的 ${ids.length} 条解析规则（${detail}）吗？规则是物理删除、不可恢复：已导入的账单行不受影响，但这些快递类型以后再也接不进账单，除非重新建档。`
    );
    batchDeleting.value = true;
    await BillParseRuleAPI.deleteBillParseRule(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
    emit("changed");
  } catch {
    // 用户取消
  } finally {
    batchDeleting.value = false;
  }
}
</script>
