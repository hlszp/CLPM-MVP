<script lang="ts" setup>
/**
 * S7-TUNE-001 整定工作台
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部：4 个统计卡片（总任务数/已完成/平均拟合度/近 7 天任务数）
 * - 中部：整定流程导航卡片（模型辨识/整定算法/闭环仿真/效果统计）
 * - 底部：最近整定任务表格（recentTasks 前 10 条）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { KnowledgeBaseApi, TuningApi } from '#/api/tuning';
import type { KpiStripItem } from '#/components/clpm';

import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisListApi } from '#/api/diagnosis';
import { getSimilarCasesApi, getTuningHistoryApi } from '#/api/tuning';
import {
  ClpmConfidenceBadge,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmKpiStrip,
  ClpmLoopLink,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { DIAGNOSIS_TERM_EXPLANATIONS } from '#/constants/clpm-ui';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'TuningWorkbench' });

const { themeColors } = useClpmTheme();

const route = useRoute();
const router = useRouter();

/** P0-03：接收诊断→整定上下文参数 */
const diagnosisContext = computed(() => {
  const from = route.query.from as string;
  const loopId = route.query.loopId as string;
  const diagnosisLabel = route.query.diagnosisLabel as string;
  const confidenceLevel = route.query.confidenceLevel as string;
  const returnTo = route.query.returnTo as string | undefined;
  if (from !== 'diagnosis' || !loopId) return null;
  return { loopId, diagnosisLabel, confidenceLevel, returnTo };
});

/** P0-03：诊断标签显示名 */
const diagnosisLabelDisplay = computed(() => {
  if (!diagnosisContext.value?.diagnosisLabel) return '';
  const label = diagnosisContext.value.diagnosisLabel;
  return DIAGNOSIS_TERM_EXPLANATIONS[label]?.term ?? label;
});

const loading = ref(false);
const historyStats = ref<null | TuningApi.HistoryStats>(null);

/** 算法显示名映射 */
const algorithmNameMap: Record<TuningApi.Algorithm, string> = {
  IMC: 'IMC 内模控制',
  LAMBDA: 'Lambda 整定',
  ZN: 'Ziegler-Nichols',
  COHEN_COON: 'Cohen-Coon',
  SIMC: 'SIMC 简化 IMC',
};

/** 模型类型显示名映射 */
const modelTypeNameMap: Record<TuningApi.ModelType, string> = {
  FOPDT: 'FOPDT 一阶加纯滞后',
  SOPDT: 'SOPDT 二阶加纯滞后',
  IPDT: 'IPDT 积分加纯滞后',
};

/** 任务状态显示名映射（Phase 2 对齐实现契约 v2.1 状态机） */
const statusNameMap: Record<TuningApi.TaskStatus, string> = {
  // Phase 2 新枚举
  DRAFT: '草稿',
  RUNNING: '执行中',
  IDENTIFIED: '已辨识',
  SIMULATED: '已仿真',
  COMPLETED: '已完成',
  INCONCLUSIVE: '不确定',
  ROLLED_BACK: '已回退',
  // 旧枚举（兼容期保留）
  PENDING: '待辨识',
  APPLIED: '已应用',
  VERIFIED: '已验证',
};

/** 任务状态颜色映射（Phase 2 对齐实现契约 v2.1 状态机） */
const statusColorMap: Record<TuningApi.TaskStatus, string> = {
  // Phase 2 新枚举
  DRAFT: 'default',
  RUNNING: 'processing',
  IDENTIFIED: 'cyan',
  SIMULATED: 'blue',
  COMPLETED: 'green',
  INCONCLUSIVE: 'orange',
  ROLLED_BACK: 'red',
  // 旧枚举（兼容期保留）
  PENDING: 'default',
  APPLIED: 'green',
  VERIFIED: 'success',
};

/** 整定流程导航卡片（P1-019：model/algorithm/simulation 合并入 /tuning/flow stepper） */
const navCards = [
  {
    key: 'flow',
    title: '整定流程',
    description: '模型辨识 → 整定算法 → 闭环仿真（可恢复步骤流）',
    icon: 'ant-design:apartment-outlined',
    path: '/tuning/detail',
  },
  {
    key: 'stats',
    title: '效果统计',
    description: '查看整定历史统计与效果分析',
    icon: 'ant-design:bar-chart-outlined',
    path: '/tuning/stats',
  },
];

