<!-- 负责人(tag) 管理：FA + useTable，参照 module_system/position 的轻量模板 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="tagSearchItems"
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
            :perm-create="['module_ecommerce:tag:create']"
            :perm-delete="['module_ecommerce:tag:delete']"
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
      width="520px"
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
          :items="tagDetailItems"
          max-height="70vh"
        />
      </template>
      <template v-else>
        <FaForm
          scrollbar
          max-height="70vh"
          :key="tagFormRenderKey"
          ref="dataFormRef"
          v-model="formData"
          :items="tagDialogFormItems"
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
import { TagAPI, type TagForm, type TagTable } from "@/api/module_ecommerce/store";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import type { FormItem } from "@/components/forms/fa-form/index.vue";
import FaForm from "@/components/forms/fa-form/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import type { FormItemRule } from "element-plus";
import { renderTableOperationCell, resolveStatusColumns, type TableOperationAction } from "@utils";

defineOptions({
  name: "EcommerceTagTab",
  inheritAttrs: false,
});

const emit = defineEmits<{ changed: [] }>();

type TagSearchForm = {
  tag_name?: string;
  status?: number;
  created_id?: number;
  updated_id?: number;
  created_time?: string[];
  updated_time?: string[];
};

const searchForm = ref<TagSearchForm>({
  tag_name: undefined,
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

const tagSearchItems = computed<SearchFormItem[]>(() => [
  {
    label: "负责人名称",
    key: "tag_name",
    type: "input",
    placeholder: "请输入负责人名称",
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
const { selectedRows, selectedIds, batchDeleting, onTableSelectionChange } =
  useTableSelection<TagTable>();

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
    apiFn: TagAPI.listTag,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<TagTable>(() => [
      { type: "selection", width: 48, fixed: "left" },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "tag_name", label: "负责人名称", minWidth: 120, showOverflowTooltip: true },
      {
        prop: "status",
        label: "状态",
        width: 88,
        status: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } },
      },
      { prop: "description", label: "备注", minWidth: 140, showOverflowTooltip: true },
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
        formatter: (row: TagTable) => formatTagOperationCell(row),
      },
    ]),
  },
});

function buildTagReplaceParams(p: TagSearchForm): Record<string, unknown> {
  return {
    tag_name: p.tag_name,
    status: p.status,
    created_id: p.created_id,
    updated_id: p.updated_id,
    created_time:
      Array.isArray(p.created_time) && p.created_time.length === 2 ? p.created_time : undefined,
    updated_time:
      Array.isArray(p.updated_time) && p.updated_time.length === 2 ? p.updated_time : undefined,
  };
}

async function handleSearchBarSearch(params: TagSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildTagReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {
    tag_name: undefined,
    status: undefined,
    created_id: undefined,
    updated_id: undefined,
    created_time: undefined,
    updated_time: undefined,
  };
  await resetSearchParams();
}

const detailFormData = ref<TagTable>({});

const tagDetailItems: import("@/components/display/fa-descriptions/index.vue").DescriptionsItem[] =
  [
    { label: "负责人名称", prop: "tag_name" },
    {
      label: "状态",
      prop: "status",
      tag: { map: { 0: { type: "success", text: "正常" }, 1: { type: "danger", text: "停用" } } },
    },
    { label: "备注", prop: "description", span: 2 },
    { label: "创建时间", prop: "created_time" },
    { label: "更新时间", prop: "updated_time" },
  ];

const formData = ref<TagForm>({
  id: undefined,
  tag_name: undefined,
  description: undefined,
  status: 0,
});

const initialFormData: TagForm = {
  id: undefined,
  tag_name: undefined,
  description: undefined,
  status: 0,
};

const { dialogVisible } = useCrudDialog();

/** 负责人名称不许含空格（§16 B3）：`张三 ` 与 `张三` 在数据库那把 ci 尺子下是同一个人，
 *  留着就会变成「Python 判重放过、唯一索引撞死」的谜案；纯空格更是等于没填。后端也 trim 兜底。 */
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
  tag_name: [
    { required: true, message: "请输入负责人名称", trigger: "blur" },
    noSpaceRule("负责人名称"),
  ],
  status: [{ required: true, message: "请选择状态", trigger: "blur" }],
});

const dataFormRef = ref<InstanceType<typeof FaForm> | null>(null);
const tagFormRenderKey = ref(0);

const { submitLoading, handleCloseDialog, handleOpenDialog, handleSubmit } = useCrudForm<TagForm>({
  formData,
  initialFormData,
  dialogVisible,
  dataFormRef,
  formRenderKey: tagFormRenderKey,
  detailApi: TagAPI.detailTag,
  createApi: TagAPI.createTag,
  updateApi: TagAPI.updateTag,
  titles: { create: "新增负责人", update: "修改负责人", detail: "负责人详情" },
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

const tagDialogFormItems = computed<FormItem[]>(() => [
  {
    label: "负责人名称",
    key: "tag_name",
    type: "input",
    span: 24,
    props: { placeholder: "请输入负责人名称", maxlength: 64 },
  },
  {
    key: "status",
    label: "状态",
    type: "radiogroup",
    span: 24,
    props: {
      options: [
        { label: "正常", value: 0 },
        // §16 AB9 甲：停用不挡挂店、不进任何算账判断，只是给人看的记号，文案必须说清楚。
        { label: "停用（仅标记，不影响挂店与利润计算）", value: 1 },
      ],
    },
  },
  {
    label: "备注",
    key: "description",
    type: "input",
    span: 24,
    props: {
      type: "textarea",
      rows: 3,
      maxlength: 255,
      showWordLimit: true,
      placeholder: "请输入备注",
    },
  },
]);

function buildTagRowActions(row: TagTable): TableOperationAction[] {
  return [
    {
      key: "detail",
      label: "详情",
      artType: "view",
      perm: "module_ecommerce:tag:detail",
      run: () => {
        if (row.id != null) void handleOpenDialog("detail", row.id);
      },
    },
    {
      key: "edit",
      label: "编辑",
      artType: "edit",
      icon: "ri:edit-2-line",
      perm: "module_ecommerce:tag:update",
      run: () => {
        if (row.id != null) void handleOpenDialog("update", row.id);
      },
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      icon: "ri:delete-bin-4-line",
      perm: "module_ecommerce:tag:delete",
      run: () => {
        if (row.id != null) deleteTagRow(row.id, row.tag_name ?? "");
      },
    },
  ];
}

function formatTagOperationCell(row: TagTable) {
  return renderTableOperationCell(buildTagRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1 tag-table-actions",
  });
}

async function deleteTagRow(id: number, name: string) {
  try {
    await confirmDelete(`确定删除「${name}」吗？`);
    await TagAPI.deleteTag([id]);
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
  try {
    await confirmBatchDelete(
      ids.length,
      selectedRows.value.map((r) => String(r.tag_name ?? r.id))
    );
    batchDeleting.value = true;
    await TagAPI.deleteTag(ids);
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
