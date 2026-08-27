<script lang="ts" setup>
import type { TaskApi } from '#/api/task';

import { computed, defineAsyncComponent, nextTick, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { Badge, TabPane, Tabs } from 'ant-design-vue';

import { getTaskListApi } from '#/api/task';
import { ClpmPageToolbar, ClpmStandardActions } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'MetricTasks' });

type TabKey = 'list' | 'strategy';

/**
 * IA 重构二期：手动/自动任务合并为统一任务列表，评估记录提升为二级菜单。
 * 收敛为「任务列表 + 策略配置」双 Tab。
 *
 * 角色默认筛选（原 P3-21 简化版）：
 * - ADMIN：默认看自动评估（STANDARD，关注定时评估计划与运行状态）
 * - 其他：默认看手动评估（BACKFILL，日常按回路/时间窗触发重算）
 * 均可通过列表内「任务类型」筛选切换为全部。
 */
const userStore = useUserStore();
const userRoles = computed(() => userStore.userInfo?.roles ?? []);
const defaultTaskType: TaskApi.TaskType = userRoles.value.includes('ADMIN')
  ? 'STANDARD'
  : 'BACKFILL';

const activeTab = ref<TabKey>('list');

/** P3-01：子组件 ref，替代 tabKeys 自增强制重建 */
interface TabRef {
  refresh?: () => Promise<void> | void;
}

const listRef = ref<null | TabRef>(null);
const strategyRef = ref<null | TabRef>(null);

/** 按 activeTab 获取对应子组件 ref */
function getActiveTabRef(): null | TabRef {
  const refMap: Record<TabKey, typeof listRef> = {
    list: listRef,
    strategy: strategyRef,
  };
  return refMap[activeTab.value]?.value ?? null;
}

const TaskListTab = defineAsyncComponent(() => import('#/views/task/list.vue'));
const StrategyTab = defineAsyncComponent(() => import('./task-strategy.vue'));

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** P2-14：任务列表活跃任务计数（RUNNING） */
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
      '评估任务管理页：任务列表（统一展示自动评估与手动评估任务，可触发标准评估、新建手动评估、取消/删除任务）、策略配置（评估参数与权重策略）。KPI 快照明细（评估结果）请见「评估记录」页。刷新按钮调用当前 Tab 的 refresh() 方法重新拉取数据。',
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
      subtitle="统一管理自动评估与手动评估任务，配置评估策略"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <!-- P3-01：所有 Tab 用 ref 绑定替代 :key 强制重建 -->
    <div class="mt-4">
      <Tabs v-model:active-key="activeTab" @change="handleTabChange">
        <TabPane key="list">
          <template #tab>
            <Badge :count="activeTaskCount" :offset="[6, 0]" size="small">
              <span>任务列表</span>
            </Badge>
          </template>
          <TaskListTab ref="listRef" :default-task-type="defaultTaskType" />
        </TabPane>
        <TabPane key="strategy" tab="策略配置">
          <StrategyTab ref="strategyRef" />
        </TabPane>
      </Tabs>
    </div>
  </Page>
</template>
