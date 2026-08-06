<script lang="ts" setup>
/**
 * 回路工作台 · 处置时间线 Tab（IA 重构 Phase B·§4.1.2）
 *
 * 内容：该回路全部生命周期事件时间轴（评估快照 / 诊断结果 / 整定任务 / Tracker 动作）
 * 数据来源：前端并行调用 4 API（评估记录 + 诊断记录 + 整定记录 + tracker），按时间合并排序
 *
 * 支持：按事件类型筛选、时间窗筛选
 * 性能：限制最近 100 条事件
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'TimelineTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty
      description="处置时间线 Tab（开发中）— 四源聚合事件时间轴（评估/诊断/整定/Tracker）"
    />
  </div>
</template>
