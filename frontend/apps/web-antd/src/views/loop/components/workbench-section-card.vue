<script lang="ts" setup>
/**
 * 工作台三区通用行容器（单页四区重构 · 2026-08-07）
 *
 * 每行结构：标题栏（左标题 + 右操作按钮 + 任务进度阶段）+ 可选进度条
 *          + 内容区（左摘要 slot flex-1 | 右迷你图 slot 固定宽）
 *
 * 工业设计口径（UI/UX v6.1 Calm UI）：紧凑布局，最大化 data-ink ratio，
 * 不加多余边框/阴影；任务运行时显示进度条与阶段文案。
 */
import { IconifyIcon } from '@vben/icons';

import { Progress, Spin } from 'ant-design-vue';

defineOptions({ name: 'WorkbenchSectionCard' });

withDefaults(
  defineProps<{
    empty?: boolean;
    emptyText?: string;
    icon?: string;
    loading?: boolean;
    /** 任务进度 0~1，传值时显示进度条 */
    progress?: null | number;
    /** 任务当前阶段文案（如"取数/预处理/指标计算"） */
    progressStage?: null | string;
    title: string;
  }>(),
  {
    icon: '',
    loading: false,
    empty: false,
    emptyText: '暂无数据',
    progress: null,
    progressStage: null,
  },
);
</script>

<template>
  <div class="wb-section">
    <!-- 标题栏 -->
    <div class="wb-section__header">
      <div class="wb-section__title">
        <IconifyIcon v-if="icon" :icon="icon" class="mr-1" :size="15" />
        <span class="font-medium">{{ title }}</span>
        <span v-if="progressStage" class="ml-2 text-xs text-gray-400">
          {{ progressStage }}
        </span>
      </div>
      <div class="wb-section__actions">
        <slot name="actions"></slot>
      </div>
    </div>

    <!-- 任务进度条（发起任务后显示） -->
    <Progress
      v-if="progress !== null"
      :percent="Math.round((progress ?? 0) * 100)"
      size="small"
      :show-info="false"
      :stroke-color="
        progress !== null && progress < 1
          ? 'var(--status-info)'
          : 'var(--status-ok)'
      "
      class="mb-1"
    />

    <!-- 内容区：左摘要 + 右迷你图 -->
    <div class="wb-section__body">
      <div class="wb-section__summary">
        <Spin :spinning="loading" size="small">
          <slot v-if="!empty"></slot>
          <div v-else class="py-4 text-center text-xs text-gray-400">
            <slot name="empty">{{ emptyText }}</slot>
          </div>
        </Spin>
      </div>
      <div v-if="$slots.chart" class="wb-section__chart">
        <slot name="chart"></slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wb-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 6px 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.wb-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.wb-section__title {
  font-size: 13px;
  color: hsl(var(--foreground));
}

.wb-section__actions {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  align-items: center;
}

.wb-section__body {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
}

.wb-section__summary {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

.wb-section__chart {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 220px;
}
</style>