/** 最近任务表格列定义 */
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '模型类型',
    dataIndex: 'modelType',
    key: 'modelType',
    width: 180,
  },
  {
    title: '算法',
    dataIndex: 'algorithm',
    key: 'algorithm',
    width: 150,
  },
  {
    title: '拟合度',
    dataIndex: 'fittingScore',
    key: 'fittingScore',
    width: 100,
    align: 'right',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 100,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
];

/** 最近任务列表（前 10 条） */
const recentTasks = computed(() => {
  const list = historyStats.value?.recentTasks || [];
  return list.slice(0, 10);
});

/** 已完成任务数（Phase 2：COMPLETED + SIMULATED + 兼容旧 APPLIED） */
const completedCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (
    (byStatus.COMPLETED || 0) +
    (byStatus.SIMULATED || 0) +
    (byStatus.APPLIED || 0)
  );
});

/** 近 7 天任务数 */
const recent7DaysCount = computed(() => {
  const list = historyStats.value?.recentTasks || [];
  const sevenDaysAgo = dayjs().subtract(7, 'day');
  return list.filter((t) => dayjs(t.createdAt).isAfter(sevenDaysAgo)).length;
});

/** 平均拟合度 */
const avgFittingScore = computed(() => {
  return historyStats.value?.avgFittingScore ?? null;
});

/** 总任务数 */
const totalTasks = computed(() => {
  return historyStats.value?.totalTasks ?? 0;
});

function getFittingStatus(value: number): NonNullable<KpiStripItem['status']> {
  if (value >= 80) return 'success';
  if (value >= 60) return 'warning';
  return 'danger';
}

const kpiStripItems = computed<KpiStripItem[]>(() => [
  {
    key: 'total',
    label: '总任务数',
    value: totalTasks.value,
    status: 'neutral',
  },
  {
    key: 'completed',
    label: '已完成',
    value: completedCount.value,
    // 整改 A-03：零值中性（0 已完成不着色）
    status: completedCount.value > 0 ? 'success' : 'neutral',
  },
  {
    key: 'fitting',
    label: '平均拟合度',
    // 整改 A-03：无数据时显示"—"且中性，不得显示伪 0.00% 红色
    value:
      avgFittingScore.value === null ? '—' : avgFittingScore.value.toFixed(2),
    unit: avgFittingScore.value === null ? '' : '%',
    status:
      avgFittingScore.value === null
        ? 'neutral'
        : getFittingStatus(avgFittingScore.value),
  },
  {
    key: 'recent',
    label: '近 7 天任务数',
    value: recent7DaysCount.value,
    status: 'neutral',
  },
]);

/** 待整定数（Phase 2：DRAFT/RUNNING/PENDING + IDENTIFIED） */
const pendingTuningCount = computed(() => {
  const byStatus = historyStats.value?.byStatus || {};
  return (
    (byStatus.DRAFT || 0) +
    (byStatus.RUNNING || 0) +
    (byStatus.PENDING || 0) +
    (byStatus.IDENTIFIED || 0)
  );
});

/**
 * V62-P2-22：风险 KPI 已改为后端统计对接（get_tuning_history_stats）。
 * 语义：只有 riskSummary.calculated=true（即任意记录已生成 risk_assessment）
 * 时，前端才能把 0 当真实值。否则显示"— 未计算"，不得用 0 冒充未知。
 */
const UNKNOWN_RISK_VALUE = '—';
const UNKNOWN_RISK_UNIT = '未计算';

