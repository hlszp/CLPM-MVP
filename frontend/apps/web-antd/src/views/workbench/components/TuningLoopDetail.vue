<script setup lang="ts">
/**
 * 单回路详情卡 · V3.2 真实 24h 趋势 + 评分快照条
 *
 * 数据流：
 *   row.loop_id → getVerificationDataApi({loopId, pointTime: now, windowHours: 24})
 *   before 窗口 = now-24h ~ now → ECharts PV/SP/OP 折线
 *   kpiBefore = KpiSummary → 底部 6 项快照条（评分 + 五率）
 *
 * 0 后端改动：复用既有 /tuning/verification/data 端点
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getVerificationDataApi } from '#/api/tuning';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

import { useWorkbenchDrill } from '../utils/drill';
import HelpBubble from './HelpBubble.vue';

interface Props { row: null | WorkbenchApi.TuneQueueItem; }
const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'openWorkbench', row: WorkbenchApi.TuneQueueItem): void;
}>();

const { drill } = useWorkbenchDrill();

/** 追溯矩阵 §5 下钻：详情区"查看整定记录" → 整定记录页（loopId 口径） */
function toRecords() {
  if (!props.row?.loop_id) return;
  drill('tuning', '/tuning/records', { loopId: props.row.loop_id });
}

const trendHelpItems = [
  { label: '数据源', text: '调 /tuning/verification/data 接口，pointTime=当前时刻，windowHours=24，取 before 窗口（now-24h~now）的 PV/SP/OP 序列。' },
  { label: 'PV/SP/OP', text: 'PV 过程值（蓝实线）/ SP 设定值（灰虚线）/ OP 操作量（棕实线）。' },
  { label: '评分快照', text: '底部 6 卡取自 kpiBefore：评分 + 完好率 / 有效自控 / 平稳率 / 精确率 / 快速率，tsStart~tsEnd 为快照时间窗。' },
];

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const PV_COLOR = '#1d4ed8';
const SP_COLOR = '#6b7280';
const OP_COLOR = '#b45309';

const loading = ref(false);
const loadError = ref('');
const data = ref<null | TuningApi.VerificationData>(null);

const chartRef = ref<EchartsUIType>();
const { renderEcharts, resize } = useEcharts(chartRef);

const loopNo = computed(() => props.row?.loop_name ?? props.row?.loop_id ?? '—');
const scoreTxt = computed(() => {
  const s = props.row?.score;
  return s === null || s === undefined ? '—' : s.toFixed(1);
});
const scoreColor = computed(() => {
  const s = props.row?.score;
  if (s === null || s === undefined) return '#8C8C8C';
  if (s < 65) return '#FF4D4F';
  if (s < 73) return '#FA8C16';
  return '#52C41A';
});
const fitTxt = computed(() => {
  const s = props.row?.fitting_score;
  return s === null || s === undefined ? '—' : s.toFixed(1);
});

function fmtTs(ts: null | string | undefined): string {
  if (!ts) return '—';
  return dayjs(ts).format('MM-DD HH:mm');
}

async function load() {
  if (!props.row?.loop_id) {
    data.value = null;
    return;
  }
  loading.value = true;
  loadError.value = '';
  try {
    const pointTime = new Date().toISOString();
    data.value = await getVerificationDataApi({
      loopId: props.row.loop_id,
      pointTime,
      windowHours: 24,
    });
    renderTrend();
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '趋势数据加载失败';
    data.value = null;
  } finally {
    loading.value = false;
  }
}

watch(() => props.row?.loop_id, load, { immediate: true });

function renderTrend() {
  const wf = data.value?.before;
  if (!wf || wf.timestamps.length === 0) {
    renderEcharts({});
    return;
  }
  const xData = wf.timestamps.map((ts) => fmtTs(ts));
  renderEcharts({
    backgroundColor: 'transparent',
    grid: { bottom: 28, left: 44, right: 14, top: 8 },
    tooltip: {
      trigger: 'axis',
      ...getTooltipPreset(),
    },
    legend: {
      data: ['PV', 'SP', 'OP'],
      textStyle: { color: chartTextColor.value, fontSize: 10 },
      top: 0,
      right: 8,
      itemWidth: 10,
      itemHeight: 6,
    },
    xAxis: {
      type: 'category',
      data: xData,
      boundaryGap: false,
      axisLabel: {
        color: chartTextColor.value,
        fontSize: 9,
        formatter: (val: string, idx: number) => (idx % 12 === 0 ? val : ''),
      },
      axisLine: { lineStyle: { color: chartSplitLineColor.value } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: chartTextColor.value, fontSize: 9 },
      splitLine: { lineStyle: { color: chartSplitLineColor.value, type: 'dashed' } },
    },
    series: [
      {
        name: 'PV', type: 'line', data: wf.pv, smooth: false, symbol: 'none',
        lineStyle: { color: PV_COLOR, width: 1.6 },
        z: 3,
      },
      {
        name: 'SP', type: 'line', data: wf.sp, smooth: false, symbol: 'none',
        lineStyle: { color: SP_COLOR, width: 1, type: 'dashed' },
        z: 2,
      },
      {
        name: 'OP', type: 'line', data: wf.op, smooth: false, symbol: 'none',
        lineStyle: { color: OP_COLOR, width: 1.4 },
        z: 1,
      },
    ],
  });
}

