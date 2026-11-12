<!-- 店铺(Store) 管理：FA + useTable，含负责人(tag)多选关联 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="storeSearchItems"
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
          <FaTableHeaderLeft :remove-ids="selectedIds">
            <ElSpace>
              <ElButton
                v-hasPerm="['module_ecommerce:store:create']"
                type="primary"
                :icon="Plus"
                :loading="createLoading"
                plain
                @click="handleAdd"
              >
                新增
              </ElButton>
              <ElButton
                v-hasPerm="['module_ecommerce:store:update']"
                type="warning"
                plain
                :disabled="selectedIds.length === 0"
                @click="openBatchTagDialog"
              >
                {{
                  selectedIds.length > 0
                    ? `批量修改负责人 (${selectedIds.length})`
                    : "批量修改负责人"
                }}
              </ElButton>
              <ElDropdown
                v-hasPerm="['module_ecommerce:store:update']"
                trigger="click"
                :disabled="selectedIds.length === 0"
                @command="handleBatchStatus"
              >
                <ElButton type="info" plain :disabled="selectedIds.length === 0">
                  批量设置状态
                  <ElIcon class="el-icon--right"><ArrowDown /></ElIcon>
                </ElButton>
                <template #dropdown>
                  <ElDropdownMenu>
                    <ElDropdownItem :command="0">批量正常</ElDropdownItem>
                    <ElDropdownItem :command="1">批量停用</ElDropdownItem>
                  </ElDropdownMenu>
                </template>
              </ElDropdown>
            </ElSpace>
          </FaTableHeaderLeft>
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
      width="640px"
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
          :items="storeDetailItems"
          max-height="70vh"
        >
          <template #tags="{ row }">
            <span v-if="!(row as StoreTable)?.tags?.length">—</span>
            <ElTag
              v-for="t in (row as StoreTable)?.tags ?? []"
              :key="t.id"
              class="mr-1"
              size="small"
              >{{ t.tag_name }}</ElTag
            >
          </template>
        </FaDescriptions>
      </template>
      <template v-else>
        <FaForm
          scrollbar
          max-height="70vh"
          :key="storeFormRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="storeDialogFormItems"
          :rules="rules"
          label-suffix=":"
          :label-width="110"
          label-position="right"
          :span="24"
          :gutter="16"
          :show-reset="false"
          :show-submit="false"
          class="crud-dialog-art-form"
        />
      </template>
    </FaDialog>

    <FaDialog
      v-model="batchTagDialogVisible"
      title="批量修改负责人"
      width="480px"
      form-mode="update"
      :confirm-loading="batchTagLoading"
      @cancel="batchTagDialogVisible = false"
      @close="batchTagDialogVisible = false"
      @confirm="submitBatchTags"
    >
      <div class="px-1 py-2">
        <div class="mb-3 text-sm text-g-600">
          将把选中的 {{ selectedIds.length }} 个店铺的负责人整体替换为下面所选（留空则清空负责人）：
        </div>
        <ElSelect
          v-model="batchTagIds"
          multiple
          filterable
          clearable
          placeholder="请选择负责人（可多选）"
          class="w-full"
        >
          <ElOption
            v-for="opt in tagOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </ElSelect>
      </div>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import StoreAPI, { TagAPI, type StoreForm, type StoreTable } from "@/api/module_ecommerce/store";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import type { FormItemRule } from "element-plus";
import { ArrowDown, Plus } from "@element-plus/icons-vue";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";

defineOptions({
  name: "EcommerceStoreTab",
  inheritAttrs: false,
});

type StoreSearchForm = {
  kuaimai_shop_id?: string;
  store_name?: string;
  platform?: string;
  status?: number;
  created_id?: number;
  updated_id?: number;
  created_time?: string[];
  updated_time?: string[];
};

const searchForm = ref<StoreSearchForm>({
  kuaimai_shop_id: undefined,
  store_name: undefined,
  platform: undefined,
  status: undefined,
  created_id: undefined,
  updated_id: undefined,
  created_time: undefined,
  updated_time: undefined,
});

const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const STATUS_OPTIONS = [
  { label: "正常", value: 0 },
  { label: "停用", value: 1 },
] as const;

// ─── 负责人(tag) 下拉选项（新建/编辑共用） ───
const tagOptions = ref<{ label: string; value: number }[]>([]);

async function loadTagOptions() {
  const res = await TagAPI.listTag({ page_no: 1, page_size: 100 });
  const items = res.data.data?.items ?? [];
  tagOptions.value = items
    .filter((t) => t.id != null)
    .map((t) => ({ label: t.tag_name ?? "", value: t.id! }));
}

