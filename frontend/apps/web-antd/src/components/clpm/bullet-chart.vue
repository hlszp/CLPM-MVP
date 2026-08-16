<script lang="ts" setup>
/**
 * ClpmBulletChart — 子弹图（Stephen Few bullet graph，整改 A-06）
 *
 * 替代单值仪表盘（gauge）：值条 + 定性区间带 + 可选目标刻线。
 * 设计纪律（色彩约定表 §5）：
 * - 区间带用灰阶（slate），不铺彩色；
 * - 值条达标时用深灰中性色，仅"需关注/低效"才着色（深琥珀/红）；
 * - 无数据显示 "—"，不渲染假值。
 *
 * 用法：
 * ```vue
 * <ClpmBulletChart label="自控率" :value="82.5" :target="90" meta="统计窗口：24小时" />
 * <ClpmBulletChart label="仪表故障率" :value="4.9" :max="30" :fair="5" :good="10" invert />
 * ```
 */
import { computed } from 'vue';

defineOptions({ name: 'ClpmBulletChart' });

const props = withDefaults(defineProps<Props>(), {
  unit: '%',
  max: 100,
  fair: 60,
  good: 80,
  invert: false,
  meta: '',
  target: undefined,
  delta: null,
  compact: false,
  color: undefined,
});

interface Props {
  /** 指标名 */
  label: string;
  /** 当前值；null 显示 "—" */
  value: null | number;
  /** 单位 */
  unit?: string;
  /** 量程上限 */
  max?: number;
  /** 分档下界（越高越好：低于 fair = 低效；invert：高于 good = 低效） */
  fair?: number;
  /** 分档上界（越高越好：≥good 为优良；invert：fair~good 为需关注） */
  good?: number;
  /** 是否"越低越好"（如故障率） */
  invert?: boolean;
  /** 角标说明（如统计窗口） */
  meta?: string;
  /** 目标值（显示为目标刻线） */
  target?: number;
  /** 环比差值（较上一统计窗口）：正=↑绿 / 负=↓红 / 持平=→灰；null 不显示 */
  delta?: null | number;
  /** 紧凑模式（窄卡片区用）：压缩字号/间距，标签数值单行不折行 */
  compact?: boolean;
  /** 主题色（值条+数值颜色）；不传则按 level 自动着色 */
  color?: string;
}

const hasValue = computed(
  () => typeof props.value === 'number' && !Number.isNaN(props.value),
);

/** 当前档位：ok / warning / danger */
const level = computed<'danger' | 'ok' | 'warning'>(() => {
  if (!hasValue.value) return 'ok';
  const v = props.value!;
  if (props.invert) {
    if (v > props.good) return 'danger';
    if (v > props.fair) return 'warning';
    return 'ok';
  }
  if (v < props.fair) return 'danger';
  if (v < props.good) return 'warning';
  return 'ok';
});

const barColor = computed(() => {
  if (props.compact && props.color) return props.color;
  if (props.compact) return 'hsl(216 91% 42%)';
  if (level.value === 'danger') return 'var(--status-error)';
  if (level.value === 'warning') return 'var(--status-warning)';
  return 'var(--color-slate-700)';
});

const valueColor = computed(() => {
  if (props.compact && props.color) return props.color;
  if (props.compact) return 'hsl(216 91% 42%)';
  if (level.value === 'danger') return 'var(--status-error)';
  if (level.value === 'warning') return 'var(--status-warning)';
  return 'hsl(var(--foreground))';
});

function toPct(v: number): number {
  return Math.min(100, Math.max(0, (v / props.max) * 100));
}

const barPct = computed(() => (hasValue.value ? toPct(props.value!) : 0));

/** 三段定性区间（灰阶：差→好 由深到浅；compact 模式更浅以突出值条；若传 color 则用同色系淡阶） */
const zones = computed(() => {
  const fairPct = toPct(props.fair);
  const goodPct = toPct(props.good);
  if (props.compact) {
    const c = props.color;
    if (c) {
      return [
        { color: `color-mix(in srgb, ${c} 22%, white)`, width: fairPct },
        { color: `color-mix(in srgb, ${c} 13%, white)`, width: goodPct - fairPct },
        { color: `color-mix(in srgb, ${c} 6%, white)`, width: 100 - goodPct },
      ];
    }
    return [
      { color: 'hsl(216 16% 88%)', width: fairPct },
      { color: 'hsl(216 16% 93%)', width: goodPct - fairPct },
      { color: 'hsl(216 16% 97%)', width: 100 - goodPct },
    ];
  }
  return [
    { color: 'var(--color-slate-200)', width: fairPct },
    { color: 'var(--color-slate-100)', width: goodPct - fairPct },
    { color: 'var(--color-slate-50)', width: 100 - goodPct },
  ];
});

const targetPct = computed(() =>
  props.target === undefined ? null : toPct(props.target),
);

const valueText = computed(() => {
  if (!hasValue.value) return '—';
  const v = props.value!;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
});

/** 环比角标（方向箭头 + 绝对值；|delta|<0.05 视为持平）
 *  拆分为 arrow/val，支持"指标名称→指标值→环比值→环比箭头"顺序 */
