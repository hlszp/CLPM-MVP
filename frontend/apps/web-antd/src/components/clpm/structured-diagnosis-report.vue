<script lang="ts" setup>
/**
 * P2-01：结构化诊断报告组件
 *
 * 将诊断结果从"有振荡"升级为结构化呈现：
 * - 原因排序：按置信度降序排列，概率条可视化
 * - 根因分析：每个原因的具体根因描述
 * - 建议下一步：可操作的处置建议
 * - 预估改善效果：量化预期提升
 *
 * 对齐 ZL 工业设计规范：Calm UI（低饱和色）+ Glanceability（1 秒扫视抓主因）
 * + Poka-Yoke（建议含具体参数调整方向）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type {
  DiagnosisActionType,
  DiagnosisLabel,
  DiagnosisUrgency,
  StructuredDiagnosisReport,
} from '#/constants/diagnosis';

import { computed, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Collapse, CollapsePanel, Progress, Tag } from 'ant-design-vue';

import { DIAGNOSIS_TERM_EXPLANATIONS } from '#/constants/clpm-ui';
import {
  DIAGNOSIS_ACTION_TYPE_COLOR,
  DIAGNOSIS_ACTION_TYPE_LABEL,
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
  DIAGNOSIS_STRUCTURED_REPORT,
  DIAGNOSIS_URGENCY_COLOR,
  DIAGNOSIS_URGENCY_LABEL,
} from '#/constants/diagnosis';

interface Props {
  /** 诊断标签列表（来自 DiagnosisDetail.diagnosisLabels） */
  labels: DiagnosisApi.DiagnosisLabelItem[];
  /** 融合可信度（0-1），用于报告整体可信度标识 */
  fusedConfidence?: null | number;
  /** 可信度等级 A/B/C/D/E */
  confidenceLevel?: 'A' | 'B' | 'C' | 'D' | 'E' | null;
  /** 是否显示"前往整定"操作按钮 */
  showTuningAction?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  fusedConfidence: null,
  confidenceLevel: null,
  showTuningAction: false,
});

const emit = defineEmits<{
  (e: 'tuning', label: DiagnosisLabel): void;
}>();

/** 动作类型 → 图标映射 */
const ACTION_TYPE_ICON_MAP: Record<DiagnosisActionType, string> = {
  tuning: 'lucide:sliders-horizontal',
  maintenance: 'lucide:wrench',
  investigation: 'lucide:search',
  review: 'lucide:user-check',
};

/** 紧急程度 → 图标映射 */
const URGENCY_ICON_MAP: Record<DiagnosisUrgency, string> = {
  high: 'lucide:alert-triangle',
  medium: 'lucide:alert-circle',
  low: 'lucide:info',
};

/** 组合后的报告项（排序 + 概率 + 结构化数据 + 展示元数据） */
interface ReportItem {
  label: DiagnosisLabel;
  labelName: string;
  labelColor: string;
  confidence: number;
  probability: number;
  report: StructuredDiagnosisReport;
  term: { detail?: string; short?: string; term?: string };
  urgencyColor: string;
  urgencyLabel: string;
  urgencyIcon: string;
  actionTypeColor: string;
  actionTypeLabel: string;
  actionTypeIcon: string;
  isPrimary: boolean;
  evidence: Record<string, unknown>;
  algorithm: string;
}

/** 总置信度（用于概率归一化） */
const totalConfidence = computed(() =>
  props.labels.reduce((sum, item) => sum + item.confidence, 0),
);

/** 概率条颜色（按概率高低渐变） */
function progressColor(pct: number): string {
  if (pct >= 50) return '#ff4d4f'; // 主因 → 红
  if (pct >= 25) return '#faad14'; // 次因 → 橙
  return '#1890ff'; // 低概率 → 蓝
}

/** 组合并排序的报告项列表 */
const reportItems = computed<ReportItem[]>(() => {
  const sorted = [...props.labels].toSorted(
    (a, b) => b.confidence - a.confidence,
  );
  return sorted.map((item, idx) => {
    const report = DIAGNOSIS_STRUCTURED_REPORT[item.label];
    const urgency = report?.urgency ?? 'low';
    const actionType = report?.actionType ?? 'review';
    const pct =
      totalConfidence.value === 0
        ? 0
        : Math.round((item.confidence / totalConfidence.value) * 100);
    return {
      label: item.label,
      labelName: DIAGNOSIS_LABEL_NAME_MAP[item.label] ?? item.label,
      labelColor: DIAGNOSIS_LABEL_COLOR_MAP[item.label] ?? 'default',
      confidence: item.confidence,
      probability: pct,
      report: report ?? {
        cause: '—',
        suggestion: '—',
        improvement: '—',
        actionType: 'review',
        urgency: 'low',
      },
      term: DIAGNOSIS_TERM_EXPLANATIONS[item.label] ?? {},
      urgencyColor: DIAGNOSIS_URGENCY_COLOR[urgency],
      urgencyLabel: DIAGNOSIS_URGENCY_LABEL[urgency],
      urgencyIcon: URGENCY_ICON_MAP[urgency] ?? 'lucide:info',
      actionTypeColor: DIAGNOSIS_ACTION_TYPE_COLOR[actionType],
      actionTypeLabel: DIAGNOSIS_ACTION_TYPE_LABEL[actionType],
      actionTypeIcon: ACTION_TYPE_ICON_MAP[actionType] ?? 'lucide:info',
      isPrimary: idx === 0,
      evidence: item.evidence,
      algorithm: item.algorithm,
    };
  });
});

