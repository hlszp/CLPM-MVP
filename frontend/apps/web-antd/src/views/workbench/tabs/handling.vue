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
 * scope 口径（追溯矩阵 §6.1 整 tab 对齐 · G2 映射，2026-08-26 落地）：
 * - /handling/orders ×5 与 /handling/loops 跟随工作台 scope：scopeQuery()
 *   （utils/drill.ts）将 scopeId（plant_node.source_node_id）经 scopeTree.node_id
 *   解析为 plantNodeId（plant_node.id，递归子树语义）；GLOBAL 或未解析到时不带参
 *   → 全局口径。
 * - /handling/statistics 仅支持 months 参数（不支持 plantNodeId），SLA 侧栏
 *   summary/monthly 与「本月闭环」统计保持全局口径（已知口径差异，见 U2 侧栏注释）。
 * - scope 切换经 watch store.scopeParams 触发整 tab 重载。
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
import { useWorkbenchDrill } from '../utils/drill';

const store = useWorkbenchStore();
const userStore = useUserStore();
const { drill, scopeQuery } = useWorkbenchDrill();

// 追溯矩阵 §6 下钻接线（U1 断言数字 → 工单列表；窗口+scope 由 drill 携带）
/** 在办数 → 工单列表（非终态：PENDING/REOPENED/EXECUTING/VERIFYING） */
function drillInProgress() {
  drill('handling', '/handling/orders', {
    status: 'PENDING,REOPENED,EXECUTING,VERIFYING',
  });
}
/** 本月已闭环 → 工单列表（CLOSED；created/verified 时间筛选属 GAP-3 待补，暂只带 status） */
function drillClosed() {
  drill('handling', '/handling/orders', { status: 'CLOSED' });
}
/** 超期数 → 工单列表（plannedBefore=now + 非终态） */
function drillOverdue() {
  drill('handling', '/handling/orders', {
    plannedBefore: new Date().toISOString(),
    status: 'PENDING,REOPENED,EXECUTING,VERIFYING',
  });
}
/**
 * 临期数 → 工单列表（plannedBefore=now+24h + 非终态）。
 * 口径说明：plannedBefore 为上限筛选，会一并含已超期工单（临期⊂≤24h 到期集合），
 * 与断言黄框"临期=24h 内到期未超期"的展示口径存在包含关系，待 GAP-3 补 plannedAfter 后精确化。
 */
function drillNear() {
  drill('handling', '/handling/orders', {
    plannedBefore: new Date(Date.now() + 24 * 3600 * 1000).toISOString(),
    status: 'PENDING,REOPENED,EXECUTING,VERIFYING',
  });
}

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

/** 徽章计数：闭环道用 statistics 真实总数（而非 fetched cap 50）。
 * 注意：statistics 为全局口径（不支持 plantNodeId），scope 非全局时该总数与
 * 泳道内 scope 过滤后的列表存在口径差（已知差异，待 A-05 后端对齐）。 */
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
    // G2 scope 映射：orders/loops 跟随工作台 scope（scopeQuery 解析 plantNodeId，
    // GLOBAL/未解析到时为空对象即全局口径）；statistics 不支持 plantNodeId，保持全局。
    // pageSize 上限 100（后端 le=100，超限 422）；orders 排序后端固定（状态分组+updated_at DESC），不传 sort
    const scope = scopeQuery();
    const results = await Promise.allSettled([
      getHandlingOrdersApi({ status: 'PENDING', pageSize: 100, ...scope }),
      getHandlingOrdersApi({ status: 'REOPENED', pageSize: 100, ...scope }),
      getHandlingOrdersApi({ status: 'EXECUTING', pageSize: 100, ...scope }),
      getHandlingOrdersApi({ status: 'VERIFYING', pageSize: 100, ...scope }),
      getHandlingOrdersApi({ status: 'CLOSED', pageSize: 50, ...scope }),
      getHandlingStatisticsApi(6),
      getHandlingLoopsApi({ sort: 'reopened', pageSize: 50, ...scope }),
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
  { label: '重开列表', text: '重开次数降序 Top 8；反向色阶（多=红/少=蓝）；点击下钻该回路档案页。' },
  { label: '任务详情', text: '单源 selectedTask 联动；「📦 查看完整工单」开既有工单详情抽屉做全量字段+流转操作。' },
];
// 断言 ? 帮助
const assertionHelpItems = [
  { label: '断言', text: '当前范围在办/闭环/超期/临期/SLA 及时率一句话摘要。' },
  { label: 'SLA 警示色', text: '超期红（due<now）/ 临期橙（<now+24h）/ 正常绿 / 无排程灰；due 代理=plannedAt。' },
  { label: '数据口径', text: '工单/回路列表跟随工作台 scope（G2 映射 plantNodeId）；SLA 侧栏的月度统计与「本月闭环」为全局口径（/handling/statistics 暂不支持 scope 筛选）。' },
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
          <b
            class="cursor-pointer text-[#FA8C16] hover:underline"
            title="点击查看在办工单（非终态）"
            @click="drillInProgress"
          >{{ assertText.inProg }}</b> 在办
          ｜ 已闭环 <b
            class="cursor-pointer text-[#52C41A] hover:underline"
            title="点击查看已闭环工单"
            @click="drillClosed"
          >{{ assertText.closedCount }}</b>
          <template v-if="assertText.overdueN > 0">
            ｜ 超期 <b
              class="cursor-pointer text-[#FF4D4F] hover:underline"
              title="点击查看超期工单（plannedBefore=now + 非终态）"
              @click="drillOverdue"
            >{{ assertText.overdueN }}</b>
          </template>
          <template v-if="assertText.nearN > 0">
            ｜ 临期 <b
              class="cursor-pointer text-[#FA8C16] hover:underline"
              title="点击查看 24h 内到期工单（plannedBefore=now+24h + 非终态，含已超期）"
              @click="drillNear"
            >{{ assertText.nearN }}</b>
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
        <!-- SLA 窄边栏：sla 派生自 scope 过滤后的在办工单；statistics（summary/monthly）
             来自 /handling/statistics，该端点不支持 plantNodeId，保持全局口径（已知差异） -->
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
          <!-- 重开列表（行点击已改为下钻回路档案页，不再联动定位在办工单） -->
          <HandlingReopenList :loops="reopenList" />
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