const storeSearchItems = computed<SearchFormItem[]>(() => [
  {
    label: "快麦店铺id",
    key: "kuaimai_shop_id",
    type: "input",
    placeholder: "请输入快麦店铺id",
    clearable: true,
    span: 6,
  },
  {
    label: "店铺名称",
    key: "store_name",
    type: "input",
    placeholder: "请输入店铺名称",
    clearable: true,
    span: 6,
  },
  {
    label: "平台",
    key: "platform",
    type: "input",
    placeholder: "请输入平台",
    clearable: true,
    span: 6,
  },
  {
    label: "状态",
    key: "status",
    type: "select",
    props: { placeholder: "请选择状态", options: STATUS_OPTIONS, clearable: true },
    span: 6,
  },
]);

const faTableRef = ref<{ elTableRef?: { clearSelection: () => void } } | null>(null);
const { selectedIds, onTableSelectionChange } = useTableSelection<StoreTable>();

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
} = useTable({
  core: {
    apiFn: StoreAPI.listStore,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<StoreTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "store_name", label: "店铺名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "kuaimai_shop_id", label: "快麦店铺id", minWidth: 140, showOverflowTooltip: true },
      { prop: "platform", label: "所属平台", minWidth: 100, showOverflowTooltip: true },
      {
        prop: "tags",
        label: "负责人",
        minWidth: 140,
        showOverflowTooltip: true,
        formatter: (row: StoreTable) =>
          row.tags?.length ? row.tags.map((t) => t.tag_name).join("、") : "—",
      },
      {
        prop: "status",
        label: "状态",
        width: 88,
        status: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } },
      },
      {
        prop: "created_time",
        label: "创建时间",
        width: 168,
        sortable: true,
        showOverflowTooltip: true,
      },
      {
        prop: "operation",
        label: "操作",
        width: 180,
        fixed: "right",
        align: "center",
        formatter: (row: StoreTable) => formatStoreOperationCell(row),
      },
    ]),
  },
});

function buildStoreReplaceParams(p: StoreSearchForm): Record<string, unknown> {
  return {
    kuaimai_shop_id: p.kuaimai_shop_id,
    store_name: p.store_name,
    platform: p.platform,
    status: p.status,
    created_id: p.created_id,
    updated_id: p.updated_id,
    created_time:
      Array.isArray(p.created_time) && p.created_time.length === 2 ? p.created_time : undefined,
    updated_time:
      Array.isArray(p.updated_time) && p.updated_time.length === 2 ? p.updated_time : undefined,
  };
}

async function handleSearchBarSearch(params: StoreSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildStoreReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {
    kuaimai_shop_id: undefined,
    store_name: undefined,
    platform: undefined,
    status: undefined,
    created_id: undefined,
    updated_id: undefined,
    created_time: undefined,
    updated_time: undefined,
  };
  await resetSearchParams();
}

const detailFormData = ref<StoreTable>({});

const storeDetailItems: import("@/components/display/fa-descriptions/index.vue").DescriptionsItem[] =
  [
    { label: "店铺名称", prop: "store_name" },
    { label: "快麦店铺id", prop: "kuaimai_shop_id" },
    { label: "所属平台", prop: "platform" },
    {
      label: "状态",
      prop: "status",
      tag: { map: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } } },
    },
    { label: "负责人", prop: "tags", slot: "tags", span: 2 },
    { label: "创建时间", prop: "created_time" },
    { label: "更新时间", prop: "updated_time" },
  ];

const formData = ref<StoreForm & { tag_ids?: number[] }>({
  id: undefined,
  kuaimai_shop_id: undefined,
  store_name: undefined,
  platform: undefined,
  status: 0,
  tag_ids: [],
});

const initialFormData: StoreForm & { tag_ids?: number[] } = {
  id: undefined,
  kuaimai_shop_id: undefined,
  store_name: undefined,
  platform: undefined,
  status: 0,
  tag_ids: [],
};

const { dialogVisible } = useCrudDialog();

/** 手填的标识字段一律不许含空格（§16 A5/B3）：带空格的快麦店铺id，订单导入那边是 trim 过的，
 *  永远认不出这一条，自动建档会给同一家店再开一个门牌；纯空格更等于没填。后端也 trim 兜底。 */
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
  kuaimai_shop_id: [
    { required: true, message: "请输入快麦店铺id", trigger: "blur" },
    noSpaceRule("快麦店铺id"),
  ],
  store_name: [
    { required: true, message: "请输入店铺名称", trigger: "blur" },
    noSpaceRule("店铺名称"),
  ],
  platform: [
    { required: true, message: "请输入所属平台", trigger: "blur" },
    noSpaceRule("所属平台"),
  ],
  status: [{ required: true, message: "请选择状态", trigger: "blur" }],
});