/** 风险相关 KPI 指标（整改 A-03：去掉与上排重复的"已完成数"，零值中性） */
const riskKpiItems = computed<KpiStripItem[]>(() => {
  const stats = historyStats.value;
  const summary = stats?.riskSummary;
  const calculated = Boolean(summary?.calculated);
  const high = calculated ? Number(summary!.high) || 0 : Number.NaN;
  const medium = calculated ? Number(summary!.medium) || 0 : Number.NaN;
  // overThreshold = MEDIUM + HIGH（PID 变幅 ≥20% 或可信度不够，属"超阈值"风险）
  const overThreshold = calculated ? high + medium : Number.NaN;
  // pendingCount：后端明确返回优先；否则回退到现有 byStatus 派生（保持兼容）
  const pending = stats?.pendingCount ?? pendingTuningCount.value;

  return [
    {
      key: 'highRisk',
      label: '风险任务数',
      value: calculated ? String(high) : UNKNOWN_RISK_VALUE,
      unit: calculated ? '项' : UNKNOWN_RISK_UNIT,
      status: calculated ? (high > 0 ? 'danger' : 'success') : 'neutral',
    },
    {
      key: 'overThreshold',
      label: '超阈值任务数',
      value: calculated ? String(overThreshold) : UNKNOWN_RISK_VALUE,
      unit: calculated ? '项' : UNKNOWN_RISK_UNIT,
      status: calculated
        ? (overThreshold > 0
          ? 'warning'
          : 'success')
        : 'neutral',
    },
    {
      key: 'pending',
      label: '待整定数',
      value: String(pending),
      unit: '项',
      status: pending > 0 ? 'warning' : 'success',
    },
  ];
});

/** 加载整定历史统计 */
async function loadHistory() {
  loading.value = true;
  try {
    const data = await getTuningHistoryApi();
    historyStats.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 跳转指定页面 */
function handleNavigate(path: string) {
  router.push(path);
}

/** 查看任务详情 */
function handleViewDetail(record: TuningApi.TuningTaskItem) {
  router.push({
    path: '/tuning/stats',
    query: { taskId: record.id },
  });
}

/** P1-019：未终态任务可「继续」整定，带 taskId 进入 flow 触发后端回显 */
function isResumable(status: TuningApi.TaskStatus): boolean {
  return ['DRAFT', 'IDENTIFIED', 'RUNNING', 'SIMULATED'].includes(status);
}

/** 继续未完成的整定任务（进入 flow stepper 并按 taskId 回显） */
function handleContinueTask(record: TuningApi.TuningTaskItem) {
  router.push({ path: '/tuning/detail', query: { taskId: record.id } });
}

/** 工具栏：刷新 */
function handleRefresh() {
  loadHistory();
  loadPendingLoops();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '整定工作台 帮助',
    content:
      '整定任务的统一入口：展示统计卡片（总任务数/已完成/平均拟合度/近 7 天任务数）、整定流程导航、待整定回路列表（聚合诊断中心建议整定的开放异常）、相似案例推荐与最近整定任务。可从诊断中心携带上下文进入发起整定，「新建整定」跳转模型辨识流程。',
  });
}

// ===== 统一工具栏（标准 3 工具：刷新 / 导出 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  export: {
    onClick: () => {},
    disabled: true,
    disabledReason: '导出功能开发中，待后端接口支持',
  },
  help: { onClick: handleHelp },
}));

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('tuning-workbench');

// P2 #37 UX13: 导出功能开发中，按钮改为 disabled + tooltip

/** 工具栏：新建整定，跳转模型辨识 */
function handleCreate() {
  router.push('/tuning/detail');
}

/** P0-03：基于诊断上下文发起整定，跳转 flow 并传递回路 */
function handleStartFromDiagnosis() {
  const ctx = diagnosisContext.value;
  if (!ctx) return;
  router.push({
    path: '/tuning/detail',
    query: {
      loopId: ctx.loopId,
      from: 'diagnosis',
      // P1-07：透传返回路径，flow 页可一键返回诊断详情
      ...(ctx.returnTo ? { returnTo: ctx.returnTo } : {}),
    },
  });
}

// ---------------------------------------------------------------------------
// P0-05：待整定回路列表（聚合诊断中心"建议整定"标签的开放异常）
// ---------------------------------------------------------------------------

/** 建议整定的诊断标签（参数类问题可通过整定改善） */
const TUNABLE_LABELS = [
  'OSCILLATION',
  'OVERAGGRESSIVE',
  'OVERCONSERVATIVE',
  'VALVE_STICTION',
] as const;

const pendingLoopsLoading = ref(false);
const pendingLoapsRaw = ref<DiagnosisApi.DiagnosisListItem[]>([]);

/** 待整定回路 = 开放状态 + 建议整定标签 */
const pendingLoops = computed(() =>
  pendingLoapsRaw.value.filter((item) =>
    (TUNABLE_LABELS as readonly string[]).includes(item.diagnosisLabel),
  ),
);

