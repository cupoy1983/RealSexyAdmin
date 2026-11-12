<!-- 账单详情（§5.2 三步式）：导入即判定 → 按状态筛着处理 → 批次确认覆盖式回写运费（ADR-0034）
     一票一行；虚假行改对要先「认领订单」（把本票运单号订正进勾选的子订单行，§22.3），
     重复行改对只翻状态；两者都能删除。**已恢复是终点**（§23，AJ 轮）：改不回、不能删、
     也不能换勾选的子订单行，界面上这条行的「改对/删除」置灰点不动（不是点了才报错，
     也不是藏起来——同列的已匹配/重量省份异常本来就是这个灰法）。
     重量省份异常只提示不动钱、不许删。 -->
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
      @search="handleSearchBarSearch"
      @reset="onResetSearch"
    >
      <!-- 账单批次：格子常驻占位，没选出账月份时是灰框（AH1）；选项label是快递公司名、末尾挂确认状态小标签（AE16/17/18/20） -->
      <template #import_batch_id>
        <ElSelect
          v-model="searchForm.import_batch_id"
          clearable
          :disabled="!searchForm.bill_month"
          :placeholder="searchForm.bill_month ? '选批次（不选=本月全部）' : '先选出账月份'"
          class="w-full"
        >
          <ElOption
            v-for="b in batchOptions"
            :key="b.batch_no"
            :label="b.carrier"
            :value="b.batch_no"
          >
            <div class="batch-option">
              <span>{{ b.carrier }}</span>
              <ElTag :type="b.confirmed ? 'success' : 'warning'" size="small" effect="light">
                {{ b.confirmed ? "已确认" : "未确认" }}
              </ElTag>
            </div>
          </ElOption>
        </ElSelect>
      </template>
    </FaSearchBar>

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshAll"
      >
        <template #left>
          <ElButton
            v-hasPerm="['module_ecommerce:logistics:import']"
            type="primary"
            :icon="UploadFilled"
            @click="openImport"
            >导入账单</ElButton
          >
          <ElButton
            v-hasPerm="['module_ecommerce:batch:confirm']"
            type="warning"
            :icon="Select"
            @click="openConfirm"
            >确认账单批次</ElButton
          >
          <ElButton
            v-hasPerm="['module_ecommerce:batch:delete']"
            type="danger"
            :icon="Delete"
            @click="openDiscard"
            >作废账单批次</ElButton
          >
        </template>
      </FaTableHeader>

      <ElAlert
        v-if="batchCounts"
        class="bill-count-line"
        type="info"
        :closable="false"
        show-icon
        :title="countLineText(batchCounts)"
      />

      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <!-- 步骤1：导入账单（账单类型 + 出账月与选文件同框，落库即判定，不弹窗不试算） -->
    <FaImportDialog
      ref="importDialogRef"
      v-model="importVisible"
      title="导入快递账单"
      width="600px"
      :content-config="importContentConfig"
      :show-template-download="false"
      :upload-data="uploadExtraData"
      :loading="uploadLoading"
      :confirm-disabled="!importBillType || !importBillMonth"
      note="按账单类型的列映射解析，导入即判定虚假/重复/重量省份（§5.2）；同月同类型只允许导入一次"
      @upload="handleImportUpload"
    >
      <template #form>
        <ElFormItem label="账单类型" label-width="100px" label-position="left" required>
          <ElSelect
            v-model="importBillType"
            placeholder="请选择账单类型（对应解析规则）"
            class="w-full"
          >
            <ElOption
              v-for="r in billTypeOptions"
              :key="r.value"
              :label="r.label"
              :value="r.value"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="出账月份" label-width="100px" label-position="left" required>
          <FaDatePicker
            v-model="importBillMonth"
            type="month"
            :disabled-date="disabledBillMonth"
            placeholder="只能选本月往前 3 个整月（不含本月）"
          />
        </ElFormItem>
      </template>
    </FaImportDialog>

    <!-- 步骤3：确认账单批次（还有虚假/重复未处理时后端硬拦） -->
    <FaDialog
      v-model="confirmDialogVisible"
      title="确认账单批次"
      width="560px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
    >
      <ElForm label-width="100px">
        <ElFormItem label="批次" required>
          <ElSelect
            v-model="confirmBatchNo"
            placeholder="请选择待确认的账单批次"
            class="w-full"
            @change="loadConfirmCounts"
          >
            <ElOption
              v-for="b in unconfirmedBills"
              :key="b.batch_no"
              :label="`${b.batch_no}（${b.row_count ?? 0} 行 / ${b.order_count ?? 0} 票）`"
              :value="b.batch_no ?? ''"
            />
          </ElSelect>
        </ElFormItem>
      </ElForm>
      <ElAlert
        v-if="confirmCounts"
        type="info"
        :closable="false"
        show-icon
        :title="countLineText(confirmCounts)"
      />
      <!-- 批次备注是「对账结果」唯一的落点（ADR-0037：合计行对不上不拒绝入库、没有弹窗、没有门禁），
           所以确认前必须在这一屏让人看见——否则那行备注等于没写。 -->
      <ElAlert
        v-if="confirmBatchRemark"
        type="warning"
        :closable="false"
        show-icon
        title="导入时的对账结果"
        :description="confirmBatchRemark"
      />
      <ElAlert
        v-if="confirmCounts && confirmCounts.fake + confirmCounts.duplicate > 0"
        type="error"
        :closable="false"
        show-icon
        title="还有虚假/重复行未处理，确认会被拒绝：请先删除或改对置「已恢复」。"
      />
      <ElAlert
        type="warning"
        :closable="false"
        show-icon
        title="确认是覆盖式回写：原件行的运费按重量摊到该票各 ERP发货 子订单，并刷新主订单运费求和。确认后批次不可再改动、不支持反确认。"
      />
      <template #footer>
        <ElButton @click="confirmDialogVisible = false">取消</ElButton>
        <ElButton
          type="primary"
          :loading="confirmLoading"
          :disabled="!confirmBatchNo"
          @click="handleConfirmBatch"
        >
          确认
        </ElButton>
      </template>
    </FaDialog>

    <!-- 作废账单批次（ADR-0033）：只针对未确认批次，物理删除，不做软删 -->
    <FaDialog
      v-model="discardDialogVisible"
      title="作废账单批次"
      width="560px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
    >
      <ElForm label-width="100px">
        <ElFormItem label="批次" required>
          <ElSelect
            v-model="discardBatchNo"
            placeholder="请选择待作废的账单批次"
            class="w-full"
            @change="loadDiscardCounts"
          >
            <ElOption
              v-for="b in unconfirmedBills"
              :key="b.batch_no"
              :label="`${b.batch_no}（${b.row_count ?? 0} 行 / ${b.order_count ?? 0} 票）`"
              :value="b.batch_no ?? ''"
            />
          </ElSelect>
        </ElFormItem>
      </ElForm>
      <ElAlert
        v-if="discardCounts"
        type="info"
        :closable="false"
        show-icon
        :title="countLineText(discardCounts)"
      />
      <ElAlert
        type="error"
        :closable="false"
        show-icon
        title="作废是完全作废：该批次连同它名下全部账单行（含已逻辑删除的行）一起从数据库删掉，不可恢复。删完同月同快递类型可以重新导入。已确认的批次不在这里，订正只能改数据库。"
      />
      <template #footer>
        <ElButton @click="discardDialogVisible = false">取消</ElButton>
        <ElButton
          type="danger"
          :loading="discardLoading"
          :disabled="!discardBatchNo"
          @click="handleDiscardBatch"
        >
          确定作废（不可恢复）
        </ElButton>
      </template>
    </FaDialog>

    <!-- 认领订单 + 人工改对（§22.3 AI 轮 ＋ §23 AJ 轮）：虚假行的运单号在订单里查不到，改对前先把这一票
         属于哪张单指认出来——手段是把运单号订正进勾选的子订单行，摊分规则一个字不改。
         这一步会改订单数据，且**一条账单行只有一次认领机会**：写完就是终点，不能改回也不能换订单，
         所以写入之前还要再点一次确认。 -->
    <FaDialog
      v-model="claimVisible"
      :title="claimTitle"
      width="720px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
    >
      <ElAlert type="info" :closable="false" show-icon :title="claimTip" />
      <ElForm label-width="100px" class="claim-form">
        <ElFormItem v-if="claimNeedsOrder" label="系统订单号" required>
          <div class="claim-order-line">
            <ElInput
              v-model="claimOrderNo"
              clearable
              placeholder="手打这一票属于哪个系统订单号（一票只填一个）"
              @keyup.enter="loadClaimCandidates"
            />
            <ElButton :loading="claimCandidateLoading" @click="loadClaimCandidates"
              >查子订单</ElButton
            >
          </div>
        </ElFormItem>
        <ElFormItem label="改对说明" required>
          <ElInput
            v-model="claimRemark"
            type="textarea"
            :rows="2"
            maxlength="255"
            placeholder="为什么判定这一行是改对（追加到异常原因，认领时还会带上快递公司名）"
          />
        </ElFormItem>
      </ElForm>

      <div v-if="claimNeedsOrder" class="claim-candidates">
        <div v-if="claimCandidates.length" class="claim-candidates-title">
          勾选这一票实际发出去的那几条子订单（多选＝一票分几包发）：
        </div>
        <div v-for="d in claimCandidates" :key="d.id" class="claim-row">
          <ElCheckbox
            :model-value="claimSelectedIds.includes(d.id as number)"
            :disabled="!!claimRowDisabledReason(d)"
            @change="toggleClaimRow(d.id as number)"
          >
            <span class="claim-row-main">
              {{ d.product_name }}
              <template v-if="d.sku_name">／{{ d.sku_name }}</template>
              · 实发 {{ d.ship_qty ?? 0 }} 件 · 重量 {{ d.weight_kg ?? "空" }}kg
            </span>
            <!-- 业务方点名要看得见这两格：靠它们判断该勾哪条（§22.3 步骤③） -->
            <span class="claim-row-waybill">
              快递模版：{{ d.courier_template || "空" }} · 快递单号：{{ d.tracking_no || "空" }} ·
              发货：{{ d.ship_time || "空" }}
              <span v-if="claimRowDisabledReason(d)" class="claim-row-blocked"
                >（{{ claimRowDisabledReason(d) }}）</span
              >
            </span>
          </ElCheckbox>
        </div>
        <!-- 整票摊不下去（§26.6 第 6 条）：后端认领当场就会拒、确认批次时也会拒，这里提前把确定键按住 -->
        <div v-if="claimCannotApportion" class="claim-candidates-empty">
          这一票勾选行的重量与实发件数都是 0，运费摊不下去——改勾别的行，或补上重量再来。
        </div>
        <div v-if="claimOrderNo && !claimCandidates.length" class="claim-candidates-empty">
          已经查过一次：这个系统订单号在订单表里没有子订单，或它压根不存在。核对一下有没有多打空格。
        </div>
      </div>

      <template #footer>
        <ElButton @click="claimVisible = false">取消</ElButton>
        <ElButton
          type="primary"
          :loading="claimLoading"
          :disabled="claimCannotApportion"
          @click="handleClaimSubmit"
        >
          置为已恢复
        </ElButton>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import LogisticsAPI, {
  BILL_STATUS_OPTIONS,
  BillParseRuleAPI,
  type BillBatchCount,
  type BillRowStatus,
  type CourierBillPageQuery,
  type CourierBillTable,
} from "@/api/module_ecommerce/logistics";
import BatchAPI, { type ImportBatchTable } from "@/api/module_ecommerce/batch";
import OrderAPI, { type OrderDetailRow } from "@/api/module_ecommerce/order";
import StoreAPI, { type StoreTable } from "@/api/module_ecommerce/store";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import {
  makeStatusFormatter,
  renderTableOperationCell,
  resolveStatusColumns,
  type TableOperationAction,
} from "@utils";
import { Delete, Select, UploadFilled } from "@element-plus/icons-vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { IContentConfig } from "@/components/modal/types";

