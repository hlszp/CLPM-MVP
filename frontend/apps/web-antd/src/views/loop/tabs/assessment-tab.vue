<script lang="ts" setup>
/**
 * 回路工作台 · 评估 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 内容：最近评估快照 + 评分趋势小图 + "去评估任务"（带 loopId）
 * 数据来源：getLoopSnapshotsApi
 *
 * 硬性规则：摘要 + 1 主图 + 跳转入口
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'AssessmentTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty
      description="评估 Tab（开发中）— 评估快照 / 评分趋势 / 跳转评估任务"
    />
  </div>
</template>
