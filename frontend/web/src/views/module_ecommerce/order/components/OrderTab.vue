<!-- 订单查询 + 导入：FA + useTable，订单只读（创建/修改/删除均通过导入完成，不提供手工CRUD）
     点击主订单行展开子订单表（懒加载，横向滚动+固定首列）；备注为主订单级字段（ADR-0031），截断+悬停浮层 -->
<template>
  <div class="fa-full-height">
    <FaSearchBar
      v-show="showSearchBar"
      ref="searchBarRef"
      v-model="searchForm"
      :items="orderSearchItems"
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
      <!-- 勾选框的文字只能走默认插槽：FaSearchBar 给每个表单项都塞了一个空的默认插槽
           （里面是 select/checkboxgroup 那些 v-if 分支），ElCheckbox 一见有插槽就不再渲染
           label 属性了，文字会整个消失，只剩一个看不懂的方框 -->
      <template #freight_missing="{ modelValue }">
        <ElCheckbox
          :model-value="modelValue.freight_missing === true"
          @update:model-value="modelValue.freight_missing = $event"
        >
          只看缺账单（ERP发货 + 有运单号 + 运费成本还没定）
        </ElCheckbox>
      </template>
    </FaSearchBar>

    <ElCard class="fa-table-card" :style="{ 'margin-top': showSearchBar ? '12px' : '0' }">
      <FaTableHeader
        v-model:columns="columnChecks"
        v-model:showSearchBar="showSearchBar"
        :loading="loading"
        @refresh="refreshData"
      >
        <template #left>
          <ElButton
            v-hasPerm="['module_ecommerce:order:import']"
            type="primary"
            :icon="UploadFilled"
            @click="openImport"
            >导入订单</ElButton
          >
          <ElButton
            v-hasPerm="['module_ecommerce:order:update']"
            type="danger"
            :icon="RefreshRight"
            @click="openUpdate"
            >更新订单</ElButton
          >
          <ElButton
            v-hasPerm="['module_ecommerce:batch:confirm']"
            type="warning"
            :icon="Select"
            @click="openConfirm"
            >确认订单批次</ElButton
          >
        </template>
      </FaTableHeader>

      <FaTable
        ref="faTableRef"
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        row-key="id"
        scrollbar-always-on
        :tooltip-options="{ popperOptions: { strategy: 'fixed' } }"
        :row-class-name="() => 'order-row--clickable'"
        @row-click="onRowClick"
        @expand-change="onExpandChange"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>

    <!-- 导入/更新都要选发货月份，月份即批次号 order-YYYY-MM（ADR-0033），与选文件同框 -->
    <FaImportDialog
      ref="importDialogRef"
      v-model="importVisible"
      title="导入订单 Excel"
      width="600px"
      :content-config="importContentConfig"
      :show-template-download="false"
      :upload-data="importExtraData"
      :loading="uploadLoading"
      :confirm-disabled="!importShipMonth"
      note="从快麦ERP导出的订单Excel（ADR-0027），一行=一条订单明细，同订单号的多行会自动合并；未发货行不入库"
      @upload="handleImportUpload"
    >
      <template #form>
        <ElFormItem label="发货月份" label-width="100px" label-position="left" required>
          <FaDatePicker
            v-model="importShipMonth"
            type="month"
            :disabled-date="disabledImportShipMonth"
            placeholder="只能选本月往前 3 个整月（不含本月）"
          />
        </ElFormItem>
        <p class="month-hint">
          发货月份就是本次批次号 order-YYYY-MM；Excel
          里任一主订单的最早发货时间不在这个月，整批中止、不落库。 可导入月份只让选本月往前 3
          个整月（不含本月），窗口之外的月份不提供补录入口。
        </p>
      </template>
    </FaImportDialog>

    <FaImportDialog
      ref="updateDialogRef"
      v-model="updateVisible"
      title="更新订单 Excel"
      width="600px"
      :content-config="updateContentConfig"
      :show-template-download="false"
      :upload-data="updateExtraData"
      :loading="updateUploadLoading"
      :confirm-disabled="!updateShipMonth"
      note="对系统已有订单做增量更新（ADR-0032）：比对子订单变化（含重量）并追加更新备注；Excel 中库里没有的订单号会整单新建。若子订单缺失或数量发生变化，整批中止、不写入任何数据变动。"
      @upload="handleUpdateUpload"
    >
      <template #form>
        <ElFormItem label="发货月份" label-width="100px" label-position="left" required>
          <FaDatePicker
            v-model="updateShipMonth"
            type="month"
            :disabled-date="disabledFutureMonth"
            placeholder="不能选未来月份"
          />
        </ElFormItem>
        <p class="month-hint">
          更新只动这个月的批次，所以不套导入那套月份窗口——往前几个月补数据是正常动作。
          目标批次不存在时更新会被拒绝，先用「导入订单」建该月批次；批次已确认后不允许更新。
        </p>
      </template>
    </FaImportDialog>

    <FaDialog
      v-model="confirmDialogVisible"
      title="确认订单批次"
      width="560px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
    >
      <ElForm label-width="100px">
        <ElFormItem label="批次" required>
          <ElSelect v-model="confirmBatchNo" placeholder="请选择待确认的订单批次" class="w-full">
            <ElOption
              v-for="b in unconfirmedOrders"
              :key="b.batch_no"
              :label="`${b.batch_no}（${b.row_count ?? 0} 行 / ${b.order_count ?? 0} 单）`"
              :value="b.batch_no ?? ''"
            />
          </ElSelect>
        </ElFormItem>
      </ElForm>
      <ElAlert
        type="info"
        :closable="false"
        show-icon
        title="门禁顺序：先确认同月账单批次，再确认订单批次（ADR-0033）。确认后利润才认这批；运费成本缺失只提示、不拦截。"
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

    <!-- 门禁预检未通过（§5.6）：账单不齐时给出两份缺口清单，②可直接声明本月无账单 -->
    <FaDialog
      v-model="gateDialogVisible"
      title="本月账单不齐"
      width="620px"
      dialog-class="crud-embed-dialog"
      modal-class="crud-embed-dialog"
    >
      <ElAlert
        type="error"
        :closable="false"
        show-icon
        :title="`订单批次 ${confirmBatchNo ?? ''} 所属月份 ${gateGaps?.bill_month ?? ''} 的账单门禁还没过，补齐后才能确认订单（ADR-0033）。`"
      />
      <div v-if="gateUnconfirmed.length" class="gate-block">
        <p class="gate-title">① 已导入但还没确认的账单批次</p>
        <p class="month-hint">{{ gateUnconfirmed.join("、") }}</p>
        <p class="month-hint">
          请到「快递账单 → 账单详情」逐条确认：虚假/重复行要先删除或改对置「已恢复」。
        </p>
      </div>
      <div v-if="gateMissing.length" class="gate-block">
        <p class="gate-title">② 这个月完全没有账单批次的快递类型</p>
        <ElSelect
          v-model="declareTypes"
          multiple
          placeholder="勾选这个月确实没有账单的快递类型"
          class="w-full"
        >
          <ElOption v-for="t in gateMissing" :key="t" :label="t" :value="t" />
        </ElSelect>
        <p class="month-hint">
          确实没有账单的，声明后会为该类型建一条零行未确认批次；批次列表里确认它，这个月就算齐了。
        </p>
      </div>
      <div v-if="gateNoRules" class="gate-block">
        <p class="gate-title">③ 一条启用中的账单解析规则都没有</p>
        <p class="month-hint">
          账单是靠解析规则读进来的。规则全部停用或删光时，确认批次等于给一批没人核对的数据盖「可信」的章，所以账单和订单批次都不允许确认（Z5）。
        </p>
        <p class="month-hint">请到「快递账单 → 解析规则」新增或启用规则，再回来重新点「确认」。</p>
      </div>
      <div v-if="gateOrphanRows > 0" class="gate-block">
        <p class="gate-title">④ 有 {{ gateOrphanRows }} 行账单挂不到批次（脏数据，不影响门禁）</p>
        <p class="month-hint">
          账单行上还写着批次号，批次表里却已经没有这一批了。业务代码造不出这种行，只能是有人直接删了批次表的数据而没删干净它名下的账单行。
        </p>
        <p class="month-hint">
          这种行在批次下拉里根本选不到（那个框只列还存在的批次）、也确认不了，但按出账月份筛会把它筛出来，「批次状态」列标红为「批次已不存在」——请到账单详情核对，该补的批次补上、该删的行删掉。
        </p>
      </div>
      <ElAlert
        v-if="gateGaps && !gateUnconfirmed.length && !gateMissing.length && !gateNoRules"
        type="success"
        :closable="false"
        show-icon
        title="缺口已补齐，回到上一个对话框重新点「确认」即可。"
      />
      <template #footer>
        <ElButton @click="gateDialogVisible = false">关闭</ElButton>
        <ElButton
          v-hasPerm="['module_ecommerce:batch:declare']"
          type="primary"
          :loading="declareLoading"
          :disabled="!declareTypes.length || gateNoRules"
          @click="handleDeclareEmpty"
        >
          声明所选本月无账单
        </ElButton>
      </template>
    </FaDialog>
  </div>
