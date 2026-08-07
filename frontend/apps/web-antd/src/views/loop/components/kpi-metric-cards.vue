<script lang="ts" setup>
/**
 * 工作台 · 8 大指标卡片网格（单页四区重构 · 2026-08-07）
 *
 * 替代 assessment-tab 的 Descriptions + Table，以卡片网格形式展示 8 大 KPI，
 * 紧凑 4×2 布局适配评估行 28% 高度。
 *
 * 8 大指标（3+1+4 选型，覆盖"评分+3核心+1综合+3关键辅助"）：
 *   综合评分 / 准确率 / 快速率 / 平稳率 / 有效自控率 / 好值率 / 自控率 / 振荡率
 *
 * 颜色口径（UI/UX v6.1 Glanceability）：
 *   正向指标（值越大越好）：≥80 绿 / ≥60 黄 / <60 红
 *   反向指标（振荡率，值越小越好）：<20 绿 / <40 黄 / ≥40 红
 *   空值：灰
 *
 * 数据来源：父级 provide 的 assessmentDetail（LoopConfidenceLatestItem）
 */
import type { LoopConfidenceLatestItem } from '#/api/metric';

import { computed, inject, type Ref } from 'vue';

import { Empty, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'KpiMetricCards' });

// ===== 8 大指标元数据 =====
interface MetricMeta {
  key: string;
  label: string;
  unit: string;
  /** true=反向指标（值越小越好，如振荡率） */
  invert: boolean;
  /** true=综合评分，强调显示 */
  emphasize?: boolean;
}

const METRIC_META: MetricMeta[] = [
  { key: 'score', label: '综合评分', unit: '', invert: false, emphasize: true },
  { key: 'accuracy_rate', label: '准确率', unit: '%', invert: false },
  { key: 'fast_rate', label: '快速率', unit: '%', invert: false },
  { key: 'steady_rate', label: '平稳率', unit: '%', invert: false },
  { key: 'effective_auto_rate', label: '有效自控率', unit: '%', invert: false },
  { key: 'good_value_rate', label: '好值率', unit: '%', invert: false },
  { key: 'auto_mode_rate', label: '自控率', unit: '%', invert: false },
  { key: 'oscillation_rate', label: '振荡率', unit: '%', invert: true },
];

// ===== 评估数据（父级 workbench.vue provide） =====
const assessmentDetail = inject<Ref<LoopConfidenceLatestItem | null>>(
  'assessmentDetail',
  computed(() => null),
);

const { themeColors } = useClpmTheme();

// ===== 派生：8 大指标取值 =====
interface MetricCard {
  label: string;
  value: null | number;
  unit: string;
  color: string;
  emphasize?: boolean;
}

/** 取指标值：综合评分从 assessmentDetail.score，其余从 metrics[key].value */
function getMetricValue(meta: MetricMeta): null | number {
  if (meta.key === 'score') {
    return assessmentDetail.value?.score ?? null;
  }
  return assessmentDetail.value?.metrics?.[meta.key]?.value ?? null;
}

/** 正向指标颜色 */
function positiveColor(v: number): string {
  if (v >= 80) return themeColors.value.SUCCESS;
  if (v >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 反向指标颜色 */
function negativeColor(v: number): string {
  if (v < 20) return themeColors.value.SUCCESS;
  if (v < 40) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

const metricCards = computed<MetricCard[]>(() =>
  METRIC_META.map((meta) => {
    const v = getMetricValue(meta);
    const color =
      v === null || v === undefined
        ? themeColors.value.NEUTRAL
        : (meta.invert
          ? negativeColor(v)
          : positiveColor(v));
    return {
      label: meta.label,
      value: v,
      unit: meta.unit,
      color,
      emphasize: meta.emphasize,
    };
  }),
);

/** 评估时间 */
const evalTimeText = computed(() => {
  const t = assessmentDetail.value?.evalTime;
  return t ? formatTime(t) : '—';
});

/** 数据范围 */
const dataRangeText = computed(() => {
  const s = assessmentDetail.value?.dataTsStart;
  const e = assessmentDetail.value?.dataTsEnd;
  if (!s && !e) return '';
  const fmt = 'MM-DD HH:mm';
  if (s && e) {
    const ds = dayjs(s);
    const de = dayjs(e);
    return ds.isSame(de, 'day')
      ? `${ds.format(fmt)}~${de.format('HH:mm')}`
      : `${ds.format(fmt)} ~ ${de.format(fmt)}`;
  }
  return dayjs(e || s).format(fmt);
});

/** 可信度等级 Tag */
const confidenceLevel = computed(() => assessmentDetail.value?.confidenceLevel);
const confidenceColor = computed(() => {
  const lv = confidenceLevel.value;
  if (lv === 'A' || lv === 'B') return 'green';
  if (lv === 'C') return 'gold';
  if (lv === 'D' || lv === 'E') return 'red';
  return 'default';
});

const hasData = computed(() => assessmentDetail.value !== null);
</script>

<template>
  <div class="kpi-cards">
    <div v-if="hasData" class="kpi-cards__grid">
      <div
        v-for="card in metricCards"
        :key="card.label"
        class="kpi-card"
        :class="{ 'kpi-card--emphasis': card.emphasize }"
      >
        <div class="kpi-card__label">{{ card.label }}</div>
        <div class="kpi-card__value" :style="{ color: card.color }">
          <template v-if="card.value === null || card.value === undefined">
            —
          </template>
          <template v-else>
            {{ Number(card.value).toFixed(card.emphasize ? 1 : 2)
            }}<span class="kpi-card__unit">{{ card.unit }}</span>
          </template>
        </div>
      </div>
    </div>

    <!-- 评估元信息（时间 + 数据范围 + 可信度） -->
    <div v-if="hasData" class="kpi-cards__meta">
      <span class="kpi-cards__meta-item">
        评估时间：<span class="font-medium">{{ evalTimeText }}</span>
      </span>
      <span v-if="dataRangeText" class="kpi-cards__meta-item">
        数据范围：{{ dataRangeText }}
      </span>
      <span v-if="confidenceLevel" class="kpi-cards__meta-item">
        可信度：
        <Tag :color="confidenceColor" class="ml-1">{{ confidenceLevel }}</Tag>
      </span>
    </div>

    <Empty
      v-if="!hasData"
      description="暂无评估数据"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      class="kpi-cards__empty"
    />
  </div>
</template>

<style scoped>
.kpi-cards {
  display: flex;
  flex-direction: column;
  gap: 6px;
  height: 100%;
  min-height: 0;
}

.kpi-cards__grid {
  display: grid;
  flex: 1;
  grid-template-rows: repeat(2, minmax(0, 1fr));
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  min-height: 0;
}

.kpi-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 6px 10px;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 50%);
  border-radius: 4px;
}

.kpi-card--emphasis {
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 30%);
}

.kpi-card__label {
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.kpi-card__value {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}

.kpi-card--emphasis .kpi-card__value {
  font-size: 22px;
}

.kpi-card__unit {
  margin-left: 2px;
  font-size: 12px;
  font-weight: 400;
}

.kpi-cards__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 12px;
  color: hsl(var(--foreground) / 55%);
}

.kpi-cards__meta-item {
  white-space: nowrap;
}

.kpi-cards__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  margin: 0;
}
</style>