const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const storeFormRenderKey = ref(0);

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } = useCrudForm<
  StoreForm & { tag_ids?: number[] }
>({
  formData,
  initialFormData,
  dialogVisible,
  dataFormRef,
  formRenderKey: storeFormRenderKey,
  detailApi: StoreAPI.detailStore,
  createApi: StoreAPI.createStore,
  updateApi: StoreAPI.updateStore,
  titles: { create: "新增店铺", update: "修改店铺", detail: "店铺详情" },
  detailFormData: detailFormData as any,
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
    await loadTagOptions();
    await handleOpenDialog("create");
  } finally {
    createLoading.value = false;
  }
}

const storeDialogFormItems = computed<FormItem[]>(() => [
  {
    label: "店铺名称",
    key: "store_name",
    type: "input",
    span: 24,
    props: { placeholder: "请输入店铺名称", maxlength: 128 },
  },
  {
    label: "快麦店铺id",
    key: "kuaimai_shop_id",
    type: "input",
    span: 24,
    props: {
      placeholder: "请输入快麦店铺id",
      maxlength: 64,
      disabled: dialogVisible.type === "update",
    },
  },
  {
    label: "所属平台",
    key: "platform",
    type: "input",
    span: 24,
    props: {
      placeholder: "请输入所属平台",
      maxlength: 64,
      disabled: dialogVisible.type === "update",
    },
  },
  {
    label: "负责人",
    key: "tag_ids",
    type: "select",
    span: 24,
    props: {
      placeholder: "请选择负责人（可多选）",
      options: tagOptions.value,
      multiple: true,
      filterable: true,
      class: "w-full! max-w-md",
    },
  },
  {
    key: "status",
    label: "状态",
    type: "radiogroup",
    span: 24,
    props: {
      options: [
        { label: "正常", value: 0 },
        // §16 AB9 甲：这个开关目前只是给人看的记号，不切断任何链路（停用店铺照样进利润表、
        // 照样被订单导入认出来）。不写清楚，人会以为停用了就能把它从账里摘出去。
        { label: "停用（仅标记，不影响导入与利润计算）", value: 1 },
      ],
    },
  },
]);

function buildStoreRowActions(row: StoreTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_ecommerce:store:detail",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      icon: "ri:edit-2-line",
      perm: "module_ecommerce:store:update",
      run: () => {
        if (row.id != null) void openEditDialog(row.id);
      },
    },
  ];
}

function formatStoreOperationCell(row: StoreTable) {
  return renderTableOperationCell(buildStoreRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1 store-table-actions",
  });
}

async function openEditDialog(id: number) {
  await loadTagOptions();
  await handleOpenDialog("update", id);
  // 详情返回的是 tags:[{id,tag_name}]，表单绑定 tag_ids，这里做映射以回显已选负责人
  const tags = (formData.value as { tags?: { id?: number }[] }).tags;
  formData.value.tag_ids = (tags ?? []).map((t) => t.id).filter((v): v is number => v != null);
}

// ─── 批量修改负责人 ───
const batchTagDialogVisible = ref(false);
const batchTagIds = ref<number[]>([]);
const batchTagLoading = ref(false);

async function openBatchTagDialog() {
  if (selectedIds.value.length === 0) return;
  await loadTagOptions();
  batchTagIds.value = [];
  batchTagDialogVisible.value = true;
}

async function submitBatchTags() {
  batchTagLoading.value = true;
  try {
    await StoreAPI.batchSetTags({ ids: selectedIds.value, tag_ids: batchTagIds.value });
    ElMessage.success("批量修改负责人成功");
    batchTagDialogVisible.value = false;
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshUpdate();
  } finally {
    batchTagLoading.value = false;
  }
}

// ─── 批量设置状态 ───
async function handleBatchStatus(statusValue: number) {
  const ids = selectedIds.value;
  if (ids.length === 0) return;
  const label = statusValue === 0 ? "正常" : "停用";
  try {
    await confirmAction(`确定把选中的 ${ids.length} 个店铺状态设为「${label}」吗？`);
    await StoreAPI.batchSetStatus({ ids, status: statusValue });
    ElMessage.success("批量设置状态成功");
    faTableRef.value?.elTableRef?.clearSelection();
    await refreshUpdate();
  } catch {
    // 用户取消
  }
}
</script>
