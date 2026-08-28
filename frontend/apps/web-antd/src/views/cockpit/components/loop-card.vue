<script lang="ts" setup>
import type { LoopCardModel } from '../loops-shared';

/**
 * 驾驶舱页2 · 回路卡（方案 11 §6.2，卡高 ~118px）
 *
 * 左 3px 五档等级色边条 · 回路号+名称 · 综合评分（五档色染）·
 * PV/SP 实时值（WebSocket 刷新）· 模式标签 · 最差维度标签（无异常不显示）·
 * 近 24h 综合评分火花线 · 选中态 accent 描边。
 */
import type { MetricSeriesPoint } from '#/api/metric';

import { computed } from 'vue';

defineOptions({ name: 'CockpitLoopCard' });

const props = withDefaults(
  defineProps<{
    loop: LoopCardModel;
    selected?: boolean;
    spark?: MetricSeriesPoint[];
  }>(),
  { selected: false, spark: () => [] },
);

const emit = defineEmits<{ select: [loopId: string] }>();

/** 等级色 CSS 变量（无评分 → 中性边框色） */
const gradeVar = computed(() => props.loop.grade?.colorVar ?? '--ck-border-2');

const scoreText = computed(() =>
  props.loop.score === null ? '—' : props.loop.score.toFixed(1),
);

function fmt(v: null | number | undefined): string {
  return v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : String(Math.round(v * 100) / 100);
}

// ---------------------------------------------------------------------------
// 火花线（纯 SVG polyline，无动画；参考 workbench/Spark.vue）
// ---------------------------------------------------------------------------
const SPARK_W = 96;
const SPARK_H = 24;
const PAD = 2;

const sparkPath = computed<null | string>(() => {
  const vals = props.spark
    .map((p) => p.value)
    .filter((v): v is number => typeof v === 'number' && !Number.isNaN(v));
  if (vals.length < 2) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const w = SPARK_W - PAD * 2;
  const h = SPARK_H - PAD * 2;
  const step = w / (vals.length - 1);
  return vals
    .map((v, i) => {
      const x = PAD + i * step;
      const y = PAD + h - ((v - min) / range) * h;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
});
</script>

<template>
  <button
    type="button"
    class="lcard"
    :class="{ selected }"
    :style="{ borderLeftColor: `var(${gradeVar})` }"
    @click="emit('select', loop.loopId)"
  >
    <!-- 行1：回路号 · 名称 ｜ 综合评分 -->
    <div class="lcard__top">
      <span class="lcard__tag" :title="`${loop.tagName} ${loop.description}`">
        {{ loop.tagName }}
        <i v-if="loop.description" class="lcard__sep">·</i>
        {{ loop.description }}
      </span>
      <span class="lcard__score" :style="{ color: `var(${gradeVar})` }">
        {{ scoreText }}
      </span>
    </div>

    <!-- 行2：PV/SP 实时值 + 模式标签 -->
    <div class="lcard__mid">
      <span class="lcard__kv">
        PV <b>{{ fmt(loop.live.pv) }}</b>
      </span>
      <span class="lcard__kv">
        SP <b>{{ fmt(loop.live.sp) }}</b>
      </span>
      <span v-if="loop.live.unit" class="lcard__unit">{{ loop.live.unit }}</span>
      <span class="lcard__mode" :class="{ manual: loop.mode === 'MANUAL' }">
        {{ loop.modeZh }}
      </span>
    </div>

    <!-- 行3：最差维度标签 + 近 24h 评分火花线 -->
    <div class="lcard__bottom">
      <span v-if="loop.worst" class="lcard__worst">{{ loop.worst }}</span>
      <span v-else class="lcard__worst lcard__worst--ok">六维正常</span>
      <svg
        v-if="sparkPath"
        class="lcard__spark"
        :width="SPARK_W"
        :height="SPARK_H"
        :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`"
        preserveAspectRatio="none"
      >
        <path
          :d="sparkPath"
          :stroke="`var(${gradeVar})`"
          fill="none"
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="1.5"
        />
      </svg>
      <span v-else class="lcard__spark lcard__spark--empty">—</span>
    </div>
  </button>
</template>

<style scoped>
.lcard {
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: space-between;
  height: 118px;
  padding: 10px 12px;
  overflow: hidden;
  text-align: left;
  cursor: pointer;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-left: 3px solid var(--ck-border-2);
  border-radius: 8px;
  transition: 0.15s;
}

.lcard:hover {
  background: var(--ck-hover);
  border-color: var(--ck-border-2);
  border-left-color: inherit;
}

.lcard.selected {
  border-color: var(--ck-accent);
  box-shadow: 0 0 0 1px var(--ck-accent);
}

.lcard__top {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}

.lcard__tag {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-text);
  white-space: nowrap;
}

.lcard__sep {
  margin: 0 4px;
  font-style: normal;
  font-weight: 400;
  color: var(--ck-text-3);
}

.lcard__score {
  flex: none;
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.lcard__mid {
  display: flex;
  flex: none;
  gap: 10px;
  align-items: baseline;
  font-size: 11px;
  color: var(--ck-text-3);
}

.lcard__kv b {
  margin-left: 2px;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.lcard__unit {
  font-size: 10px;
}

.lcard__mode {
  flex: none;
  padding: 1px 7px;
  margin-left: auto;
  font-size: 11px;
  color: var(--ck-grade-excellent);
  background: var(--ck-panel-3);
  border: 1px solid var(--ck-border);
  border-radius: 9px;
}

.lcard__mode.manual {
  color: var(--ck-grade-fair);
  border-color: var(--ck-border-2);
}

.lcard__bottom {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
}

.lcard__worst {
  padding: 1px 7px;
  font-size: 11px;
  color: var(--ck-grade-warning);
  background: var(--ck-panel-3);
  border-radius: 4px;
}

.lcard__worst--ok {
  color: var(--ck-text-3);
}

.lcard__spark {
  flex: none;
}

.lcard__spark--empty {
  width: 96px;
  font-size: 10px;
  color: var(--ck-text-3);
  text-align: center;
}
</style>
