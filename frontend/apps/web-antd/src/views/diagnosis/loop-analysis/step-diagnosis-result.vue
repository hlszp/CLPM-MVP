<script lang="ts" setup>
/**
 * F2g Step 3 — 诊断触发与结果分析
 *
 * 触发 POST /diagnosis/trigger → 轮询 → 渲染：
 *  1. 标签卡片网格（label-evidence-card × N，算法价值传递）
 *  2. 推理过程时间线（reasoning-timeline）
 *  3. 10 类可视化（按 meta.visualizationKey 映射 diagnosis-visualization 组件）
 *  4. 推荐方案列表
 */
import type { Component } from 'vue';

import type { UseLoopAnalysisReturn } from './use-loop-analysis';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { Alert, Button, Empty, Spin, Tag } from 'ant-design-vue';

import { ClpmDataCanvas } from '#/components/clpm';
import ChoudhuryCard from '#/components/diagnosis-visualization/choudhury-card.vue';
import CusumChart from '#/components/diagnosis-visualization/cusum-chart.vue';
import IaeCard from '#/components/diagnosis-visualization/iae-card.vue';
import KanoCard from '#/components/diagnosis-visualization/kano-card.vue';
import QualityTimelineChart from '#/components/diagnosis-visualization/quality-timeline-chart.vue';
import SaturationChart from '#/components/diagnosis-visualization/saturation-chart.vue';
import ScatterChart from '#/components/diagnosis-visualization/scatter-chart.vue';
import SlowResponseCard from '#/components/diagnosis-visualization/slow-response-card.vue';
import SpectrumChart from '#/components/diagnosis-visualization/spectrum-chart.vue';
import StepResponseChart from '#/components/diagnosis-visualization/step-response-chart.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';

import LabelEvidenceCard from './label-evidence-card.vue';
import ReasoningTimeline from './reasoning-timeline.vue';

defineOptions({ name: 'StepDiagnosisResult' });

const props = defineProps<{
  state: UseLoopAnalysisReturn;
}>();

const emit = defineEmits<{
  next: [];
}>();

const { themeColors } = useClpmTheme();

const isRunning = computed(
  () =>
    props.state.diag.status === 'PENDING' ||
    props.state.diag.status === 'RUNNING',
);

const isFailed = computed(
  () =>
    props.state.diag.status === 'FAILED' ||
    props.state.diag.status === 'CANCELLED',
);

const isSuccess = computed(
  () => props.state.diag.status === 'SUCCESS' && !!props.state.diag.detail,
);

/** INCONCLUSIVE：可信度等级 E */
const isInconclusive = computed(
  () => props.state.diag.detail?.confidenceLevel === 'E',
);

/** 标签列表 */
const labels = computed<DiagnosisApi.DiagnosisLabelItem[]>(
  () => props.state.diag.detail?.diagnosisLabels ?? [],
);

/** 算法元数据按 label 索引 */
const metaByLabel = computed<Record<string, DiagnosisApi.AlgorithmMetaItem>>(
  () => {
    const map: Record<string, DiagnosisApi.AlgorithmMetaItem> = {};
    for (const item of props.state.algorithmMeta?.items ?? []) {
      map[item.label] = item;
    }
    return map;
  },
);

/** 可视化项映射（key → 组件 + 标题 + 数据） */
const visualizationBlocks = computed<
  {
    component: Component;
    data: unknown;
    disabled?: boolean;
    key: string;
    title: string;
  }[]
>(() => {
  const v = props.state.diag.visualization;
  if (!v) return [];
  return [
    {
      component: SpectrumChart,
      data: v.spectrum,
      key: 'spectrum',
      title: 'FFT 频谱分析',
    },
    {
      component: StepResponseChart,
      data: v.stepResponse,
      key: 'stepResponse',
      title: '阶跃响应',
    },
    {
      component: CusumChart,
      data: v.cusumAnalysis,
      key: 'cusumAnalysis',
      title: 'CUSUM 累积和',
    },
    {
      component: ScatterChart,
      data: v.scatterPlot,
      key: 'scatterPlot',
      title: 'PV-OP 散点图',
    },
    {
      component: QualityTimelineChart,
      data: v.qualityTimeline,
      key: 'qualityTimeline',
      title: '质量码时序',
    },
    {
      component: SaturationChart,
      data: v.saturationAnalysis,
      key: 'saturationAnalysis',
      title: 'OP 饱和分析',
    },
    {
      component: SlowResponseCard,
      data: v.slowResponse,
      key: 'slowResponse',
      title: '响应迟缓分析',
    },
    {
      component: ChoudhuryCard,
      data: v.choudhury,
      key: 'choudhury',
      title: 'Choudhury 非线性检测',
    },
    {
      component: IaeCard,
      data: v.iaeAnalysis,
      key: 'iaeAnalysis',
      title: 'IAE 零交叉分析',
    },
    { component: KanoCard, data: v.kano, key: 'kano', title: 'Kano 统计法' },
  ];
});

