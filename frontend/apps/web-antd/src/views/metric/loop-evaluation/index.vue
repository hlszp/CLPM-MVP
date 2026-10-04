<script lang="ts" setup>
/**
 * 回路评估（2026-10-03 整合方案 C1 裁决：三视图合并）
 *
 * 原「回路性能 / 评估记录 / 指标矩阵」三页同数据源（kpi_snapshot 快照）、
 * 同筛选语义，本页以三 Tab 承载，切换视图不重选筛选：
 * - 当前榜单（rank）：每回路一行，最新快照 + 详情抽屉 + 等级统计（原回路性能）
 * - 历史快照（history）：快照流水 + 来源列（原评估记录，/metric/history）
 * - 指标矩阵（matrix）：全回路 × 全指标宽表（原指标矩阵，/metric/matrix）
 *
 * URL query 为真相源：view=rank|history|matrix（视图）+ 各 Tab 原生参数
 * （如 history 的 source/taskId、matrix 的 tab/window/plantNodeId/loopId）。
 * KeepAlive 缓存三个视图组件，切换不重挂载、筛选状态不丢。
 *
 * 旧路径 redirect：/metric/loop-performance、/metric/history、/metric/matrix
 * → 本页对应 view（query 原样透传，书签/深链/E2E 全兼容）。
 */
import type { Component } from 'vue';

import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';

defineOptions({ name: 'MetricLoopEvaluation' });

const route = useRoute();
const router = useRouter();

type ViewKey = 'history' | 'matrix' | 'rank';

const viewComponents: Record<ViewKey, Component> = {
  rank: defineAsyncComponent(() => import('./tabs/rank-tab.vue')),
  history: defineAsyncComponent(() => import('./tabs/history-tab.vue')),
  matrix: defineAsyncComponent(() => import('./tabs/matrix-tab.vue')),
};

const VIEWS: { key: ViewKey; label: string }[] = [
  { key: 'rank', label: '当前榜单' },
  { key: 'history', label: '历史快照' },
  { key: 'matrix', label: '指标矩阵' },
];

function normalizeView(v: unknown): ViewKey {
  return v === 'history' || v === 'matrix' || v === 'rank' ? v : 'rank';
}

const activeView = ref<ViewKey>(normalizeView(route.query.view));

// URL query 为真相源：视图切换 replace query（不重建组件实例，同页先例）
watch(
  () => route.query.view,
  (v) => {
    activeView.value = normalizeView(v);
  },
);

function handleViewChange(key: number | string) {
  activeView.value = normalizeView(key);
  router.replace({ query: { ...route.query, view: activeView.value } });
}

const activeComponent = computed(() => viewComponents[activeView.value]);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路评估"
      subtitle="回路级评估中心 · 当前榜单 / 历史快照 / 指标矩阵"
    />
    <Tabs
      :active-key="activeView"
      size="small"
      @change="handleViewChange"
    >
      <TabPane
        v-for="v in VIEWS"
        :key="v.key"
        :tab="v.label"
      />
    </Tabs>
    <div class="le-body">
      <KeepAlive include="LoopEvalRankTab, LoopEvalHistoryTab, LoopEvalMatrixTab">
        <component :is="activeComponent" />
      </KeepAlive>
    </div>
  </Page>
</template>

<style scoped>
.le-body {
  min-height: 0;
}

/* Tab 视图容器：替代被剥离的 Page 壳（三视图内部各自滚动） */
.le-body :deep(.le-tab-pane) {
  display: block;
}
</style>
