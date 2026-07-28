<script lang="ts" setup>
/**
 * F3a 算法价值传递卡片 — label-evidence-card.vue
 *
 * 直观呈现单个诊断标签的算法专业性：
 *  - 标签名 + 可信度等级角标 + 算法版本
 *  - 算法中文名 + 原理说明（2 行截断 + tooltip 全文）
 *  - 关键特征值（按 meta.featureKeys 从 evidence 取值）
 *  - 阈值判定（meta.threshold 配置 + 触发标记）
 *  - 置信度 / 最高标签置信度 / 证据摘要
 *  - 置信度等级释义（A-E 五级）
 *
 * 降级：meta 为空时只显示标签名 + 置信度 + 证据，兼容旧数据。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { Progress, Tag, Tooltip } from 'ant-design-vue';

import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';

defineOptions({ name: 'LabelEvidenceCard' });

const props = withDefaults(
  defineProps<{
    /** 可信度等级 A-E */
    confidenceLevel?: 'A' | 'B' | 'C' | 'D' | 'E' | null;
    /** 详情级最高标签置信度 0~1（同标签多算法融合后的最高值，不再跨标签融合） */
    fusedConfidence?: number;
    /** 单标签诊断结果 */
    item: DiagnosisApi.DiagnosisLabelItem;
    /** 算法元数据（来自 GET /diagnosis/algorithms/meta） */
    meta?: DiagnosisApi.AlgorithmMetaItem;
  }>(),
  {
    confidenceLevel: null,
    fusedConfidence: undefined,
    meta: undefined,
  },
);

const { themeColors } = useClpmTheme();

/** 标签颜色（Ant Tag 颜色名） */
const tagColor = computed(() => {
  if (props.meta?.isEnabled === false) return 'default';
  return DIAGNOSIS_LABEL_COLOR_MAP[props.item.label] || 'default';
});

/** 标签中文名 */
const labelName = computed(
  () => props.item.labelName || getDiagnosisLabelName(props.item.label),
);

/** 可信度等级角标颜色 */
const levelColor = computed<string>(() => {
  switch (props.confidenceLevel) {
    case 'A': {
      return themeColors.value.SUCCESS;
    }
    case 'B': {
      return themeColors.value.INFO;
    }
    case 'C': {
      return themeColors.value.WARNING;
    }
    default: {
      return themeColors.value.DANGER;
    }
  }
});

/** 关键特征值列表（按 meta.featureKeys 从 evidence 取值） */
const featureRows = computed<{ key: string; value: string }[]>(() => {
  const keys = props.meta?.featureKeys ?? [];
  const evidence = (props.item.evidence ?? {}) as Record<string, unknown>;
  const rows: { key: string; value: string }[] = [];
  for (const key of keys) {
    const raw = evidence[key];
    if (raw === undefined || raw === null) continue;
    rows.push({ key, value: formatValue(raw) });
  }
  return rows;
});

/** 阈值配置列表（从 meta.threshold 按 thresholdKeys 取） */
const thresholdRows = computed<{ key: string; value: string }[]>(() => {
  const keys = props.meta?.thresholdKeys ?? [];
  const threshold = props.meta?.threshold ?? {};
  const rows: { key: string; value: string }[] = [];
  for (const key of keys) {
    const raw = threshold[key];
    if (raw === undefined || raw === null) continue;
    rows.push({ key, value: formatValue(raw) });
  }
  return rows;
});

/** 证据摘要（优先取 evidence.reasoning / description） */
const evidenceSummary = computed(() => {
  const evidence = (props.item.evidence ?? {}) as Record<string, unknown>;
  const text = (evidence.reasoning ?? evidence.description ?? '') as string;
  return typeof text === 'string' ? text : '';
});

/** 置信度百分比 */
const confidencePct = computed(() => {
  const v = Number(props.item.confidence);
  return Number.isFinite(v) ? Math.round(v * 100) : 0;
});

/** 最高标签置信度百分比（同标签多算法融合后的最高值，不再跨标签融合） */
const fusedPct = computed(() => {
  if (props.fusedConfidence === undefined) return null;
  const v = Number(props.fusedConfidence);
  return Number.isFinite(v) ? Math.round(v * 100) : null;
});

function formatValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '—';
  if (typeof raw === 'number') {
    return Number.isInteger(raw) ? String(raw) : raw.toFixed(4);
  }
  if (typeof raw === 'string') return raw;
  try {
    return JSON.stringify(raw);
  } catch {
    return String(raw);
  }
}
</script>

<template>
  <ClpmDataCanvas class="label-evidence-card">
    <!-- 头部：标签 + 可信度等级 + 算法版本 -->
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2">
        <Tag :color="tagColor" class="!m-0 !font-medium">{{ labelName }}</Tag>
        <span
          v-if="confidenceLevel"
          class="rounded px-1.5 py-0.5 text-xs font-semibold text-white"
          :style="{ backgroundColor: levelColor }"
        >
          {{ confidenceLevel }} 级可信
        </span>
      </div>
      <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
        {{ item.algorithm || meta?.algorithmVersion || '—' }}
      </span>
    </div>

    <!-- 算法名 + 原理 -->
    <template v-if="meta">
      <div
        class="mt-3 text-sm font-medium"
        :style="{ color: themeColors.INFO }"
      >
        {{ meta.algorithmName }}
      </div>
      <Tooltip :title="meta.principle" placement="topLeft">
        <p
          class="mt-1 line-clamp-2 text-xs leading-relaxed"
          :style="{ color: themeColors.NEUTRAL }"
        >
          {{ meta.principle }}
        </p>
      </Tooltip>
    </template>

    <!-- 两栏：关键特征值 + 阈值判定 -->
    <div
      v-if="featureRows.length > 0 || thresholdRows.length > 0"
      class="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2"
    >
      <div class="rounded border border-solid p-2">
        <div class="mb-1 text-xs font-medium">关键特征值</div>
        <div
          v-for="row in featureRows"
          :key="row.key"
          class="flex justify-between text-xs"
        >
          <span :style="{ color: themeColors.NEUTRAL }">{{ row.key }}</span>
          <span class="font-mono">{{ row.value }}</span>
        </div>
        <div
          v-if="featureRows.length === 0"
          class="text-xs"
          :style="{ color: themeColors.NEUTRAL }"
        >
          暂无
        </div>
      </div>
      <div class="rounded border border-solid p-2">
        <div class="mb-1 flex items-center justify-between text-xs font-medium">
          <span>阈值判定</span>
          <span :style="{ color: themeColors.SUCCESS }">✓ 已触发</span>
        </div>
        <div
          v-for="row in thresholdRows"
          :key="row.key"
          class="flex justify-between text-xs"
        >
          <span :style="{ color: themeColors.NEUTRAL }">{{ row.key }}</span>
          <span class="font-mono">{{ row.value }}</span>
        </div>
        <div
          v-if="thresholdRows.length === 0"
          class="text-xs"
          :style="{ color: themeColors.NEUTRAL }"
        >
          暂无
        </div>
      </div>
    </div>

    <!-- 置信度行 -->
    <div class="mt-3 flex items-center gap-4">
      <div class="flex-1">
        <div class="mb-1 flex justify-between text-xs">
          <span :style="{ color: themeColors.NEUTRAL }">置信度</span>
          <span class="font-mono">{{ confidencePct }}%</span>
        </div>
        <Progress
          :percent="confidencePct"
          :show-info="false"
          size="small"
          :stroke-color="levelColor"
        />
      </div>
      <div v-if="fusedPct !== null" class="flex-1">
        <div class="mb-1 flex justify-between text-xs">
          <span :style="{ color: themeColors.NEUTRAL }">最高标签置信度</span>
          <span class="font-mono">{{ fusedPct }}%</span>
        </div>
        <Progress
          :percent="fusedPct"
          :show-info="false"
          size="small"
          :stroke-color="themeColors.INFO"
        />
      </div>
    </div>

    <!-- 证据摘要 -->
    <p
      v-if="evidenceSummary"
      class="mt-2 text-xs leading-relaxed"
      :style="{ color: themeColors.NEUTRAL }"
    >
      证据：{{ evidenceSummary }}
    </p>

    <!-- 置信度等级释义 -->
    <p
      v-if="meta?.confidenceLevelExplanation"
      class="mt-2 text-xs"
      :style="{ color: themeColors.NEUTRAL, opacity: 0.7 }"
    >
      {{ meta.confidenceLevelExplanation }}
    </p>
  </ClpmDataCanvas>
</template>
