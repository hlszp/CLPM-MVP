<script lang="ts" setup>
/**
 * B4 综合评分分解图
 *
 * 可视化基础评分公式：P = (A·a + F·f + S·s)/(a+f+s) × R
 * - A=准确率, F=快速率, S=稳定率（0~100）
 * - a/f/s 为权重（默认 40/30/30，可通过 getLoopTypeWeightsApi 按控制类型获取）
 * - R=有效自控率（折扣因子，0~100）
 *
 * 纯 HTML/CSS 组件（无 ECharts），Calm UI 工业风，无动画。
 */
import { computed } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

const props = withDefaults(
  defineProps<{
    score: null | number;
    accuracy: null | number;
    fast: null | number;
    stability: null | number;
    effectiveAutoRate: null | number;
    weightA?: number;
    weightF?: number;
    weightS?: number;
  }>(),
  {
    weightA: 40,
    weightF: 30,
    weightS: 30,
  },
);

const { themeColors } = useClpmTheme();

/** 安全归一化到 0~100 */
function safePct(v: null | number | undefined): number {
  if (v === null || v === undefined) return 0;
  return Math.max(0, Math.min(100, v));
}

const weightTotal = computed(
  () => props.weightA + props.weightF + props.weightS,
);

const baseScore = computed(() => {
  if (weightTotal.value === 0) return 0;
  return (
    (safePct(props.accuracy) * props.weightA +
      safePct(props.fast) * props.weightF +
      safePct(props.stability) * props.weightS) /
    weightTotal.value
  );
});

const effectiveAutoPct = computed(() => safePct(props.effectiveAutoRate));
const displayScore = computed(() => safePct(props.score));
const computedFinal = computed(
  () => (baseScore.value * effectiveAutoPct.value) / 100,
);

const items = computed(() => [
  {
    key: 'A',
    label: '准确率 A',
    value: safePct(props.accuracy),
    weight: props.weightA,
    weightPct: weightTotal.value
      ? (props.weightA / weightTotal.value) * 100
      : 0,
    color: themeColors.value.SUCCESS,
    contribution:
      weightTotal.value === 0
        ? 0
        : (safePct(props.accuracy) * props.weightA) / weightTotal.value,
  },
  {
    key: 'F',
    label: '快速率 F',
    value: safePct(props.fast),
    weight: props.weightF,
    weightPct: weightTotal.value
      ? (props.weightF / weightTotal.value) * 100
      : 0,
    color: themeColors.value.WARNING,
    contribution:
      weightTotal.value === 0
        ? 0
        : (safePct(props.fast) * props.weightF) / weightTotal.value,
  },
  {
    key: 'S',
    label: '稳定率 S',
    value: safePct(props.stability),
    weight: props.weightS,
    weightPct: weightTotal.value
      ? (props.weightS / weightTotal.value) * 100
      : 0,
    color: themeColors.value.ACCENT,
    contribution:
      weightTotal.value === 0
        ? 0
        : (safePct(props.stability) * props.weightS) / weightTotal.value,
  },
]);
</script>

<template>
  <div class="score-breakdown">
    <!-- 顶部：综合评分 -->
    <div class="score-header">
      <span class="score-label">综合评分 P</span>
      <span class="score-value" :style="{ color: themeColors.INFO }">
        {{ displayScore.toFixed(1) }}
      </span>
    </div>

    <!-- 公式 -->
    <div class="formula">P = (A·a + F·f + S·s) / (a+f+s) × R</div>

    <!-- 加权贡献条 -->
    <div class="bars">
      <div v-for="item in items" :key="item.key" class="bar-row">
        <div class="bar-head">
          <span class="bar-label">{{ item.label }}</span>
          <span class="bar-weight">
            权重 {{ item.weight }} ({{ item.weightPct.toFixed(0) }}%)
          </span>
        </div>
        <div class="bar-track">
          <div
            class="bar-fill"
            :style="{
              width: `${item.value}%`,
              backgroundColor: item.color,
            }"
          ></div>
        </div>
        <div class="bar-foot">
          <span class="bar-value">{{ item.value.toFixed(2) }}</span>
          <span class="bar-contrib"
            >贡献 {{ item.contribution.toFixed(2) }}</span
          >
        </div>
      </div>
    </div>

    <!-- 折扣因子 -->
    <div class="bar-row discount">
      <div class="bar-head">
        <span class="bar-label">有效自控率 R（折扣因子）</span>
        <span class="bar-weight">× {{ effectiveAutoPct.toFixed(1) }}%</span>
      </div>
      <div class="bar-track discount-track">
        <div
          class="bar-fill discount-fill"
          :style="{
            width: `${effectiveAutoPct}%`,
            backgroundColor: themeColors.DANGER,
          }"
        ></div>
      </div>
      <div class="bar-foot">
        <span class="bar-value"
          >基础评分 {{ baseScore.toFixed(1) }} ×
          {{ effectiveAutoPct.toFixed(1) }}% =
          {{ computedFinal.toFixed(1) }}</span
        >
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.score-breakdown {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  padding: 12px;
}

.score-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--ant-color-border-secondary, rgb(0 0 0 / 6%));
}

.score-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--ant-color-text, #1f2937);
}

.score-value {
  font-family: var(--font-mono);
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}

.formula {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ant-color-text-tertiary, #9ca3af);
}

.bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.bar-row {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.bar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.bar-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ant-color-text, #374151);
}

.bar-weight {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ant-color-text-tertiary, #9ca3af);
}

.bar-track {
  width: 100%;
  height: 10px;
  overflow: hidden;
  background: var(--ant-color-fill-quaternary, rgb(0 0 0 / 4%));
  border-radius: 5px;
}

.bar-fill {
  height: 100%;
  border-radius: 5px;
}

.discount-track {
  background: var(--ant-color-fill-quaternary, rgb(0 0 0 / 4%));
}

.discount-fill {
  opacity: 0.75;
}

.discount {
  padding-top: 8px;
  margin-top: 4px;
  border-top: 1px dashed var(--ant-color-border-secondary, rgb(0 0 0 / 6%));
}

.bar-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.bar-value {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ant-color-text-secondary, #6b7280);
}

.bar-contrib {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ant-color-text-tertiary, #9ca3af);
}
</style>
