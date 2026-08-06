<script lang="ts" setup>
/**
 * 回路工作台 · 效果对比 Tab（IA 重构 Phase B·§4.1.3）
 *
 * 内容：选两次整定任务，并排对比 PID 参数 / 闭环响应曲线 / 综合评分变化 / 诊断标签变迁
 * 数据来源：tuning + metric + diagnosis（前端组合）
 *
 * 空态：无整定记录或仅 1 个时提示
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'ComparisonTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty
      description="效果对比 Tab（开发中）— 选择两次整定任务对比 PID/响应/评分/标签"
    />
  </div>
</template>
