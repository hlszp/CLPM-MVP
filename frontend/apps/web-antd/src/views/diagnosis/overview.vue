<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * S4-DIAG-013 诊断总览页（v6.1 IA 调整新增）
 *
 * 替代原"统计报表"页 + 直接进入列表的入口，作为诊断中心的默认着陆页。
 * 对齐 IDS v3.2 §2.4 + PRD §4.4 + UI/UX v6.1 §4.2
 *
 * 内容区：
 * - 顶部 4 个 KPI 统计卡片：异常回路数（今日） / 待处理任务数 / 已闭环任务数 / 平均闭环时长（小时）
 * - 中间区域：左侧标签分布饼图，右侧近 30 天异常趋势折线图
 * - 底部区域：Top 5 异常回路表格
 *
 * 数据来源：
 * - getDiagnosisAnalyticsApi：标签分布 + 处理效率趋势
 * - getDiagnosisListApi：今日异常回路列表 + 状态计数 + Top 5 异常回路
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Space, Table, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisAnalyticsApi, getDiagnosisListApi } from '#/api/diagnosis';
import {
  ClpmConfidenceBadge,
  ClpmDataCanvas,
  ClpmEmptyState,
  ClpmInfoTip,
  ClpmKpiCard,
  ClpmLoopLink,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { DIAGNOSIS_TERM_EXPLANATIONS } from '#/constants/clpm-ui';
import {
  DIAGNOSIS_LABEL_COLOR_HEX_MAP,
  DIAGNOSIS_LABEL_COLOR_MAP,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { $t } from '#/locales';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisOverview' });

const router = useRouter();
const { isDark, themeColors } = useClpmTheme();
const { getStatusMeta } = useIndustrialStatus();

/** 加载态与错误态 */
const loading = ref(false);
const hasError = ref(false);

/** 统计数据 */
const analyticsData = ref<DiagnosisApi.AnalyticsResult | null>(null);

/** 今日异常回路列表（用于 Top 5） */
const todayDiagnosisList = ref<DiagnosisApi.DiagnosisListItem[]>([]);

// ECharts refs
const pieChartRef = ref<EchartsUIType>();
const trendChartRef = ref<EchartsUIType>();

const { renderEcharts: renderPie } = useEcharts(pieChartRef);
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

/** 图表空态：无数据时不渲染空框架，由 ClpmDataCanvas 空态接管 */
const pieEmpty = ref(false);
const trendEmpty = ref(false);

/** 处理状态选项（用于状态名展示） */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '已实施', value: 'IMPLEMENTED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** KPI 卡片数据（响应式 computed） */
const kpiCards = ref([
  {
    key: 'abnormal_today',
    title: '异常回路数（今日）',
    value: 0,
    unit: '个',
    status: 'error' as const,
    icon: 'ant-design:alert-outlined',
    infoTip: '近 24 小时内被诊断为异常的回路数量',
    contextText: '近 24 小时',
    precision: 0,
  },
  {
    key: 'pending',
    title: '待处理任务数',
    value: 0,
    unit: '条',
    status: 'warning' as const,
    icon: 'ant-design:clock-circle-outlined',
    infoTip: '处理状态为"待处理"的诊断记录数',
    contextText: '需关注',
    precision: 0,
  },
  {
    key: 'implemented',
    title: '已闭环任务数',
    value: 0,
    unit: '条',
    status: 'ok' as const,
    icon: 'ant-design:check-circle-outlined',
    infoTip: '处理状态为"已实施"的诊断记录数',
    contextText: '近 30 天累计',
    precision: 0,
  },
  {
    key: 'avg_close_hours',
    title: '平均闭环时长',
    value: 0,
    unit: 'h',
    status: 'info' as const,
    icon: 'ant-design:field-time-outlined',
    infoTip: '近 30 天平均任务闭环时长（小时）',
    contextText: '近 30 天',
    precision: 1,
  },
]);

/** Top 5 异常回路表格列定义 */
const topColumns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 180 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '诊断标签',
    dataIndex: 'diagnosisLabel',
    key: 'diagnosisLabel',
    width: 140,
  },
  {
    title: '可信度',
    dataIndex: 'confidence',
    key: 'confidence',
    width: 80,
    align: 'center',
  },
  {
    title: '诊断时间',
    dataIndex: 'diagnosedAt',
    key: 'diagnosedAt',
    width: 150,
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 100,
  },
  { title: '操作', key: 'action', width: 140, fixed: 'right' },
];

