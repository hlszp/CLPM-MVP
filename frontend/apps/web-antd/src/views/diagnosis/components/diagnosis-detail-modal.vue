<script setup lang="ts">
/**
 * 诊断详情弹窗 —— 遮罩模式，点击概览行弹出。
 *
 * 分区呈现（2026-08-18）：
 * ① 回路基本信息（位号/名称/等级/诊断次序/触发方式/诊断时间/复核态）
 * ② 最新性能评估指标（KPI 快照：评分+6 大指标+可信度，latestOnly）
 * ③ 诊断结论 + 证据链（复用 DiagnosisResultPanel：分类卡/症状/证据/建议）
 */
import { computed, ref, watch } from 'vue';

import dayjs from 'dayjs';
import {
  Descriptions,
  DescriptionsItem,
  Empty,
  Modal,
  Skeleton,
  Spin,
  Tag,
} from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { KpiSnapshotItem } from '#/api/metric';
import { getDiagnosisRunDetailApi } from '#/api/diagnosis';
import { getLoopSnapshotsApi } from '#/api/metric';
import {
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  REVIEW_STATUS_COLOR,
  REVIEW_STATUS_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
  scoreGrade,
} from '../constants';
import DiagnosisResultPanel from './diagnosis-result-panel.vue';

const props = defineProps<{
  item: DiagnosisApi.LatestRunItem | null;
}>();

const open = defineModel<boolean>('open', { default: false });

const detailLoading = ref(false);
const runDetail = ref<DiagnosisApi.RunDetail | null>(null);
const kpiLoading = ref(false);
const kpi = ref<KpiSnapshotItem | null>(null);

/** naive UTC → 本地时间 */
function fmtLocal(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso) ? naiveIso : `${naiveIso}Z`;
  return dayjs(withZ).format('YYYY-MM-DD HH:mm');
}

async function load(item: DiagnosisApi.LatestRunItem) {
  // KPI 与诊断详情并行加载
  kpiLoading.value = true;
  kpi.value = null;
  getLoopSnapshotsApi({ loopId: item.loopId, latestOnly: true, pageSize: 1 })
    .then((res) => {
      kpi.value = res.items?.[0] ?? null;
    })
    .catch(() => {
      kpi.value = null;
    })
    .finally(() => {
      kpiLoading.value = false;
    });

  if (!item.runId) {
    runDetail.value = null;
    detailLoading.value = false;
    return;
  }
  detailLoading.value = true;
  runDetail.value = null;
  try {
    runDetail.value = await getDiagnosisRunDetailApi(item.runId);
  } catch {
    runDetail.value = null;
  } finally {
    detailLoading.value = false;
  }
}

watch(open, (v) => {
  if (v && props.item) {
    load(props.item);
  }
});

const grade = computed(() => scoreGrade(kpi.value?.score));

