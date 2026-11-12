<!-- 商品成本主表(SkuCost) 管理：唯一事实来源(ADR-0022)，NULL=缺失/0=有意0成本(ADR-0023) -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="skuCostSearchItems"
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
            :perm-create="['module_ecommerce:sku_cost:create']"
            :perm-delete="['module_ecommerce:sku_cost:delete']"
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
      width="560px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
      :form-mode="dialogVisible.type"
      :confirm-loading="submitLoading"
      @cancel="handleCloseDialog"
      @close="handleCloseDialog"
      @confirm="handleSubmit()"
    >
      <template v-if="dialogVisible.type === 'detail'">
        <FaDescriptions
          :column="2"
          :data="detailFormData"
          :items="skuCostDetailItems"
          max-height="70vh"
        />
      </template>
      <template v-else>
        <FaForm
          scrollbar
          max-height="70vh"
          :key="skuCostFormRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="skuCostDialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="100"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        />
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import { SkuCostAPI, type SkuCostForm, type SkuCostTable } from "@/api/module_ecommerce/order";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";
import { ElTag, type FormItemRule } from "element-plus";

defineOptions({
  name: "EcommerceSkuCostTab",
  inheritAttrs: false,
});

type SkuCostSearchForm = {
  main_sku_code?: string;
  sku_code?: string;
  product_name?: string;
  cost_source?: string;
  cost_missing?: boolean;
  created_id?: number;
  updated_id?: number;
  created_time?: string[];
  updated_time?: string[];
};

const searchForm = ref<SkuCostSearchForm>({});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const COST_SOURCE_OPTIONS = [
  { label: "手工录入", value: "manual" },
  { label: "批量导入", value: "import" },
];

const skuCostSearchItems = computed<SearchFormItem[]>(() => [
  { label: "主商家编码", key: "main_sku_code", type: "input", clearable: true, span: 6 },
  { label: "规格商家编码", key: "sku_code", type: "input", clearable: true, span: 6 },
  { label: "商品名称", key: "product_name", type: "input", clearable: true, span: 6 },
  {
    label: "成本来源",
    key: "cost_source",
    type: "select",
    props: { options: COST_SOURCE_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "成本价缺失",
    key: "cost_missing",
    type: "select",
    props: {
      options: [
        { label: "是", value: true },
        { label: "否", value: false },
      ],
      clearable: true,
    },
    span: 6,
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedRows, selectedIds, batchDeleting, onTableSelectionChange } =
  useTableSelection<SkuCostTable>();
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
    apiFn: SkuCostAPI.listSkuCost,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<SkuCostTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "main_sku_code", label: "主商家编码", minWidth: 130, showOverflowTooltip: true },
      {
        prop: "sku_code",
        label: "规格商家编码",
        minWidth: 130,
        showOverflowTooltip: true,
        formatter: (row: SkuCostTable) =>
          row.sku_code ? row.sku_code : h(ElTag, { size: "small", type: "info" }, () => "商品级"),
      },
      { prop: "product_name", label: "商品名称", minWidth: 160, showOverflowTooltip: true },
      { prop: "sku_name", label: "规格名称", minWidth: 110, showOverflowTooltip: true },
      {
        prop: "cost_price",
        label: "成本价",
        width: 100,
        formatter: (row: SkuCostTable) => (row.cost_price == null ? "缺失" : row.cost_price),
      },
      {
        prop: "cost_source",
        label: "成本来源",
        width: 100,
        status: {
          manual: { type: "primary", text: "手工录入" },
          import: { type: "info", text: "批量导入" },
        },
      },
      {
        prop: "operation",
        label: "操作",
        width: 180,
        fixed: "right",
        align: "center",
        formatter: (row: SkuCostTable) => formatOperationCell(row),
      },
    ]),
  },
});

function buildReplaceParams(p: SkuCostSearchForm): Record<string, unknown> {
  return { ...p };
}

async function handleSearchBarSearch(params: SkuCostSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {};
  await resetSearchParams();
}

const detailFormData = ref<SkuCostTable>({});

const skuCostDetailItems: import("@/components/display/fa-descriptions/index.vue").DescriptionsItem[] =
  [
    { label: "主商家编码", prop: "main_sku_code" },
    { label: "规格商家编码", prop: "sku_code" },
    { label: "商品名称", prop: "product_name" },
    { label: "规格名称", prop: "sku_name" },
    { label: "成本价", prop: "cost_price" },
    { label: "成本来源", prop: "cost_source" },
    { label: "备注", prop: "remark", span: 2 },
  ];

const formData = ref<SkuCostForm>({});
const initialFormData: SkuCostForm = {
  id: undefined,
  sku_code: undefined,
  main_sku_code: undefined,
  sku_name: undefined,
  product_name: undefined,
  cost_price: undefined,
  cost_source: "manual",
  remark: undefined,
};

const { dialogVisible } = useCrudDialog();

/** 商家编码不许含空格（§16 A1/B3）：编码是取价唯一的钥匙，带空格存进去，
 *  导入侧 trim+转大写后的那条永远对不上它；纯空格后端会归一成 None，查重条件会被整个丢掉。 */
