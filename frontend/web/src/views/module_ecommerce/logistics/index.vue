<!-- 快递账单：账单详情（导入·异常处理·批次确认）/ 解析规则管理 -->
<template>
  <div class="fa-full-height">
    <ElTabs v-model="activeTab" class="fa-tabs-fill">
      <ElTabPane label="账单详情" name="bill-detail">
        <BillDetailTab v-if="activeTab === 'bill-detail'" />
      </ElTabPane>
      <ElTabPane label="解析规则管理" name="parse-rule">
        <BillParseRuleTab v-if="activeTab === 'parse-rule'" />
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<script setup lang="ts">
import BillDetailTab from "./components/BillDetailTab.vue";
import BillParseRuleTab from "./components/BillParseRuleTab.vue";

defineOptions({
  name: "EcommerceLogistics",
  inheritAttrs: false,
});

const activeTab = ref<"bill-detail" | "parse-rule">("bill-detail");
</script>
