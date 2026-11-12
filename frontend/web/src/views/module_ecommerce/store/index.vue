<!-- 店铺管理：店铺 / 负责人，两个子域合并进设计文档 §6 的同一个二级菜单（店铺从订单数据自动建档，无映射表） -->
<template>
  <div class="fa-full-height">
    <ElTabs v-model="activeTab" class="fa-tabs-fill">
      <ElTabPane label="店铺" name="store">
        <StoreTab v-if="activeTab === 'store'" />
      </ElTabPane>
      <ElTabPane label="负责人" name="tag">
        <TagTab v-if="activeTab === 'tag'" />
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<script setup lang="ts">
import StoreTab from "./components/StoreTab.vue";
import TagTab from "./components/TagTab.vue";

defineOptions({
  name: "EcommerceStore",
  inheritAttrs: false,
});

const activeTab = ref<"store" | "tag">("store");
</script>