defineOptions({
  name: "EcommerceBillDetailTab",
  inheritAttrs: false,
});

type SearchForm = {
  import_batch_id?: string;
  bill_month?: string;
  waybill_no?: string;
  store_id?: number;
  status?: BillRowStatus;
};

const searchForm = ref<SearchForm>({});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const storeOptions = ref<{ label: string; value: number }[]>([]);
async function loadStoreOptions() {
  const res = await StoreAPI.listStore({ page_no: 1, page_size: 100 });
  const items: StoreTable[] = res.data.data?.items ?? [];
  storeOptions.value = items
    .filter((s) => s.id != null)
    .map((s) => ({ label: s.store_name ?? `店铺#${s.id}`, value: s.id as number }));
}

const searchItems = computed<SearchFormItem[]>(() => [
  {
    label: "出账月份",
    key: "bill_month",
    type: "date",
    props: {
      type: "month",
      placeholder: "近 12 个月",
      disabledDate: disabledQueryMonth,
    },
    span: 6,
  },
  // 批次下拉由出账月份决定能不能点；格子本身常驻占位（AE15 的「不选就没有这个框」已被 AH1 改掉：
  // 框架对 hidden 是整格 filter 掉，后面的框会跟着左跳）
  {
    label: "账单批次",
    key: "import_batch_id",
    span: 6,
  },
  {
    label: "行状态",
    key: "status",
    type: "select",
    props: { options: BILL_STATUS_OPTIONS, clearable: true },
    span: 6,
  },
  { label: "运单号", key: "waybill_no", type: "input", clearable: true, span: 6 },

  {
    label: "店铺名称",
    key: "store_id",
    type: "select",
    props: { options: storeOptions.value, clearable: true, filterable: true },
    span: 6,
  },
]);

