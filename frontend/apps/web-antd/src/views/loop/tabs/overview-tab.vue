<script lang="ts" setup>
/**
 * 回路工作台 · 概览 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 内容：基本信息 + 7 tag + 评分 + 诊断标签 + 可信度 + 数据质量
 *       3 动作按钮：发起评估 / 发起诊断 / 开始整定
 *
 * 硬性规则：摘要 + 1 主图 + 跳转入口，禁止搬全表
 *
 * Phase B 骨架：桩组件，内容待后续从 detail.vue 概览迁移
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'OverviewTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty
      description="概览 Tab（开发中）— 基本信息 / KPI / 趋势波形 / 动作按钮"
    />
  </div>
</template>