</template>

<script setup lang="ts">
import OrderAPI, { type OrderDetailRow, type OrderTable } from "@/api/module_ecommerce/order";
import StoreAPI, { type StoreTable } from "@/api/module_ecommerce/store";
import BatchAPI, { type ImportBatchTable } from "@/api/module_ecommerce/batch";
import LogisticsAPI, { type BillGateGap } from "@/api/module_ecommerce/logistics";
import type { SearchFormItem } from "@/components/forms/fa-search-bar/index.vue";
import type FaSearchBar from "@/components/forms/fa-search-bar/index.vue";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import { resolveStatusColumns } from "@utils";
import { RefreshRight, Select, UploadFilled } from "@element-plus/icons-vue";
import { ElCheckbox, ElEmpty, ElMessage, ElMessageBox } from "element-plus";
import type { VNode } from "vue";
import type { IContentConfig } from "@/components/modal/types";

defineOptions({
  name: "EcommerceOrderTab",
  inheritAttrs: false,
});

type OrderSearchForm = {
  system_order_no?: string;
  store_id?: number;
  platform?: string;
  refund_status?: string;
  shua_status?: string;
  province?: string;
  city?: string;
  update_status?: string;
  ship_time?: string[];
  freight_missing?: boolean;
};

const searchForm = ref<OrderSearchForm>({});
const showSearchBar = ref(true);
const searchBarRef = ref<InstanceType<typeof FaSearchBar> | null>(null);
const searchBarRules: Record<string, unknown> = {};

