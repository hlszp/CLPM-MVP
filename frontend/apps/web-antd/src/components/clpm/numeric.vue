<script lang="ts" setup>
/**
 * UI-02 ClpmNumeric 等宽数字组件（v6.1 §7.16.1 / §14 C-03）
 *
 * 强制等宽数字（tabular-nums + --font-mono），防止实时刷新时数字跳动。
 * 覆盖场景：评分、KPI、PV/SP/OP、PID 参数、位号、Tag、版本号、时间戳。
 *
 * 用法：
 * ```vue
 * <ClpmNumeric :value="92.4" :precision="1" unit="%" trend="up" />
 * <ClpmNumeric :value="loopTag" mono />  // 位号
 * <ClpmNumeric empty />  // INCONCLUSIVE
 * ```
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmNumeric' });

const props = withDefaults(defineProps<Props>(), {
  value: null,
  precision: 0,
  unit: '',
  trend: undefined,
  mono: true,
  emptyText: '—',
  empty: false,
  size: 'inherit',
  groupSeparator: false,
  weight: 600,
});

interface Props {
  /** 数值或字符串（位号/版本号等） */
  value?: null | number | string | undefined;
  /** 小数精度（仅数值生效），默认 0 */
  precision?: number;
  /** 单位（如 %、°C、PCS/min），等宽字体 */
  unit?: string;
  /** 趋势箭头：up=升（绿）、down=降（红）、flat=持平（灰） */
  trend?: 'down' | 'flat' | 'up';
  /** 是否使用等宽字体，默认 true（位号/Tag/版本号也建议 true） */
  mono?: boolean;
  /** 空值占位符，默认 '—'（用于 INCONCLUSIVE、无数据等） */
  emptyText?: string;
  /** 是否强制显示空值占位符（用于可信度 E 级、INCONCLUSIVE 等） */
  empty?: boolean;
  /** 数值字号，默认 inherit；可选 'xs' | 'sm' | 'md' | 'lg' | 'xl' */
  size?: 'inherit' | 'lg' | 'md' | 'sm' | 'xl' | 'xs';
  /** 千分位分隔，默认 false */
  groupSeparator?: boolean;
  /** 字重，默认 600 */
  weight?: number;
}

/** 格式化后的显示值 */
const displayValue = computed(() => {
  if (props.empty || props.value === null || props.value === undefined) {
    return props.emptyText;
  }

  const v = props.value;

  // 字符串直接返回（位号/版本号等）
  if (typeof v === 'string') return v;

  // 数值格式化
  if (typeof v === 'number') {
    if (Number.isNaN(v) || !Number.isFinite(v)) return props.emptyText;

    let formatted: string;
    formatted = props.precision > 0 ? v.toFixed(props.precision) : String(Math.round(v));

    if (props.groupSeparator) {
      const [intPart = '', decPart] = formatted.split('.');
      const intWithSep = intPart.replaceAll(/\B(?=(\d{3})+(?!\d))/g, ',');
      formatted = decPart ? `${intWithSep}.${decPart}` : intWithSep;
    }

    return formatted;
  }

  return String(v);
});

/** 是否显示空占位 */
const isEmpty = computed(
  () => props.empty || props.value === null || props.value === undefined,
);

/** 趋势箭头图标名 */
const trendIcon = computed(() => {
  if (!props.trend) return '';
  return {
    up: 'lucide:arrow-up',
    down: 'lucide:arrow-down',
    flat: 'lucide:minus',
  }[props.trend];
});

/** 趋势色 class */
const trendClass = computed(() => {
  if (!props.trend) return '';
  return {
    up: 'clpm-numeric--up',
    down: 'clpm-numeric--down',
    flat: 'clpm-numeric--flat',
  }[props.trend];
});

/** 字号 class */
const sizeClass = computed(() => {
  if (props.size === 'inherit') return '';
  return `clpm-numeric--${props.size}`;
});

/** 是否为空占位（使用中性色） */
const emptyClass = computed(() =>
  isEmpty.value ? 'clpm-numeric--empty' : '',
);
</script>

<template>
  <span
    class="clpm-numeric" :class="[
      mono ? 'clpm-numeric--mono' : '',
      sizeClass,
      trendClass,
      emptyClass,
    ]"
    :style="{ fontWeight: weight }"
  >
    <IconifyIcon
      v-if="trendIcon"
      :icon="trendIcon"
      class="clpm-numeric__trend-icon"
    />
    <span class="clpm-numeric__value">{{ displayValue }}</span>
    <span v-if="unit && !isEmpty" class="clpm-numeric__unit">{{ unit }}</span>
  </span>
</template>

<style scoped>
.clpm-numeric {
  display: inline-flex;
  gap: 2px;
  align-items: baseline;
  line-height: 1.2;
  color: hsl(var(--foreground));
}

.clpm-numeric--mono {
  font-family: var(--font-mono);
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

/* 字号变体 */
.clpm-numeric--xs {
  font-size: 11px;
}

.clpm-numeric--sm {
  font-size: 12px;
}

.clpm-numeric--md {
  font-size: 14px;
}

.clpm-numeric--lg {
  font-size: 18px;
}

.clpm-numeric--xl {
  font-size: 24px;
}

/* 趋势色 */
.clpm-numeric--up {
  color: hsl(var(--status-ok));
}

.clpm-numeric--down {
  color: hsl(var(--status-error));
}

.clpm-numeric--flat {
  color: hsl(var(--status-neutral));
}

.clpm-numeric__trend-icon {
  align-self: center;
  margin-right: 1px;
  font-size: 0.85em;
}

.clpm-numeric__value {
  font-weight: inherit;
}

.clpm-numeric__unit {
  margin-left: 2px;
  font-size: 0.75em;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

/* 空占位使用中性色 + 较细字重 */
.clpm-numeric--empty {
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}
</style>