// 行状态五值（ADR-0034）：虚假/重复要人工处理，其余三值是参与回写的原件行
const STATUS_TAG = {
  matched: { type: "success", text: "已匹配" },
  weight_province: { type: "warning", text: "重量省份异常" },
  restored: { type: "primary", text: "已恢复" },
  fake: { type: "danger", text: "虚假异常" },
  duplicate: { type: "danger", text: "重复异常" },
} as const;

// 批次状态三值：钱的去向只有前两种（未确认=橙、已确认=绿，沿用页内色板）；红色不是业务状态，
// 是脏数据报警——批次行被手工删掉、名下账单行还挂着它的编号（AE13 乙 + AE14，ADR-0033）。
// 必须声明在 `useTable` 之前：列工厂 `() => [...]` 在 setup 里就会被调用一趟，那一趟直接读这个常量。
const BATCH_STATE_TAG = {
  unconfirmed: { type: "warning", text: "未确认" },
  confirmed: { type: "success", text: "已确认" },
  missing: { type: "danger", text: "批次已不存在" },
} as const;

type BatchState = keyof typeof BATCH_STATE_TAG;

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
} = useTable({
  core: {
    apiFn: LogisticsAPI.listCourierBill,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<CourierBillTable>(() => [
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "waybill_no", label: "运单号", minWidth: 140, showOverflowTooltip: true },
      { prop: "system_order_nos", label: "系统订单号", minWidth: 150, showOverflowTooltip: true },
      { prop: "detail_count", label: "子订单数", width: 90 },
      { prop: "status", label: "行状态", width: 120, status: STATUS_TAG },
      { prop: "actual_freight", label: "计入成本", width: 100 },
      { prop: "bill_date", label: "账单日期", width: 110 },
      { prop: "bill_month", label: "出账月", width: 90 },
      { prop: "bill_type", label: "账单类型", width: 100 },
      { prop: "bill_weight", label: "账单重量(kg)", width: 110 },
      { prop: "receiver_province", label: "收件省", width: 90 },
      { prop: "store_name", label: "店铺", minWidth: 130, showOverflowTooltip: true },
      { prop: "waybill_fee", label: "面单费", width: 90 },
      { prop: "bill_other_fee", label: "其它费用", width: 90 },
      { prop: "anomaly_remark", label: "异常原因", minWidth: 220, showOverflowTooltip: true },
      {
        // prop 是合成键：FaTable 与列设置都按 prop 做 v-for 的 key，跟「批次号」同键会撞车
        prop: "batch_status",
        label: "批次状态",
        width: 120,
        formatter: makeStatusFormatter<CourierBillTable>({
          valueGetter: (row) => batchStateOf(row.import_batch_id),
          map: BATCH_STATE_TAG,
        }),
      },
      { prop: "import_batch_id", label: "批次号", minWidth: 160, showOverflowTooltip: true },
      {
        prop: "operation",
        label: "操作",
        width: 190,
        fixed: "right",
        align: "center",
        formatter: (row: CourierBillTable) => formatOperationCell(row),
      },
    ]),
  },
});

