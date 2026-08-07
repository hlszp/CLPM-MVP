<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';

import { computed } from 'vue';

import { Tooltip } from 'ant-design-vue';

defineOptions({ name: 'ClpmDataHealthBadges' });

const props = withDefaults(defineProps<Props>(), {
  health: () => ({}),
  compact: false,
  showPvCompleteness: true,
});

/**
 * 数据健康度徽标组（方案 A §5 / 方案 C 轻量版）
 *
 * 工业设计口径（UI/UX v6.1 Calm UI / Poka-Yoke）：
 * - 三指标：可信度（A~E）+ 预处理有效率（validRate）+ PV 完整度（pvCompleteness）
 * - 色彩语义：绿=优 / 蓝=良 / 黄=注意 / 橙=警告 / 红=差 / 灰=无数据
 * - 不加动画，紧凑堆叠，最大化 data-ink ratio
 *
 * 兼容两种来源：
 * 1. 回路监控：health = LoopApi.LoopDataHealth（validRate/confidenceLevel/pvCompleteness）
 * 2. 测点配置：health = { quality, loopPvCompleteness, loopIntegrityStatus, ... }
 *    调用方将 loopPvCompleteness 映射为 pvCompleteness 传入
 */

interface DataHealthLike {
  /** 预处理：好值率/有效率（0~1） */
  validRate?: null | number;
  /** 回路可信度：A/B/C/D/E */
  confidenceLevel?: LoopApi.ConfidenceLevel | null;
  /** PV 列完整度（0~1） */
  pvCompleteness?: null | number;
  /** 完整性状态：OK/WARNING/CRITICAL/DATA_UNAVAILABLE */
  integrityStatus?: null | string;
  /** 缺失列列表（Tooltip 展示） */
  missingColumns?: null | string[];
  /** 最近巡检日期 */
  lastIntegrityCheck?: null | string;
}

interface Props {
  health?: DataHealthLike | null;
  /** 紧凑模式（测点页表格行高受限时用） */
  compact?: boolean;
  /** 是否显示 PV 完整度徽标（默认 true；回路实时页设 false 避免与 PV 数值列重复） */
  showPvCompleteness?: boolean;
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
    D: { color: '#ea580c', bg: 'rgba(234, 88, 12, 0.12)', label: 'D 警告' },
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
  if (rate >= 0.2) return { color: '#ea580c', bg: 'rgba(234, 88, 12, 0.12)' };
  return { color: 'var(--destructive)', bg: 'hsl(var(--destructive) / 12%)' };
}

function pct(rate: null | number | undefined) {
  if (rate === null || rate === undefined) return null;
  return `${(rate * 100).toFixed(1)}%`;
}

// 完整度状态配色
function integrityTier(status?: null | string, completeness?: null | number) {
  if (status === 'DATA_UNAVAILABLE' || status === 'CRITICAL') {
    return { color: 'var(--destructive)', bg: 'hsl(var(--destructive) / 12%)' };
  }
  if (status === 'WARNING') {
    return { color: 'var(--warning)', bg: 'hsl(var(--warning) / 14%)' };
  }
  if (status === 'OK') {
    return { color: 'var(--success)', bg: 'hsl(var(--success) / 12%)' };
  }
  // 无状态时按完整度值回退判定
  return (
    rateTier(completeness) ?? {
      color: 'var(--muted-foreground)',
      bg: 'hsl(var(--muted) / 12%)',
    }
  );
}

const confMeta = computed(() => {
  const lvl = props.health?.confidenceLevel;
  if (!lvl) return null;
  return CONF_META[lvl] ?? null;
});

const validRateTier = computed(() => rateTier(props.health?.validRate));
const validRateText = computed(() => pct(props.health?.validRate));

const pvTier = computed(() =>
  integrityTier(props.health?.integrityStatus, props.health?.pvCompleteness),
);
const pvText = computed(() => pct(props.health?.pvCompleteness));

/** 是否有任何徽标可显示（PV 徽标受 showPvCompleteness 开关控制） */
const hasAnyBadge = computed(() => {
  if (confMeta.value) return true;
  if (validRateText.value) return true;
  if (props.showPvCompleteness && pvText.value) return true;
  return false;
});

const missingColsText = computed(() => {
  const cols = props.health?.missingColumns;
  if (!cols || cols.length === 0) return null;
  return `缺失列：${cols.join('、')}`;
});

const tooltipText = computed(() => {
  const parts: string[] = [];
  if (props.health?.lastIntegrityCheck) {
    parts.push(`巡检：${props.health.lastIntegrityCheck.slice(0, 10)}`);
  }
  if (missingColsText.value) parts.push(missingColsText.value);
  return parts.length > 0 ? parts.join('；') : null;
});
</script>

<template>
  <Tooltip
    v-if="tooltipText"
    :title="tooltipText"
    placement="top"
    :mouse-enter-delay="0.2"
  >
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
      <span
        v-if="showPvCompleteness && pvText"
        class="clpm-dhb__badge"
        :style="{ color: pvTier.color, background: pvTier.bg }"
        >PV {{ pvText }}</span
      >
      <span v-if="!hasAnyBadge" class="clpm-dhb__empty">—</span>
    </div>
  </Tooltip>
  <div v-else class="clpm-dhb" :class="{ 'clpm-dhb--compact': compact }">
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
    <span
      v-if="pvText"
      class="clpm-dhb__badge"
      :style="{ color: pvTier.color, background: pvTier.bg }"
      >PV {{ pvText }}</span
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
