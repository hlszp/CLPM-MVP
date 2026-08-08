<script lang="ts" setup>
import type { DataSourceApi } from '#/api/datasource';
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
import { IconifyIcon } from '@vben/icons';

import { Button, message, Tag, Tooltip } from 'ant-design-vue';

import { getDatasourceHealthApi } from '#/api/datasource';
import { getDiagnosisTasksApi, getTrackerListApi } from '#/api/diagnosis';
import { getTaskListApi } from '#/api/task';
import { getTuningTasksApi } from '#/api/tuning';
import {
  ClpmAiDrawer,
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmPredictionCard,
  ClpmStandardActions,
} from '#/components/clpm';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import dayjs from 'dayjs';

import { formatTime, normalizeUtcTimestamp } from '#/utils/format';
import DiagnosisSummaryCard from '#/views/diagnosis/components/diagnosis-summary-card.vue';
import TrackerEffectivenessCard from '#/views/diagnosis/components/tracker-effectiveness-card.vue';

defineOptions({ name: 'DashboardWorkbench' });

const router = useRouter();
const { canAccessTuning } = useClpmRoles();

// AI 洞察两级门禁（workbench 场景无需 loopId，门禁2 恒通过）
const { init: initAiGate, gateStatus, gateTooltip } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);
const aiGateStatus = computed(() => gateStatus(null, false));
const aiGateTooltip = computed(() => gateTooltip(aiGateStatus.value));

const loading = ref(false);
const lastRefresh = ref('');
/** P1-023：单项接口失败计数，用于提示用户部分数据可能不完整 */
const failedCount = ref(0);
const diagnosisPending = ref(0);
const trackerActive = ref(0);
/** C1-3：验证中超期条目数（VERIFYING > 24h 未闭环） */
const verifyOverdue = ref(0);
const metricPending = ref(0);
const tuningTotal = ref(0);

/** P1-05：数据链路健康状态 */
const linkHealth = ref<DataSourceApi.DataSourceHealth | null>(null);
const linkHealthLoading = ref(false);