// 虚假/重复只出现在未确认批次里（确认时已清空），所以行状态足以判定能不能动
const EDITABLE_STATUSES: BillRowStatus[] = ["fake", "duplicate"];

/** 改对与删除共用一道判据：已恢复是终点（§23），界面上不再给它任何写入口 */
function canProcess(row: CourierBillTable): boolean {
  return (
    EDITABLE_STATUSES.includes(row.status as BillRowStatus) &&
    batchStateOf(row.import_batch_id) !== "confirmed"
  );
}

function buildRowActions(row: CourierBillTable): TableOperationAction[] {
  return [
    {
      key: "restore",
      label: "改对",
      artType: "edit",
      perm: "module_ecommerce:logistics:update",
      disabled: !canProcess(row),
      run: () => void openClaimDialog(row),
    },
    {
      key: "delete",
      label: "删除",
      artType: "delete",
      icon: "ri:delete-bin-4-line",
      perm: "module_ecommerce:logistics:delete",
      disabled: !canProcess(row),
      run: () => void handleDelete(row),
    },
  ];
}

function formatOperationCell(row: CourierBillTable) {
  return renderTableOperationCell(buildRowActions(row), {
    wrapperClass: "inline-flex flex-wrap items-center justify-end gap-1",
  });
}

// ── 认领订单 + 人工改对（§22.3 AI 轮）──────────────────────────────────────
const claimVisible = ref(false);
const claimLoading = ref(false);
const claimCandidateLoading = ref(false);
const claimRow = ref<CourierBillTable | null>(null);
const claimOrderNo = ref("");
const claimRemark = ref("");
const claimCandidates = ref<OrderDetailRow[]>([]);
const claimSelectedIds = ref<number[]>([]);

/** 只有虚假行要带着订单号走认领；重复行的单号本来就查得到子订单，只翻状态（§22.3 步骤①） */
const claimNeedsOrder = computed(() => claimRow.value?.status === "fake");

/**
 * 勾选框灰掉的原因（§26.6 第 2、3 条闸落到界面上）；返回空串表示这一行能勾。
 * 后端 `_check_claim_gates` 判的是同两条，这里拦得住是因为 `OrderDetailOutSchema`
 * 把 `distribution_order_type` 带回来了（那个字段原先不在响应里，前端压根算不出来）。
 */
function claimRowDisabledReason(d: OrderDetailRow): string {
  if (d.is_erp_shipped === false) return "没走 ERP 发货，挂上也匹配不到，不能勾";
  if (d.distribution_order_type) return "分销订单不参与账单匹配（ADR-0036），不能勾";
  return "";
}

const claimSelectedRows = computed(() =>
  claimCandidates.value.filter((d) => claimSelectedIds.value.includes(d.id as number))
);