const REFUND_STATUS_OPTIONS = [
  { label: "无退款", value: "none" },
  { label: "整单退款", value: "full" },
  { label: "部分退款", value: "partial" },
];
const SHUA_STATUS_OPTIONS = [
  { label: "非刷单", value: "not_shua" },
  { label: "待核对", value: "pending" },
  { label: "核对成功", value: "matched" },
  { label: "核对失败(重大错误)", value: "mismatch" },
];
const UPDATE_STATUS_OPTIONS = [
  { label: "有更新", value: "updated" },
  { label: "无更新", value: "not_updated" },
];

// 店铺下拉选项（显示名称、传id）
const storeOptions = ref<{ label: string; value: number }[]>([]);
async function loadStoreOptions() {
  const res = await StoreAPI.listStore({ page_no: 1, page_size: 100 });
  const items: StoreTable[] = res.data.data?.items ?? [];
  storeOptions.value = items
    .filter((s) => s.id != null)
    .map((s) => ({ label: s.store_name ?? `店铺#${s.id}`, value: s.id as number }));
}
onMounted(loadStoreOptions);

const orderSearchItems = computed<SearchFormItem[]>(() => [
  { label: "系统订单号", key: "system_order_no", type: "input", clearable: true, span: 6 },
  {
    label: "店铺",
    key: "store_id",
    type: "select",
    props: { options: storeOptions.value, clearable: true, filterable: true },
    span: 6,
  },
  { label: "平台", key: "platform", type: "input", clearable: true, span: 6 },
  {
    label: "退款状态",
    key: "refund_status",
    type: "select",
    props: { options: REFUND_STATUS_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "刷单核对状态",
    key: "shua_status",
    type: "select",
    props: { options: SHUA_STATUS_OPTIONS, clearable: true },
    span: 6,
  },
  { label: "省份", key: "province", type: "input", clearable: true, span: 6 },
  { label: "城市", key: "city", type: "input", clearable: true, span: 6 },
  {
    label: "更新状态",
    key: "update_status",
    type: "select",
    props: { options: UPDATE_STATUS_OPTIONS, clearable: true },
    span: 6,
  },
  {
    label: "发货时间",
    key: "ship_time",
    type: "datetimerange",
    props: {
      style: { width: "100%" },
      type: "datetimerange",
      rangeSeparator: "至",
      startPlaceholder: "开始",
      endPlaceholder: "结束",
      valueFormat: "YYYY-MM-DD HH:mm:ss",
    },
    span: 6,
  },
  // 「只看缺账单」是主订单级的筛选项（§24 AK 轮）：勾上只剩「下面还有缺账单子订单」的那些主订单。
  // 文字不在这里，在模板的 #freight_missing 插槽里（label 属性会被 FaSearchBar 的空插槽吃掉）
  { label: "", key: "freight_missing", type: "checkbox", span: 6 },
]);

const faTableRef = ref<{
  elTableRef?: {
    clearSelection: () => void;
    toggleRowExpansion: (row: unknown, expanded?: boolean) => void;
  };
} | null>(null);

// ── 子订单展开行（懒加载，展开时才请求） ─────────────────────────────────
const detailRows = reactive<Record<number, OrderDetailRow[]>>({});
const detailLoading = reactive<Record<number, boolean>>({});
const expandedIds = ref<number[]>([]);

async function loadDetails(id: number) {
  if (detailRows[id] || detailLoading[id]) return;
  detailLoading[id] = true;
  try {
    const res = await OrderAPI.listOrderDetails(id);
    detailRows[id] = res.data.data ?? [];
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    detailLoading[id] = false;
  }
}

function onRowClick(row: OrderTable) {
  faTableRef.value?.elTableRef?.toggleRowExpansion(row);
}

function onExpandChange(row: OrderTable, expandedRows: OrderTable[] | boolean) {
  const expanded = Array.isArray(expandedRows)
    ? expandedRows.some((r) => r.id === row.id)
    : Boolean(expandedRows);
  const id = row.id;
  if (id == null) return;
  if (expanded) {
    if (!expandedIds.value.includes(id)) expandedIds.value.push(id);
    void loadDetails(id);
  } else {
    expandedIds.value = expandedIds.value.filter((x) => x !== id);
  }
}

// 展开区子订单表列定义（横向滚动 + 平台订单号固定首列）
interface SubColumn {
  prop: string;
  label: string;
  width?: number;
  minWidth?: number;
  fixed?: "left";
  num?: boolean;
  tooltip?: boolean;
  render?: (row: OrderDetailRow) => string;
}

const subColumns: SubColumn[] = [
  { prop: "platform_order_no", label: "平台订单号", width: 160, fixed: "left", tooltip: true },
  { prop: "warehouse_name", label: "仓库名称", width: 110, tooltip: true },
  { prop: "order_type", label: "订单类型", width: 90 },
  { prop: "detail_status", label: "子订单状态", width: 100 },
  { prop: "courier_template", label: "快递模板", width: 130, tooltip: true },
  { prop: "tracking_no", label: "快递单号", width: 150, tooltip: true },
  { prop: "main_sku_code", label: "主商家编码", width: 120, tooltip: true },
  {
    prop: "sku_code",
    label: "规格商家编码",
    width: 140,
    tooltip: true,
    // 不分规格的商品这里是空，含义是「无规格」不是「值缺失」(ADR-0023)，不挂默认「—」占位（§14.2）
    render: (r) => r.sku_code ?? "",
  },
  { prop: "product_name", label: "商品名称", minWidth: 200, tooltip: true },
  { prop: "sku_name", label: "规格名称", width: 120, tooltip: true },
  { prop: "weight_kg", label: "重量(kg)", width: 90, num: true },
  {
    prop: "is_erp_shipped",
    label: "是否ERP发货",
    width: 110,
    render: (r) => (r.is_erp_shipped ? "是" : "否"),
  },
  { prop: "ship_qty", label: "实发数量", width: 90, num: true },
  { prop: "return_qty", label: "实退数量", width: 90, num: true },
  { prop: "paid_amount", label: "已付金额", width: 100, num: true },
  { prop: "refund_amount", label: "退货金额", width: 100, num: true },
  {
    prop: "cost_price",
    label: "成本价",
    width: 90,
    num: true,
    render: (r) => (r.cost_price == null ? "缺失" : String(r.cost_price)),
  },
  { prop: "computed_product_cost", label: "发货成本", width: 100, num: true },
  { prop: "computed_return_cost", label: "退货成本", width: 100, num: true },
  {
    prop: "freight_cost",
    label: "运费成本",
    width: 100,
    num: true,
    // 三态语义（ADR-0023/0034）：NULL=还没定(缺账单)、0=有意按0、>0=账单实额
    render: (r) => (r.freight_cost == null ? "还没定" : String(r.freight_cost)),
  },
  { prop: "pay_time", label: "付款时间", width: 160, tooltip: true },
  { prop: "ship_time", label: "发货时间", width: 160, tooltip: true },
  { prop: "finish_time", label: "完成时间", width: 160, tooltip: true },
  { prop: "after_sales_no", label: "售后工单号", width: 150, tooltip: true },
  { prop: "update_remark", label: "更新备注", width: 240, tooltip: true },
];

// 列宽合计：子订单表按此固定像素宽渲染，横向滚动由外层容器承担
const SUB_TABLE_WIDTH = subColumns.reduce((sum, c) => sum + (c.width ?? c.minWidth ?? 120), 0);

function cellText(col: SubColumn, row: OrderDetailRow): string {
  if (col.render) return col.render(row);
  const value = (row as Record<string, unknown>)[col.prop];
  return value == null || value === "" ? "—" : String(value);
}

/** 子订单表用原生 <table> 渲染（不再嵌套 ElTable）：
 *  嵌套 ElTable 的悬停行高亮、溢出浮层、滚动条 gutter 都会在鼠标移入展开区时
 *  触发表格重排，表现为整块子订单详情左右跳动。原生表格的布局完全由 CSS 决定，
 *  悬停只改背景色、不产生重排；溢出文本用原生 title 提示，不往 body 挂浮层。 */
const SubOrderTable = defineComponent({
  name: "SubOrderTable",
  props: { orderId: { type: Number, required: true } },
  setup(p) {
    return () => {
      const children: VNode[] = [];
      if (detailLoading[p.orderId]) {
        children.push(h("div", { class: "sub-order__status" }, "加载中…"));
      } else if (!detailRows[p.orderId]?.length) {
        children.push(h(ElEmpty, { description: "暂无子订单", imageSize: 60 }));
      } else {
        const rows = detailRows[p.orderId] ?? [];
        children.push(
          h("div", { class: "sub-order-scroll" }, [
            h("table", { class: "sub-order-table", style: { width: `${SUB_TABLE_WIDTH}px` } }, [
              h(
                "colgroup",
                null,
                subColumns.map((col) =>
                  h("col", { style: { width: `${col.width ?? col.minWidth ?? 120}px` } })
                )
              ),
              h("thead", null, [
                h(
                  "tr",
                  null,
                  subColumns.map((col) =>
                    h("th", { class: col.fixed ? "sub-order__fixed" : undefined }, col.label)
                  )
                ),
              ]),
              h(
                "tbody",
                null,
                rows.map((row) =>
                  h(
                    "tr",
                    null,
                    subColumns.map((col) => {
                      const text = cellText(col, row);
                      return h(
                        "td",
                        {
                          class: [
                            col.num ? "sub-order__num" : "",
                            col.fixed ? "sub-order__fixed" : "",
                          ]
                            .filter(Boolean)
                            .join(" "),
                          title: col.tooltip ? text : undefined,
                        },
                        [h("div", { class: "sub-order-cell" }, text)]
                      );
                    })
                  )
                )
              ),
            ]),
          ])
        );
      }
      return h("div", { class: "sub-order-wrap" }, children);
    };
  },
});

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
    apiFn: OrderAPI.listOrder,
    apiParams: { page_no: 1, page_size: 10 },
    columnsFactory: resolveStatusColumns<OrderTable>(() => [
      {
        type: "expand",
        width: 46,
        formatter: (row: OrderTable) => h(SubOrderTable, { orderId: row.id! }),
      },
      { type: "globalIndex", width: 56, label: "序号" },
      { prop: "system_order_no", label: "系统订单号", minWidth: 150, showOverflowTooltip: true },
      { prop: "store_name", label: "店铺名称", minWidth: 140, showOverflowTooltip: true },
      { prop: "platform", label: "平台", width: 90 },
      { prop: "order_status", label: "订单状态", width: 100, showOverflowTooltip: true },
      { prop: "buyer_paid_amount", label: "实付金额", width: 100 },
      { prop: "refund_amount", label: "退款金额", width: 100 },
      {
        prop: "refund_status",
        label: "退款状态",
        width: 100,
        status: {
          none: { type: "info", text: "无退款" },
          full: { type: "danger", text: "整单退款" },
          partial: { type: "warning", text: "部分退款" },
        },
      },
      { prop: "shipped_cost_amount", label: "发货成本", width: 100 },
      { prop: "return_cost_amount", label: "退货成本", width: 100 },
      { prop: "freight_income", label: "运费收入", width: 100 },
      {
        prop: "freight_cost_amount",
        label: "运费成本",
        width: 100,
        formatter: (row: OrderTable) =>
          row.freight_cost_amount == null ? "—" : String(row.freight_cost_amount),
      },
      { prop: "province", label: "省份", width: 90 },
      { prop: "city", label: "城市", width: 90 },
      { prop: "system_remark", label: "系统备注", width: 160, showOverflowTooltip: true },
      { prop: "seller_remark", label: "卖家备注", width: 160, showOverflowTooltip: true },
      {
        prop: "shua_status",
        label: "刷单核对",
        width: 110,
        status: {
          not_shua: { type: "info", text: "非刷单" },
          pending: { type: "warning", text: "待核对" },
          matched: { type: "success", text: "核对成功" },
          mismatch: { type: "danger", text: "重大错误" },
        },
      },
    ]),
  },
});

