<!-- 订单与商品成本：订单查询导入 / SKU成本维护 / 缺失成本待补充 -->
<template>
  <div class="fa-full-height">
    <ElTabs v-model="activeTab" class="fa-tabs-fill">
      <ElTabPane label="订单查询" name="order">
        <OrderTab v-if="activeTab === 'order'" />
      </ElTabPane>
      <ElTabPane label="商品成本" name="sku-cost">
        <SkuCostTab v-if="activeTab === 'sku-cost'" />
      </ElTabPane>
      <ElTabPane label="缺失成本待补充" name="missing-cost">
        <MissingCostTab v-if="activeTab === 'missing-cost'" />
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<script setup lang="ts">
import OrderTab from "./components/OrderTab.vue";
import SkuCostTab from "./components/SkuCostTab.vue";
import MissingCostTab from "./components/MissingCostTab.vue";

defineOptions({
  name: "EcommerceOrder",
  inheritAttrs: false,
});

const activeTab = ref<"order" | "sku-cost" | "missing-cost">("order");
</script>