const kpiMetrics = computed(() => {
  const k = kpi.value;
  if (!k) return [];
  return [
    { label: '优良值率', value: k.goodValueRate, unit: '%' },
    { label: '自控率', value: k.autoModeRate, unit: '%' },
    { label: '有效自控率', value: k.effectiveAutoRate, unit: '%' },
    { label: '稳定率', value: k.steadyRate, unit: '%' },
    { label: '准确率', value: k.accuracyRate, unit: '%' },
    { label: '快速率', value: k.fastRate, unit: '%' },
    { label: '振荡率', value: k.oscillationRate, unit: '%' },
    { label: '饱和率', value: k.saturationRate, unit: '%' },
    { label: '仪表故障率', value: k.instrumentFaultRate, unit: '%' },
  ].filter((m) => m.value != null);
});
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`诊断详情 · ${item?.loopTagName ?? ''}`"
    :footer="null"
    width="960"
    wrap-class-name="diag-detail-modal"
  >
    <div v-if="item" class="space-y-4">
      <!-- ① 回路基本信息 -->
      <Descriptions :column="4" bordered size="small" title="回路基本信息">
        <DescriptionsItem label="回路位号">
          {{ item.loopTagName }}
        </DescriptionsItem>
        <DescriptionsItem label="回路名称">
          {{ item.loopDescription || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="回路等级">
          <span
            v-if="item.importanceLevel"
            :style="{ color: IMPORTANCE_LEVEL_COLOR[item.importanceLevel] }"
          >
            {{ IMPORTANCE_LEVEL_TEXT[item.importanceLevel] }}
          </span>
          <span v-else>—</span>
        </DescriptionsItem>
        <DescriptionsItem label="诊断次序">
          {{ item.runCount ? `第 ${item.runCount} 次` : '未诊断' }}
        </DescriptionsItem>
        <DescriptionsItem label="触发方式">
          <span
            v-if="item.triggerType"
            :style="{ color: TRIGGER_TYPE_COLOR[item.triggerType] }"
          >
            {{ item.triggerTypeLabel ?? TRIGGER_TYPE_TEXT[item.triggerType] }}
          </span>
          <span v-else>—</span>
        </DescriptionsItem>
        <DescriptionsItem label="诊断时间">
          {{ fmtLocal(item.lastDiagnosedAt) }}
        </DescriptionsItem>
        <DescriptionsItem label="复核状态">
          <span
            v-if="item.reviewStatus"
            :style="{ color: REVIEW_STATUS_COLOR[item.reviewStatus] }"
          >
            {{ REVIEW_STATUS_TEXT[item.reviewStatus] }}
            <template v-if="item.reviewStatus === 'REVIEWED' && item.reviewedBy">
              （{{ item.reviewedBy }}）
            </template>
          </span>
          <span v-else>—</span>
        </DescriptionsItem>
        <DescriptionsItem label="诊断状态">
          {{ item.status ?? '—' }}
        </DescriptionsItem>
      </Descriptions>

      <!-- ② 最新性能评估指标 -->
      <div>
        <div class="diag-detail-section-title">最新性能评估指标</div>
        <Skeleton v-if="kpiLoading" :paragraph="{ rows: 2 }" active />
        <Empty
          v-else-if="!kpi"
          description="暂无性能评估数据（该回路尚未生成 KPI 快照）"
        />
        <div v-else class="diag-detail-kpi">
          <div class="diag-detail-kpi__score">
            <div class="text-xs text-neutral-400">综合评分</div>
            <div
              class="text-3xl font-semibold tabular-nums"
              :style="{ color: grade?.color }"
            >
              {{ kpi.score != null ? kpi.score.toFixed(1) : '—' }}
            </div>
            <Tag v-if="grade" :color="grade.color" style="margin: 0">
              {{ grade.label }}
            </Tag>
          </div>
          <div class="diag-detail-kpi__grid">
            <div v-for="m in kpiMetrics" :key="m.label" class="diag-detail-kpi__item">
              <span class="diag-detail-kpi__label">{{ m.label }}</span>
              <span class="diag-detail-kpi__value tabular-nums">
                {{ m.value }}{{ m.unit }}
              </span>
            </div>
          </div>
          <div class="diag-detail-kpi__meta">
            可信度：{{ kpi.confidenceLevel ?? '—' }} · 评估窗口：{{
              fmtLocal(kpi.tsStart)
            }}
            ~ {{ fmtLocal(kpi.tsEnd) }}
          </div>
        </div>
      </div>

      <!-- ③ 诊断结论 + 证据链 -->
      <div>
        <div class="diag-detail-section-title">诊断结论与证据</div>
        <Empty v-if="!item.runId" description="该回路尚未诊断" />
        <Spin v-else-if="detailLoading" class="block py-6" />
        <DiagnosisResultPanel v-else-if="runDetail" :detail="runDetail" />
        <Empty v-else description="诊断详情加载失败" />
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.diag-detail-section-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground) / 80%);
}

.diag-detail-kpi {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  background: hsl(var(--accent) / 30%);
  border-radius: 8px;
}

.diag-detail-kpi__score {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
  justify-content: center;
  min-width: 120px;
  padding: 8px 16px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.diag-detail-kpi__grid {
  display: grid;
  flex: 1;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 6px 12px;
}

.diag-detail-kpi__item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  justify-content: space-between;
  min-width: 0;
  font-size: 12px;
}

.diag-detail-kpi__label {
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-detail-kpi__value {
  font-weight: 500;
}

.diag-detail-kpi__meta {
  width: 100%;
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 45%);
}
</style>