function buildReplaceParams(p: OrderSearchForm): Record<string, unknown> {
  return { ...p };
}

async function handleSearchBarSearch(params: OrderSearchForm) {
  await searchBarRef.value?.validate?.();
  replaceSearchParams(buildReplaceParams(params));
  await getData();
}

async function onResetSearch() {
  searchForm.value = {};
  await resetSearchParams();
}

const importVisible = ref(false);
const importDialogRef = ref<{ handleClose: () => void }>();
const uploadLoading = ref(false);
const importShipMonth = ref<string>();

function monthIndex(d: Date) {
  return d.getFullYear() * 12 + d.getMonth();
}

/** 可导入月份窗口=本月往前 3 个整月、不含本月（ADR-0033：窗口只作界面护栏，接口只拦格式与未来月） */
function disabledImportShipMonth(date: Date): boolean {
  const now = new Date();
  const idx = monthIndex(date);
  return (
    idx > monthIndex(new Date(now.getFullYear(), now.getMonth() - 1, 1)) ||
    idx < monthIndex(new Date(now.getFullYear(), now.getMonth() - 3, 1))
  );
}

/** 更新弹窗不套窗口，只挡未来月 */
function disabledFutureMonth(date: Date): boolean {
  return monthIndex(date) > monthIndex(new Date());
}

