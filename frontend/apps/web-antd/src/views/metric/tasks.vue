<script lang="ts" setup>
import { defineAsyncComponent, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs } from 'ant-design-vue';

import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { usePageToolbar, showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'MetricTasks' });

type TabKey = 'auto' | 'history' | 'manual' | 'strategy';

const activeTab = ref<TabKey>('manual');

/** 各 Tab 组件 key，切换时自增以强制重载 */
const tabKeys = ref<Record<TabKey, number>>({
  manual: 0,
  auto: 0,
  history: 0,
  strategy: 0,
});

const ManualTab = defineAsyncComponent(() => import('./recompute.vue'));
const AutoTab = defineAsyncComponent(() => import('#/views/task/list.vue'));
const HistoryTab = defineAsyncComponent(
  () => import('./history-snapshots.vue'),
);
const StrategyTab = defineAsyncComponent(() => import('./task-strategy.vue'));

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

function handleTabChange(key: number | string) {
  const k = String(key) as TabKey;
  activeTab.value = k;
  tabKeys.value[k] += 1;
}

/** 工具栏刷新：强制重载当前 Tab（子组件各自重新拉取数据） */
function handleRefresh() {
  loading.value = true;
  tabKeys.value[activeTab.value] += 1;
  setTimeout(() => {
    loading.value = false;
  }, 300);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '评估任务 帮助',
    content:
      '评估任务管理页：手动任务（按回路/时间窗口触发重算）、自动任务（定时评估计划）、评估历史（KPI 快照查询）、策略配置（评估参数与权重策略）。刷新按钮重载当前 Tab 内容。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="评估任务"
      subtitle="管理手动重算任务、自动评估任务、评估历史与策略配置"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <TabPane key="manual" tab="手动任务">
          <ManualTab :key="tabKeys.manual" />
        </TabPane>
        <TabPane key="auto" tab="自动任务">
          <AutoTab :key="tabKeys.auto" />
        </TabPane>
        <TabPane key="history" tab="评估历史">
          <HistoryTab :key="tabKeys.history" />
        </TabPane>
        <TabPane key="strategy" tab="策略配置">
          <StrategyTab :key="tabKeys.strategy" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
