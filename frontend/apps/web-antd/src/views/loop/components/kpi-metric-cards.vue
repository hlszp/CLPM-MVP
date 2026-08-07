<script lang="ts" setup>
/**
 * 工作台 · 12 大指标卡片网格（单页四区重构 v2 · 2026-08-07）
 *
 * 12 卡片 = 1 综合评分 + 3 核心 + 8 辅助（3+1+8 体系，覆盖 GB/T 44693.2-2024）：
 *   综合评分 / 准确率 / 快速率 / 平稳率 / 有效自控率 / 好值率 /
 *   自控率 / 饱和率 / 振荡率 / 仪表故障率 / 稳定时间 / 粘滞指数
 *
 * 紧凑 6×2 布局适配评估行左半区（50% 宽）。
 *
 * 颜色口径（UI/UX v6.1 Glanceability）：
 *   正向指标（值越大越好）：≥80 绿 / ≥60 黄 / <60 红
 *   反向指标（振荡率/饱和率/仪表故障率，值越小越好）：<20 绿 / <40 黄 / ≥40 红
 *   信息指标（稳定时间/粘滞指数）：中性色，不做阈值染色
 *   空值：灰
 *
 * 数据来源：父级 provide 的 assessmentDetail（LoopConfidenceLatestItem）
 *   - 综合评分取 assessmentDetail.score
 *   - 其余取 assessmentDetail.metrics[dbCode].value（dbCode 为 snake_case DB 列名）
 */
import type { LoopConfidenceLatestItem } from '#/api/metric';

import { computed, inject, type Ref } from 'vue';

import { Empty, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'KpiMetricCards' });

// ===== 12 大指标元数据 =====
type MetricKind = 'info' | 'negative' | 'positive';

interface MetricMeta {
  key: string;
  label: string;
  unit: string;
  kind: MetricKind;
  /** true=综合评分，强调显示 */
  emphasize?: boolean;
}

const METRIC_META: MetricMeta[] = [
  { key: 'score', label: '综合评分', unit: '', kind: 'info', emphasize: true },
  { key: 'accuracy_rate', label: '准确率', unit: '%', kind: 'positive' },
  { key: 'fast_rate', label: '快速率', unit: '%', kind: 'positive' },
  { key: 'steady_rate', label: '平稳率', unit: '%', kind: 'positive' },
  {
    key: 'effective_auto_rate',
    label: '有效自控率',
    unit: '%',
    kind: 'positive',
  },
  { key: 'good_value_rate', label: '好值率', unit: '%', kind: 'positive' },
  { key: 'auto_mode_rate', label: '自控率', unit: '%', kind: 'positive' },
  { key: 'saturation_rate', label: '饱和率', unit: '%', kind: 'negative' },
  { key: 'oscillation_rate', label: '振荡率', unit: '%', kind: 'negative' },
  {
    key: 'instrument_fault_rate',
    label: '仪表故障率',
    unit: '%',
    kind: 'negative',
  },
  { key: 'settling_time', label: '稳定时间', unit: 's', kind: 'info' },
  { key: 'stiction_index', label: '粘滞指数', unit: '', kind: 'info' },
];

// ===== 评估数据（父级 workbench.vue provide） =====
const assessmentDetail = inject<Ref<LoopConfidenceLatestItem | null>>(
  'assessmentDetail',
  computed(() => null),
);

const { themeColors } = useClpmTheme();

// ===== 派生：12 大指标取值 =====
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
    let color: string;
    if (v === null || v === undefined) {
      color = themeColors.value.NEUTRAL;
    } else if (meta.kind === 'positive') {
      color = positiveColor(v);
    } else if (meta.kind === 'negative') {
      color = negativeColor(v);
    } else {
      color = themeColors.value.INFO;
    }
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

/**
 * 回路性能等级（由综合评分按 GB/T 44693.2 默认阈值推导）。
 * ≥90 优秀 / ≥75 良好 / ≥60 合格 / ≥45 警告 / <45 不合格 / null 待评估
 */
const loopGrade = computed<null | { color: string; label: string }>(() => {
  const s = assessmentDetail.value?.score;
  if (s === null || s === undefined) return null;
  if (s >= 90) return { color: 'green', label: '优秀' };
  if (s >= 75) return { color: 'blue', label: '良好' };
  if (s >= 60) return { color: 'gold', label: '合格' };
  if (s >= 45) return { color: 'orange', label: '警告' };
  return { color: 'red', label: '不合格' };
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

    <!-- 评估元信息（等级 + 时间 + 数据范围 + 可信度） -->
    <div v-if="hasData" class="kpi-cards__meta">
      <span v-if="loopGrade" class="kpi-cards__meta-item">
        回路等级：
        <Tag :color="loopGrade.color" class="ml-1">{{ loopGrade.label }}</Tag>
      </span>
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
  gap: 4px;
  height: 100%;
  min-height: 0;
}

.kpi-cards__grid {
  display: grid;
  flex: 1;
  grid-template-rows: repeat(2, minmax(0, 1fr));
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 4px;
  min-height: 0;
}

.kpi-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 4px 8px;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 50%);
  border-radius: 4px;
}

.kpi-card--emphasis {
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 30%);
}

.kpi-card__label {
  font-size: 11px;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.kpi-card__value {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}

.kpi-card--emphasis .kpi-card__value {
  font-size: 19px;
}

.kpi-card__unit {
  margin-left: 2px;
  font-size: 11px;
  font-weight: 400;
}

.kpi-cards__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 11px;
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