function openImport() {
  importShipMonth.value = undefined;
  importVisible.value = true;
}

const importContentConfig = computed<IContentConfig>(() => ({
  permPrefix: "module_ecommerce:order",
  cols: [],
  indexAction: async () => ({}),
}));

const importExtraData = computed(() => ({ ship_month: importShipMonth.value ?? "" }));

async function handleImportUpload(formData: FormData) {
  uploadLoading.value = true;
  try {
    const res = await OrderAPI.importOrder(formData);
    // 关框的同时清掉已选文件：同框之后残留的文件会被下一次「确定」重复上传
    importDialogRef.value?.handleClose();
    ElMessage.success(res.data.data || "导入成功");
    await refreshData();
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    uploadLoading.value = false;
  }
}

const updateVisible = ref(false);
const updateDialogRef = ref<{ handleClose: () => void }>();
const updateUploadLoading = ref(false);
const updateShipMonth = ref<string>();

function openUpdate() {
  updateShipMonth.value = undefined;
  updateVisible.value = true;
}

const updateContentConfig = computed<IContentConfig>(() => ({
  permPrefix: "module_ecommerce:order",
  cols: [],
  indexAction: async () => ({}),
}));

const updateExtraData = computed(() => ({ ship_month: updateShipMonth.value ?? "" }));

