<script lang="ts" setup>
/**
 * 工作台 R5 证据区 · 诊断卡（2026-08-19）
 *
 * 页面标杆设计 01 v1.6-v1.8「R5 诊断卡」落地：
 * - 结论行：主分类 Tag（severity 着色）+ 置信度 + 复核状态
 * - 率类负向指标（振荡率/坏值率/饱和率/粘滞系数）：WorkbenchMetricBars
 *   negative 模式（长=差）
 * - 非率类（稳定时间 s / 行程指数）：底部紧凑文本行原值透传
 * - 未诊断：占位引导「发起诊断」（保持 R5 三卡布局稳定不跳动）
 *
 * 口径：metricSummary（诊断时间窗 KPI 均值 + 算子特征兜底，0~100 统一，
 * 替代标杆 v1.6 的"KPI 快照直取"口径）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { Button, Tag, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import { SEVERITY_COLOR } from '../../diagnosis/constants';
import WorkbenchMetricBars from './workbench-metric-bars.vue';

defineOptions({ name: 'WorkbenchDiagnosisCard' });

const props = defineProps<Props>();

const emit = defineEmits<{ diagnose: [] }>();

interface Props {
  /** 最新诊断概览（null=未诊断或未加载） */
  item: DiagnosisApi.LatestRunItem | null;
}

/** 有诊断记录（runId 非空） */
const hasRun = computed(() => props.item?.runId != null);

/** naive UTC ISO 补 Z 转本地时区（与诊断结果面板同口径） */
function toLocal(iso: null | string | undefined, tpl: string): string {
  if (!iso) return '—';
  const s = /[Zz]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return dayjs(s).format(tpl);
}

/** 诊断时间（header meta，父级消费） */
const timeText = computed(() => toLocal(props.item?.lastDiagnosedAt, 'MM-DD HH:mm'));

defineExpose({ timeText });

/** 率类负向指标横条（0~100，长=差；按危害度排序：振荡→坏值→饱和→粘滞） */
const negativeBars = computed(() => {
  const neg = props.item?.metricSummary?.negative;
  if (!neg) return [];
  const defs: Array<{
    key: keyof DiagnosisApi.MetricSummary['negative'];
    name: string;
  }> = [
    { key: 'oscillationRate', name: '振荡率' },
    { key: 'badValueRate', name: '坏值率' },
    { key: 'saturationRate', name: '饱和率' },
    { key: 'stictionIndex', name: '粘滞系数' },
  ];
  return defs
    .filter((d) => neg[d.key] != null)
    .map((d) => ({ name: d.name, value: Number(neg[d.key]) }));
});

/** 非率类指标紧凑文本（原值透传，按各自单位展示） */
const extraText = computed(() => {
  const neg = props.item?.metricSummary?.negative;
  if (!neg) return '';
  const parts: string[] = [];
  if (neg.settlingTime != null)
    parts.push(`稳定时间 ${Number(neg.settlingTime).toFixed(1)} s`);
  if (neg.outputTravelIndex != null)
    parts.push(`行程指数 ${Number(neg.outputTravelIndex).toFixed(1)}`);
  return parts.join(' · ');
});

const confidenceText = computed(() => {
  const c = props.item?.primaryConfidence;
  return c == null ? '—' : `${Math.round(c * 100)}%`;
});

const isReviewed = computed(() => props.item?.reviewStatus === 'REVIEWED');
</script>

<template>
  <div class="wb-diag">
    <!-- 有诊断记录 -->
    <template v-if="hasRun">
      <div class="wb-diag__conclusion">
        <Tag
          :color="SEVERITY_COLOR[item?.severity ?? ''] ?? 'default'"
          class="wb-diag__cat"
        >
          {{ item?.primaryCategoryLabel ?? '—' }}
        </Tag>
        <Tooltip title="主分类融合置信度">
          <span class="wb-diag__conf">置信度 {{ confidenceText }}</span>
        </Tooltip>
        <span
          class="wb-diag__review"
          :class="{ 'wb-diag__review--done': isReviewed }"
        >
          {{ isReviewed ? '已复核' : '待复核' }}
        </span>
      </div>
      <div class="wb-diag__bars">
        <WorkbenchMetricBars :metrics="negativeBars" negative />
      </div>
      <div v-if="extraText" class="wb-diag__extra">{{ extraText }}</div>
    </template>
    <!-- 未诊断：占位引导 -->
    <div v-else class="wb-diag__empty">
      <span class="wb-diag__empty-text">该回路尚未诊断</span>
      <Button size="small" type="primary" ghost @click="emit('diagnose')">
        发起诊断
      </Button>
    </div>
  </div>
</template>

<style scoped>
.wb-diag {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 8px;
}

/* 结论行：分类 Tag + 置信度 + 复核状态 */
.wb-diag__conclusion {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 2px;
}

.wb-diag__cat {
  margin-right: 0;
  font-size: 11px;
  line-height: 18px;
}

.wb-diag__conf {
  font-size: 11px;
  color: hsl(var(--foreground) / 65%);
  cursor: help;
}

/* 复核状态：待复核=橙（需人工介入）、已复核=绿（闭环） */
.wb-diag__review {
  margin-left: auto;
  font-size: 10px;
  color: #ea580c;
}

.wb-diag__review--done {
  color: #16a34a;
}

/* 横条区 */
.wb-diag__bars {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

/* 非率类紧凑文本行 */
.wb-diag__extra {
  flex: 0 0 auto;
  font-size: 10px;
  color: hsl(var(--foreground) / 50%);
  text-align: right;
}

/* 未诊断空态 */
.wb-diag__empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
}

.wb-diag__empty-text {
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}
</style>