const pendingLoopColumns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 160,
    ellipsis: true,
  },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 140,
    ellipsis: true,
  },
  { title: '诊断标签', dataIndex: 'labelName', key: 'labelName', width: 110 },
  {
    title: '可信度',
    dataIndex: 'confidenceLevel',
    key: 'confidenceLevel',
    width: 70,
    align: 'center',
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 90,
    align: 'right',
  },
  {
    title: '发现时间',
    dataIndex: 'diagnosedAt',
    key: 'diagnosedAt',
    width: 160,
  },
  { title: '操作', key: 'action', width: 120, fixed: 'right' },
];

/** 加载待整定回路（查询开放状态异常，前端过滤建议整定标签） */
async function loadPendingLoops() {
  pendingLoopsLoading.value = true;
  try {
    const [pending, inProgress, reopened] = await Promise.all([
      getDiagnosisListApi({
        actionStatus: 'PENDING',
        page: 1,
        pageSize: 100,
      }),
      getDiagnosisListApi({
        actionStatus: 'IN_PROGRESS',
        page: 1,
        pageSize: 100,
      }),
      getDiagnosisListApi({
        actionStatus: 'REOPENED',
        page: 1,
        pageSize: 100,
      }),
    ]);
    pendingLoapsRaw.value = [
      ...(pending.items ?? []),
      ...(inProgress.items ?? []),
      ...(reopened.items ?? []),
    ];
  } catch {
    pendingLoapsRaw.value = [];
  } finally {
    pendingLoopsLoading.value = false;
  }
}

/** P0-05：从待整定列表发起整定 */
function handleStartTuning(record: DiagnosisApi.DiagnosisListItem) {
  router.push({
    path: '/tuning/detail',
    query: {
      loopId: record.loopId,
      diagnosisLabel: record.diagnosisLabel,
      from: 'diagnosis',
      // P1-07：携带返回路径（诊断详情页），flow 页可一键返回
      returnTo: `/diagnosis/detail?loopId=${record.loopId}`,
    },
  });
}

// ---------------------------------------------------------------------------
// P3-01：相似案例推荐（待整定回路选中后展示 Top 5 历史成功案例）
// ---------------------------------------------------------------------------

const similarCases = ref<KnowledgeBaseApi.KnowledgeEntry[]>([]);
const similarLoading = ref(false);
const similarLoopId = ref<null | string>(null);
const similarLoopTag = ref<string>('');

/** 算法显示名（知识库卡片复用） */
function algorithmDisplay(algo: null | string): string {
  if (!algo) return '-';
  const map: Record<string, string> = {
    IMC: 'IMC',
    LAMBDA: 'Lambda',
    ZN: 'Z-N',
    COHEN_COON: 'Cohen-Coon',
    SIMC: 'SIMC',
  };
  return map[algo] ?? algo;
}

/** PID 变化摘要 */
function pidChangeText(
  before: null | Record<string, number>,
  after: null | Record<string, number>,
): string {
  if (!before || !after) return '-';
  const fmt = (v: unknown) =>
    v === undefined || v === null || Number.isNaN(Number(v))
      ? '-'
      : Number(v).toFixed(2);
  const bp = fmt(before.kp ?? before.K);
  const ap = fmt(after.kp ?? after.K);
  const bi = fmt(before.ti ?? before.Ti);
  const ai = fmt(after.ti ?? after.Ti);
  return `P ${bp}→${ap}  I ${bi}→${ai}`;
}

/** 加载相似案例 */
async function loadSimilarCases(record: DiagnosisApi.DiagnosisListItem) {
  similarLoopId.value = record.loopId;
  similarLoopTag.value = record.tagName;
  similarLoading.value = true;
  try {
    const resp = await getSimilarCasesApi({
      loopId: record.loopId,
      limit: 5,
    });
    similarCases.value = resp?.items ?? [];
  } catch {
    similarCases.value = [];
  } finally {
    similarLoading.value = false;
  }
}

/** 跳转知识库详情 */
function handleViewKnowledgeBase() {
  router.push('/tuning/knowledge-base');
}

/** P2-23：相似案例详情抽屉 */
const caseDetailOpen = ref(false);
const caseDetailItem = ref<KnowledgeBaseApi.KnowledgeEntry | null>(null);

function handleViewCaseDetail(item: KnowledgeBaseApi.KnowledgeEntry) {
  caseDetailItem.value = item;
  caseDetailOpen.value = true;
}