// 中止时后端返回 HTTP 200 + 业务错误码，拦截器按 showErrorMessage:false 不弹 toast，
// 而是 reject 整个 response，中止清单在 data.data / data.msg 里。
function extractUpdateMessage(error: unknown): string {
  const resp = error as { data?: { data?: string; msg?: string } } | undefined;
  return resp?.data?.data || resp?.data?.msg || "更新失败";
}

// 用 white-space:pre-line 保留后端多行文案（\n 分隔的中止原因 / 汇总），避免 HTML 注入
function showUpdateResult(title: string, msg: string, type: "success" | "error") {
  return ElMessageBox.alert(h("div", { style: "white-space: pre-line" }, msg), title, {
    type,
    confirmButtonText: "知道了",
    customStyle: { maxWidth: "640px" },
  }).catch(() => {});
}

async function handleUpdateUpload(formData: FormData) {
  updateUploadLoading.value = true;
  try {
    const res = await OrderAPI.updateOrder(formData);
    updateDialogRef.value?.handleClose();
    await showUpdateResult("更新订单完成", res.data.data || res.data.msg || "更新成功", "success");
    await refreshData();
  } catch (error: unknown) {
    // 中止一律不留痕（ADR-0032 修订）：原因只在这段清单里报一次，库里没有任何变动
    updateDialogRef.value?.handleClose();
    await showUpdateResult("更新中止", extractUpdateMessage(error), "error");
    await refreshData();
    if (import.meta.env.DEV) console.error(error);
  } finally {
    updateUploadLoading.value = false;
  }
}

