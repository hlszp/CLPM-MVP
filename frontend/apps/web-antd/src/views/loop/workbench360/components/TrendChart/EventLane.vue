<script setup lang="ts">
/**
 * 事件标注层（workbench360 P1，v3 §5.3 / D15）
 *
 * 固定高度独立层（26px，迷你态 22px）——不随趋势图压缩变形；
 * 徽标 = 白底描边圆角 pill + 指示三角，按视口时间百分比定位（钳制 7%–93%）。
 *
 * P1 数据来源（诚实化）：
 * - MANUAL 段徽标 ⏸：来自趋势数据 trend.mode 真实序列（段中点取位）；
 * - ▼诊断/◆整定/▮验证：事件锚点数据属 P2-P4 范围，P1 不渲染（events 传入空）。
 */
import { computed, type PropType } from 'vue';

import { resolveModeLabel } from '#/composables/use-loop-realtime';
import {
  WB360_EVENT_MARK_COLORS,
} from '#/constants/clpm-ui';

import { resampleFrames } from './resample';
import type { TimeRange, TrendEventMark, TrendFrame } from './types';

const props = defineProps({
  /** 事件锚点（P2-P4 接入；P1 传空数组） */
  events: {
    type: Array as PropType<TrendEventMark[]>,
    default: () => [],
  },
  /** 迷你态（22px + 角标文案） */
  mini: {
      type: Boolean,
      default: false,
  },
  modeMapping: {
    type: Object as PropType<null | Record<string, string>>,
    default: null,
  },
  /** 当前视口（与趋势图 viewX 联动） */
  viewX: {
    type: Object as PropType<TimeRange>,
    required: true,
  },
  frames: {
    type: Array as PropType<TrendFrame[]>,
    default: () => [],
  },
});

const emit = defineEmits<{
  (e: 'open-section', key: TrendEventMark['section']): void;
}>();

interface Pill {
  color: string;
  glyph: string;
  key: string;
  leftPct: number;
  section?: TrendEventMark['section'];
  title: string;
}

/** MANUAL 段徽标（真实 trend.mode 序列派生） */
const manualPills = computed<Pill[]>(() => {
  const v = props.viewX;
  const span = v.t1 - v.t0;
  if (span <= 0 || props.frames.length === 0) return [];
  // 以视口为网格抽样 mode 序列（同绘制引擎口径，720 点足够定位段）
  const rs = resampleFrames(props.frames, v.t0, v.t1, 720);
  const isManual = (m: null | number) =>
    resolveModeLabel(m, props.modeMapping) === 'Manual';
  const pills: Pill[] = [];
  let start = -1;
  const n = rs.mode.length;
  for (let i = 0; i <= n; i++) {
    const man = i < n && isManual(rs.mode[i]);
    if (man && start < 0) start = i;
    if (!man && start >= 0) {
      const midTs = v.t0 + (span * (start + i - 1)) / (2 * (n - 1));
      pills.push({
        color: WB360_EVENT_MARK_COLORS.manual,
        glyph: '⏸',
        key: `manual-${start}`,
        leftPct: pctOf(midTs, v),
        title: '手动段',
      });
      start = -1;
    }
  }
  return pills;
});

const eventPills = computed<Pill[]>(() => {
  const v = props.viewX;
  return props.events
    .filter((e) => e.ts >= v.t0 && e.ts <= v.t1)
    .map((e) => ({
      color: colorOf(e),
      glyph: e.glyph,
      key: e.key,
      leftPct: pctOf(e.ts, v),
      section: e.section,
      title: e.label,
    }));
});

function colorOf(e: TrendEventMark): string {
  if (e.section === 'diag') return WB360_EVENT_MARK_COLORS.diag;
  if (e.section === 'tuning') return WB360_EVENT_MARK_COLORS.tuning;
  if (e.section === 'handling') return WB360_EVENT_MARK_COLORS.verify;
  return WB360_EVENT_MARK_COLORS.manual;
}

/** 视口百分比（钳制 7%–93%，防出界） */
function pctOf(ts: number, v: TimeRange): number {
  const span = v.t1 - v.t0;
  const pct = ((ts - v.t0) / span) * 100;
  return Math.max(7, Math.min(93, pct));
}

const pills = computed(() => [...manualPills.value, ...eventPills.value]);
</script>

<template>
  <div
    class="wb360-lane"
    :class="{ mini }"
    aria-label="事件标注"
  >
    <span v-if="mini" class="wb360-lane-cap">监视（迷你）</span>
    <button
      v-for="p in pills"
      :key="p.key"
      type="button"
      class="wb360-pill"
      :style="{ color: p.color, left: `${p.leftPct}%` }"
      :title="p.title"
      @click="p.section && emit('open-section', p.section)"
    >
      <span>{{ p.glyph }}</span>{{ p.title }}
      <i v-if="p.section" class="wb360-tri"></i>
    </button>
  </div>
</template>

<style scoped>
.wb360-lane {
  position: relative;
  flex: none;
  height: 26px;
  margin: 0 54px 0 46px;
}

.wb360-lane.mini {
  height: 22px;
}

.wb360-lane-cap {
  position: absolute;
  top: 5px;
  left: 0;
  font-size: 11px;
  color: hsl(var(--muted-foreground) / 60%);
}

.wb360-pill {
  position: absolute;
  top: 3px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 18px;
  padding: 0 8px;
  border: 1px solid currentcolor;
  border-radius: 9px;
  background: var(--wb360-panel);
  color: inherit;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transform: translateX(-50%);
}

.wb360-pill:hover {
  box-shadow: 0 1px 2px hsl(var(--foreground) / 12%);
}

.wb360-tri {
  position: absolute;
  top: 17px;
  left: 50%;
  margin-left: -4px;
  border: 4px solid transparent;
  border-top-color: currentcolor;
}
</style>
