<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';

import { computed } from 'vue';

defineOptions({ name: 'ClpmDataHealthBadges' });

const props = withDefaults(defineProps<Props>(), {
  health: () => ({}),
  compact: false,
});

/**
 * 数据健康度徽标组（方案 A §5 / 方案 C 轻量版）
 *
 * 工业设计口径（UI/UX v6.1 Calm UI / Poka-Yoke）：
 * - 双指标：可信度（A~E）+ 预处理有效率（validRate）
 * - 色彩语义：绿=优 / 蓝=良 / 黄=注意 / 红=差（警告并入红，不设独立橙档）/ 灰=无数据
 * - 不加动画，紧凑堆叠，最大化 data-ink ratio
 *
 * 数据完整性检查模块已下线（2026-09-07），PV 完整度徽标随巡检快照一并移除。
 */

interface DataHealthLike {
  /** 预处理：好值率/有效率（0~1） */
  validRate?: null | number;
  /** 回路可信度：A/B/C/D/E */
  confidenceLevel?: LoopApi.ConfidenceLevel | null;
}

interface Props {
  health?: DataHealthLike | null;
  /** 紧凑模式（测点页表格行高受限时用） */
  compact?: boolean;
}

// 可信度配色：A 优 / B 良 / C 注意 / D 警告 / E 差
const CONF_META: Record<string, { bg: string; color: string; label: string }> =
  {
    A: {
      color: 'var(--success)',
      bg: 'hsl(var(--success) / 12%)',
      label: 'A 优',
    },
    B: {
      color: 'var(--primary)',
      bg: 'hsl(var(--primary) / 12%)',
      label: 'B 良',
    },
    C: {
      color: 'var(--warning)',
      bg: 'hsl(var(--warning) / 14%)',
      label: 'C 注意',
    },
    D: {
      color: 'var(--destructive)',
      bg: 'hsl(var(--destructive) / 12%)',
      label: 'D 警告',
    },
    E: {
      color: 'var(--destructive)',
      bg: 'hsl(var(--destructive) / 12%)',
      label: 'E 差',
    },
  };

function rateTier(rate: null | number | undefined) {
  if (rate === null || rate === undefined) return null;
  if (rate >= 0.95)
    return { color: 'var(--success)', bg: 'hsl(var(--success) / 12%)' };
  if (rate >= 0.8)
    return { color: 'var(--primary)', bg: 'hsl(var(--primary) / 12%)' };
  if (rate >= 0.6)
    return { color: 'var(--warning)', bg: 'hsl(var(--warning) / 14%)' };
  // < 0.6 → destructive（原橙色档并入 DANGER，与评分降级链同口径：不设独立橙色档）
  return { color: 'var(--destructive)', bg: 'hsl(var(--destructive) / 12%)' };
}

function pct(rate: null | number | undefined) {
  if (rate === null || rate === undefined) return null;
  return `${(rate * 100).toFixed(1)}%`;
}

const confMeta = computed(() => {
  const lvl = props.health?.confidenceLevel;
  if (!lvl) return null;
  return CONF_META[lvl] ?? null;
});

const validRateTier = computed(() => rateTier(props.health?.validRate));
const validRateText = computed(() => pct(props.health?.validRate));

/** 是否有任何徽标可显示 */
const hasAnyBadge = computed(() => {
  if (confMeta.value) return true;
  if (validRateText.value) return true;
  return false;
});
</script>

<template>
  <div class="clpm-dhb" :class="{ 'clpm-dhb--compact': compact }">
    <span
      v-if="confMeta"
      class="clpm-dhb__badge"
      :style="{ color: confMeta.color, background: confMeta.bg }"
      >{{ confMeta.label }}</span
    >
    <span
      v-if="validRateText"
      class="clpm-dhb__badge"
      :style="{ color: validRateTier!.color, background: validRateTier!.bg }"
      >有效 {{ validRateText }}</span
    >
    <span v-if="!hasAnyBadge" class="clpm-dhb__empty">—</span>
  </div>
</template>

<style scoped>
.clpm-dhb {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  justify-content: center;
  line-height: 1.4;
}

.clpm-dhb--compact {
  gap: 3px;
}

.clpm-dhb__badge {
  padding: 1px 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  white-space: nowrap;
  border-radius: 3px;
}

.clpm-dhb--compact .clpm-dhb__badge {
  padding: 0 5px;
  font-size: 10px;
  line-height: 14px;
}

.clpm-dhb__empty {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}
</style>
