<script lang="ts" setup>
/**
 * 回路工作台 · 整定 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 内容：整定历史列表 + "开始整定"（带 loopId 进向导）
 * 数据来源：getTuningTasksApi
 *
 * 硬性规则：摘要 + 跳转入口
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'TuningTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty description="整定 Tab（开发中）— 整定历史列表 / 开始整定向导" />
  </div>
</template>