/**
 * 整票摊不下去（§26.6 第 6 条）。判据与后端逐字一致：比例尺 Σ(重量×件数) 与退化用的 Σ件数
 * 同时为 0——所以「有重量但件数填 0」也算摊不下去，光看重量合计会漏。
 */
const claimCannotApportion = computed(() => {
  if (!claimNeedsOrder.value || !claimSelectedRows.value.length) return false;
  const qtySum = claimSelectedRows.value.reduce((sum, d) => sum + Number(d.ship_qty ?? 0), 0);
  const weightSum = claimSelectedRows.value.reduce(
    (sum, d) => sum + Number(d.weight_kg ?? 0) * Number(d.ship_qty ?? 0),
    0
  );
  return weightSum <= 0 && qtySum <= 0;
});

const claimTitle = computed(() => {
  const waybill = claimRow.value?.waybill_no ?? "";
  return claimNeedsOrder.value ? `认领订单并改对：运单 ${waybill}` : `人工改对：运单 ${waybill}`;
});

const claimTip = computed(() => {
  switch (claimRow.value?.status) {
    case "duplicate":
      return "重复行的运单号本来就查得到子订单，不用填系统订单号。改对之后这一票的「面单费+其它费用」开始计入成本，仍在批次确认那一刻才真正写进订单。";
    case "fake":
      return "这一票在订单里查不到「ERP发货且非分销」的子订单才判成虚假。填系统订单号、勾选这一票实际发出去的子订单行——运单号会订正进那几条子订单，匹配与摊分照原有规则走，不另开一条算钱的线路。勾选一旦写下去就是终点：不能改回虚假、不能删这一行、也不能换订单，写错了只能直接修改数据库（§23）。";
    default:
      return "";
  }
});

function openClaimDialog(row: CourierBillTable) {
  claimRow.value = row;
  claimOrderNo.value = "";
  claimRemark.value = "";
  claimCandidates.value = [];
  claimSelectedIds.value = [];
  claimVisible.value = true;
}

async function loadClaimCandidates() {
  const orderNo = claimOrderNo.value.trim();
  claimCandidates.value = [];
  claimSelectedIds.value = [];
  if (!orderNo) {
    ElMessage.warning("先填系统订单号（一票只填一个）");
    return;
  }
  claimCandidateLoading.value = true;
  try {
    const res = await OrderAPI.listOrder({ page_no: 1, page_size: 20, system_order_no: orderNo });
    // 后端这一格是 like 查询：一个号是另一个号的前缀时会多回来几条，只认完全相等的那一条
    const order = (res.data.data?.items ?? []).find((o) => o.system_order_no === orderNo);
    if (!order?.id) {
      ElMessage.warning(`系统订单号 ${orderNo} 在订单表里查不到（核对有没有多打空格）`);
      return;
    }
    const detailRes = await OrderAPI.listOrderDetails(order.id);
    claimCandidates.value = detailRes.data.data ?? [];
    if (!claimCandidates.value.length) {
      ElMessage.warning(`订单 ${orderNo} 名下没有子订单`);
    }
  } catch (error: unknown) {
    // 候选行走的是订单详情接口（权限 module_ecommerce:order:detail）：只有账单权限的账号会在这儿被拦
    if (import.meta.env.DEV) console.error(error);
  } finally {
    claimCandidateLoading.value = false;
  }
}

function toggleClaimRow(id: number) {
  const index = claimSelectedIds.value.indexOf(id);
  if (index >= 0) {
    claimSelectedIds.value.splice(index, 1);
    return;
  }
  claimSelectedIds.value.push(id);
}

async function handleClaimSubmit() {
  const row = claimRow.value;
  if (!row) return;
  const remark = claimRemark.value.trim();
  if (!remark) {
    ElMessage.warning("请说明改对的理由（会追加到异常原因）");
    return;
  }
  const orderNo = claimOrderNo.value.trim();
  if (claimNeedsOrder.value && (!orderNo || !claimSelectedIds.value.length)) {
    ElMessage.warning("认领订单要填系统订单号，并勾选至少一条子订单行");
    return;
  }
  if (claimNeedsOrder.value) {
    // 这一步动的是订单数据，且写完就没有任何界面补救了，所以写入前必须再点一次确认（§22.3 步骤④＋§23.3）
    try {
      await ElMessageBox.confirm(
        `会把运单号 ${row.waybill_no} 写入勾选的 ${claimSelectedIds.value.length} 条子订单行的「快递单号」，并在它们各自的更新备注里记下原来的单号。` +
          "这一步写下去就不能改：这一行会变成「已恢复」，不能改回虚假、不能删、也不能换勾选的子订单行，勾错了只能直接修改数据库。确认写入？",
        "这一步会修改订单数据，且不可撤销",
        { type: "warning", confirmButtonText: "确认写入", cancelButtonText: "先不写" }
      );
    } catch {
      return;
    }
  }
  claimLoading.value = true;
  try {
    const res = await LogisticsAPI.restoreCourierBill({
      ids: [row.id as number],
      remark,
      ...(claimNeedsOrder.value
        ? { system_order_no: orderNo, detail_ids: [...claimSelectedIds.value] }
        : {}),
    });
    ElMessage.success(
      claimNeedsOrder.value
        ? `已恢复 ${res.data.data ?? 0} 行：运费仍在批次确认那一刻才摊进子订单`
        : `已恢复 ${res.data.data ?? 0} 行`
    );
    claimVisible.value = false;
    await refreshAll();
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    claimLoading.value = false;
  }
}

