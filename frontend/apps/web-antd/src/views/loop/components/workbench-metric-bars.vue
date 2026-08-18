<script lang="ts" setup>
/**
 * 工作台 R5 评估证据区 · 指标横道图（Phase 1 重构 · 2026-08-12）
 *
 * 8 项主要指标达成度横向棒图：
 * - 纯 HTML/CSS 实现（不用 ECharts），便于响应式与精细排版
 * - 每行：指标名 | 棒图条 | 数值
 * - 棒图条高度 6px，圆角 2px
 * - 达成度颜色：≥80 绿 #1a7f4b / 60-79 琥珀 #b45309 / <60 红 #c23434
 * - 阈值标线：棒图条上的竖线标记
 * - 负向模式：标签 "▲ 负向指标：条越长越差"
 *
 * 数据来源：父级传入 metrics 列表。
 */
import { computed } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'WorkbenchMetricBars' });

const props = withDefaults(defineProps<Props>(), {
  negative: false,
  showHint: true,
});

interface MetricBarItem {
  name: string;
  value: number; // 0-100
  threshold?: number; // 0-100
  color?: string;
}

interface Props {
  metrics: MetricBarItem[];
  /** 是否负向语义（长=差，用于诊断扩展指标） */
  negative?: boolean;
  /** 是否显示底部正向/负向指标提示行 */
  showHint?: boolean;
}

const { themeColors } = useClpmTheme();

/** 正向达成度颜色 */
function positiveColor(v: number): string {
  if (v >= 80) return '#1a7f4b';
  if (v >= 60) return '#b45309';
  return '#c23434';
}

/** 负向达成度颜色（反转：值越大越差） */
function negativeColor(v: number): string {
  if (v < 20) return '#1a7f4b';
  if (v < 40) return '#b45309';
  return '#c23434';
}

interface Row {
  name: string;
  value: number;
  display: string;
  color: string;
  widthPct: number;
  threshold?: number;
}

const rows = computed<Row[]>(() =>
  props.metrics.map((m) => {
    const raw = m.value;
    const v = typeof raw === 'number' && !Number.isNaN(raw) ? raw : 0;
    const clamped = Math.max(0, Math.min(100, v));
    const color =
      m.color ?? (props.negative ? negativeColor(v) : positiveColor(v));
    return {
      color,
      display: Number.isFinite(v) ? v.toFixed(1) : '—',
      name: m.name,
      threshold: m.threshold,
      value: v,
      widthPct: clamped,
    };
  }),
);

const hasData = computed(() => rows.value.length > 0);

const labelText = computed(() =>
  props.negative ? '▲ 负向指标：条越长越差' : '正向指标：条越长越好',
);

const labelColor = computed(() =>
  props.negative ? themeColors.value.WARNING : themeColors.value.INFO,
);
</script>

<template>
  <div class="metric-bars">
    <div v-if="hasData" class="metric-bars__list">
      <div
        v-for="(row, idx) in rows"
        :key="`${row.name}-${idx}`"
        class="metric-bars__row"
      >
        <div class="metric-bars__name" :title="row.name">
          {{ row.name }}
        </div>
        <div class="metric-bars__track">
          <div
            class="metric-bars__fill"
            :style="{
              width: `${row.widthPct}%`,
              backgroundColor: row.color,
            }"
          ></div>
          <div
            v-if="row.threshold !== undefined && row.threshold !== null"
            class="metric-bars__threshold"
            :style="{ left: `${Math.max(0, Math.min(100, row.threshold))}%` }"
          ></div>
        </div>
        <div class="metric-bars__value" :style="{ color: row.color }">
          {{ row.display }}
        </div>
      </div>
      <div
        v-if="props.showHint"
        class="metric-bars__hint"
        :style="{ color: labelColor }"
      >
        {{ labelText }}
      </div>
    </div>
    <div v-else class="metric-bars__empty">
      <span>暂无指标数据</span>
    </div>
  </div>
</template>

<style scoped>
.metric-bars {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
}

.metric-bars__list {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  justify-content: space-around;
  min-height: 0;
}

.metric-bars__row {
  display: grid;
  grid-template-columns: 88px 1fr 48px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}

.metric-bars__name {
  overflow: hidden;
  text-overflow: ellipsis;
  color: hsl(var(--foreground) / 75%);
  white-space: nowrap;
}

.metric-bars__track {
  position: relative;
  width: 100%;
  height: 6px;
  background: hsl(var(--muted) / 50%);
  border-radius: 2px;
}

.metric-bars__fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.2s ease;
}

.metric-bars__threshold {
  position: absolute;
  top: -2px;
  width: 2px;
  height: 10px;
  background: hsl(var(--foreground) / 60%);
  border-radius: 1px;
}

.metric-bars__value {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.metric-bars__hint {
  margin-top: 4px;
  font-size: 11px;
  text-align: right;
  opacity: 0.85;
}

.metric-bars__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
}
</style>
