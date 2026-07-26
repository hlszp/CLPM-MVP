<script lang="ts" setup>
/**
 * 整改有效率卡片（D4-4 管理闭环增强）
 *
 * 对齐 PRD §4.1 工作台门户 + 整改计划 D4 A/B 闭环看板。
 * 卡片自包含：独立拉取 /tracker/effectiveness 聚合统计（近 30 天），展示：
 * - 整改有效率环形进度（核心指标，improvedCount / verifiedCount）
 * - KpiStrip：已实施 / 已验证 / 改善 / 恶化 / 待验证
 * - 每日有效率趋势折线图（近 30 天，echarts）
 *
 * 设计依据：UI/UX v6.1 Calm UI + data-ink ratio；
 * 依赖 D4-1 effect_verified 字段 + D4-2 周期任务回写 + D4-3 统计接口。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { KpiStripItem } from '#/components/clpm';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Skeleton } from 'ant-design-vue';

import { getTrackerEffectivenessApi } from '#/api/diagnosis';
import { ClpmKpiStrip } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'TrackerEffectivenessCard' });

const router = useRouter();
const { isDark, themeColors, chartColors } = useClpmTheme();

const loading = ref(false);
const data = ref<DiagnosisApi.TrackerEffectivenessData | null>(null);

const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

/** 加载整改有效率统计（近 30 天全厂） */
async function load() {
  loading.value = true;
  try {
    data.value = await getTrackerEffectivenessApi({
      timeWindow: 'last_30_days',
    });
    await nextTick();
    renderTrendChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 整改有效率百分比（0~100），无验证数据时为 null */
const effectiveRatePct = computed(() => {
  const rate = data.value?.effectiveRate;
  if (rate === null || rate === undefined) return null;
  return Math.round(rate * 1000) / 10; // 保留 1 位小数
});

/** 环形进度 SVG 周长（r=44, 周长≈276.46） */
const CIRCLE_CIRCUMFERENCE = 2 * Math.PI * 44;
const circleDashoffset = computed(() => {
  const pct = effectiveRatePct.value;
  if (pct === null) return CIRCLE_CIRCUMFERENCE;
  return CIRCLE_CIRCUMFERENCE * (1 - pct / 100);
});

/** 环形进度颜色：≥75% 绿 / 50~75% 黄 / <50% 红 / null 灰 */
const rateColor = computed(() => {
  const pct = effectiveRatePct.value;
  if (pct === null) return themeColors.value.NEUTRAL;
  if (pct >= 75) return themeColors.value.SUCCESS;
  if (pct >= 50) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
});

/** KpiStrip 项：已实施 / 已验证 / 改善 / 恶化 / 待验证 */
const kpiItems = computed<KpiStripItem[]>(() => {
  const d = data.value;
  return [
    {
      key: 'implemented',
      label: '已实施',
      value: d?.totalImplemented ?? 0,
      unit: '条',
      status: 'primary',
    },
    {
      key: 'verified',
      label: '已验证',
      value: d?.verifiedCount ?? 0,
      unit: '条',
      status: 'neutral',
    },
    {
      key: 'improved',
      label: '改善',
      value: d?.improvedCount ?? 0,
      unit: '条',
      status: 'success',
    },
    {
      key: 'deteriorated',
      label: '恶化',
      value: d?.deterioratedCount ?? 0,
      unit: '条',
      status: 'danger',
    },
    {
      key: 'pending',
      label: '待验证',
      value: d?.pendingVerificationCount ?? 0,
      unit: '条',
      status: 'warning',
      clickable: true,
    },
  ];
});

function renderTrendChart() {
  const trend = data.value?.trend ?? [];
  if (trend.length === 0) return;

  const dates = trend.map((t) => t.date.slice(5)); // MM-DD
  const rates = trend.map((t) =>
    t.effectiveRate !== null && t.effectiveRate !== undefined
      ? Math.round(t.effectiveRate * 1000) / 10
      : null,
  );

  renderTrend({
    grid: { bottom: 28, left: '2%', right: '2%', top: 16, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: {
        color: chartColors.value.text,
        fontSize: 10,
        rotate: dates.length > 15 ? 45 : 0,
      },
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: {
        color: chartColors.value.text,
        fontSize: 10,
        formatter: '{value}%',
      },
      splitLine: {
        lineStyle: { color: chartColors.value.splitLine, type: 'dashed' },
      },
    },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = params[0];
        const idx = p.dataIndex;
        const item = trend[idx];
        if (!item) return '';
        const rateText =
          item.effectiveRate !== null && item.effectiveRate !== undefined
            ? `${(item.effectiveRate * 100).toFixed(1)}%`
            : '无数据';
        return `${item.date}<br/>有效率：${rateText}<br/>验证 ${item.verifiedCount} 条 / 改善 ${item.improvedCount} 条`;
      },
    },
    series: [
      {
        name: '整改有效率',
        type: 'line',
        data: rates,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.SUCCESS}30` },
              { offset: 1, color: `${themeColors.value.SUCCESS}05` },
            ],
          },
        },
        connectNulls: true,
      },
    ],
  });
}

function handleKpiClick(item: KpiStripItem) {
  if (item.key === 'pending') {
    router.push({
      path: '/diagnosis/tracker',
      query: { status: 'IMPLEMENTED' },
    });
  }
}

function goTracker() {
  router.push('/diagnosis/tracker');
}

onMounted(load);

watch(isDark, () => {
  nextTick(() => renderTrendChart());
});

defineExpose({ refresh: load });
</script>

<template>
  <div class="effectiveness-card" data-testid="tracker-effectiveness-card">
    <div class="effectiveness-card__header">
      <div class="effectiveness-card__title-group">
        <IconifyIcon
          icon="ant-design:check-circle-outlined"
          class="effectiveness-card__icon"
        />
        <div>
          <div class="effectiveness-card__title">整改有效率</div>
          <div class="effectiveness-card__subtitle">近 30 天 · 全厂聚合</div>
        </div>
      </div>
      <button
        type="button"
        class="effectiveness-card__link"
        data-testid="effectiveness-view-all"
        @click="goTracker"
      >
        查看全部
        <IconifyIcon icon="ant-design:right-outlined" />
      </button>
    </div>

    <Skeleton
      v-if="loading"
      :loading="loading"
      active
      :paragraph="{ rows: 4 }"
    />
    <template v-else>
      <!-- 核心指标：环形进度 + 摘要 -->
      <div class="effectiveness-card__core">
        <div class="effectiveness-card__ring">
          <svg viewBox="0 0 100 100" class="effectiveness-card__ring-svg">
            <circle
              class="effectiveness-card__ring-track"
              cx="50"
              cy="50"
              r="44"
              fill="none"
              :stroke="chartColors.track"
              stroke-width="6"
            />
            <circle
              class="effectiveness-card__ring-fill"
              cx="50"
              cy="50"
              r="44"
              fill="none"
              :stroke="rateColor"
              stroke-width="6"
              stroke-linecap="round"
              :stroke-dasharray="CIRCLE_CIRCUMFERENCE"
              :stroke-dashoffset="circleDashoffset"
              transform="rotate(-90 50 50)"
            />
          </svg>
          <div class="effectiveness-card__ring-text">
            <div
              v-if="effectiveRatePct !== null"
              class="effectiveness-card__ring-value"
              :style="{ color: rateColor }"
            >
              {{ effectiveRatePct }}%
            </div>
            <div v-else class="effectiveness-card__ring-empty">暂无数据</div>
            <div class="effectiveness-card__ring-label">整改有效率</div>
          </div>
        </div>
        <div class="effectiveness-card__summary">
          <div class="effectiveness-card__summary-row">
            <span class="effectiveness-card__summary-label">已验证</span>
            <span class="effectiveness-card__summary-value"
              >{{ data?.verifiedCount ?? 0 }} /
              {{ data?.totalImplemented ?? 0 }}</span
            >
          </div>
          <div class="effectiveness-card__summary-row">
            <span class="effectiveness-card__summary-label">改善</span>
            <span
              class="effectiveness-card__summary-value"
              :style="{ color: themeColors.SUCCESS }"
              >{{ data?.improvedCount ?? 0 }} 条</span
            >
          </div>
          <div class="effectiveness-card__summary-row">
            <span class="effectiveness-card__summary-label">恶化</span>
            <span
              class="effectiveness-card__summary-value"
              :style="{ color: themeColors.DANGER }"
              >{{ data?.deterioratedCount ?? 0 }} 条</span
            >
          </div>
        </div>
      </div>

      <!-- KpiStrip -->
      <ClpmKpiStrip :items="kpiItems" clickable @item-click="handleKpiClick" />

      <!-- 每日有效率趋势 -->
      <div class="effectiveness-card__section">
        <div class="effectiveness-card__section-title">每日有效率趋势</div>
        <div
          v-if="(data?.trend ?? []).length === 0"
          class="effectiveness-card__empty"
        >
          近 30 天无验证数据
        </div>
        <EchartsUI
          v-else
          ref="trendChartRef"
          height="140px"
          data-testid="effectiveness-trend-chart"
        />
      </div>
    </template>
  </div>
</template>

<style lang="scss" scoped>
.effectiveness-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 18px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.effectiveness-card__header {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.effectiveness-card__title-group {
  display: flex;
  gap: 10px;
  align-items: center;
}

.effectiveness-card__icon {
  font-size: 20px;
  color: hsl(var(--primary));
}

.effectiveness-card__title {
  font-size: 15px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.effectiveness-card__subtitle {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.effectiveness-card__link {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  font-size: 13px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
  transition: opacity 0.2s;

  &:hover {
    opacity: 0.8;
  }
}

/* 核心指标：环形进度 + 摘要 */
.effectiveness-card__core {
  display: flex;
  gap: 20px;
  align-items: center;
}

.effectiveness-card__ring {
  position: relative;
  flex-shrink: 0;
  width: 100px;
  height: 100px;
}

.effectiveness-card__ring-svg {
  width: 100%;
  height: 100%;
}

.effectiveness-card__ring-track {
  opacity: 0.3;
}

.effectiveness-card__ring-fill {
  transition: stroke-dashoffset 0.6s ease;
}

.effectiveness-card__ring-text {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  justify-content: center;
}

.effectiveness-card__ring-value {
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
}

.effectiveness-card__ring-empty {
  font-size: 12px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
}

.effectiveness-card__ring-label {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.effectiveness-card__summary {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}

.effectiveness-card__summary-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.effectiveness-card__summary-label {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.effectiveness-card__summary-value {
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.effectiveness-card__section {
  margin-top: 4px;
}

.effectiveness-card__section-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.effectiveness-card__empty {
  padding: 16px 0;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}
</style>