async function handleDelete(row: CourierBillTable) {
  try {
    await confirmDelete(
      `确定删除运单号 ${row.waybill_no} 这一行吗？（逻辑删除，仅未确认批次的虚假/重复行可删。删掉本批次最后一条账单行时，批次连同它名下已删除的行一并物理删除，不可恢复）`
    );
  } catch {
    return;
  }
  const result = await LogisticsAPI.deleteCourierBill([row.id as number]);
  ElMessage.success(result.data.data || "删除成功");
  await refreshAll();
}

// ── 批次：状态判定 + 顶部计数 + 由出账月份带出的下拉 ─────────────────────────
const batchStatus = reactive<Record<string, string>>({});
const batchCounts = ref<BillBatchCount | null>(null);

function batchStateOf(batchNo?: string | null): BatchState {
  if (!batchNo) return "missing";
  const status = batchStatus[batchNo];
  if (status === "confirmed" || status === "unconfirmed") return status;
  return "missing";
}

function countLineText(c: BillBatchCount | null): string {
  if (!c) return "";
  const distribution = Object.entries(c.ship_month_distribution ?? {})
    .map(([month, count]) => `${month} ${count} 票`)
    .join("、");
  return (
    `本批次合计 ${c.total} 行：已匹配 ${c.matched}、已恢复 ${c.restored}、重量省份 ${c.weight_province}、` +
    `虚假 ${c.fake}、重复 ${c.duplicate}（虚假/重复未处理完不让确认）` +
    (distribution ? `；发货月分布：${distribution}（按票去重，挂不上订单的不计）` : "")
  );
}

async function loadBatchCounts() {
  const batchNo = searchForm.value.import_batch_id?.trim();
  if (!batchNo) {
    batchCounts.value = null;
    return;
  }
  try {
    const res = await LogisticsAPI.billBatchCount(batchNo);
    batchCounts.value = res.data.data ?? null;
  } catch {
    batchCounts.value = null;
  }
}

async function loadBatchStatuses(): Promise<Set<string>> {
  const res = await BatchAPI.listBatch({ page_no: 1, page_size: 100, file_category: "bill" });
  const batchNos = new Set<string>();
  // 整张表重建而不是累加：留着已消失批次的旧状态，「批次已不存在」就永远标不出来
  for (const key of Object.keys(batchStatus)) {
    delete batchStatus[key];
  }
  for (const b of res.data.data?.items ?? []) {
    if (b.batch_no) {
      batchStatus[b.batch_no] = b.status ?? "unconfirmed";
      batchNos.add(b.batch_no);
    }
  }
  return batchNos;
}

// ── 账单批次下拉：选项只由出账月份决定（AE15/16/17/20，设计文档 19.1） ─────────
const batchOptions = ref<{ batch_no: string; carrier: string; confirmed: boolean }[]>([]);
const carrierNames = ref<Record<string, string>>({});

/** 下拉label用快递公司名而不是 bill_type 编码；规则停用或删掉后老批次仍要能读出名字 */
async function loadCarrierNames() {
  const res = await BillParseRuleAPI.listBillParseRule({ page_no: 1, page_size: 100 });
  const map: Record<string, string> = {};
  for (const r of res.data.data?.items ?? []) {
    if (r.bill_type) {
      map[r.bill_type] = r.carrier_name ?? r.bill_type;
    }
  }
  carrierNames.value = map;
}

async function loadBatchOptions() {
  const month = searchForm.value.bill_month;
  if (!month) {
    batchOptions.value = [];
    return;
  }
  const res = await BatchAPI.listBatch({
    page_no: 1,
    page_size: 100,
    file_category: "bill",
    batch_month: month,
  });
  batchOptions.value = (res.data.data?.items ?? [])
    .map((b) => {
      const batchNo = b.batch_no ?? "";
      return {
        batch_no: batchNo,
        carrier: carrierNames.value[b.sub_category ?? ""] ?? b.sub_category ?? batchNo,
        confirmed: b.status === "confirmed",
      };
    })
    .filter((o) => o.batch_no)
    .sort((a, b) => a.carrier.localeCompare(b.carrier, "zh-CN"));
}

// 月份一变就重取该月批次并清掉原选中项：留着上月批次配新月份去查，交集恒空，界面看着像
// 「这个月还没导账」，其实是两个条件打架（AE16）。清的是表单值，列表仍等点「查询」。
watch(
  () => searchForm.value.bill_month,
  async () => {
    searchForm.value.import_batch_id = undefined;
    await loadBatchOptions();
  }
);

