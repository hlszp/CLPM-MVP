<script lang="ts" setup>
/**
 * ClpmKpiCard - ZL 致联工业风格 KPI 卡片
 *
 * 对齐 ZL-MES-UI-Design-Kit/clpm_dashboard.html §"顶部 KPI 卡片区" 与
 * IndustrialDesignReference.html §1 状态语义色卡片：
 * - 左上：标题（+ 可选 info tooltip）
 * - 右上：装饰图标（背景圆角方块 + 状态色背景 + 状态色图标）
 * - 中部：大数字（font-mono tabular-nums）+ 单位
 * - 底部上下文：左侧文字（如"参评 5 回路"），右侧变化量 badge
 * - 微型图表（三选一，按优先级）：progress 进度条 / microBars 迷你柱状 / sparkline 折线
 *
 * 状态色严格走 ZL 工业语义 token（--status-ok/warning/error/info/neutral），
 * 装饰图标背景走对应 50 级浅色（--color-*-50），图标色走 500 级。
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tooltip } from 'ant-design-vue';

defineOptions({ name: 'ClpmKpiCard' });

const props = withDefaults(defineProps<Props>(), {
  unit: '',
  status: 'neutral',
  icon: '',
  precision: 1,
  groupSeparator: true,
  contextText: '',
  delta: undefined,
  deltaUnit: '',
  deltaReverse: false,
  infoTip: '',
  progress: undefined,
  microBars: undefined,
  sparkline: undefined,
  loading: false,
  clickable: false,
});

const emit = defineEmits<{
  click: [event: MouseEvent];
}>();

type KpiStatus = 'error' | 'info' | 'neutral' | 'ok' | 'warning';

interface Props {
  /** 卡片标题（如"综合性能"/"平均自控率"） */
  title: string;
  /** 主值（数字或字符串，数字自动按 precision 格式化） */
  value: number | string;
  /** 单位（如 "%"、"分"、"次"） */
  unit?: string;
  /** 状态色：决定装饰图标背景 + 大数字色 + 进度条填充色 */
  status?: KpiStatus;
  /** 装饰图标 Iconify 名（如 'ant-design:chart-line' / 'lucide:robot'） */
  icon?: string;
  /** 数值精度（value 为 number 时生效），默认 1 */
  precision?: number;
  /** 千位分隔符，默认 true */
  groupSeparator?: boolean;
  /** 底部上下文左侧文字（如"参评 5 回路"） */
  contextText?: string;
  /** 变化量：数字时自动判断方向 + 加正号；字符串原样显示 */
  delta?: number | string;
  /** delta 单位（如 "%"/"分"，用于 tooltip） */
  deltaUnit?: string;
  /** 是否反向 delta（true 时 delta 正值显示红色，负值绿色；默认 false） */
  deltaReverse?: boolean;
  /** 信息提示文案（显示在标题旁的 info 图标 tooltip） */
  infoTip?: string;
  /** 进度条值（0-100），提供时显示底部进度条 */
  progress?: number;
  /** 迷你柱状图数据（number[]），提供时显示底部迷你柱状 */
  microBars?: number[];
  /** sparkline 折线数据（number[]，至少 2 个点） */
  sparkline?: number[];
  /** 是否加载中 */
  loading?: boolean;
  /** 是否可点击 */
  clickable?: boolean;
}

/** 主值格式化：数字按 precision + 千位分隔符；字符串原样 */
const formattedValue = computed(() => {
  if (typeof props.value !== 'number') return props.value;
  if (Number.isNaN(props.value)) return '—';
  const fixed = props.value.toFixed(props.precision);
  if (!props.groupSeparator) return fixed;
  const [intPart = '', decPart] = fixed.split('.');
  const withSep = intPart.replaceAll(/\B(?=(\d{3})+(?!\d))/g, ',');
  return decPart ? `${withSep}.${decPart}` : withSep;
});

/** 装饰图标背景色：状态色对应 50 级浅色 */
const iconBgVar = computed(() => {
  const map: Record<KpiStatus, string> = {
    ok: 'var(--color-emerald-50)',
    warning: 'var(--color-amber-50)',
    error: 'var(--color-rose-50)',
    info: 'var(--color-blue-50)',
    neutral: 'var(--color-slate-100)',
  };
  return map[props.status];
});

/** 装饰图标色：状态色对应 500 级 */
const iconColorVar = computed(() => {
  const map: Record<KpiStatus, string> = {
    ok: 'var(--color-emerald-600)',
    warning: 'var(--color-amber-600)',
    error: 'var(--color-rose-600)',
    info: 'var(--color-blue-600)',
    neutral: 'var(--color-slate-500)',
  };
  return map[props.status];
});