/** 加载总览数据 */
async function loadOverview() {
  loading.value = true;
  hasError.value = false;
  try {
    // 并行加载统计 + 今日异常列表（timeWindow=last_24_hours）
    const now = dayjs();
    const start = now.subtract(30, 'day');
    const [analytics, todayList] = await Promise.all([
      getDiagnosisAnalyticsApi({
        startTime: start.format('YYYY-MM-DD HH:mm:ss'),
        endTime: now.format('YYYY-MM-DD HH:mm:ss'),
        granularity: 'day',
      }),
      getDiagnosisListApi({
        timeWindow: 'last_24_hours',
        page: 1,
        pageSize: 100,
      }),
    ]);

    analyticsData.value = analytics;
    todayDiagnosisList.value = todayList.items || [];

    // 计算 KPI 数值（待处理/已闭环改用后端全量聚合计数，不受分页影响）
    const abnormalToday = todayList.total || 0;
    const statusCounts = todayList.aggregates?.statusCounts ?? {};
    const pendingCount = statusCounts.PENDING ?? 0;
    const implementedCount = statusCounts.IMPLEMENTED ?? 0;
    // 平均闭环时长取 efficiencyTrend 最后一个值
    const trend = analytics.efficiencyTrend;
    const avgCloseHours =
      trend &&
      trend.avgCloseDurationHours &&
      trend.avgCloseDurationHours.length > 0
        ? (trend.avgCloseDurationHours[
            trend.avgCloseDurationHours.length - 1
          ] ?? 0)
        : 0;

    const values = [
      abnormalToday,
      pendingCount,
      implementedCount,
      Number(avgCloseHours.toFixed(1)),
    ];
    for (const [index, value] of values.entries()) {
      const card = kpiCards.value[index];
      if (card) card.value = value;
    }

    // 渲染图表
    nextTick(() => {
      renderPieChart();
      renderTrendChart();
    });
  } catch {
    hasError.value = true;
  } finally {
    loading.value = false;
  }
}

/** 渲染标签分布饼图 */
function renderPieChart() {
  const dist = analyticsData.value?.labelDistribution || [];
  if (dist.length === 0) {
    pieEmpty.value = true;
    return;
  }
  pieEmpty.value = false;

  renderPie({
    legend: { bottom: 0, orient: 'horizontal' },
    series: [
      {
        avoidLabelOverlap: false,
        data: dist.map((d) => ({
          itemStyle: { color: DIAGNOSIS_LABEL_COLOR_HEX_MAP[d.label] },
          name: d.labelName,
          value: d.count,
        })),
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
            shadowOffsetX: 0,
          },
        },
        label: { formatter: '{b}: {c} ({d}%)', show: true },
        radius: ['40%', '70%'],
        type: 'pie',
      },
    ],
    tooltip: { trigger: 'item' },
  });
}