async function refreshAll() {
  await Promise.all([refreshData(), loadBatchStatuses(), loadBatchCounts(), loadBatchOptions()]);
}

// ── 导入后的回读：等这一笔真正落库 ──────────────────────────────────────
// 后端一个请求一个事务，而提交发生在把「导入成功」回给浏览器之后；刚拿到回执就去刷既读不到
// 账单行（列表空），也会让批次计数报「批次不存在」。批次行和它的账单行是同一次提交，所以用
// 不会报错的批次列表探到新批次号已经出现，再照常刷新。
const IMPORT_LANDED_PROBE_TIMES = 10;
const IMPORT_LANDED_PROBE_INTERVAL = 400;

async function refreshAfterImport(batchNo?: string) {
  for (let i = 0; i < IMPORT_LANDED_PROBE_TIMES; i += 1) {
    if (!batchNo || (await loadBatchStatuses()).has(batchNo)) break;
    await new Promise((resolve) => setTimeout(resolve, IMPORT_LANDED_PROBE_INTERVAL));
  }
  await refreshAll();
}

/**
 * 表单→筛选项：空值的键干脆不带。`replaceSearchParams` 是整包替换，直接写
 * `{ import_batch_id: "" }` 会把出账月份一起抹掉，界面留着筛选、列表却变了范围。
 */
function currentFormParams(): Partial<CourierBillPageQuery> {
  const out: Partial<CourierBillPageQuery> = {};
  for (const [key, value] of Object.entries(searchForm.value)) {
    if (value === undefined || value === null || value === "") continue;
    Object.assign(out, { [key]: value });
  }
  return out;
}

async function handleSearchBarSearch(params: SearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams({ ...params });
  await getData();
  await loadBatchCounts();
}

async function onResetSearch() {
  searchForm.value = {};
  batchCounts.value = null;
  await resetSearchParams();
}

// ── 步骤1：导入账单 ─────────────────────────────────────────────────────
const importVisible = ref(false);
const importDialogRef = ref<{ handleClose: () => void }>();
const uploadLoading = ref(false);
const importBillType = ref<string>();
const importBillMonth = ref<string>();
const billTypeOptions = ref<{ label: string; value: string }[]>([]);

async function loadBillTypeOptions() {
  const res = await BillParseRuleAPI.listBillParseRule({ page_no: 1, page_size: 100, status: 0 });
  billTypeOptions.value = (res.data.data?.items ?? []).map((r) => ({
    label: `${r.carrier_name}（${r.bill_type}）`,
    value: r.bill_type ?? "",
  }));
}

async function openImport() {
  await loadBillTypeOptions();
  importBillType.value = undefined;
  importBillMonth.value = undefined;
  importVisible.value = true;
}

/** 可导入月份窗口=本月往前 3 个整月、不含本月（ADR-0033：窗口只是界面护栏，接口只拦格式与未来月） */
function monthIndex(d: Date) {
  return d.getFullYear() * 12 + d.getMonth();
}

function disabledBillMonth(date: Date): boolean {
  const now = new Date();
  const idx = monthIndex(date);
  return (
    idx > monthIndex(new Date(now.getFullYear(), now.getMonth() - 1, 1)) ||
    idx < monthIndex(new Date(now.getFullYear(), now.getMonth() - 3, 1))
  );
}

/**
 * 可查询月份窗口=本月往前 12 个整月、不含本月及以后（CONTEXT.md「可查询月份窗口」）。
 * 窗口 ⊇ 可导入窗口；账单最快次月出，所以本月压根没有批次。超期的账要翻走 SQL（AE16 + AE22 甲）。
 */
function disabledQueryMonth(date: Date): boolean {
  const now = new Date();
  const idx = monthIndex(date);
  return (
    idx > monthIndex(new Date(now.getFullYear(), now.getMonth() - 1, 1)) ||
    idx < monthIndex(new Date(now.getFullYear(), now.getMonth() - 12, 1))
  );
}

const uploadExtraData = computed(() => ({
  bill_type: importBillType.value ?? "",
  bill_month: importBillMonth.value ?? "",
}));

const importContentConfig = computed<IContentConfig>(() => ({
  permPrefix: "module_ecommerce:logistics",
  cols: [],
  indexAction: async () => ({}),
}));

async function handleImportUpload(formData: FormData) {
  uploadLoading.value = true;
  try {
    const res = await LogisticsAPI.importBill(formData);
    const result = res.data.data;
    // 关框的同时清掉已选文件：同框之后残留的文件会被下一次「确定」重复上传
    importDialogRef.value?.handleClose();
    ElMessage.success(result?.message || "导入成功");
    // 导完落到「这一月 + 这一个批次」：先写月份，等 watch 把旧批次清掉，再写新批次号，
    // 否则新批次会因为不在下拉选项里而只显示出一串编码（AE15/16）。
    searchForm.value.bill_month = importBillMonth.value;
    await nextTick();
    searchForm.value.import_batch_id = result?.batch_no;
    replaceSearchParams(currentFormParams());
    await refreshAfterImport(result?.batch_no);
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    uploadLoading.value = false;
  }
}

