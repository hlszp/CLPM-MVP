<script lang="ts" setup>
/**
 * 统计报告-管理总览（/reports/overview，IA 优化 P0）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.5
 * 固定 3×4=12 格 KPI 骨架（S1 填 5 格，S2/S3 槽位留白，禁止 v-if 增减卡片）；
 * 图表区 Segmented 切换（S1 仅健康趋势，S2/S3 待 P3）；
 * TOP 问题回路表；顶部统一时间+装置筛选条；标题旁 ClpmStageIndicator；
 * 底部留空容器（P1 放升级引导）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { RangePicker, Segmented, Table, Tag, TreeSelect } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  getReportOverviewApi,
  type ReportsApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmPageToolbar,
  ClpmStageIndicator,
  ClpmToolbarButton,
  ClpmUpgradePrompt,
} from '#/components/clpm';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'ReportsOverview' });

// ===== 固定 12 格骨架（S1~S3 槽位，禁止动态增减）=====
interface KpiSlot {
  key: string;
  label: string;
  unit: string;
  stage: 'S1' | 'S2' | 'S3';
  icon: string;
}
const KPI_SLOTS: KpiSlot[] = [
  { key: 'totalLoops', label: '回路总数', unit: '个', stage: 'S1', icon: 'lucide:network' },
  { key: 'healthRate', label: '健康率', unit: '%', stage: 'S1', icon: 'lucide:heart-pulse' },
  { key: 'evaluationRate', label: '参评率', unit: '%', stage: 'S1', icon: 'lucide:clipboard-check' },
  { key: 'anomalyCount', label: '异常数', unit: '个', stage: 'S1', icon: 'lucide:alert-triangle' },
  { key: 'dataHealthRate', label: '数据健康率', unit: '%', stage: 'S1', icon: 'lucide:database-check' },
  { key: 'closedLoopRate', label: '闭环率', unit: '%', stage: 'S2', icon: 'lucide:refresh-cw' },
  { key: 'avgCycleHours', label: '平均处置时长', unit: 'h', stage: 'S2', icon: 'lucide:timer' },
  { key: 'tuningThisMonth', label: '本月整定', unit: '次', stage: 'S2', icon: 'lucide:sliders-horizontal' },
  { key: 'ineffectiveRate', label: '无效重开率', unit: '%', stage: 'S2', icon: 'lucide:undo-2' },
  { key: 'kpiImprovement', label: 'KPI 改善', unit: '分', stage: 'S3', icon: 'lucide:trending-up' },
  { key: 'autoRateImprovement', label: '自控提升', unit: 'pp', stage: 'S3', icon: 'lucide:gauge' },
  { key: 'benchmarkGap', label: '标杆差', unit: '分', stage: 'S3', icon: 'lucide:flag' },
];

const loading = ref(false);
const data = ref<null | ReportsApi.OverviewData>(null);

// 筛选条
const dateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(30, 'day'),
  dayjs(),
]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);
const stage = ref<ReportsApi.Stage>('S1');

const chartTab = ref<'benefit' | 'closedLoop' | 'health'>('health');

const { getEchartsBase, getLineSeriesPreset, getSeriesColor, getTooltipPreset } =
  useEchartsPreset();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

// S1 KPI 数据按 key 映射
const kpiMap = computed(() => {
  const m = new Map<string, ReportsApi.OverviewKpi>();
  for (const k of data.value?.kpis ?? []) m.set(k.key, k);
  return m;
});

type KpiStatus = 'error' | 'info' | 'neutral' | 'ok' | 'warning';

interface MergedSlot extends KpiSlot {
  context: string;
  status: KpiStatus;
  value: number | string;
  locked: boolean;
}

const kpiSlots = computed<MergedSlot[]>(() =>
  KPI_SLOTS.map((slot) => {
    const k = kpiMap.value.get(slot.key);
    if (k) {
      return {
        ...slot,
        value: k.value ?? '—',
        status: (k.status as KpiStatus) ?? 'neutral',
        context: k.context ?? '',
        locked: slot.stage !== 'S1',
      };
    }
    return {
      ...slot,
      value: '—',
      status: 'neutral' as KpiStatus,
      context: `${slot.stage} 待开通`,
      locked: slot.stage !== 'S1',
    };
  }),
);

const topColumns = [
  { dataIndex: 'loopTagName', title: '回路' },
  { dataIndex: 'unitPath', title: '装置.单元', width: 180 },
  { dataIndex: 'latestScore', title: '最新评分', width: 100 },
  { dataIndex: 'primaryCategoryLabel', title: '诊断主分类', width: 180 },
  { dataIndex: 'severity', title: '严重度', width: 90 },
];

async function loadPlants() {
  try {
    plantTree.value = await getPlantNodeTreeApi();
  } catch {
    plantTree.value = [];
  }
}

async function load() {
  loading.value = true;
  try {
    const [start, end] = dateRange.value ?? [];
    data.value = await getReportOverviewApi({
      stage: stage.value,
      startDate: start?.format('YYYY-MM-DD'),
      endDate: end?.format('YYYY-MM-DD'),
      plantNodeId: plantNodeId.value,
    });
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
  }
}

function renderChart() {
  const trend = data.value?.healthTrend ?? [];
  if (trend.length === 0) return;
  const option = {
    ...getEchartsBase(),
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
    },
    xAxis: {
      ...getEchartsBase().xAxis,
      data: trend.map((p) => p.date),
    },
    yAxis: {
      ...getEchartsBase().yAxis,
      min: 0,
      max: 100,
    },
    series: [
      {
        name: '平均健康分',
        data: trend.map((p) => p.score),
        ...getLineSeriesPreset(getSeriesColor('info')),
      },
    ],
  };
  renderEcharts(option);
}

function handleHelp() {
  showPageHelp({
    title: '管理总览 帮助',
    content: `
      <p><b>定位</b>：面向管理层的全局健康看板，固定 12 格骨架按成熟度 S1/S2/S3 自适应填充。</p>
      <p><b>S1 基础可视</b>：回路总数、健康率、参评率、异常数、数据健康率 + 健康趋势 + TOP 问题回路。</p>
      <p><b>口径</b>：健康率=评分≥60 回路占比；参评率=窗口内有 KPI 快照回路占比；数据健康率=PV 好值率均值。</p>
    `,
  });
}

watch([data], renderChart, { deep: false });

onMounted(() => {
  loadPlants();
  load();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="全局健康 · 趋势 · TOP 问题回路（按管理成熟度自适应）"
      title="管理总览"
    >
      <template #context>
        <ClpmStageIndicator :stage="stage" size="small" />
      </template>
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 统一筛选条：时间 + 装置 -->
    <div class="reports-filter-bar">
      <span class="reports-filter-bar__label">时间范围</span>
      <RangePicker v-model:value="dateRange" allow-clear />
      <span class="reports-filter-bar__label">装置</span>
      <TreeSelect
        v-model:value="plantNodeId"
        :tree-data="plantTree"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        allow-clear
        placeholder="全部装置"
        style="width: 240px"
        tree-default-expand-all
      />
      <a class="reports-filter-bar__apply" @click="load">查询</a>
      <Segmented
        v-model:value="stage"
        :options="[
          { label: 'S1 基础可视', value: 'S1' },
          { label: 'S2 闭环管理', value: 'S2', disabled: true },
          { label: 'S3 持续优化', value: 'S3', disabled: true },
        ]"
        size="small"
      />
    </div>

    <!-- 固定 3×4=12 格 KPI 骨架（S1 填 5 格，S2/S3 留白，禁止动态增减） -->
    <ClpmDataCanvas
      :loading="loading"
      :skeleton-rows="2"
      class="reports-kpi-canvas"
    >
      <div class="reports-kpi-grid">
        <ClpmKpiCard
          v-for="slot in kpiSlots"
          :key="slot.key"
          :context-text="slot.context"
          :icon="slot.icon"
          :status="slot.status"
          :title="slot.label"
          :unit="slot.unit"
          :value="slot.value"
          :class="{ 'is-locked-slot': slot.locked }"
        />
      </div>
    </ClpmDataCanvas>

    <!-- 图表区：Segmented 切换（S1 仅健康趋势） -->
    <ClpmDataCanvas class="reports-chart-canvas" title="趋势分析">
      <template #extra>
        <Segmented
          v-model:value="chartTab"
          size="small"
          :options="[
            { label: '健康趋势', value: 'health' },
            { label: '闭环趋势', value: 'closedLoop', disabled: true },
            { label: '收益趋势', value: 'benefit', disabled: true },
          ]"
        />
      </template>
      <div class="reports-chart-body">
        <EchartsUI ref="chartRef" height="280px" />
        <div
          v-if="!data?.healthTrend?.length"
          class="reports-chart-empty"
        >
          该时段暂无健康趋势数据
        </div>
      </div>
    </ClpmDataCanvas>

    <!-- TOP 问题回路 -->
    <ClpmDataCanvas
      class="reports-top-canvas"
      title="TOP 问题回路（评分最低）"
      :empty="!data?.topProblemLoops?.length"
      empty-text="暂无问题回路"
    >
      <Table
        :columns="topColumns"
        :data-source="data?.topProblemLoops ?? []"
        :pagination="false"
        row-key="loopId"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'severity'">
            <Tag v-if="record.severity" :color="record.severity === 'HIGH' ? 'red' : record.severity === 'MEDIUM' ? 'orange' : 'default'">
              {{ record.severity === 'HIGH' ? '高' : record.severity === 'MEDIUM' ? '中' : '低' }}
            </Tag>
            <span v-else class="text-neutral-400">—</span>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 底部升级引导（虚线边框 + lock 图标 + 灰字，仅管理总览出现） -->
    <ClpmUpgradePrompt
      stage="S2"
      title="升级到闭环管理阶段"
      description="启用诊断与处置模块后，此处将展示闭环率、处置时效、异常分布等 S2 管理指标。"
    />
  </Page>
</template>

<style scoped>
.reports-filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  margin: 8px 0;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.reports-filter-bar__label {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.reports-filter-bar__apply {
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
}

.reports-kpi-canvas {
  margin-bottom: 12px;
}

.reports-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.reports-kpi-grid :deep(.is-locked-slot) {
  opacity: 0.55;
}

.reports-chart-canvas {
  margin-bottom: 12px;
}

.reports-chart-body {
  position: relative;
  min-height: 280px;
}

.reports-chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  pointer-events: none;
}

.reports-top-canvas {
  margin-bottom: 12px;
}

.reports-upgrade-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 56px;
  font-size: 12px;
  border: 1px dashed hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}
</style>