/** 渲染近 30 天异常趋势折线图（已解决数 + 平均闭环时长双 Y 轴） */
function renderTrendChart() {
  const trend = analyticsData.value?.efficiencyTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) {
    trendEmpty.value = true;
    return;
  }
  trendEmpty.value = false;

  renderTrend({
    backgroundColor: 'transparent',
    grid: {
      bottom: 30,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['已解决数', '平均闭环时长'],
      top: 5,
    },
    series: [
      {
        data: trend.resolvedCount,
        itemStyle: { color: themeColors.value.SUCCESS },
        name: '已解决数',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: trend.avgCloseDurationHours,
        itemStyle: { color: themeColors.value.WARNING },
        name: '平均闭环时长',
        smooth: true,
        type: 'line',
        yAxisIndex: 1,
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          try {
            const d = new Date(val);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${mm}-${dd}`;
          } catch {
            return val;
          }
        },
      },
      boundaryGap: false,
      data: trend.timestamps,
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { formatter: '{value}' },
        name: '已解决数',
        nameTextStyle: { color: themeColors.value.SUCCESS },
        type: 'value',
      },
      {
        axisLabel: { formatter: '{value}h' },
        name: '平均闭环时长',
        nameTextStyle: { color: themeColors.value.WARNING },
        splitLine: { show: false },
        type: 'value',
      },
    ],
  });
}

/** Top 5 异常回路（按综合评分升序，最低分优先） */
const topAbnormalLoops = computed(() =>
  [...todayDiagnosisList.value]
    .toSorted((a, b) => a.compositeScore - b.compositeScore)
    .slice(0, 5),
);

/** 跳转诊断详情 */
function handleViewDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** F4：一键诊断 — 跳转回路分析页并预填 loopId */
function handleQuickDiagnose(loopId: string) {
  router.push({ path: '/diagnosis/loop-analysis', query: { loopId } });
}

/** 跳转诊断任务列表 */
function handleViewAll() {
  router.push('/diagnosis/tasks');
}

/** 重试加载 */
function handleRetry() {
  loadOverview();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '诊断总览 帮助',
    content:
      '诊断中心默认着陆页：展示异常回路数（今日）、待处理/已闭环任务数、平均闭环时长 4 项 KPI；标签分布饼图与近 30 天异常趋势（已解决数 + 平均闭环时长双 Y 轴）；底部 Top 5 异常回路表格按综合评分升序排列。点击「查看全部」进入诊断任务列表。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRetry, loading: loading.value },
  help: { onClick: handleHelp },
}));

/** 标签颜色 */
function labelColor(label: DiagnosisLabel): string {
  return DIAGNOSIS_LABEL_COLOR_MAP[label];
}

function labelName(label: DiagnosisLabel): string {
  return getDiagnosisLabelName(label);
}

function statusName(status: DiagnosisApi.ActionStatus): string {
  return statusOptions.find((o) => o.value === status)?.label || status;
}

// 深色模式切换时重新渲染所有图表
watch(isDark, () => {
  nextTick(() => {
    renderPieChart();
    renderTrendChart();
  });
});

onMounted(() => {
  loadOverview();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :title="$t('diagnosis.overview.title')"
      subtitle="诊断中心总览：异常回路概览、标签分布、近 30 天异常趋势与 Top 5 异常回路。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
        <ClpmToolbarButton
          icon="ant-design:unordered-list-outlined"
          label="查看全部"
          variant="primary"
          @click="handleViewAll"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 顶部 4 个 KPI 统计卡片 -->
    <div
      class="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4 flex-shrink-0"
    >
      <ClpmKpiCard
        v-for="card in kpiCards"
        :key="card.key"
        :title="card.title"
        :value="card.value"
        :unit="card.unit"
        :status="card.status"
        :icon="card.icon"
        :info-tip="card.infoTip"
        :context-text="card.contextText"
        :precision="card.precision"
        neutral-when-zero
        :loading="loading"
      />
    </div>

    <!-- 中间区域：左侧标签分布饼图 + 右侧异常趋势折线图 -->
    <div class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
      <ClpmDataCanvas
        title="诊断标签分布"
        description="近 30 天 8 类诊断标签占比"
        :loading="loading"
        :error="hasError"
        :empty="pieEmpty"
        empty-reason="近 30 天无诊断标签记录"
        loading-variant="opacity"
        @retry="handleRetry"
      >
        <EchartsUI ref="pieChartRef" height="320px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="近 30 天异常趋势"
        description="已解决数与平均闭环时长双 Y 轴趋势"
        :loading="loading"
        :error="hasError"
        :empty="trendEmpty"
        empty-reason="近 30 天无闭环处理记录"
        loading-variant="opacity"
        @retry="handleRetry"
      >
        <EchartsUI ref="trendChartRef" height="320px" />
      </ClpmDataCanvas>
    </div>

    <!-- 底部区域：Top 5 异常回路表格 -->
    <ClpmDataCanvas
      class="mt-4"
      title="Top 5 异常回路"
      description="按综合评分升序排列，优先关注评分最低的回路"
      :loading="loading"
      :error="hasError"
      :empty="topAbnormalLoops.length === 0 && !loading && !hasError"
      loading-variant="opacity"
      @retry="handleRetry"
    >
      <Table
        :columns="topColumns"
        :data-source="topAbnormalLoops"
        :pagination="false"
        :row-key="(record: DiagnosisApi.DiagnosisListItem) => record.loopId"
        :scroll="{ x: 930 }"
        size="middle"
      >
        <template #emptyText>
          <ClpmEmptyState
            :title="loading ? '加载中...' : '暂无异常回路'"
            :description="
              !loading ? '当前没有检测到异常回路，系统运行状态良好' : ''
            "
            icon="lucide:check-circle-2"
          />
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            <ClpmLoopLink
              :loop-id="record.loopId"
              :tag-name="record.tagName"
              :unit-name="record.unitName"
              default-target="diagnosis"
            />
          </template>
          <template v-else-if="column.key === 'diagnosisLabel'">
            <Space :size="4">
              <Tag :color="labelColor(record.diagnosisLabel as DiagnosisLabel)">
                {{ record.labelName || labelName(record.diagnosisLabel) }}
              </Tag>
              <ClpmInfoTip
                v-if="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]
                "
                :tip="
                  (
                    DIAGNOSIS_TERM_EXPLANATIONS as Record<
                      string,
                      { term: string; short: string; detail?: string }
                    >
                  )[record.diagnosisLabel]!.short
                "
              />
            </Space>
          </template>
          <template v-else-if="column.key === 'confidence'">
            <ClpmConfidenceBadge :confidence="record.confidence" />
          </template>
          <template v-else-if="column.key === 'diagnosedAt'">
            <span class="clpm-num">{{ formatTime(record.diagnosedAt) }}</span>
          </template>
          <template v-else-if="column.key === 'actionStatus'">
            <Tag
              :color="getStatusMeta(record.actionStatus as string).color"
              :style="{
                background: getStatusMeta(record.actionStatus as string)
                  .bgColor,
                borderColor: getStatusMeta(record.actionStatus as string)
                  .borderColor,
              }"
            >
              {{ statusName(record.actionStatus as DiagnosisApi.ActionStatus) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click="handleViewDetail(record.loopId)"
            >
              详情
            </Button>
            <Button
              type="link"
              size="small"
              @click="handleQuickDiagnose(record.loopId)"
            >
              诊断
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>
  </Page>
</template>
