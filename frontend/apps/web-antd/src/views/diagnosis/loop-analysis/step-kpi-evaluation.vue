<script lang="ts" setup>
/**
 * F2f Step 2 — KPI 评估触发与结果展示
 *
 * 触发 POST /tasks/custom/evaluate → 轮询 → 渲染综合评分卡 + 12 KPI 条 + 评估详情 Drawer
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { UseLoopAnalysisReturn } from './use-loop-analysis';

import type { TaskApi } from '#/api/task';
import type { KpiStripItem } from '#/components/clpm';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, Descriptions, Drawer, Spin } from 'ant-design-vue';

import { ClpmDataCanvas, ClpmKpiCard, ClpmKpiStrip } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

import { METRIC_DISPLAY } from './use-loop-analysis';

defineOptions({ name: 'StepKpiEvaluation' });

const props = defineProps<{
  state: UseLoopAnalysisReturn;
}>();

const emit = defineEmits<{
  next: [];
}>();

const { themeColors } = useClpmTheme();

/** TaskResultItem 字段名（camelCase）→ metric code 映射 */
const RESULT_FIELD_MAP: Record<string, string> = {
  accuracyRate: 'accuracy_rate',
  autoModeRate: 'auto_mode_rate',
  effectiveAutoRate: 'effective_auto_rate',
  fastRate: 'fast_rate',
  goodValueRate: 'good_value_rate',
  idealSettlingTime: 'ideal_settling_time',
  oscillationRate: 'oscillation_rate',
  outputTripIndex: 'output_trip_index',
  saturationRate: 'saturation_rate',
  settlingTime: 'settling_time',
  stabilityRate: 'stability_rate',
  stictionIndex: 'stiction_index',
};

const detailVisible = ref(false);

const result = computed<null | TaskApi.TaskResultItem>(() => {
  return props.state.kpi.results[0] ?? null;
});

const isRunning = computed(
  () =>
    props.state.kpi.status === 'PENDING' ||
    props.state.kpi.status === 'RUNNING',
);

const isFailed = computed(
  () =>
    props.state.kpi.status === 'FAILED' ||
    props.state.kpi.status === 'CANCELLED',
);

const isSuccess = computed(
  () => props.state.kpi.status === 'SUCCESS' && !!result.value,
);

/** 综合评分 */
const compositeScore = computed(() => result.value?.score ?? null);

/** 评分状态色 */
const scoreStatus = computed<'error' | 'info' | 'neutral' | 'ok' | 'warning'>(
  () => {
    const s = Number(compositeScore.value);
    if (!Number.isFinite(s)) return 'neutral';
    if (s >= 80) return 'ok';
    if (s >= 60) return 'info';
    if (s >= 40) return 'warning';
    return 'error';
  },
);

/** 12 KPI 条目 */
const kpiStripItems = computed<KpiStripItem[]>(() => {
  const r = result.value;
  if (!r) return [];
  const items: KpiStripItem[] = [];
  for (const [field, code] of Object.entries(RESULT_FIELD_MAP)) {
    const display = METRIC_DISPLAY[code];
    if (!display) continue;
    const raw = (r as unknown as Record<string, unknown>)[field];
    const num = Number(raw);
    items.push({
      key: code,
      label: display.label,
      status: 'neutral',
      unit: display.unit,
      value: Number.isFinite(num) ? num : '—',
    });
  }
  return items;
});

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 雷达图：3 核心指标 */
function renderRadar() {
  const r = result.value;
  if (!r) return;
  renderEcharts({
    radar: {
      indicator: [
        { max: 100, name: '准确率' },
        { max: 100, name: '快速率' },
        { max: 100, name: '平稳率' },
      ],
    },
    series: [
      {
        areaStyle: { opacity: 0.2 },
        data: [
          {
            name: '核心指标',
            value: [r.accuracyRate ?? 0, r.fastRate ?? 0, r.steadyRate ?? 0],
          },
        ],
        type: 'radar',
      },
    ],
  });
}

function handleViewDetail() {
  detailVisible.value = true;
}

function handleNext() {
  emit('next');
}

