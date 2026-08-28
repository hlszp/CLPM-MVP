<script lang="ts" setup>
/**
 * 模块归档横幅（报告模块优化 P0-5，2026-08-28）
 *
 * 报告页口径（方案 R2/R3）：可插拔模块（诊断/整定/处置）禁用时，报告页
 * 照常展示历史归档数据，仅以灰色信息横幅提示——与工作台 ModuleBanner
 * （维护态，橙色警示）视觉区分。
 *
 * 用法：<ClpmModuleArchivedBanner :modules="['handling']" />
 */
import { computed } from 'vue';

import { moduleEnabled, type ModuleKey } from '#/composables/use-modules';

const props = defineProps<{
  /** 需要检查的模块 key 列表（任一禁用即显示） */
  modules: ModuleKey[];
}>();

const MODULE_LABELS: Record<string, string> = {
  diagnosis: '诊断',
  handling: '处置',
  tuning: '整定',
};

const disabledLabels = computed(() =>
  props.modules
    .filter((m) => !moduleEnabled(m))
    .map((m) => MODULE_LABELS[m] ?? m),
);

const message = computed(() =>
  `${disabledLabels.value.join('、')}模块已停用，以下为历史数据归档，查询与导出不受影响`,
);
</script>

<template>
  <div
    v-if="disabledLabels.length > 0"
    class="clpm-archived-banner"
    role="status"
  >
    <span class="clpm-archived-banner__icon">ⓘ</span>
    <span>{{ message }}</span>
  </div>
</template>

<style scoped>
.clpm-archived-banner {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 6px 12px;
  margin: 8px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--muted) / 60%);
  border: 1px dashed hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-archived-banner__icon {
  font-weight: 600;
}
</style>