/** 大数字色：状态色 500 级（neutral 时用主文本色） */
const valueColorVar = computed(() => {
  if (props.status === 'neutral') return 'hsl(var(--foreground))';
  return iconColorVar.value;
});

/** 进度条填充色：状态色 500 级 */
const progressFillVar = computed(() => iconColorVar.value);

/** delta 方向：up/down/flat */
function getDeltaDirection(delta: number | string | undefined): 'down' | 'flat' | 'up' {
  if (delta === undefined || delta === '') return 'flat';
  const num = typeof delta === 'number' ? delta : Number.parseFloat(delta);
  if (Number.isNaN(num)) return 'flat';
  if (num > 0) return 'up';
  if (num < 0) return 'down';
  return 'flat';
}

/** delta 显示文案：数字时加正号 */
function getDeltaText(delta: number | string | undefined): string {
  if (delta === undefined || delta === '') return '';
  if (typeof delta === 'number') {
    return delta > 0 ? `+${delta}` : `${delta}`;
  }
  return delta;
}

/** delta badge 的色：正向反向逻辑 */
const deltaStatus = computed<'down' | 'up'>(() => {
  const dir = getDeltaDirection(props.delta);
  if (dir === 'flat') return 'up';
  // 正向：up=good(绿)、down=bad(红)
  // 反向：up=bad(红)、down=good(绿)
  if (props.deltaReverse) {
    return dir === 'up' ? 'down' : 'up';
  }
  return dir === 'up' ? 'up' : 'down';
});

const hasMicroChart = computed(
  () =>
    props.progress !== undefined ||
    (props.microBars && props.microBars.length > 0) ||
    (props.sparkline && props.sparkline.length >= 2),
);

const microChartType = computed<'bars' | 'line' | 'progress' | undefined>(() => {
  if (props.progress !== undefined) return 'progress';
  if (props.microBars && props.microBars.length > 0) return 'bars';
  if (props.sparkline && props.sparkline.length >= 2) return 'line';
  return undefined;
});

/** sparkline 最后一个点的 y 坐标 */
const sparklineLastPointY = computed(() => {
  const data = props.sparkline;
  if (!data || data.length < 2) return 12;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const last = data[data.length - 1];
  if (last === undefined) return 12;
  return 24 - ((last - min) / range) * 22 - 1;
});

/** sparkline SVG path */
function buildSparklinePath(data: number[], width = 100, height = 24): string {
  if (!data || data.length < 2) return '';
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `M ${points.join(' L ')}`;
}

function handleClick(event: MouseEvent) {
  if (!props.clickable) return;
  emit('click', event);
}
</script>

<template>
  <div
    class="clpm-kpi-card"
    :class="[
      `is-${status}`,
      clickable ? 'is-clickable' : '',
      loading ? 'is-loading' : '',
    ]"
    @click="handleClick"
  >
    <!-- 顶部：标题 + 装饰图标 -->
    <div class="clpm-kpi-card__header">
      <div class="clpm-kpi-card__title-row">
        <span class="clpm-kpi-card__title">{{ title }}</span>
        <Tooltip v-if="infoTip" :title="infoTip">
          <IconifyIcon
            icon="ant-design:info-circle-outlined"
            class="clpm-kpi-card__info-icon"
          />
        </Tooltip>
      </div>
      <div
        v-if="icon"
        class="clpm-kpi-card__icon-wrap"
        :style="{ background: iconBgVar, color: iconColorVar }"
      >
        <IconifyIcon :icon="icon" />
      </div>
    </div>

    <!-- 中部：大数字 + 单位 -->
    <div class="clpm-kpi-card__value-row">
      <span class="clpm-kpi-card__value" :style="{ color: valueColorVar }">
        {{ formattedValue }}
      </span>
      <span v-if="unit" class="clpm-kpi-card__unit">{{ unit }}</span>
    </div>

    <!-- 底部上下文：左侧文字 + 右侧 delta -->
    <div v-if="contextText || delta !== undefined" class="clpm-kpi-card__context">
      <span v-if="contextText" class="clpm-kpi-card__context-text">
        {{ contextText }}
      </span>
      <span
        v-if="delta !== undefined && delta !== ''"
        class="clpm-kpi-card__delta"
        :class="`clpm-kpi-card__delta--${deltaStatus}`"
      >
        <span class="clpm-kpi-card__delta-arrow">
          <template v-if="getDeltaDirection(delta) === 'up'">↑</template>
          <template v-else-if="getDeltaDirection(delta) === 'down'">↓</template>
          <template v-else>→</template>
        </span>
        <span class="clpm-kpi-card__delta-text">{{ getDeltaText(delta) }}</span>
        <span v-if="deltaUnit" class="clpm-kpi-card__delta-unit">{{ deltaUnit }}</span>
      </span>
    </div>

    <!-- 微型图表 -->
    <div v-if="hasMicroChart" class="clpm-kpi-card__micro">
      <!-- 进度条 -->
      <div
        v-if="microChartType === 'progress'"
        class="clpm-kpi-card__progress-track"
      >
        <div
          class="clpm-kpi-card__progress-fill"
          :style="{
            width: `${Math.min(Math.max(progress!, 0), 100)}%`,
            background: progressFillVar,
          }"
        ></div>
      </div>

      <!-- 迷你柱状图 -->
      <div
        v-else-if="microChartType === 'bars'"
        class="clpm-kpi-card__bars"
      >
        <div
          v-for="(v, i) in microBars"
          :key="i"
          class="clpm-kpi-card__bar"
          :style="{
            height: `${Math.min(Math.max((v / Math.max(...microBars!)) * 100, 5), 100)}%`,
            background: iconColorVar,
            opacity: i === microBars!.length - 1 ? 1 : 0.7,
          }"
        ></div>
      </div>

      <!-- sparkline 折线 -->
      <svg
        v-else-if="microChartType === 'line'"
        class="clpm-kpi-card__sparkline"
        :width="120"
        :height="24"
        viewBox="0 0 120 24"
        preserveAspectRatio="none"
      >
        <path
          :d="buildSparklinePath(sparkline ?? [], 120, 24)"
          fill="none"
          :stroke="iconColorVar"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <circle
          :cx="120"
          :cy="sparklineLastPointY"
          r="2.5"
          :fill="iconColorVar"
        />
      </svg>
    </div>
  </div>
