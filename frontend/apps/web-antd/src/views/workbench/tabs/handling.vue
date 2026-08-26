<script setup lang="ts">
import type { StaffLoadItem } from '../components/handling/types';

/**
 * 工作台 Tab5：问题处置 · V3 上下主结构（外壳不滚动，仅分区内滚动）
 *
 * 布局（1:1 套整定 V3 范式 + 决策 A 看板优先）：
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ U1 flex-none ~32px：黄框断言（在办/闭环/超期/临期/SLA 一句话）       │
 *   │ U2 flex-1 flex-row：SLA 窄边栏(~150px) + 4 泳道看板(flex-1)        │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ 下部行动区 flex-[1.15] · 深蓝条 26px                                │
 *   │ LOW：人员负载(flex-1) × 重开列表(flex-1) × 任务详情(flex-[1.4])    │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * 数据流（决策 B 前端拼装 · 0 后端）：
 * - getHandlingOrdersApi ×5（PENDING/REOPENED/EXECUTING/VERIFYING/CLOSED）
 * - getHandlingStatisticsApi（SLA summary + 近6月 monthly）
 * - getHandlingLoopsApi（重开列表）
 * - 前端 computed 聚合 kanban / staff_load / sla / reopen_list / assertion
 *
 * 联动：
 * - 单源 selectedTask：TaskCard@click / LaneCol@select 都写它
 * - 漏斗 store.handlingLaneFilter：总览 FunnelStat 点击 → 高亮泳道 + 清除 chip
 * - StaffHBar 点人 → handlerFilter 降透明非匹配卡片
 * - TaskDetailCard「完整工单」→ 既有 order-detail-drawer.vue 兜底
 *
 * 已知限制：workbench scope（scopeType/scopeId 数值）不映射 handling plantNodeId（字符串），
 *   处置 Tab 取全局口径；待 A-05 后端对齐 scope 后按作用域过滤。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, ref, watch } from 'vue';

import { useUserStore } from '@vben/stores';

import {
  getHandlingLoopsApi,
  getHandlingOrdersApi,
  getHandlingStatisticsApi,
  getOrderKpiComparisonApi,
} from '#/api/handling';
import { useWorkbenchStore } from '#/store/workbench';

import OrderDetailDrawer from '../../handling/components/order-detail-drawer.vue';
import HandlingReopenList from '../components/handling/HandlingReopenList.vue';
import HandlingSlaSummary from '../components/handling/HandlingSlaSummary.vue';
import OpsKanban from '../components/handling/OpsKanban.vue';
import { computeSlaBreakdown } from '../components/handling/sla-util';
import StaffHBar from '../components/handling/StaffHBar.vue';
import TaskDetailCard from '../components/handling/TaskDetailCard.vue';
import HelpBubble from '../components/HelpBubble.vue';

const store = useWorkbenchStore();
const userStore = useUserStore();

// ============ 数据 refs ============
const pending = ref<HandlingApi.OrderItem[]>([]);
const reopened = ref<HandlingApi.OrderItem[]>([]);
const executing = ref<HandlingApi.OrderItem[]>([]);
const verifying = ref<HandlingApi.OrderItem[]>([]);
const closed = ref<HandlingApi.OrderItem[]>([]);
const statistics = ref<HandlingApi.StatisticsData | null>(null);
const loops = ref<HandlingApi.LoopAggregateItem[]>([]);
const loading = ref(false);
const errorMsg = ref<null | string>(null);

// ============ 单源选中 ============
/** 单源：当前选中工单（TaskCard@click / LaneCol@select 都写它） */
const selectedTask = ref<HandlingApi.OrderItem | null>(null);
/** StaffHBar 点人过滤（null=清除） */
const selectedHandler = ref<null | string>(null);
/** KPI 前后对比（VERIFYING/CLOSED 工单按需拉取） */
const kpiCompare = ref<HandlingApi.KpiComparison | null>(null);
/** 工单详情抽屉 */
const drawerOpen = ref(false);
const drawerOrderId = ref<null | string>(null);

const canOperate = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) => ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r));
});

// ============ computed 聚合 ============
const kanban = computed(() => ({
  closed: closed.value,
  executing: executing.value,
  // REOPENED 并入待办道，TaskCard 据 status==='REOPENED' 打「重开」标
  pending: [...pending.value, ...reopened.value],
  verifying: verifying.value,
}));

