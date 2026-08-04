<script lang="ts" setup>
/**
 * 工作台 — 跨模块待办门户（V62-P1-017）
 *
 * 对齐 PRD §4.1 + 实现契约 v2.3
 * - 顶部跨模块待办计数 KpiStrip：诊断待处理 / 异常跟踪待办 / 评估待执行 / 整定任务
 *   （整定卡片仅对有整定权限的角色渲染；工作台对 PE_ENGINEER/SPONSOR 可见，但整定模块仅 ADMIN/IC_ENGINEER/EXPERT）
 * - 复用 DiagnosisSummaryCard（诊断与异常跟踪聚合）+ TrackerEffectivenessCard（整改有效率）
 * - 装置性能完整看板归属 /metric/pid-dashboard，此处仅保留入口卡，消除与装置性能的重复心智入口
 *
 * 数据原则：所有计数走真实接口（status 过滤 + total），无数据显 0；单项接口失败不阻断其余
 */
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, message } from 'ant-design-vue';

import { getDiagnosisTasksApi, getTrackerListApi } from '#/api/diagnosis';
import { getTaskListApi } from '#/api/task';
import { getTuningTasksApi } from '#/api/tuning';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import DiagnosisSummaryCard from '#/views/diagnosis/components/diagnosis-summary-card.vue';
import TrackerEffectivenessCard from '#/views/diagnosis/components/tracker-effectiveness-card.vue';

defineOptions({ name: 'DashboardWorkbench' });

const router = useRouter();
const { canAccessTuning } = useClpmRoles();

const loading = ref(false);
const lastRefresh = ref('');
/** P1-023：单项接口失败计数，用于提示用户部分数据可能不完整 */
const failedCount = ref(0);
const diagnosisPending = ref(0);
const trackerActive = ref(0);
const metricPending = ref(0);
const tuningTotal = ref(0);

const todoKpiItems = computed<KpiStripItem[]>(() => {
  const items: KpiStripItem[] = [
    {
      key: 'diagnosis',
      label: '诊断待处理',
      value: diagnosisPending.value,
      unit: '条',
      status: 'warning',
      clickable: true,
    },
    {
      key: 'tracker',
      label: '异常跟踪待办',
      value: trackerActive.value,
      unit: '条',
      status: 'danger',
      clickable: true,
    },
    {
      key: 'metric',
      label: '评估待执行',
      value: metricPending.value,
      unit: '条',
      status: 'primary',
      clickable: true,
    },
  ];
  if (canAccessTuning.value) {
    items.push({
      key: 'tuning',
      label: '整定任务',
      value: tuningTotal.value,
      unit: '条',
      status: 'neutral',
      clickable: true,
    });
  }
  return items;
});

function handleTodoClick(item: KpiStripItem) {
  const map: Record<string, string> = {
    diagnosis: '/diagnosis/tasks',
    metric: '/metric/tasks',
    tracker: '/diagnosis/tracker',
    tuning: '/tuning/workbench',
  };
  const path = map[item.key];
  if (path) router.push(path);
}

async function loadCounts() {
  loading.value = true;
  failedCount.value = 0;
  try {
    const tasks: Promise<unknown>[] = [
      (async () => {
        const r = await getDiagnosisTasksApi({
          page: 1,
          pageSize: 1,
          status: 'PENDING',
        });
        diagnosisPending.value = r.total ?? 0;
      })(),
      (async () => {
        const r = await getTrackerListApi({
          page: 1,
          pageSize: 1,
          timeWindow: 'last_7_days',
        });
        const sc = r.aggregates?.statusCounts ?? {};
        trackerActive.value = (sc.PENDING ?? 0) + (sc.IN_PROGRESS ?? 0);
      })(),
      (async () => {
        const r = await getTaskListApi({
          page: 1,
          pageSize: 1,
          status: 'PENDING',
        });
        metricPending.value = r.total ?? 0;
      })(),
    ];
    if (canAccessTuning.value) {
      tasks.push(
        (async () => {
          const r = await getTuningTasksApi({ page: 1, pageSize: 1 });
          tuningTotal.value = r.total ?? 0;
        })(),
      );
    }
    const results = await Promise.allSettled(tasks);
    // P1-023：统计失败项，单项失败不阻断其余计数展示
    failedCount.value = results.filter((r) => r.status === 'rejected').length;
    if (failedCount.value > 0) {
      message.warning(
        `部分待办计数加载失败（${failedCount.value} 项），可点击刷新重试`,
      );
    }
  } finally {
    loading.value = false;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  }
}

function goPidDashboard() {
  router.push('/metric/pid-dashboard');
}

onMounted(() => {
  loadCounts();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="工作台"
      subtitle="跨模块待办门户"
      :loading="loading"
      :last-refresh="lastRefresh"
      status-type="info"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="loadCounts"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 跨模块待办计数（点击跳转对应模块） -->
    <ClpmKpiStrip
      class="mt-4"
      :items="todoKpiItems"
      :loading="loading"
      @item-click="handleTodoClick"
    />

    <!-- 诊断与异常跟踪聚合 + 整改有效率（自包含，各自拉取数据） -->
    <div class="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
      <DiagnosisSummaryCard />
      <TrackerEffectivenessCard />
    </div>

    <!-- 装置性能入口卡：完整看板在 /metric/pid-dashboard，消除重复心智入口 -->
    <ClpmDataCanvas class="mt-4" title="装置性能">
      <div class="flex items-center justify-between gap-4">
        <div class="text-sm" style="color: hsl(var(--muted-foreground))">
          全厂装置性能总览、回路排行、等级分布与实时自控率已归属「性能评估 ·
          装置性能」。
        </div>
        <Button type="primary" @click="goPidDashboard">进入装置性能</Button>
      </div>
    </ClpmDataCanvas>
  </Page>
</template>