/** 默认展开第一项 */
const activeKeys = ref<string[]>(['0']);

/** 点击"前往整定" */
function handleTuning(label: DiagnosisLabel) {
  emit('tuning', label);
}
</script>

<template>
  <div class="clpm-structured-diagnosis-report">
    <!-- 原因排序总览 -->
    <div class="mb-4">
      <div
        class="mb-3 flex items-center gap-2 text-sm font-medium"
        style="color: hsl(var(--foreground))"
      >
        <IconifyIcon icon="lucide:list-ordered" :size="16" />
        原因排序
        <span class="text-xs" style="color: hsl(var(--muted-foreground))">
          （按概率降序，归一化为 100%）
        </span>
      </div>
      <div class="space-y-2">
        <div
          v-for="(item, idx) in reportItems"
          :key="idx"
          class="flex items-center gap-3"
        >
          <div class="w-28 shrink-0">
            <Tag :color="item.labelColor" class="m-0">
              {{ item.labelName }}
            </Tag>
          </div>
          <div class="flex-1">
            <Progress
              :percent="item.probability"
              :stroke-color="progressColor(item.probability)"
              :show-info="true"
              size="small"
            />
          </div>
          <div class="flex w-32 shrink-0 items-center gap-1">
            <Tag :color="item.urgencyColor" class="m-0">
              <IconifyIcon :icon="item.urgencyIcon" :size="11" class="mr-0.5" />
              {{ item.urgencyLabel }}
            </Tag>
            <Tag :color="item.actionTypeColor" class="m-0">
              {{ item.actionTypeLabel }}
            </Tag>
          </div>
        </div>
      </div>
    </div>

    <!-- 详细分析（折叠面板） -->
    <Collapse v-model:active-key="activeKeys" :bordered="false">
      <CollapsePanel
        v-for="(item, idx) in reportItems"
        :key="String(idx)"
        :header="`${item.labelName}（${
          item.isPrimary ? '主因' : '次因'
        } ${item.probability}%）`"
      >
        <template #extra>
          <Tag :color="item.labelColor" class="m-0">
            {{ item.labelName }}
          </Tag>
        </template>

        <div class="space-y-3">
          <!-- 根因分析 -->
          <div>
            <div
              class="mb-1 flex items-center gap-1.5 text-xs font-medium"
              style="color: hsl(var(--muted-foreground))"
            >
              <IconifyIcon icon="lucide:search" :size="13" />
              根因分析
            </div>
            <div class="text-sm" style="color: hsl(var(--foreground))">
              {{ item.report.cause }}
            </div>
            <div
              v-if="item.term?.detail"
              class="mt-1 text-xs"
              style="color: hsl(var(--muted-foreground))"
            >
              {{ item.term.detail }}
            </div>
          </div>

          <!-- 建议下一步 -->
          <div>
            <div
              class="mb-1 flex items-center gap-1.5 text-xs font-medium"
              style="color: hsl(var(--muted-foreground))"
            >
              <IconifyIcon :icon="item.actionTypeIcon" :size="13" />
              建议下一步
              <Tag
                :color="item.actionTypeColor"
                class="ml-1 m-0"
                style="font-size: 11px; line-height: 18px"
              >
                {{ item.actionTypeLabel }}
              </Tag>
            </div>
            <div class="text-sm" style="color: hsl(var(--foreground))">
              {{ item.report.suggestion }}
            </div>
          </div>

          <!-- 预估改善效果 -->
          <div
            class="rounded border p-2"
            style="
              background: hsl(var(--muted));
              border-color: hsl(var(--border));
            "
          >
            <div
              class="mb-1 flex items-center gap-1.5 text-xs font-medium"
              style="color: hsl(var(--muted-foreground))"
            >
              <IconifyIcon icon="lucide:trending-up" :size="13" />
              预估改善效果
            </div>
            <div class="text-sm" style="color: hsl(var(--foreground))">
              {{ item.report.improvement }}
            </div>
          </div>

          <!-- 诊断证据（可折叠） -->
          <details v-if="item.evidence" class="text-xs">
            <summary
              class="cursor-pointer"
              style="color: hsl(var(--muted-foreground))"
            >
              诊断证据（{{ item.algorithm }}）
            </summary>
            <pre
              class="mt-1 whitespace-pre-wrap rounded bg-black/5 p-2 text-xs"
              >{{ JSON.stringify(item.evidence, null, 2) }}</pre
            >
          </details>

          <!-- 操作按钮 -->
          <div
            v-if="showTuningAction && item.report.actionType === 'tuning'"
            class="flex justify-end"
          >
            <button
              type="button"
              class="flex items-center gap-1 rounded border border-purple-300 px-3 py-1 text-xs text-purple-600 transition hover:bg-purple-50"
              @click="handleTuning(item.label)"
            >
              <IconifyIcon icon="lucide:sliders-horizontal" :size="13" />
              前往整定工作台
            </button>
          </div>
        </div>
      </CollapsePanel>
    </Collapse>
  </div>
</template>