/** 在办工单（pending+executing+verifying，SLA/staff_load 派生源） */
const inProgress = computed(() => [
  ...pending.value,
  ...reopened.value,
  ...executing.value,
  ...verifying.value,
]);

const sla = computed(() => computeSlaBreakdown(inProgress.value));

const staffLoad = computed<StaffLoadItem[]>(() => {
  const map = new Map<string, StaffLoadItem>();
  for (const t of inProgress.value) {
    const h = t.handler || '未指派';
    const row = map.get(h) ?? { handler: h, pending: 0, executing: 0, verifying: 0, overdue: 0 };
    switch (t.status) {
    case 'EXECUTING': {
    row.executing += 1;
    break;
    } 
    case 'PENDING':
    case 'REOPENED': {
    row.pending += 1;
    break;
    }
    case 'VERIFYING': { {
    row.verifying += 1;
    // No default
    }
    break;
    }
    }
    // overdue 由 SLA 派生（plannedAt<now）
    if (t.plannedAt && new Date(`${t.plannedAt}Z`).getTime() < Date.now()) row.overdue += 1;
    map.set(h, row);
  }
  return [...map.values()].toSorted((a, b) => (b.pending + b.executing + b.verifying) - (a.pending + a.executing + a.verifying));
});

const reopenList = computed(() =>
  loops.value
    .filter((l) => l.orderCounts.reopened > 0)
    .toSorted((a, b) => b.orderCounts.reopened - a.orderCounts.reopened)
    .slice(0, 8),
);

/** 徽章计数：闭环道用 statistics 真实总数（而非 fetched cap 50） */
const laneCounts = computed(() => ({
  closed: statistics.value?.summary.closedThisMonth ?? closed.value.length,
}));

const assertText = computed(() => {
  const inProg = inProgress.value.length;
  const closedCount = statistics.value?.summary.closedThisMonth ?? closed.value.length;
  const overdueN = sla.value.overdue;
  const nearN = sla.value.near;
  const timelyDenom = sla.value.normal + sla.value.near + sla.value.overdue;
  const timelyRate = timelyDenom > 0 ? Math.round((sla.value.normal / timelyDenom) * 100) : null;
  return { closedCount, inProg, nearN, overdueN, timelyRate };
});

