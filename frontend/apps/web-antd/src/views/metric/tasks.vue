<script lang="ts" setup>
import { defineAsyncComponent, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { TabPane, Tabs } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';

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
const HistoryTab = defineAsyncComponent(() => import('./history-snapshots.vue'));
const StrategyTab = defineAsyncComponent(() => import('./task-strategy.vue'));

function handleTabChange(key: number | string) {
  const k = String(key) as TabKey;
  activeTab.value = k;
  tabKeys.value[k] += 1;
}
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="评估任务"
      subtitle="管理手动重算任务、自动评估任务、评估历史与策略配置"
    />
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
