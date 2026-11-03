<!-- 薪资考勤：考勤统计（钉钉智能考勤月报）+ 薪资绩效（预留占位） -->
<template>
  <div class="fa-full-height">
    <ElTabs v-model="activeTab" class="fa-tabs-fill">
      <ElTabPane label="考勤统计" name="attendance">
        <MonthlyReportTab v-if="activeTab === 'attendance'" />
      </ElTabPane>
      <ElTabPane label="薪资绩效" name="salary-performance">
        <SalaryPerformanceTab v-if="activeTab === 'salary-performance'" />
      </ElTabPane>
    </ElTabs>
  </div>
</template>

<script setup lang="ts">
import MonthlyReportTab from "./components/MonthlyReportTab.vue";
import SalaryPerformanceTab from "./components/SalaryPerformanceTab.vue";

defineOptions({
  name: "InternalSalaryAttendance",
  inheritAttrs: false,
});

const activeTab = ref<"attendance" | "salary-performance">("attendance");
</script>