const todoKpiItems = computed<KpiStripItem[]>(() => {
  // 整改 A-03：零值中性——待办为 0 时不着色（零待办是好状态）
  const items: KpiStripItem[] = [
    {
      key: 'diagnosis',
      label: '诊断待处理',
      value: diagnosisPending.value,
      unit: '条',
      status: diagnosisPending.value > 0 ? 'warning' : 'neutral',
      clickable: true,
    },
    {
      key: 'tracker',
      label: '异常跟踪待办',
      value: trackerActive.value,
      unit: '条',
      status: trackerActive.value > 0 ? 'danger' : 'neutral',
      clickable: true,
    },
    {
      key: 'verifyOverdue',
      label: '验证超期',
      value: verifyOverdue.value,
      unit: '条',
      status: verifyOverdue.value > 0 ? 'danger' : 'neutral',
      clickable: true,
    },
    {
      key: 'metric',
      label: '评估待执行',
      value: metricPending.value,
      unit: '条',
      status: metricPending.value > 0 ? 'primary' : 'neutral',
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
  // 整改 A-12：非零待办优先展示（稳定排序）
  return items.sort((a, b) => Number(Number(b.value) > 0) - Number(Number(a.value) > 0));
});

function handleTodoClick(item: KpiStripItem) {
  const map: Record<string, string> = {
    diagnosis: '/diagnosis/tasks',
    metric: '/metric/tasks',
    tracker: '/diagnosis/tracker',
    tuning: '/tuning/workbench',
    // C1-3：验证超期直达 tracker 验证中筛选
    verifyOverdue: '/diagnosis/tracker?status=VERIFYING',
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
        // C1-3：验证中超期（VERIFYING > 24h 未闭环）进待办
        verifyOverdue.value = r.aggregates?.verifyOverdueCount ?? 0;
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

/** P1-05：数据链路健康状态计算 */
const signalrStatus = computed(() => {
  const h = linkHealth.value;
  if (!h) return { color: 'default', text: '未知', icon: 'lucide:circle-help' };
  if (!h.signalrEnabled)
    return { color: 'default', text: '未启用', icon: 'lucide:circle-slash' };
  if (h.signalrSubscriberRunning)
    return { color: 'success', text: '运行中', icon: 'lucide:radio' };
  return { color: 'error', text: '已停机', icon: 'lucide:wifi-off' };
});

const networkModeText = computed(() => {
  const mode = linkHealth.value?.networkMode;
  if (mode === 'wan') return '公网（Tailscale）';
  return '局域网直连';
});

/** 加载数据链路健康状态 */
async function loadLinkHealth() {
  linkHealthLoading.value = true;
  try {
    linkHealth.value = await getDatasourceHealthApi();
  } catch {
    linkHealth.value = null;
  } finally {
    linkHealthLoading.value = false;
  }
}

onMounted(() => {
  loadCounts();
  loadLinkHealth();
});

/** 刷新全部数据（工具栏刷新按钮） */
function handleRefresh() {
  loadCounts();
  loadLinkHealth();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '工作台 帮助',
    content:
      '跨模块待办门户：聚合诊断待处理、异常跟踪待办、评估待执行与整定任务计数，点击卡片可跳转对应模块。同时展示数据链路健康状态（实时订阅/网络模式/最近同步/Tailscale）、异常预测与整改有效率，便于快速掌握全局运营状态。',
  });
}

// ===== 统一工具栏（标准 3 工具：刷新 / AI 洞察 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  ai: {
    onClick: () => {
      aiDrawerOpen.value = true;
    },
    disabled: aiGateStatus.value !== 'active',
    disabledReason: aiGateTooltip.value,
    tooltip: aiGateTooltip.value,
  },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="系统概览"
      subtitle="跨模块待办门户"
      :loading="loading"
      :last-refresh="lastRefresh"
      status-type="info"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 跨模块待办计数（点击跳转对应模块） -->
    <ClpmKpiStrip
      class="mt-4"
      :items="todoKpiItems"
      :loading="loading"
      @item-click="handleTodoClick"
    />

    <!-- P3-05：异常预测与提前预警（紧跟待办，形成"需关注回路"区域） -->
    <ClpmPredictionCard class="mt-4" :top-n="10" />

    <!-- P1-05：数据链路健康状态卡片（常驻工作台首屏） -->
    <ClpmDataCanvas
      class="mt-4"
      title="数据链路健康状态"
      description="实时订阅与历史数据源连通性一览，断连时红色告警"
    >
      <div class="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <!-- 实时订阅状态 -->
        <div class="flex flex-col items-center gap-1 rounded border p-3">
          <IconifyIcon
            :icon="signalrStatus.icon"
            :size="24"
            :style="{
              color:
                signalrStatus.color === 'success'
                  ? 'hsl(var(--status-ok))'
                  : signalrStatus.color === 'error'
                    ? 'hsl(var(--status-error))'
                    : 'hsl(var(--muted-foreground))',
            }"
          />
          <span class="text-xs" style="color: hsl(var(--muted-foreground))">
            实时订阅
          </span>
          <Tag :color="signalrStatus.color" class="!m-0">
            {{ signalrStatus.text }}
          </Tag>
        </div>

        <!-- 网络模式 -->
        <div class="flex flex-col items-center gap-1 rounded border p-3">
          <IconifyIcon
            :icon="
              linkHealth?.networkMode === 'wan'
                ? 'lucide:globe'
                : 'lucide:network'
            "
            :size="24"
            style="color: hsl(var(--status-info))"
          />
          <span class="text-xs" style="color: hsl(var(--muted-foreground))">
            网络模式
          </span>
          <span class="text-sm font-medium">{{ networkModeText }}</span>
        </div>

        <!-- 最近同步时间 -->
        <div class="flex flex-col items-center gap-1 rounded border p-3">
          <IconifyIcon
            icon="lucide:refresh-cw"
            :size="24"
            style="color: hsl(var(--status-info))"
          />
          <span class="text-xs" style="color: hsl(var(--muted-foreground))">
            最近同步
          </span>
          <Tooltip
            v-if="linkHealth?.lastSyncAt"
            :title="formatTime(linkHealth.lastSyncAt)"
          >
            <span class="text-sm font-medium font-mono">
              {{
                dayjs(normalizeUtcTimestamp(linkHealth.lastSyncAt)).format(
                  'MM-DD HH:mm:ss',
                )
              }}
            </span>
          </Tooltip>
          <span
            v-else
            class="text-sm"
            style="color: hsl(var(--muted-foreground))"
          >
            —
          </span>
        </div>

        <!-- Tailscale 状态 -->
        <div class="flex flex-col items-center gap-1 rounded border p-3">
          <IconifyIcon
            :icon="
              linkHealth?.tailscaleAvailable
                ? 'lucide:shield-check'
                : 'lucide:shield-off'
            "
            :size="24"
            :style="{
              color: linkHealth?.tailscaleAvailable
                ? 'hsl(var(--status-ok))'
                : 'hsl(var(--muted-foreground))',
            }"
          />
          <span class="text-xs" style="color: hsl(var(--muted-foreground))">
            Tailscale
          </span>
          <Tag
            :color="linkHealth?.tailscaleAvailable ? 'success' : 'default'"
            class="!m-0"
          >
            {{ linkHealth?.tailscaleAvailable ? '可用' : '不可用' }}
          </Tag>
        </div>
      </div>
    </ClpmDataCanvas>

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

    <!-- AI 洞察右抽屉（工具栏 AI 图标触发，§5.2） -->
    <ClpmAiDrawer v-model:open="aiDrawerOpen" scene="workbench" />
  </Page>
</template>
