<script lang="ts" setup>
/**
 * 回路工作台 · 诊断 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 内容：诊断标签 + 置信度 + 模板小结 + "查看诊断详情"（带 loopId）+ "发起诊断"
 * 数据来源：getDiagnosisDetailApi
 *
 * 硬性规则：摘要 + 1 主图 + 跳转入口
 */
import { Empty } from 'ant-design-vue';

defineOptions({ name: 'DiagnosisTab' });

defineProps<{
  loopId: string;
}>();
</script>

<template>
  <div class="p-4">
    <Empty
      description="诊断 Tab（开发中）— 诊断标签 / 置信度 / 模板小结 / 跳转诊断详情"
    />
  </div>
</template>