const noSpaceRule = (label: string): FormItemRule => ({
  validator: (_rule: unknown, value: unknown, callback: (error?: Error) => void) => {
    const text = String(value ?? "");
    if (!text.trim()) return callback(new Error(`${label}不能为空（纯空格也算空）`));
    if (/\s/.test(text)) return callback(new Error(`${label}不能包含空格`));
    return callback();
  },
  trigger: "blur",
});

const rules = reactive({
  main_sku_code: [
    { required: true, message: "请输入主商家编码", trigger: "blur" },
    noSpaceRule("主商家编码"),
  ],
  product_name: [{ required: true, message: "请输入商品名称", trigger: "blur" }],
});

const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const skuCostFormRenderKey = ref(0);

// 编码一空，规格名称立刻跟着清掉：界面上锁死的那格和真正提交的内容不能各说一套
watch(
  () => formData.value.sku_code,
  (code) => {
    if (!code) formData.value.sku_name = undefined;
  }
);

/** 成本价清空后 Vue 侧是 undefined/""：undefined 会被 JSON 序列化丢键，"" 会让后端 422。
 *  统一补成显式 null，「留空=缺失」(ADR-0023) 才真能落库。 */
function toSubmitPayload(form: SkuCostForm): SkuCostForm {
  const cost = form.cost_price;
  return { ...form, cost_price: cost === undefined || cost === "" ? null : cost };
}

async function handleCreateSkuCost(form: SkuCostForm) {
  return SkuCostAPI.createSkuCost(toSubmitPayload(form));
}

async function handleUpdateSkuCost(id: number, form: SkuCostForm) {
  return SkuCostAPI.updateSkuCost(id, toSubmitPayload(form));
}

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } =
  useCrudForm<SkuCostForm>({
    formData,
    initialFormData,
    dialogVisible,
    dataFormRef,
    formRenderKey: skuCostFormRenderKey,
    detailApi: SkuCostAPI.detailSkuCost,
    createApi: handleCreateSkuCost,
    updateApi: handleUpdateSkuCost,
    titles: { create: "新增商品成本", update: "修改商品成本", detail: "商品成本详情" },
    detailFormData,
    onCreateSuccess: async () => {
      await refreshCreate();
    },
    onUpdateSuccess: async () => {
      await refreshUpdate();
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

const skuCostDialogFormItems = computed<FormItem[]>(() => {
  const isUpdate = dialogVisible.type === "update";
  // 无规格（规格商家编码为空）的行，规格名称必须留空——这一格直接锁死，不给填（AA7）
  const noSpec = !formData.value.sku_code;
  return [
    {
      label: "主商家编码",
      key: "main_sku_code",
      type: "input",
      span: 24,
      props: {
        placeholder: "请输入主商家编码，新增后不可修改",
        maxlength: 64,
        disabled: isUpdate,
      },
    },
    {
      label: "规格商家编码",
      key: "sku_code",
      type: "input",
      span: 24,
      props: {
        placeholder: "商品不分规格时留空；与主商家编码相同视为不分规格",
        maxlength: 64,
        disabled: isUpdate,
      },
    },
    {
      label: "商品名称",
      key: "product_name",
      type: "input",
      span: 24,
      props: { placeholder: "请输入商品名称", maxlength: 255 },
    },
    {
      label: "规格名称",
      key: "sku_name",
      type: "input",
      span: 24,
      props: {
        placeholder: noSpec ? "不分规格，留空" : "请输入规格名称",
        maxlength: 128,
        disabled: noSpec,
      },
    },
    {
      label: "成本价",
      key: "cost_price",
      type: "number",
      span: 24,
      props: { placeholder: "留空=缺失，0=有意0成本", controlsPosition: "right", step: 0.01 },
    },
    {
      label: "成本来源",
      key: "cost_source",
      type: "select",
      span: 24,
      props: { options: COST_SOURCE_OPTIONS },
    },
    {
      label: "备注",
      key: "remark",
      type: "input",
      span: 24,
      props: { type: "textarea", rows: 3, maxlength: 255, placeholder: "请输入备注" },
    },
  ];
});

function costRowLabel(row: SkuCostTable) {
  const main = row.main_sku_code ?? "";
  return row.sku_code ? `${main} / ${row.sku_code}` : main || String(row.id ?? "");
}

function buildRowActions(row: SkuCostTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_ecommerce:sku_cost:detail",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      icon: "ri:edit-2-line",
      perm: "module_ecommerce:sku_cost:update",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      icon: "ri:delete-bin-4-line",
      perm: "module_ecommerce:sku_cost:delete",
      run: () => {
        if (row.id != null) deleteRow(row.id, costRowLabel(row));
      },
    },
  ];
}

function formatOperationCell(row: SkuCostTable) {
  return renderTableOperationCell(buildRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

async function deleteRow(id: number, name: string) {
  try {
    await confirmDelete(`确定删除「${name}」吗？`);
    await SkuCostAPI.deleteSkuCost([id]);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消
  }
}

async function handleBatchDelete() {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  try {
    await confirmBatchDelete(
      ids.length,
      selectedRows.value.map((r) => costRowLabel(r))
    );
    batchDeleting.value = true;
    await SkuCostAPI.deleteSkuCost(ids);
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshRemove();
  } catch {
    // 用户取消
  } finally {
    batchDeleting.value = false;
  }
}
</script>