/** 推荐方案按 priority 排序 */
const sortedRecommendations = computed(() =>
  [...props.state.diag.recommendations].toSorted(
    (a, b) => a.priority - b.priority,
  ),
);

function handleNext() {
  emit('next');
}

/** P3-01：暴露 refresh() 给 loop-analysis.vue 调用（诊断结果纯基于 state，refresh 空实现保持接口一致） */
function refresh() {
  /* 诊断可视化与标签完全基于 state，无需显式刷新 */
}

defineExpose({ refresh });
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 触发按钮 + 状态 -->
    <ClpmDataCanvas title="诊断分析">
      <div class="flex items-center gap-4">
        <Button
          type="primary"
          :loading="isRunning"
          :disabled="isRunning"
          @click="state.triggerDiagnosis()"
        >
          {{ isSuccess ? '重新诊断' : '触发诊断' }}
        </Button>
        <span
          v-if="isRunning"
          class="text-sm"
          :style="{ color: themeColors.INFO }"
        >
          诊断中...
        </span>
        <span
          v-else-if="state.diag.status"
          class="text-sm"
          :style="{
            color: isSuccess ? themeColors.SUCCESS : themeColors.NEUTRAL,
          }"
        >
          {{ state.diag.status === 'SUCCESS' ? '诊断完成' : state.diag.status }}
        </span>
      </div>
    </ClpmDataCanvas>

    <!-- 失败提示 -->
    <Alert
      v-if="isFailed"
      type="error"
      show-icon
      message="诊断未成功完成"
      :description="state.diag.errorMessage || '请检查数据完整性后重试'"
    />

    <!-- INCONCLUSIVE 提示 -->
    <Alert
      v-if="isSuccess && isInconclusive"
      type="warning"
      show-icon
      message="数据不足（INCONCLUSIVE）"
      description="当前时间窗数据完整率过低，诊断结果可信度不足。建议先在「回路管理 - 历史数据导入」补齐数据后再诊断。"
    />

    <!-- 诊断结果 -->
    <Spin :spinning="isRunning">
      <template v-if="isSuccess && state.diag.detail">
        <!-- 标签卡片网格 -->
        <ClpmDataCanvas title="诊断标签与算法证据" class="mb-4">
          <div v-if="labels.length === 0" class="py-4">
            <Empty
              description="未识别异常标签"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
            />
          </div>
          <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2">
            <LabelEvidenceCard
              v-for="item in labels"
              :key="item.label"
              :item="item"
              :meta="metaByLabel[item.label]"
              :fused-confidence="state.diag.detail?.fusedConfidence"
              :confidence-level="state.diag.detail?.confidenceLevel ?? null"
            />
          </div>
        </ClpmDataCanvas>

        <!-- 推理过程时间线 -->
        <ReasoningTimeline
          :waveform="state.diag.waveform"
          :labels="labels"
          :meta="state.algorithmMeta"
          class="mb-4"
        />

        <!-- 10 类可视化 -->
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
          <ClpmDataCanvas
            v-for="block in visualizationBlocks"
            :key="block.key"
            :title="block.title"
          >
            <component
              :is="block.component"
              :data="block.data"
              :disabled="false"
            />
          </ClpmDataCanvas>
        </div>

        <!-- 推荐方案 -->
        <ClpmDataCanvas
          v-if="sortedRecommendations.length > 0"
          title="处置建议"
          class="mt-4"
        >
          <div class="flex flex-col gap-3">
            <div
              v-for="(rec, idx) in sortedRecommendations"
              :key="idx"
              class="rounded border border-solid p-3"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="font-medium">{{ rec.labelName }}</span>
                <Tag color="blue">优先级 {{ rec.priority }}</Tag>
              </div>
              <div class="mt-1 text-sm" :style="{ color: themeColors.INFO }">
                {{ rec.action }}
              </div>
              <p class="mt-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
                {{ rec.description }}
              </p>
            </div>
          </div>
        </ClpmDataCanvas>

        <div class="flex justify-end">
          <Button type="primary" @click="handleNext">下一步：A/B 对比</Button>
        </div>
      </template>
    </Spin>
  </div>
</template>