// ============ 数据加载 ============
async function loadHandling() {
  loading.value = true;
  errorMsg.value = null;
  selectedTask.value = null;
  kpiCompare.value = null;
  try {
    // plantNodeId：workbench scope 不映射 handling plantNodeId（字符串），取全局口径
    // pageSize 上限 100（后端 le=100，超限 422）；orders 排序后端固定（状态分组+updated_at DESC），不传 sort
    const results = await Promise.allSettled([
      getHandlingOrdersApi({ status: 'PENDING', pageSize: 100 }),
      getHandlingOrdersApi({ status: 'REOPENED', pageSize: 100 }),
      getHandlingOrdersApi({ status: 'EXECUTING', pageSize: 100 }),
      getHandlingOrdersApi({ status: 'VERIFYING', pageSize: 100 }),
      getHandlingOrdersApi({ status: 'CLOSED', pageSize: 50 }),
      getHandlingStatisticsApi(6),
      getHandlingLoopsApi({ sort: 'reopened', pageSize: 50 }),
    ]);
    const [r0, r1, r2, r3, r4, r5, r6] = results;
    pending.value = r0.status === 'fulfilled' ? r0.value.items : [];
    reopened.value = r1.status === 'fulfilled' ? r1.value.items : [];
    executing.value = r2.status === 'fulfilled' ? r2.value.items : [];
    verifying.value = r3.status === 'fulfilled' ? r3.value.items : [];
    closed.value = r4.status === 'fulfilled' ? r4.value.items : [];
    statistics.value = r5.status === 'fulfilled' ? r5.value : null;
    loops.value = r6.status === 'fulfilled' ? r6.value.items : [];
    const failed = results.filter((r) => r.status === 'rejected').length;
    if (failed === results.length) {
      errorMsg.value = '数据加载失败，请检查 17101 后端';
    } else if (failed > 0) {
      errorMsg.value = `${failed} 项请求失败（部分数据降级显示）`;
    }
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '处置数据加载失败';
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

// ============ 联动处理 ============
function onSelectTask(task: HandlingApi.OrderItem) {
  selectedTask.value = task;
  // 选任务卡时清除泳道过滤（spec §3 范式约束）
  if (store.handlingLaneFilter) store.setHandlingLaneFilter(null);
}

function onSelectHandler(handler: string) {
  // 再次点击同一人 → 清除过滤
  selectedHandler.value = selectedHandler.value === handler ? null : handler;
}

function onReopenSelect(loop: HandlingApi.LoopAggregateItem) {
  // 重开列表点击 → 定位该回路首个在办工单（联动 TaskDetailCard）
  const t = inProgress.value.find((x) => x.loopId === loop.loopId);
  if (t) onSelectTask(t);
}

function onOpenDrawer(orderId: string) {
  drawerOrderId.value = orderId;
  drawerOpen.value = true;
}

function clearLaneFilter() {
  store.setHandlingLaneFilter(null);
}

/** 漏斗过滤 chip 状态码 → 中文标签 */
const LANE_FILTER_LABEL: Record<HandlingApi.OrderStatus, string> = {
  CANCELLED: '已作废',
  CLOSED: '已闭环',
  EXECUTING: '处理中',
  PENDING: '待办',
  REOPENED: '待办(重开)',
  VERIFYING: '验证中',
};
const laneFilterLabel = computed(() =>
  store.handlingLaneFilter ? (LANE_FILTER_LABEL[store.handlingLaneFilter] ?? store.handlingLaneFilter) : '',
);

// ============ KPI 对比按需拉取 ============
let kpiSeq = 0;
watch(selectedTask, async (task) => {
  kpiCompare.value = null;
  if (!task) return;
  // 仅 VERIFYING/CLOSED 工单有 KPI 前后对比意义；其余不拉取
  if (task.status !== 'VERIFYING' && task.status !== 'CLOSED') return;
  const seq = ++kpiSeq;
  try {
    const cmp = await getOrderKpiComparisonApi(task.id);
    if (seq === kpiSeq) kpiCompare.value = cmp;
  } catch {
    // 失败或无数据 → 隐藏 KPI 对比区（不阻断流转时间线）
    if (seq === kpiSeq) kpiCompare.value = null;
  }
});

onMounted(() => {
  loadHandling();
});
watch(
  () => store.scopeParams,
  () => loadHandling(),
  { deep: true },
);

// 行动区 ? 帮助
const actionHelpItems = [
  { label: '人员负载', text: '横向堆叠条按处理人聚合在办数（待办/处理中/验证中分色段）；点人过滤看板。' },
  { label: '重开列表', text: '重开次数降序 Top 8；反向色阶（多=红/少=蓝）；点击定位该回路在办工单。' },
  { label: '任务详情', text: '单源 selectedTask 联动；「📦 查看完整工单」开既有工单详情抽屉做全量字段+流转操作。' },
];
// 断言 ? 帮助
const assertionHelpItems = [
  { label: '断言', text: '当前范围在办/闭环/超期/临期/SLA 及时率一句话摘要。' },
  { label: 'SLA 警示色', text: '超期红（due<now）/ 临期橙（<now+24h）/ 正常绿 / 无排程灰；due 代理=plannedAt。' },
  { label: '数据缺口', text: '处置 Tab 取全局口径（workbench scope 不映射 handling plantNodeId）；待 A-05 后端对齐。' },
];
</script>

<template>
  <!-- 严格 overflow-hidden 链：外壳不滚动，仅分区内滚动 -->
  <div class="flex h-full min-h-0 flex-col overflow-hidden p-2" style="gap: 4px">
    <!-- 加载/错误提示 -->
    <div
      v-if="loading"
      class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
    >
      正在加载处置数据…
    </div>
    <div
      v-else-if="errorMsg"
      class="flex-none rounded border border-[#FFE58F] bg-[#FFFBE6] px-3 py-1 text-[11px] text-[#8C4A00]"
    >
      {{ errorMsg }}
      <button class="ml-2 underline" @click="loadHandling">重试</button>
    </div>

    <!-- ========== 上部：综合信息区（~45%） ========== -->
    <div class="flex min-h-0 flex-[0.9] flex-col overflow-hidden" style="gap: 4px">
      <!-- U1 黄框断言 -->
      <div
        class="flex flex-none items-center rounded-[2px] border border-[#FFE58F] bg-[#FFFBE6] px-2 py-[3px] text-[11px] leading-tight"
        style="min-height: 32px"
      >
        <div class="min-w-0 flex-1 text-[#593A00]">
          <b class="mr-1 text-[#8C4A00]">⚠</b>
          <b class="text-[#FA8C16]">{{ assertText.inProg }}</b> 在办
          ｜ 已闭环 <b class="text-[#52C41A]">{{ assertText.closedCount }}</b>
          <template v-if="assertText.overdueN > 0">
            ｜ 超期 <b class="text-[#FF4D4F]">{{ assertText.overdueN }}</b>
          </template>
          <template v-if="assertText.nearN > 0">
            ｜ 临期 <b class="text-[#FA8C16]">{{ assertText.nearN }}</b>
          </template>
          <template v-if="assertText.timelyRate !== null">
            ｜ SLA 及时率 <b :class="assertText.timelyRate >= 80 ? 'text-[#52C41A]' : 'text-[#FA8C16]'">{{ assertText.timelyRate }}%</b>
          </template>
          <span class="ml-1 text-[9.5px] text-[#8C4A00] opacity-70">
            {{ (store.scopeParams as { plantName?: string }).plantName ?? '全局' }} / {{ (store.scopeParams as { window?: string }).window ?? '30d' }}
          </span>
        </div>
        <HelpBubble :size="13" theme="blue" title="处置断言说明" :items="assertionHelpItems" class="ml-2 flex-none" />
      </div>

      <!-- U2 SLA 窄边栏 + 4 泳道看板 -->
      <div class="flex min-h-0 flex-1 overflow-hidden" style="gap: 4px">
        <!-- SLA 窄边栏 -->
        <div class="flex min-h-0 w-[150px] flex-none flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
          <HandlingSlaSummary
            :sla="sla"
            :statistics="statistics?.summary ?? null"
            :monthly="statistics?.monthly ?? []"
          />
        </div>
        <!-- 4 泳道看板 + 漏斗过滤 chip -->
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden">
          <!-- 漏斗联动清除 chip -->
          <div
            v-if="store.handlingLaneFilter"
            class="flex flex-none items-center gap-1 pb-[2px] text-[10px]"
          >
            <span
              class="inline-flex items-center rounded-[8px] bg-[#E6F7FF] px-[6px] py-[1px] text-[#1F4E79] ring-1 ring-[#1F4E79]"
            >
              漏斗过滤：{{ laneFilterLabel }}
              <button
                class="ml-1 text-[#1F4E79] hover:opacity-70"
                @click="clearLaneFilter"
              >×</button>
            </span>
          </div>
          <div class="flex min-h-0 flex-1 overflow-hidden">
            <OpsKanban
              :lanes="kanban"
              :lane-filter="store.handlingLaneFilter"
              :selected-task-id="selectedTask?.id ?? null"
              :handler-filter="selectedHandler"
              :lane-counts="laneCounts"
              @select="onSelectTask"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- ========== 下部：行动区（~55%） ========== -->
    <div class="flex min-h-0 flex-[1.15] flex-col overflow-hidden rounded-[2px] border border-[#1F4E79] bg-white">
      <div
        class="flex h-[26px] flex-none items-center border-b border-[#1F4E79] bg-[#1F4E79] px-2 text-[11px] font-semibold text-white"
      >
        <span class="mr-1.5 inline-block h-[12px] w-[4px] rounded-[2px] bg-[#52C41A]"></span>
        行动区 · 人员负载 × 重开列表 × 任务详情
        <HelpBubble :size="13" theme="white" title="行动区操作说明" :items="actionHelpItems" class="ml-1.5" />
        <span class="ml-auto text-[10px] font-normal opacity-90">
          在办 {{ assertText.inProg }} · 闭环 {{ assertText.closedCount }}
        </span>
      </div>
      <div class="flex min-h-0 flex-1 overflow-hidden" style="gap: 4px">
        <!-- 人员负载 -->
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
          <StaffHBar
            :staff="staffLoad"
            :selected-handler="selectedHandler"
            @select="onSelectHandler"
          />
        </div>
        <!-- 重开列表 -->
        <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
          <HandlingReopenList :loops="reopenList" @select="onReopenSelect" />
        </div>
        <!-- 任务详情 -->
        <div class="flex min-h-0 flex-[1.4] flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-[#F7F9FC]">
          <TaskDetailCard
            :task="selectedTask"
            :kpi-compare="kpiCompare"
            @open-drawer="onOpenDrawer"
          />
        </div>
      </div>
    </div>

    <!-- 工单详情抽屉兜底（既有组件，不重写本体） -->
    <OrderDetailDrawer
      v-model:open="drawerOpen"
      :order-id="drawerOrderId"
      :can-operate="canOperate"
    />
  </div>
</template>
