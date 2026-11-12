<!-- 缺失成本待补充清单：订单明细 cost_missing=true（ADR-0023），只读，补成本价需去「商品成本」tab -->
<template>
  <div class="fa-full-height">
    <ElCard class="fa-table-card">
      <FaTableHeader :loading="loading" @refresh="refreshData">
        <template #left>
          <ElAlert
            type="warning"
            :closable="false"
            show-icon
            title="这里只读。补充成本价请到「商品成本」页签按主商家编码（有规格时连同规格商家编码）新增/编辑，之后跑一次「更新订单」即可重算"
          />
        </template>
      </FaTableHeader>
      <FaTable
        :loading="loading"
        :data="data"
        :columns="columns"
        :pagination="pagination"
        @pagination:size-change="handleSizeChange"
        @pagination:current-change="handleCurrentChange"
      />
    </ElCard>
  </div>
</template>

<script setup lang="ts">
import OrderAPI, { type OrderDetailRow } from "@/api/module_ecommerce/order";
import FaTableHeader from "@/components/tables/fa-table-header/index.vue";
import { ElTag } from "element-plus";

defineOptions({
  name: "EcommerceMissingCostTab",
  inheritAttrs: false,
});

const { columns, data, loading, pagination, handleSizeChange, handleCurrentChange, refreshData } =
  useTable({
    core: {
      apiFn: OrderAPI.missingCostList,
      apiParams: { page_no: 1, page_size: 10 },
      columnsFactory: () => [
        { type: "globalIndex", width: 56, label: "序号" },
        { prop: "order_id", label: "订单ID", width: 90 },
        { prop: "main_sku_code", label: "主商家编码", minWidth: 130, showOverflowTooltip: true },
        {
          prop: "sku_code",
          label: "规格商家编码",
          minWidth: 130,
          showOverflowTooltip: true,
          formatter: (row: OrderDetailRow) =>
            row.sku_code ? row.sku_code : h(ElTag, { size: "small", type: "info" }, () => "商品级"),
        },
        { prop: "product_name", label: "商品名称", minWidth: 160, showOverflowTooltip: true },
        { prop: "ship_qty", label: "实发数量", width: 90 },
        { prop: "return_qty", label: "实退数量", width: 90 },
        { prop: "created_time", label: "订单明细创建时间", width: 168, showOverflowTooltip: true },
      ],
    },
  });
</script>