// ── 确认订单批次（门禁：同月账单批次须全部已确认，ADR-0033） ───────────────
const confirmDialogVisible = ref(false);
const confirmLoading = ref(false);
const confirmBatchNo = ref<string>();
const unconfirmedOrders = ref<ImportBatchTable[]>([]);

async function openConfirm() {
  const res = await BatchAPI.listBatch({
    page_no: 1,
    page_size: 100,
    file_category: "order",
    status: "unconfirmed",
  });
  unconfirmedOrders.value = res.data.data?.items ?? [];
  if (!unconfirmedOrders.value.length) {
    ElMessage.info("没有待确认的订单批次");
    return;
  }
  confirmBatchNo.value = unconfirmedOrders.value[0]?.batch_no;
  confirmDialogVisible.value = true;
}

async function handleConfirmBatch() {
  if (!confirmBatchNo.value) return;
  confirmLoading.value = true;
  try {
    // 先做只读预检：缺口用对话框呈现（②还能就地声明无账单），靠确认接口的报错文案没法操作
    const gapRes = await LogisticsAPI.billGateGaps(confirmBatchNo.value.slice(-7));
    const gaps = gapRes.data.data;
    if (
      gaps &&
      (gaps.unconfirmed_batches.length || gaps.missing_types.length || gaps.no_active_rules)
    ) {
      gateGaps.value = gaps;
      declareTypes.value = [];
      confirmDialogVisible.value = false;
      gateDialogVisible.value = true;
      return;
    }
    const res = await OrderAPI.confirmOrderBatch(confirmBatchNo.value);
    confirmDialogVisible.value = false;
    const result = res.data.data;
    const text = [result?.message, result?.warning].filter(Boolean).join("\n\n");
    await showUpdateResult("确认订单批次", text || "批次已确认", "success");
    await refreshData();
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    confirmLoading.value = false;
  }
}

