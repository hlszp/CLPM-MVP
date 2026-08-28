<script lang="ts" setup>
/**
 * 驾驶舱总览 §3 绩效发展趋势（方案 11 §5.2）
 *
 * 数据：getBoardTrendApi（/dashboard/board/trend，恒全厂口径）。
 * 窗口映射：24h→last_24_hours（小时粒度）/ 7d→last_7_days / 30d→last_30_days。
 *
 * 设计为「综合评分+自动投用率双曲线 + 五色等级分布堆叠面积」；
 * 因 BoardTrendResult 无等级分布序列，按 C3 降级约定仅呈现双折线，
 * 并在标题栏注明（不造数）。悬浮 tooltip，无点击行为。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DashboardApi } from '#/api/dashboard';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import dayjs from 'dayjs';

import { getBoardTrendApi } from '#/api/dashboard';
import { useCockpitStore } from '#/store/cockpit';
import { normalizeUtcTimestamp } from '#/utils/format';

import { useCockpitTheme } from '../composables/use-cockpit-theme';
import { WINDOW_MAP } from '../utils/format';

const cockpitStore = useCockpitStore();
const { chartColors, gradeColors, isLight } = useCockpitTheme();

const loading = ref(true);
const trend = ref<DashboardApi.BoardTrendResult | null>(null);

async function load() {
  loading.value = true;
  try {
    trend.value = await getBoardTrendApi({
      timeWindow: WINDOW_MAP[cockpitStore.timeWindow],
    });
  } catch {
    trend.value = null;
  } finally {
    loading.value = false;
  }
}

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(
  () => (trend.value?.timestamps?.length ?? 0) > 0,
);

function fmtTick(ts: string): string {
  const d = dayjs(normalizeUtcTimestamp(ts));
  return cockpitStore.timeWindow === '24h'
    ? d.format('HH:mm')
    : d.format('MM-DD');
}

function buildOption() {
  const t = trend.value;
  const cc = chartColors.value;
  if (!t || t.timestamps.length === 0) return {};
  const xData = t.timestamps.map((ts) => fmtTick(ts));
  const scoreColor = isLight.value ? '#2563eb' : '#60a5fa';
  const autoColor = gradeColors.value.EXCELLENT;
  return {
    animation: false,
    grid: { bottom: 24, left: 12, right: 14, top: 32, containLabel: true },
    legend: {
      icon: 'roundRect',
      itemHeight: 8,
      itemWidth: 12,
      textStyle: { color: cc.text, fontSize: 11 },
      top: 2,
    },
    series: [
      {
        data: t.avgScore,
        itemStyle: { color: scoreColor },
        lineStyle: { color: scoreColor, width: 2 },
        name: '综合评分',
        showSymbol: false,
        symbol: 'circle',
        type: 'line' as const,
      },
      {
        data: t.autoModeRate,
        itemStyle: { color: autoColor },
        lineStyle: { color: autoColor, width: 1.5 },
        name: '自动投用率',
        showSymbol: false,
        symbol: 'circle',
        type: 'line' as const,
      },
    ],
    textStyle: { color: cc.textStrong },
    tooltip: {
      backgroundColor: isLight.value ? '#ffffff' : '#13203a',
      borderColor: cc.splitLine,
      borderWidth: 1,
      textStyle: { color: cc.textStrong, fontSize: 12 },
      trigger: 'axis' as const,
      valueFormatter: (v: unknown) =>
        typeof v === 'number' ? v.toFixed(1) : '—',
    },
    xAxis: {
      axisLabel: { color: cc.text, fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: cc.splitLine } },
      boundaryGap: false,
      data: xData,
      type: 'category' as const,
    },
    yAxis: {
      axisLabel: { color: cc.text, fontSize: 10 },
      max: 100,
      min: 0,
      splitLine: {
        lineStyle: { color: cc.splitLine, opacity: 0.6, type: 'dashed' as const },
      },
      type: 'value' as const,
    },
  };
}

function refresh() {
  renderEcharts(buildOption());
}

onMounted(load);

// 时间窗切换 → 重新拉取；仅主题切换 → 重算配色
watch(() => cockpitStore.timeWindow, load);
watch(isLight, refresh);

/** C5 混合刷新：由父级（5min 定时/手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

watch([trend, loading], () => {
  if (!loading.value) refresh();
});
</script>

<template>
  <div class="cockpit-panel trend">
    <div class="cockpit-panel__hd">
      绩效发展趋势
      <span class="sub">
        综合评分 / 自动投用率 · 全厂口径（等级分布序列接口缺失，暂不堆叠）
      </span>
    </div>
    <div class="trend__bd">
      <div v-if="loading" class="trend__state">加载中…</div>
      <div v-else-if="!hasData" class="trend__state">暂无趋势数据</div>
      <div v-show="!loading && hasData" class="trend__chart">
        <EchartsUI ref="chartRef" height="100%" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.trend__bd {
  position: relative;
  flex: 1;
  min-height: 0;
  padding: 4px 8px 8px;
}

.trend__chart {
  width: 100%;
  height: 100%;
}

.trend__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}
</style>
