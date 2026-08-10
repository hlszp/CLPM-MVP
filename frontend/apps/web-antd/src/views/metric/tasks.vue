<script lang="ts" setup>
import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { Badge, TabPane, Tabs } from 'ant-design-vue';

import { getTaskListApi } from '#/api/task';
import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'MetricTasks' });

type TabKey = 'auto' | 'history' | 'manual' | 'strategy';

/**
 * P3-21：默认 Tab 按角色选择
 * - ADMIN：默认「自动任务」（关注定时评估计划与运行状态）
 * - IC_ENGINEER / 其他：默认「手动任务」（日常按回路/时间窗触发重算）
 */
const userStore = useUserStore();
const userRoles = computed(() => userStore.userInfo?.roles ?? []);
const defaultTab: TabKey = userRoles.value.includes('ADMIN')
  ? 'auto'
  : 'manual';
const activeTab = ref<TabKey>(defaultTab);

/** P3-01：子组件 ref，替代 tabKeys 自增强制重建 */
interface TabRef {
  refresh?: () => Promise<void> | void;
}

const manualRef = ref<null | TabRef>(null);
const autoRef = ref<null | TabRef>(null);
const historyRef = ref<null | TabRef>(null);
const strategyRef = ref<null | TabRef>(null);

/** 按 activeTab 获取对应子组件 ref */
function getActiveTabRef(): null | TabRef {
  const refMap: Record<TabKey, typeof manualRef> = {
    manual: manualRef,
    auto: autoRef,
    history: historyRef,
    strategy: strategyRef,
  };
  return refMap[activeTab.value]?.value ?? null;
}

const ManualTab = defineAsyncComponent(() => import('./recompute.vue'));
const AutoTab = defineAsyncComponent(() => import('#/views/task/list.vue'));
const HistoryTab = defineAsyncComponent(
  () => import('./history-snapshots.vue'),
);
const StrategyTab = defineAsyncComponent(() => import('./task-strategy.vue'));

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** P2-14：自动任务 Tab 活跃任务计数 */
const activeTaskCount = ref(0);

async function loadActiveTaskCount() {
  try {
    const result = await getTaskListApi({
      status: 'RUNNING',
      page: 1,
      pageSize: 1,
    });
    activeTaskCount.value = result.total ?? 0;
  } catch {
    // 错误已由拦截器处理
  }
}

function handleTabChange(key: number | string) {
  const k = String(key) as TabKey;
  activeTab.value = k;
}

/** P3-01：工具栏刷新：调用当前活动 Tab 子组件 refresh() 方法 */
async function handleRefresh() {
  loading.value = true;
  try {
    await nextTick();
    await getActiveTabRef()?.refresh?.();
    await loadActiveTaskCount();
  } finally {
    loading.value = false;
  }
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '评估任务 帮助',
    content:
      '评估任务管理页：手动任务（按回路/时间窗口触发重算）、自动任务（定时评估计划）、评估历史（KPI 快照查询）、策略配置（评估参数与权重策略）。刷新按钮调用当前 Tab 的 refresh() 方法重新拉取数据。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  loadActiveTaskCount();
});
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
    <!-- P3-01：所有 Tab 用 ref 绑定替代 :key 强制重建 -->
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <TabPane key="manual" tab="手动任务">
          <ManualTab ref="manualRef" />
        </TabPane>
        <TabPane key="auto">
          <template #tab>
            <Badge :count="activeTaskCount" :offset="[6, 0]" size="small">
              <span>自动任务</span>
            </Badge>
          </template>
          <AutoTab ref="autoRef" />
        </TabPane>
        <TabPane key="history" tab="评估历史">
          <HistoryTab ref="historyRef" />
        </TabPane>
        <TabPane key="strategy" tab="策略配置">
          <StrategyTab ref="strategyRef" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
