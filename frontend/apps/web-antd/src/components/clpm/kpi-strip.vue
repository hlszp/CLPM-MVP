<script lang="ts" setup>
export interface KpiStripItem {
  /** 变化量：数字或字符串。数字时自动判断方向，字符串原样显示 */
  delta?: number | string;
  /** 是否可点击下钻，默认 false */
  clickable?: boolean;
  /** 唯一标识 */
  key: string;
  /** 指标名称 */
  label: string;
  /** 趋势小图数据（数值数组），如 [10, 12, 8, 15, 11] */
  sparkline?: number[];
  /** 状态色：success/warning/danger/neutral */
  status?: 'danger' | 'neutral' | 'success' | 'warning';
  /** 单位 */
  unit?: string;
  /** 主值 */
  value: number | string;
}

defineOptions({ name: 'ClpmKpiStrip' });

interface Props {
  items: KpiStripItem[];
  loading?: boolean;
  /** 全局可点击，优先级低于 item.clickable */
  clickable?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  clickable: false,
});

const emit = defineEmits<{
  'item-click': [item: KpiStripItem];
}>();

/** 解析 delta 方向：up/down/flat */
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

/** 判断 item 是否可点击 */
function isClickable(item: KpiStripItem): boolean {
  return item.clickable ?? props.clickable;
}

/** 处理点击 */
function handleClick(item: KpiStripItem) {
  if (!isClickable(item)) return;
  emit('item-click', item);
}

/**
 * 生成 sparkline SVG path
 * 简单线性归一化：取数据范围内的值映射到 0~30px 高度
 */
function buildSparklinePath(data: number[], width = 80, height = 28): string {
  if (!data || data.length < 2) return '';
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);
  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `M ${points.join(' L ')}`;
}

/** sparkline 描边色 */
function getSparklineColor(status?: string): string {
  switch (status) {
    case 'success': {
      return 'hsl(142 71% 45%)';
    }
    case 'warning': {
      return 'hsl(38 92% 50%)';
    }
    case 'danger': {
      return 'hsl(0 84% 60%)';
    }
    default: {
      return 'hsl(211 98% 52%)';
    }
  }
}
</script>

<template>
  <section class="clpm-kpi-strip" :class="{ 'is-loading': loading }">
    <!-- 骨架屏 -->
    <template v-if="loading">
      <div
        v-for="i in items.length || 4"
        :key="`skeleton-${i}`"
        class="clpm-kpi-strip__item clpm-kpi-strip__skeleton"
      >
        <div class="clpm-kpi-strip__skeleton-line clpm-kpi-strip__skeleton-line--label"></div>
        <div class="clpm-kpi-strip__skeleton-line clpm-kpi-strip__skeleton-line--value"></div>
        <div class="clpm-kpi-strip__skeleton-line clpm-kpi-strip__skeleton-line--delta"></div>
      </div>
    </template>

    <!-- 实际内容 -->
    <template v-else>
      <div
        v-for="item in items"
        :key="item.key"
        class="clpm-kpi-strip__item"
        :class="{
          'is-clickable': isClickable(item),
          [`is-${item.status || 'neutral'}`]: true,
        }"
        @click="handleClick(item)"
      >
        <div class="clpm-kpi-strip__label">{{ item.label }}</div>
        <div class="clpm-kpi-strip__main">
          <div class="clpm-kpi-strip__value-row">
            <span class="clpm-kpi-strip__value" :class="`is-${item.status || 'neutral'}`">
              {{ item.value }}
            </span>
            <span v-if="item.unit" class="clpm-kpi-strip__unit">{{ item.unit }}</span>
          </div>
          <!-- sparkline 趋势小图 -->
          <svg
            v-if="item.sparkline && item.sparkline.length >= 2"
            class="clpm-kpi-strip__sparkline"
            :width="80"
            :height="28"
            viewBox="0 0 80 28"
            preserveAspectRatio="none"
          >
            <path
              :d="buildSparklinePath(item.sparkline)"
              fill="none"
              :stroke="getSparklineColor(item.status)"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </div>
        <!-- delta 变化量 + 方向箭头 -->
        <div
          v-if="item.delta !== undefined && item.delta !== ''"
          class="clpm-kpi-strip__delta"
          :class="`clpm-kpi-strip__delta--${getDeltaDirection(item.delta)}`"
        >
          <span class="clpm-kpi-strip__delta-arrow">
            <template v-if="getDeltaDirection(item.delta) === 'up'">↑</template>
            <template v-else-if="getDeltaDirection(item.delta) === 'down'">↓</template>
            <template v-else>→</template>
          </span>
          <span class="clpm-kpi-strip__delta-text">{{ getDeltaText(item.delta) }}</span>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.clpm-kpi-strip {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  display: grid;
  gap: 0;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  overflow: hidden;
}

.clpm-kpi-strip__item {
  border-right: 1px solid hsl(var(--border));
  cursor: default;
  min-width: 0;
  padding: 10px 12px;
  transition: background 0.15s;
}

.clpm-kpi-strip__item:last-child {
  border-right: 0;
}

.clpm-kpi-strip__item.is-clickable {
  cursor: pointer;
}

.clpm-kpi-strip__item.is-clickable:hover {
  background: hsl(210 30% 96%);
}

.clpm-kpi-strip__label {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clpm-kpi-strip__main {
  align-items: flex-end;
  display: flex;
  gap: 8px;
  justify-content: space-between;
  margin-top: 3px;
}

.clpm-kpi-strip__value-row {
  align-items: baseline;
  display: flex;
  flex: 1 1 auto;
  gap: 4px;
  min-width: 0;
}

.clpm-kpi-strip__value {
  color: hsl(var(--foreground));
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-feature-settings: 'tnum';
  font-size: 20px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  line-height: 24px;
}

.clpm-kpi-strip__unit {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
}

/* sparkline */
.clpm-kpi-strip__sparkline {
  flex: 0 0 auto;
  opacity: 0.8;
}

/* delta 变化量 */
.clpm-kpi-strip__delta {
  align-items: center;
  display: flex;
  font-size: 12px;
  gap: 2px;
  margin-top: 2px;
}

.clpm-kpi-strip__delta-arrow {
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
}

.clpm-kpi-strip__delta--up {
  color: hsl(142 71% 45%);
}

.clpm-kpi-strip__delta--down {
  color: hsl(0 84% 60%);
}

.clpm-kpi-strip__delta--flat {
  color: hsl(var(--muted-foreground));
}

/* 状态色 */
.is-success { color: hsl(var(--success)); }
.is-warning { color: hsl(var(--warning)); }
.is-danger { color: hsl(var(--destructive)); }
.is-neutral { color: hsl(var(--foreground)); }

/* 骨架屏 */
.clpm-kpi-strip__skeleton {
  pointer-events: none;
}

.clpm-kpi-strip__skeleton-line {
  animation: clpm-kpi-skeleton 1.5s ease-in-out infinite;
  background: linear-gradient(
    90deg,
    hsl(var(--muted)) 25%,
    hsl(var(--accent)) 37%,
    hsl(var(--muted)) 63%
  );
  background-size: 400% 100%;
  border-radius: 2px;
}

.clpm-kpi-strip__skeleton-line--label {
  height: 12px;
  margin-bottom: 8px;
  width: 60%;
}

.clpm-kpi-strip__skeleton-line--value {
  height: 20px;
  margin-bottom: 6px;
  width: 80%;
}

.clpm-kpi-strip__skeleton-line--delta {
  height: 12px;
  width: 40%;
}

@keyframes clpm-kpi-skeleton {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}
</style>