defineExpose({ resize });

type KpiKey = 'accuracyRate' | 'effectiveAutoRate' | 'fastRate' | 'goodValueRate' | 'score' | 'steadyRate';
const KPI_KEYS: { key: KpiKey; label: string; percent?: boolean }[] = [
  { key: 'score',               label: '评分' },
  { key: 'goodValueRate',       label: '完好率', percent: true },
  { key: 'effectiveAutoRate',   label: '有效自控', percent: true },
  { key: 'steadyRate',          label: '平稳率', percent: true },
  { key: 'accuracyRate',        label: '精确率', percent: true },
  { key: 'fastRate',            label: '快速率', percent: true },
];

const kpi = computed(() => data.value?.kpiBefore ?? null);
const hasKpi = computed(() => !!kpi.value);

function fmtKpi(v: null | number | string | undefined, percent?: boolean): string {
  if (typeof v !== 'number') return '—';
  return percent ? `${v.toFixed(1)}%` : v.toFixed(1);
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-[3px]">

    <Empty
      v-if="!row"
      class="m-auto"
      description="请在左侧清单选择一条回路"
    />

    <template v-else>
      <!-- 顶栏 5 元素 -->
      <div class="flex flex-none items-center gap-[6px] overflow-hidden p-[2px_0]">
        <span class="overflow-hidden text-[11.5px] font-bold text-[#1F4E79] truncate">
          📌 {{ loopNo }}（{{ row.unit_name ?? '—' }}）
        </span>
        <span
          class="flex-none rounded-[2px] px-[5px] text-[9.5px] font-bold"
          :style="{ background: `${scoreColor}22`, color: scoreColor }"
        >{{ scoreTxt }} 分</span>
        <span class="flex-none rounded-[2px] bg-[#F6FFED] px-[5px] text-[9.5px] font-semibold text-[#389E0D]">适配 {{ fitTxt }}</span>
        <span class="overflow-hidden text-[9.5px] text-[#8C8C8C] truncate">
          {{ row.algorithm ?? '—' }} · 来源：{{ row.source }}
        </span>
        <a
          class="ml-auto flex-none cursor-pointer text-[10px] text-[#1F4E79] hover:underline"
          title="查看该回路的整定记录"
          @click="toRecords"
        >查看整定记录 →</a>
        <button
          class="flex-none rounded-[2px] bg-[#52C41A] px-[12px] py-[3px] text-[10.5px] font-semibold text-white shadow-[0_1px_0_#389E0D]"
          title="整定仿真配置弹窗"
          @click="emit('openWorkbench', row)"
        >▶ 整定仿真</button>
      </div>

      <!-- 主趋势图（flex:1 吃满剩余高度） -->
      <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#1F4E7933] bg-white">
        <div class="flex h-[20px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10px] font-semibold text-[#1F4E79]">
          <span class="mr-[5px] inline-block h-[9px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
          最近 24h 趋势 · {{ loopNo }}
          <HelpBubble :size="12" theme="blue" title="24h 趋势 + 评分快照说明" :items="trendHelpItems" class="ml-1" />
          <span class="ml-auto text-[9px] font-normal text-[#8C8C8C]">PV 蓝 / SP 灰虚 / OP 棕</span>
        </div>
        <div class="relative min-h-0 flex-1">
          <Spin :spinning="loading" size="small" class="h-full">
            <div
              v-if="loadError"
              class="flex h-full items-center justify-center px-2 text-center text-[10px] text-[#FF4D4F]"
            >
              {{ loadError }}
            </div>
            <EchartsUI v-else ref="chartRef" class="h-full w-full" />
          </Spin>
        </div>
      </div>

      <!-- 底部 24h 评分快照条（flex-none 52px） -->
      <div class="flex h-[52px] flex-none gap-[3px]">
        <template v-if="hasKpi && kpi">
          <div
            v-for="item in KPI_KEYS"
            :key="item.key"
            class="flex flex-1 flex-col justify-between rounded-[2px] border border-[#E4E7ED] bg-[#FAFBFC] p-[3px_5px]"
          >
            <span class="text-[9px] text-[#8C8C8C]">{{ item.label }}</span>
            <span
              class="text-[13px] font-bold leading-none tabular-nums"
              :style="{ color: scoreColor }"
            >
              {{ fmtKpi(kpi[item.key], item.percent) }}
            </span>
            <span class="truncate text-[8.5px] text-[#BFBFBF]" :title="`${fmtTs(kpi.tsStart)} ~ ${fmtTs(kpi.tsEnd)}`">
              {{ fmtTs(kpi.tsStart) }}~
            </span>
          </div>
        </template>
        <div
          v-else
          class="flex flex-1 items-center justify-center rounded-[2px] border border-dashed border-[#E4E7ED] text-[10px] text-[#8C8C8C]"
        >
          暂无 24h 评分快照
        </div>
      </div>
    </template>
  </div>
</template>