const deltaInfo = computed(() => {
  const d = props.delta;
  if (d === null || d === undefined || Number.isNaN(d)) return null;
  const abs = Math.abs(d);
  if (abs < 0.05) {
    return { cls: 'clpm-bullet__delta--flat', arrow: '→', val: '0.0', tip: '环比持平' };
  }
  return d > 0
    ? {
        cls: 'clpm-bullet__delta--up',
        arrow: '↑',
        val: abs.toFixed(1),
        tip: `环比 +${abs.toFixed(1)}`,
      }
    : {
        cls: 'clpm-bullet__delta--down',
        arrow: '↓',
        val: abs.toFixed(1),
        tip: `环比 -${abs.toFixed(1)}`,
      };
});
</script>

<template>
  <div class="clpm-bullet" :class="{ 'clpm-bullet--compact': compact }">
    <div class="clpm-bullet__head">
      <span class="clpm-bullet__label">{{ label }}</span>
      <span class="clpm-bullet__value-row">
        <span class="clpm-bullet__value" :style="{ color: valueColor }">
          {{ valueText
          }}<span v-if="hasValue && unit" class="clpm-bullet__unit">{{
            unit
          }}</span>
        </span>
        <span
          v-if="deltaInfo"
          class="clpm-bullet__delta"
          :class="deltaInfo.cls"
          :title="deltaInfo.tip"
        >
          <span class="clpm-bullet__delta-val">{{ deltaInfo.val }}</span>
          <span class="clpm-bullet__delta-arrow">{{ deltaInfo.arrow }}</span>
        </span>
      </span>
    </div>
    <div class="clpm-bullet__track">
      <div class="clpm-bullet__zones">
        <div
          v-for="(zone, i) in zones"
          :key="i"
          class="clpm-bullet__zone"
          :style="{ background: zone.color, width: `${zone.width}%` }"
        ></div>
      </div>
      <div
        v-if="hasValue"
        class="clpm-bullet__bar"
        :style="{ background: barColor, width: `${barPct}%` }"
      ></div>
      <div
        v-if="targetPct !== null"
        class="clpm-bullet__target"
        :style="{ left: `${targetPct}%` }"
        title="目标值"
      ></div>
    </div>
    <div v-if="meta && !compact" class="clpm-bullet__meta">{{ meta }}</div>
  </div>
</template>

<style scoped>
.clpm-bullet {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.clpm-bullet__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.clpm-bullet__value-row {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
  min-width: 0;
}

.clpm-bullet__label {
  font-size: 13px;
  color: hsl(var(--muted-foreground));
  flex-shrink: 0;
}

.clpm-bullet__delta {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 15px;
  border-radius: 3px;
  white-space: nowrap;
  flex-shrink: 0;
}

.clpm-bullet__delta-val {
  font-variant-numeric: tabular-nums;
}

.clpm-bullet__delta-arrow {
  font-weight: 700;
  line-height: 1;
}

.clpm-bullet__delta--up {
  color: var(--status-ok);
  background: color-mix(in srgb, var(--status-ok) 12%, transparent);
}

.clpm-bullet__delta--down {
  color: var(--status-error);
  background: color-mix(in srgb, var(--status-error) 12%, transparent);
}

.clpm-bullet__delta--flat {
  color: hsl(var(--muted-foreground));
  background: hsl(var(--accent));
}

.clpm-bullet__value {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.clpm-bullet__unit {
  margin-left: 2px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

.clpm-bullet__track {
  position: relative;
  height: 14px;
  overflow: hidden;
  border-radius: 2px;
}

.clpm-bullet__zones {
  position: absolute;
  inset: 0;
  display: flex;
}

.clpm-bullet__zone {
  height: 100%;
}

.clpm-bullet__bar {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 0;
  border-radius: 1px;
  transition: width 200ms ease-out;
}

.clpm-bullet__target {
  position: absolute;
  top: -1px;
  bottom: -1px;
  width: 2px;
  background: var(--color-slate-900);
}

.clpm-bullet__meta {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* ══════ 紧凑模式（窄卡片区：行2 仪表带）══════
 * 三行布局：① 指标名称 ② 指标值 + 环比值/箭头 ③ 横向进度条（蓝条+灰底）
 * 隐藏 meta（统计窗口说明），值/条统一使用工业蓝 */
.clpm-bullet--compact {
  gap: 4px;
}

.clpm-bullet--compact .clpm-bullet__head {
  flex-direction: column;
  gap: 2px;
  align-items: flex-start;
}

.clpm-bullet--compact .clpm-bullet__label {
  font-size: 11px;
  line-height: 1.2;
  white-space: nowrap;
  color: hsl(var(--muted-foreground));
}

.clpm-bullet--compact .clpm-bullet__value-row {
  display: flex;
  width: 100%;
  gap: 6px;
  align-items: baseline;
  justify-content: space-between;
}

.clpm-bullet--compact .clpm-bullet__value {
  font-size: 16px;
  font-weight: 500;
  line-height: 1.1;
  white-space: nowrap;
}

.clpm-bullet--compact .clpm-bullet__unit {
  font-size: 10px;
  font-weight: 500;
  margin-left: 1px;
}

.clpm-bullet--compact .clpm-bullet__delta {
  padding: 0 4px;
  font-size: 9px;
  line-height: 13px;
  gap: 1px;
  align-self: center;
}

.clpm-bullet--compact .clpm-bullet__delta-arrow {
  font-size: 10px;
}

.clpm-bullet--compact .clpm-bullet__track {
  height: 8px;
  border-radius: 2px;
  overflow: hidden;
}

.clpm-bullet--compact .clpm-bullet__bar {
  top: 0;
  bottom: 0;
  border-radius: 2px;
}
</style>