/** 拟合度格式化 */
function formatFittingScore(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(2)}%`;
}

/** 拟合度颜色 */
function fittingScoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return '';
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

onMounted(() => {
  loadHistory();
  loadPendingLoops();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="整定工作台"
      subtitle="模型辨识、算法、仿真与效果统计的统一入口"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
        <ClpmToolbarButton
          icon="create"
          label="新建整定"
          variant="primary"
          @click="handleCreate"
        />
      </template>
    </ClpmPageToolbar>

    <Spin :spinning="loading">
      <!-- P0-03：来自诊断中心的上下文提示卡片 -->
      <Alert
        v-if="diagnosisContext"
        class="mt-3"
        type="info"
        show-icon
        banner
        :closable="true"
        message="来自诊断中心的整定请求"
      >
        <template #description>
          <div class="flex flex-wrap items-center gap-3">
            <span>
              回路：
              <span class="font-mono font-medium">{{
                diagnosisContext.loopId
              }}</span>
            </span>
            <span v-if="diagnosisLabelDisplay">
              诊断标签：
              <Tag color="orange">{{ diagnosisLabelDisplay }}</Tag>
            </span>
            <span
              v-if="diagnosisContext.confidenceLevel"
              class="flex items-center gap-1"
            >
              可信度：
              <ClpmConfidenceBadge
                :level="diagnosisContext.confidenceLevel as any"
              />
            </span>
            <Button
              v-if="diagnosisContext.returnTo"
              size="small"
              class="ml-auto"
              @click="router.push(diagnosisContext.returnTo)"
            >
              ← 返回诊断
            </Button>
            <Button
              type="primary"
              size="small"
              :class="diagnosisContext.returnTo ? '' : 'ml-auto'"
              @click="handleStartFromDiagnosis"
            >
              基于此诊断发起整定
            </Button>
          </div>
        </template>
      </Alert>

      <div class="mb-4 mt-4">
        <ClpmKpiStrip :items="kpiStripItems" />
      </div>

      <!-- 风险相关 KPI 指标 -->
      <div class="mb-4">
        <ClpmKpiStrip :items="riskKpiItems" />
      </div>

      <ClpmDataCanvas title="整定流程" class="mb-4">
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Card
            v-for="item in navCards"
            :key="item.key"
            hoverable
            size="small"
            :body-style="{ padding: '20px' }"
            class="cursor-pointer transition-shadow duration-200 hover:shadow-md"
            @click="handleNavigate(item.path)"
          >
            <div class="flex flex-col items-start">
              <div
                class="mb-3 flex h-10 w-10 items-center justify-center rounded bg-blue-50 text-xl text-blue-600"
              >
                <IconifyIcon :icon="item.icon" />
              </div>
              <div class="text-base font-semibold text-gray-800">
                {{ item.title }}
              </div>
              <div class="mt-1 text-xs text-gray-500">
                {{ item.description }}
              </div>
              <Button type="link" size="small" class="mt-2 !px-0">
                进入 →
              </Button>
            </div>
          </Card>
        </div>
      </ClpmDataCanvas>

      <!-- P0-05：待整定回路列表（聚合诊断中心建议整定的开放异常） -->
      <ClpmDataCanvas
        title="待整定回路"
        description="来自诊断中心的建议整定回路（振荡/参数过激/参数过保守/阀门粘滞）"
        class="mb-4"
      >
        <Table
          :columns="pendingLoopColumns"
          :data-source="pendingLoops"
          :loading="pendingLoopsLoading"
          :pagination="false"
          :row-key="(record: DiagnosisApi.DiagnosisListItem) => record.loopId"
          :scroll="{ x: 900 }"
          :size="tableSize"
        >
          <template #emptyText>
            <ClpmEmptyState
              title="暂无待整定回路"
              description="诊断中心未发现建议整定的开放异常（振荡/参数过激/参数过保守/阀门粘滞）"
              :actions="[
                {
                  label: '前往诊断中心',
                  icon: 'lucide:stethoscope',
                  primary: true,
                  onClick: () => router.push('/diagnosis/tracker'),
                },
              ]"
            />
          </template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              <ClpmLoopLink
                :loop-id="record.loopId"
                :tag-name="record.tagName"
                :show-menu="false"
                default-target="detail"
              />
            </template>
            <template v-else-if="column.key === 'labelName'">
              <Tag color="orange">
                {{
                  DIAGNOSIS_TERM_EXPLANATIONS[record.diagnosisLabel]?.term ??
                  record.labelName
                }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'confidenceLevel'">
              <ClpmConfidenceBadge
                :confidence="record.fusedConfidence"
                :valid-rate="null"
              />
            </template>
            <template v-else-if="column.key === 'compositeScore'">
              <span class="font-mono">{{
                Number(record.compositeScore).toFixed(1)
              }}</span>
            </template>
            <template v-else-if="column.key === 'diagnosedAt'">
              <span class="font-mono text-xs">{{
                formatTime(record.diagnosedAt)
              }}</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <div class="flex flex-wrap gap-1">
                <Button
                  type="primary"
                  size="small"
                  @click="
                    handleStartTuning(record as DiagnosisApi.DiagnosisListItem)
                  "
                >
                  发起整定
                </Button>
                <Button
                  size="small"
                  @click="
                    loadSimilarCases(record as DiagnosisApi.DiagnosisListItem)
                  "
                >
                  相似案例
                </Button>
              </div>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>

      <!-- P3-01：相似案例推荐（选中待整定回路后展示 Top 5） -->
      <ClpmDataCanvas
        v-if="similarLoopId"
        title="相似案例推荐"
        :description="`基于回路 ${similarLoopTag} 的控制类型/问题类型，从知识库匹配的历史成功整定案例（Top 5）`"
        class="mb-4"
      >
        <template #extra>
          <Button type="link" size="small" @click="handleViewKnowledgeBase">
            查看全部知识库 →
          </Button>
        </template>
        <Spin :spinning="similarLoading">
          <ClpmEmptyState
            v-if="!similarLoading && similarCases.length === 0"
            title="暂无相似案例"
            description="知识库中尚无匹配的历史整定案例。完成并验证整定后将自动沉淀为新条目。"
            :actions="[
              {
                label: '浏览知识库',
                icon: 'lucide:book-open',
                primary: true,
                onClick: handleViewKnowledgeBase,
              },
            ]"
          />
          <div v-else class="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Card
              v-for="item in similarCases"
              :key="item.id"
              size="small"
              :body-style="{ padding: '12px 14px' }"
              class="cursor-pointer transition-shadow hover:shadow-md"
              @click="handleViewCaseDetail(item)"
            >
              <div class="mb-2 flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-sm font-medium">
                    {{ item.tagName }}
                  </span>
                  <Tag v-if="item.diagnosisLabel" color="orange">
                    {{
                      DIAGNOSIS_TERM_EXPLANATIONS[item.diagnosisLabel]?.term ??
                      item.diagnosisLabel
                    }}
                  </Tag>
                </div>
                <Tag :color="item.effectVerified === false ? 'red' : 'green'">
                  {{
                    item.effectVerified === false
                      ? '恶化'
                      : item.effectVerified === true
                        ? '改善'
                        : '未验证'
                  }}
                </Tag>
              </div>
              <div class="mb-1 flex items-center gap-3 text-xs text-gray-500">
                <span>{{ algorithmDisplay(item.algorithm) }}</span>
                <span v-if="item.modelType">· {{ item.modelType }}</span>
                <ClpmConfidenceBadge
                  v-if="item.confidenceLevel"
                  :level="item.confidenceLevel as any"
                />
              </div>
              <div class="mb-1 text-xs text-gray-600">
                <span class="text-gray-400">PID 变化：</span>
                {{ pidChangeText(item.pidBefore, item.pidAfter) }}
              </div>
              <div class="text-xs text-gray-500">
                <span v-if="item.improvedCount" class="text-green-600">
                  改善 {{ item.improvedCount }} 项
                </span>
                <span v-if="item.deterioratedCount" class="ml-2 text-red-600">
                  恶化 {{ item.deterioratedCount }} 项
                </span>
                <span v-if="item.implementedAt" class="ml-2">
                  · {{ formatTime(item.implementedAt) }}
                </span>
              </div>
            </Card>
          </div>
        </Spin>
      </ClpmDataCanvas>

      <!-- 最近整定任务表格 -->
      <ClpmDataCanvas title="最近整定任务">
        <Table
          :columns="columns"
          :data-source="recentTasks"
          :loading="loading"
          :pagination="false"
          :row-key="(record: TuningApi.TuningTaskItem) => record.id"
          :scroll="{ x: 950 }"
          :size="tableSize"
        >
          <template #emptyText>
            <ClpmEmptyState
              scene="task"
              :actions="[
                {
                  label: '新建整定',
                  icon: 'lucide:plus',
                  primary: true,
                  onClick: handleCreate,
                },
              ]"
            />
          </template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tagName'">
              <span class="font-mono text-xs font-medium">
                {{ record.tagName || record.loopId }}
              </span>
            </template>
            <template v-else-if="column.key === 'modelType'">
              {{
                modelTypeNameMap[record.modelType as TuningApi.ModelType] ||
                record.modelType
              }}
            </template>
            <template v-else-if="column.key === 'algorithm'">
              {{
                algorithmNameMap[record.algorithm as TuningApi.Algorithm] ||
                record.algorithm
              }}
            </template>
            <template v-else-if="column.key === 'fittingScore'">
              <span
                class="font-mono"
                :style="{
                  color: fittingScoreColor(record.fittingScore),
                }"
              >
                {{ formatFittingScore(record.fittingScore) }}
              </span>
            </template>
            <template v-else-if="column.key === 'status'">
              <Tag
                :color="statusColorMap[record.status as TuningApi.TaskStatus]"
              >
                {{
                  statusNameMap[record.status as TuningApi.TaskStatus] ||
                  record.status
                }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'createdAt'">
              {{ formatTime(record.createdAt) }}
            </template>
            <template v-else-if="column.key === 'action'">
              <Button
                v-if="isResumable(record.status as TuningApi.TaskStatus)"
                type="link"
                size="small"
                @click="handleContinueTask(record as TuningApi.TuningTaskItem)"
              >
                继续
              </Button>
              <Button
                v-else
                type="link"
                size="small"
                @click="handleViewDetail(record as TuningApi.TuningTaskItem)"
              >
                查看详情
              </Button>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </Spin>

    <!-- P2-23：相似案例详情抽屉 -->
    <Drawer
      v-model:open="caseDetailOpen"
      title="整定案例详情"
      width="560"
      :destroy-on-close="true"
    >
      <template v-if="caseDetailItem">
        <Descriptions :column="1" bordered size="small">
          <DescriptionsItem label="回路位号">
            {{ caseDetailItem.tagName }}
          </DescriptionsItem>
          <DescriptionsItem label="问题类型">
            <Tag v-if="caseDetailItem.diagnosisLabel" color="orange">
              {{
                DIAGNOSIS_TERM_EXPLANATIONS[caseDetailItem.diagnosisLabel]
                  ?.term ?? caseDetailItem.diagnosisLabel
              }}
            </Tag>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="算法">
            {{ algorithmDisplay(caseDetailItem.algorithm) }}
          </DescriptionsItem>
          <DescriptionsItem label="模型类型">
            {{ caseDetailItem.modelType || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <ClpmConfidenceBadge
              v-if="caseDetailItem.confidenceLevel"
              :level="caseDetailItem.confidenceLevel as any"
            />
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="PID 变化">
            {{
              pidChangeText(caseDetailItem.pidBefore, caseDetailItem.pidAfter)
            }}
          </DescriptionsItem>
          <DescriptionsItem label="效果">
            <Tag
              :color="caseDetailItem.effectVerified === false ? 'red' : 'green'"
            >
              {{
                caseDetailItem.effectVerified === false
                  ? '恶化'
                  : caseDetailItem.effectVerified === true
                    ? '改善'
                    : '未验证'
              }}
            </Tag>
            <span
              v-if="caseDetailItem.improvedCount"
              class="ml-2 text-green-600"
            >
              改善 {{ caseDetailItem.improvedCount }} 项
            </span>
            <span
              v-if="caseDetailItem.deterioratedCount"
              class="ml-2 text-red-600"
            >
              恶化 {{ caseDetailItem.deterioratedCount }} 项
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="实施时间">
            {{
              caseDetailItem.implementedAt
                ? formatTime(caseDetailItem.implementedAt)
                : '—'
            }}
          </DescriptionsItem>
        </Descriptions>
      </template>
    </Drawer>
  </Page>
</template>