// 结果变化时重绘雷达图
watch(
  () => props.state.kpi.results,
  () => {
    if (isSuccess.value) {
      nextTick(renderRadar);
    }
  },
  { deep: true },
);
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 触发按钮 + 状态 -->
    <ClpmDataCanvas title="KPI 评估">
      <div class="flex items-center gap-4">
        <Button
          type="primary"
          :loading="isRunning"
          :disabled="isRunning"
          @click="state.triggerKpiEvaluation()"
        >
          {{ isSuccess ? '重新评估' : '触发评估' }}
        </Button>
        <span
          v-if="isRunning"
          class="text-sm"
          :style="{ color: themeColors.INFO }"
        >
          评估中{{
            state.kpi.currentStage ? `（${state.kpi.currentStage}）` : '...'
          }}
          {{
            state.kpi.progress > 0
              ? `${Math.round(state.kpi.progress * 100)}%`
              : ''
          }}
        </span>
        <span
          v-else-if="state.kpi.status"
          class="text-sm"
          :style="{
            color: isSuccess ? themeColors.SUCCESS : themeColors.NEUTRAL,
          }"
        >
          {{ state.kpi.status === 'SUCCESS' ? '评估完成' : state.kpi.status }}
        </span>
      </div>
    </ClpmDataCanvas>

    <!-- 失败提示 -->
    <Alert
      v-if="isFailed"
      type="error"
      show-icon
      message="评估未成功完成"
      :description="state.kpi.errorMessage || '请检查数据完整性后重试'"
    />

    <!-- 评估结果 -->
    <template v-if="isSuccess && result">
      <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <ClpmKpiCard
          title="综合评分"
          :value="compositeScore ?? '—'"
          unit="分"
          :status="scoreStatus"
          icon="lucide:gauge"
          :precision="1"
        />
        <ClpmKpiCard
          title="可信度等级"
          :value="result.confidenceLevel || '—'"
          status="info"
          icon="lucide:shield-check"
          :context-text="
            result.validRate !== null && result.validRate !== undefined
              ? `有效数据率 ${Math.round(Number(result.validRate) * 100)}%`
              : ''
          "
        />
        <ClpmKpiCard
          title="算法版本"
          :value="result.algorithmVersion || '—'"
          status="neutral"
          icon="lucide:code-branch"
        />
      </div>

      <ClpmKpiStrip :items="kpiStripItems" />

      <ClpmDataCanvas title="核心指标雷达">
        <EchartsUI ref="chartRef" height="320px" />
      </ClpmDataCanvas>

      <div class="flex items-center justify-between">
        <Button @click="handleViewDetail">查看评估详情</Button>
        <Button type="primary" @click="handleNext">下一步：诊断分析</Button>
      </div>
    </template>

    <!-- 评估详情 Drawer -->
    <Drawer
      v-model:open="detailVisible"
      title="评估详情"
      width="60%"
      placement="right"
    >
      <Spin :spinning="false">
        <Descriptions v-if="result" :column="2" bordered size="small">
          <Descriptions.Item label="回路">{{
            result.loopTagName
          }}</Descriptions.Item>
          <Descriptions.Item label="时间窗">
            {{ result.tsStart || '—' }} ~ {{ result.tsEnd || '—' }}
          </Descriptions.Item>
          <Descriptions.Item label="综合评分">{{
            result.score ?? '—'
          }}</Descriptions.Item>
          <Descriptions.Item label="可信度等级">{{
            result.confidenceLevel || '—'
          }}</Descriptions.Item>
          <Descriptions.Item label="准确率"
            >{{ result.accuracyRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="快速率"
            >{{ result.fastRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="平稳率"
            >{{ result.steadyRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="有效自控率"
            >{{ result.effectiveAutoRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="良值率"
            >{{ result.goodValueRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="振荡率"
            >{{ result.oscillationRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="饱和率"
            >{{ result.saturationRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="自控率"
            >{{ result.autoModeRate ?? '—' }}%</Descriptions.Item
          >
          <Descriptions.Item label="粘滞指数">{{
            result.stictionIndex ?? '—'
          }}</Descriptions.Item>
          <Descriptions.Item label="输出跳变指数">{{
            result.outputTripIndex ?? '—'
          }}</Descriptions.Item>
          <Descriptions.Item label="稳定时间"
            >{{ result.settlingTime ?? '—' }} s</Descriptions.Item
          >
          <Descriptions.Item label="理想稳定时间"
            >{{ result.idealSettlingTime ?? '—' }} s</Descriptions.Item
          >
          <Descriptions.Item label="有效数据率">
            {{
              result.validRate !== null && result.validRate !== undefined
                ? `${Math.round(Number(result.validRate) * 100)}%`
                : '—'
            }}
          </Descriptions.Item>
          <Descriptions.Item label="采样频率">{{
            result.samplingFreq || '—'
          }}</Descriptions.Item>
        </Descriptions>
      </Spin>
    </Drawer>
  </div>
</template>
