<script lang="ts" setup>
import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Tabs, TabPane } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';

defineOptions({ name: 'MetricTasks' });

const activeTab = ref<'manual' | 'auto' | 'strategy'>('manual');

const manualKey = ref(0);
const autoKey = ref(0);
const strategyKey = ref(0);

function handleTabChange(key: string | number) {
  activeTab.value = key as 'manual' | 'auto' | 'strategy';
  if (key === 'manual') manualKey.value++;
  if (key === 'auto') autoKey.value++;
  if (key === 'strategy') strategyKey.value++;
}
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="评估任务"
      subtitle="管理手动重算任务、自动评估任务记录与任务策略配置"
    />
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <TabPane key="manual" tab="手动任务">
          <component :is="(() => import('./recompute.vue'))()" :key="manualKey" />
        </TabPane>
        <TabPane key="auto" tab="自动任务">
          <component :is="(() => import('#/views/task/list.vue'))()" :key="autoKey" />
        </TabPane>
        <TabPane key="strategy" tab="任务策略">
          <component :is="(() => import('./task-strategy.vue'))()" :key="strategyKey" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