// ── 声明本月无账单：为零行的快递类型建未确认批次，让门禁认这个月齐了 ────────
const gateDialogVisible = ref(false);
const gateGaps = ref<BillGateGap | null>(null);
const declareTypes = ref<string[]>([]);
const declareLoading = ref(false);

const gateUnconfirmed = computed(() => gateGaps.value?.unconfirmed_batches ?? []);
const gateMissing = computed(() => gateGaps.value?.missing_types ?? []);
const gateNoRules = computed(() => gateGaps.value?.no_active_rules ?? false);
const gateOrphanRows = computed(() => gateGaps.value?.orphan_bill_rows ?? 0);

async function handleDeclareEmpty() {
  if (!gateGaps.value || !declareTypes.value.length) return;
  declareLoading.value = true;
  try {
    const res = await LogisticsAPI.declareEmptyBatches({
      bill_month: gateGaps.value.bill_month,
      bill_types: declareTypes.value,
    });
    ElMessage.success(res.data.data || "空账单批次声明成功");
    gateGaps.value = {
      ...gateGaps.value,
      missing_types: gateGaps.value.missing_types.filter((t) => !declareTypes.value.includes(t)),
    };
    declareTypes.value = [];
  } catch (error: unknown) {
    if (import.meta.env.DEV) console.error(error);
  } finally {
    declareLoading.value = false;
  }
}
</script>

<style scoped>
.month-hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.gate-block {
  margin-top: 12px;
}

.gate-title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 600;
}

:deep(.order-row--clickable) {
  cursor: pointer;
}

/* ElTable 展开单元格默认 20px 50px 内边距，会在子订单表四周留一圈白边 */
:deep(.el-table__expanded-cell) {
  padding: 0;
}

:deep(.sub-order-wrap) {
  padding: 8px 12px;
}

:deep(.sub-order-scroll) {
  overflow-x: auto;
  /* 固定预留横向滚动条槽位：避免该滚动条随内容/悬停忽隐忽现导致高度微抖，
     进而牵连祖先滚动容器（.page-content）宽度跳动（外层搜索栏、主表格列跟着左右移） */
  scrollbar-gutter: stable;
  /* 横向滚动条加粗到 6px，方便鼠标拖拽，不影响站内其它滚动条 */
  scrollbar-width: auto;
}

:deep(.sub-order-scroll)::-webkit-scrollbar {
  height: 6px !important;
}

:deep(.sub-order-scroll)::-webkit-scrollbar-thumb {
  border-radius: 6px;
}

:deep(.sub-order__status) {
  padding: 12px;
  color: #909399;
  text-align: center;
}
/* 原生表格：固定布局 + 显式列宽，悬停只改背景、绝不重排 */
:deep(.sub-order-table) {
  font-size: 12px;
  color: var(--el-text-color-regular);
  table-layout: fixed;
  border-spacing: 0;
  border-collapse: separate;
}

:deep(.sub-order-table th),
:deep(.sub-order-table td) {
  padding: 6px 8px;
  vertical-align: middle;
  text-align: left;
  border-right: 1px solid var(--el-table-border-color);
  border-bottom: 1px solid var(--el-table-border-color);
}

:deep(.sub-order-table th:first-child),
:deep(.sub-order-table td:first-child) {
  border-left: 1px solid var(--el-table-border-color);
}

:deep(.sub-order-table thead th) {
  font-weight: 600;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-lighter);
  border-top: 1px solid var(--el-table-border-color);
}

:deep(.sub-order-table tbody tr:hover td) {
  background: var(--fa-hover-color);
}
/* 首列 sticky 固定：需不透明背景，悬停时同步换背景避免内容透出 */
:deep(.sub-order-table .sub-order__fixed) {
  position: sticky;
  left: 0;
  z-index: 1;
  background: var(--el-bg-color);
}

:deep(.sub-order-table thead .sub-order__fixed) {
  z-index: 2;
  background: var(--el-fill-color-lighter);
}

:deep(.sub-order-table tbody tr:hover .sub-order__fixed) {
  background: var(--fa-hover-color);
}

:deep(.sub-order__num) {
  text-align: right;
}

:deep(.sub-order-cell) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