</template>

<style scoped>
.clpm-kpi-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 16px 20px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: var(--radius-industrial-lg);
  transition: box-shadow 0.2s ease, transform 0.15s ease;
}

.clpm-kpi-card.is-clickable {
  cursor: pointer;
}

.clpm-kpi-card.is-clickable:hover {
  box-shadow: 0 4px 12px rgb(15 23 42 / 8%);
  transform: translateY(-1px);
}

.clpm-kpi-card.is-loading {
  pointer-events: none;
  opacity: 0.7;
}

/* —— 顶部 —— */
.clpm-kpi-card__header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}

.clpm-kpi-card__title-row {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
}

.clpm-kpi-card__title {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.clpm-kpi-card__info-icon {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: help;
  opacity: 0.6;
}

.clpm-kpi-card__icon-wrap {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: 18px;
  border-radius: var(--radius-industrial-lg);
}

/* —— 中部 —— */
.clpm-kpi-card__value-row {
  display: flex;
  gap: 4px;
  align-items: baseline;
  margin-top: 2px;
}

.clpm-kpi-card__value {
  font-family: var(--font-mono);
  font-size: 28px;
  font-weight: 700;
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  letter-spacing: -0.02em;
}

.clpm-kpi-card__unit {
  font-size: 14px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
}

/* —— 底部上下文 —— */
.clpm-kpi-card__context {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  margin-top: 6px;
  font-size: 11px;
  border-top: 1px solid hsl(var(--border) / 50%);
}

.clpm-kpi-card__context-text {
  color: hsl(var(--muted-foreground));
}

.clpm-kpi-card__delta {
  display: inline-flex;
  gap: 2px;
  align-items: center;
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 3px;
}

.clpm-kpi-card__delta-arrow {
  font-size: 11px;
  line-height: 1;
}

.clpm-kpi-card__delta--up {
  color: var(--color-emerald-700);
  background: var(--color-emerald-50);
}

.clpm-kpi-card__delta--down {
  color: var(--color-rose-700);
  background: var(--color-rose-50);
}

/* —— 微型图表 —— */
.clpm-kpi-card__micro {
  margin-top: 4px;
}

.clpm-kpi-card__progress-track {
  width: 100%;
  height: 4px;
  overflow: hidden;
  background: hsl(var(--border) / 40%);
  border-radius: 2px;
}

.clpm-kpi-card__progress-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}

.clpm-kpi-card__bars {
  display: flex;
  gap: 2px;
  align-items: flex-end;
  height: 24px;
}

.clpm-kpi-card__bar {
  flex: 1 1 0;
  min-width: 2px;
  border-radius: 2px 2px 0 0;
  transition: height 0.3s ease;
}

.clpm-kpi-card__sparkline {
  width: 100%;
  height: 24px;
  opacity: 0.9;
}
</style>