// ── 步骤3：确认账单批次 ─────────────────────────────────────────────────
const confirmDialogVisible = ref(false);
const confirmLoading = ref(false);
const confirmBatchNo = ref<string>();
const confirmCounts = ref<BillBatchCount | null>(null);
const unconfirmedBills = ref<ImportBatchTable[]>([]);
const confirmBatchRemark = computed(
  () => unconfirmedBills.value.find((b) => b.batch_no === confirmBatchNo.value)?.remark ?? ""
);

async function loadUnconfirmedBills(): Promise<boolean> {
  const res = await BatchAPI.listBatch({
    page_no: 1,
    page_size: 100,
    file_category: "bill",
    status: "unconfirmed",
  });
  unconfirmedBills.value = res.data.data?.items ?? [];
  if (!unconfirmedBills.value.length) {
    ElMessage.info("没有未确认的账单批次");
    return false;
  }
  return true;
}

async function openConfirm() {
  if (!(await loadUnconfirmedBills())) return;
  confirmBatchNo.value = unconfirmedBills.value[0]?.batch_no;
  confirmCounts.value = null;
  confirmDialogVisible.value = true;
  await loadConfirmCounts();
}

async function loadConfirmCounts() {
  if (!confirmBatchNo.value) {
    confirmCounts.value = null;
    return;
  }
  const res = await LogisticsAPI.billBatchCount(confirmBatchNo.value);
  confirmCounts.value = res.data.data ?? null;
}

async function handleConfirmBatch() {
  if (!confirmBatchNo.value) return;
  confirmLoading.value = true;
  try {
    const res = await LogisticsAPI.confirmBillBatch(confirmBatchNo.value);
    confirmDialogVisible.value = false;
    ElMessageBox.alert(res.data.data?.message || "批次已确认", "确认账单批次", {
      confirmButtonText: "知道了",
      type: "success",
    }).catch(() => {});
    await refreshAll();
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    confirmLoading.value = false;
  }
}

// ── 作废账单批次：物理删除未确认批次及其全部账单行（ADR-0033） ────────────
const discardDialogVisible = ref(false);
const discardLoading = ref(false);
const discardBatchNo = ref<string>();
const discardCounts = ref<BillBatchCount | null>(null);

async function openDiscard() {
  if (!(await loadUnconfirmedBills())) return;
  discardBatchNo.value = unconfirmedBills.value[0]?.batch_no;
  discardCounts.value = null;
  discardDialogVisible.value = true;
  await loadDiscardCounts();
}

async function loadDiscardCounts() {
  if (!discardBatchNo.value) {
    discardCounts.value = null;
    return;
  }
  const res = await LogisticsAPI.billBatchCount(discardBatchNo.value);
  discardCounts.value = res.data.data ?? null;
}

async function handleDiscardBatch() {
  if (!discardBatchNo.value) return;
  discardLoading.value = true;
  try {
    const res = await LogisticsAPI.discardBillBatch(discardBatchNo.value);
    discardDialogVisible.value = false;
    // 正筛着这个批次的话把筛选清掉，否则列表会停在已被删除的批次上
    if (searchForm.value.import_batch_id?.trim() === discardBatchNo.value) {
      searchForm.value.import_batch_id = undefined;
      replaceSearchParams(currentFormParams());
    }
    ElMessage.success(res.data.data || "账单批次已作废");
    await refreshAll();
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    discardLoading.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadStoreOptions(), loadCarrierNames(), loadBatchStatuses()]);
});
</script>

<style scoped>
.bill-count-line {
  margin-bottom: 12px;
}

/* 批次下拉的一行：公司名在左、确认状态小标签顶到右（AE18 乙：颜色要跳出来） */
.batch-option {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

/* 搜索栏里的日期框自带固定宽度、不跟格子撑满，于是「月份短、批次长」。只在这一页拉平，
   不动框架的 FaDatePicker（AH2，设计文档 19.1b） */
.fa-search-bar :deep(.el-date-editor) {
  width: 100%;
}

/* 认领订单弹窗（§22.3）：订单号与「查子订单」同排，候选行一条一行、单号信息另起一行灰字 */
.claim-order-line {
  display: flex;
  gap: 8px;
  width: 100%;
}

.claim-candidates {
  max-height: 260px;
  margin-top: 4px;
  overflow-y: auto;
}

.claim-candidates-title {
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.claim-row {
  padding: 4px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}

.claim-row :deep(.el-checkbox__label) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.5;
}

.claim-row-waybill {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.claim-row-blocked {
  color: var(--el-color-danger);
}

.claim-candidates-empty {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
