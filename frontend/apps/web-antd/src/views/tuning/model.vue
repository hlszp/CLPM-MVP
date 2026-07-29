<script lang="ts" setup>
/**
 * S7-TUNE-002 模型辨识页（Phase 2 重构）
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5 + 实现契约 v2.1
 * - 顶部筛选表单：回路选择 / 时间范围 / 辨识策略 / 候选模型阶次
 * - 中部进度区：异步任务进度条（按阶段细粒度更新）+ 可辨识片段预览
 * - 结果区：主模型参数 + 可信度徽章 + 候选模型对比卡片 + ECharts 拟合曲线
 * - 底部操作区：使用此模型进行整定 → 跳转 /tuning/algorithm
 *
 * Phase 2 变更：
 * - 辨识策略 AUTO/HISTORY_ONLY/STEP_ONLY（原仅阶跃实验）
 * - 异步任务化（原同步阻塞）
 * - 多候选模型并行辨识（原单一模型）
 * - 可信度等级 A/B/C/D/E（原仅拟合度）
 * - INCONCLUSIVE 时显示阶跃实验兜底引导
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, nextTick, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  CollapsePanel,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  InputNumber,
  message,
  Progress,
  Select,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { identifyModelApi, previewSegmentsApi } from '#/api/tuning';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import ConfidenceBadge from '#/components/metric/confidence-badge.vue';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'TuningModel' });

const router = useRouter();
const tuningStore = useTuningStore();
const { isDark, themeColors } = useClpmTheme();
const { canEditAdvancedParams } = useClpmRoles();

const loading = ref(false);
const segmentLoading = ref(false);
const riskConfirmed = ref(false);

/** 阶跃实验路径结果（STEP_ONLY 策略，同步返回） */
const stepResult = ref<null | TuningApi.IdentifyResult>(null);

/** 历史辨识结果（AUTO/HISTORY_ONLY 策略，异步任务完成后写入 store） */
const historyResult = computed(() => tuningStore.identifyResult);

/** 异步任务进度 */
const taskProgress = computed(() => tuningStore.taskProgress);

/** 可辨识片段预览 */
const segments = ref<null | TuningApi.IdentifySegmentsResult>(null);

/** 筛选表单状态 */
const filter = reactive({
  identifyStrategy: 'AUTO' as TuningApi.IdentifyStrategy,
  candidateModelTypes: ['FOPDT', 'SOPDT'] as TuningApi.HistoryModelType[],
  thetaEstimate: undefined as number | undefined,
  // STEP_ONLY 路径仍用 modelType + method
  modelType: 'FOPDT' as TuningApi.ModelType,
  method: 'TWO_POINT' as TuningApi.IdentifyMethod,
});

/** P1-021：回路与时间窗由 flow 统一上下文头选择，store 代理 */
const loopId = computed(() => tuningStore.currentLoopId);
const timeRange = computed<[dayjs.Dayjs, dayjs.Dayjs]>(() => {
  const r = tuningStore.currentLoopTimeRange;
  if (r && r[0] && r[1]) {
    return [dayjs(r[0]), dayjs(r[1])] as [dayjs.Dayjs, dayjs.Dayjs];
  }
  return [dayjs().subtract(24, 'hour'), dayjs()] as [dayjs.Dayjs, dayjs.Dayjs];
});

/** 辨识策略选项 */
const strategyOptions: { label: string; value: TuningApi.IdentifyStrategy }[] =
  [
    {
      label: '自动（优先历史数据，失败兜底阶跃实验）',
      value: 'AUTO',
    },
    { label: '仅历史数据辨识', value: 'HISTORY_ONLY' },
    { label: '仅阶跃实验（同步）', value: 'STEP_ONLY' },
  ];

/** 阶跃实验模型类型选项（保留 IPDT） */
const modelTypeOptions: { label: string; value: TuningApi.ModelType }[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
  { label: 'IPDT 积分加纯滞后', value: 'IPDT' },
];

/** 历史辨识候选类型（当前转换链仅支持 FOPDT/SOPDT） */
const historyModelTypeOptions: {
  label: string;
  value: TuningApi.HistoryModelType;
}[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
];

/** 辨识方法选项（仅 STEP_ONLY 路径使用） */
const methodOptions: { label: string; value: TuningApi.IdentifyMethod }[] = [
  { label: '两点法', value: 'TWO_POINT' },
  { label: '面积法', value: 'AREA' },
  { label: '组合法', value: 'COMBINED' },
];

/** 是否为阶跃实验路径 */
const isStepOnly = computed(() => filter.identifyStrategy === 'STEP_ONLY');

/** 统一结果形状（step / history 两条路径归一化） */
interface NormalizedResult {
  modelType: string;
  params: TuningApi.ModelParams | Record<string, any> | null;
  fittingScore: number;
  algorithmVersion: string;
  dataPoints: number;
  fittedCurve?: { fitted: number[]; pv: number[]; timestamps: number[] } | null;
}

/** 当前结果（统一适配 stepResult / historyResult） */
const currentResult = computed<NormalizedResult | null>(() => {
  if (isStepOnly.value) {
    return stepResult.value
      ? {
          modelType: stepResult.value.modelType,
          params: stepResult.value.params,
          fittingScore: stepResult.value.fittingScore,
          algorithmVersion: stepResult.value.algorithmVersion,
          dataPoints: stepResult.value.dataPoints,
          fittedCurve: stepResult.value.fittedCurve,
        }
      : null;
  }
  // 历史辨识路径
  const h = historyResult.value;
  if (!h) return null;
  return {
    modelType: h.modelType ?? '',
    params: h.params ?? null,
    fittingScore: h.fittingScore ?? 0,
    algorithmVersion: h.algorithmVersion ?? '',
    dataPoints: h.dataPoints ?? 0,
    fittedCurve: null, // 历史辨识路径无拟合曲线
  };
});

/** 候选模型列表（历史辨识路径） */
const candidateModels = computed(
  () => historyResult.value?.candidateModels || [],
);

/** 是否 INCONCLUSIVE */
const isInconclusive = computed(
  () =>
    !isStepOnly.value &&
    historyResult.value !== null &&
    (historyResult.value?.confidenceLevel === 'INCONCLUSIVE' ||
      historyResult.value?.success === false),
);

interface ModelUsageGate {
  blockedReason: null | string;
  modelSource: null | TuningApi.ModelSource;
  requiresRiskConfirmation: boolean;
}

/**
 * 依据服务端可审计字段进行 fail-closed 门禁。
 * 页面仅控制入口呈现，服务端仍需按 sourceRecordId 重新读取记录并复核。
 */
const modelUsageGate = computed<ModelUsageGate>(() => {
  if (!currentResult.value) {
    return {
      blockedReason: '尚无可用辨识结果',
      modelSource: null,
      requiresRiskConfirmation: false,
    };
  }

  if (isStepOnly.value) {
    if (stepResult.value?.stepValidationPassed !== true) {
      return {
        blockedReason: '该结果未通过受控单阶跃验证，不可进入整定或推荐仿真。',
        modelSource: 'STEP_EXPERIMENT',
        requiresRiskConfirmation: false,
      };
    }
    if (!stepResult.value.recordId) {
      return {
        blockedReason:
          '受控阶跃结果缺少可审计的记录 ID，不可进入整定或推荐仿真。',
        modelSource: 'STEP_EXPERIMENT',
        requiresRiskConfirmation: false,
      };
    }
    return {
      blockedReason: null,
      modelSource: 'STEP_EXPERIMENT',
      requiresRiskConfirmation: false,
    };
  }

  const result = historyResult.value;
  if (!result || result.success === false) {
    return {
      blockedReason: '历史辨识结论为 INCONCLUSIVE，不可用于整定或推荐仿真。',
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }
  if (result.thetaSource === 'HEURISTIC_2TS') {
    return {
      blockedReason:
        '纯滞后采用 2Ts 启发值，可信度最高为 C，不可直接用于整定或推荐仿真；请提供明确 θ 后重新辨识。',
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }
  if (result.identifyMethod === 'HISTORICAL_IV') {
    return {
      blockedReason: 'IV 辨识当前仍为实验性能力，不可进入整定或推荐仿真。',
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }

  const confidence = result.confidenceLevel;
  if (
    confidence === 'D' ||
    confidence === 'E' ||
    confidence === 'INCONCLUSIVE'
  ) {
    return {
      blockedReason: `可信度 ${confidence} 不允许用于整定或推荐仿真。`,
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }
  if (!confidence || !['A', 'B', 'C'].includes(confidence)) {
    return {
      blockedReason: '可信度等级未明确，不可进入整定或推荐仿真。',
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }
  if (!result.recordId) {
    return {
      blockedReason: '辨识结果缺少可审计的记录 ID，不可进入整定或推荐仿真。',
      modelSource: 'IDENTIFICATION_RECORD',
      requiresRiskConfirmation: false,
    };
  }

  return {
    blockedReason: null,
    modelSource: 'IDENTIFICATION_RECORD',
    requiresRiskConfirmation: confidence === 'C',
  };
});

const canEnterRecommendedFlow = computed(
  () =>
    modelUsageGate.value.blockedReason === null &&
    (!modelUsageGate.value.requiresRiskConfirmation || riskConfirmed.value),
);

// ECharts ref
const chartRef = ref<EchartsUIType>();
const { renderEcharts: renderChart } = useEcharts(chartRef);

/** 拟合度颜色 */
function fittingScoreColor(val: number): string {
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

/** 进度百分比 */
const progressPercent = computed(() => {
  if (!taskProgress.value) return 0;
  return Math.round(taskProgress.value.progress || 0);
});

/** 进度状态 */
const progressStatus = computed<'active' | 'exception' | 'normal' | 'success'>(
  () => {
    if (!taskProgress.value) return 'normal';
    if (taskProgress.value.status === 'FAILED') return 'exception';
    if (taskProgress.value.status === 'SUCCESS') return 'success';
    return 'active';
  },
);

/** 执行模型辨识 */
async function handleIdentify() {
  if (!loopId.value) {
    message.warning('请选择回路');
    return;
  }
  if (!timeRange.value || timeRange.value.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = timeRange.value;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }

  // P1-021：回路由统一上下文头选择并同步 store，此处不再 setCurrentLoop
  riskConfirmed.value = false;

  if (isStepOnly.value) {
    // STEP_ONLY 走同步阶跃实验路径（向后兼容）
    loading.value = true;
    const hide = message.loading(
      `正在进行 ${filter.modelType} 阶跃实验辨识（${filter.method}）…`,
      0,
    );
    try {
      const result = await identifyModelApi({
        loopId: loopId.value,
        startTime: start.toISOString(),
        endTime: end.toISOString(),
        modelType: filter.modelType,
        method: filter.method,
      });
      stepResult.value = result;
      // 清空历史结果
      tuningStore.identifyResult = null;
      nextTick(() => renderFittedCurve());
      hide();
      message.success('阶跃实验辨识完成');
    } catch {
      hide();
    } finally {
      loading.value = false;
    }
    return;
  }

  // AUTO / HISTORY_ONLY 走异步历史辨识路径
  loading.value = true;
  tuningStore.identifyResult = null;
  tuningStore.taskProgress = null;
  try {
    const taskId = await tuningStore.submitIdentify({
      loopId: loopId.value,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
      identifyStrategy: filter.identifyStrategy,
      candidateModelTypes: filter.candidateModelTypes.length
        ? filter.candidateModelTypes
        : undefined,
      thetaEstimate: filter.thetaEstimate,
    });
    message.info(`异步辨识任务已提交（taskId: ${taskId.slice(0, 8)}…）`);
    // 启动轮询
    tuningStore.startPolling(taskId, (progress) => {
      loading.value = false;
      if (progress.status === 'SUCCESS') {
        message.success('历史数据辨识完成');
        nextTick(() => renderFittedCurve());
      } else if (progress.status === 'FAILED') {
        message.error(`辨识失败：${progress.error || '未知错误'}`);
      }
    });
  } catch {
    loading.value = false;
  }
}

/** 预览可辨识片段 */
async function handlePreviewSegments() {
  if (!loopId.value || !timeRange.value || timeRange.value.length !== 2) {
    message.warning('请先选择回路和时间范围');
    return;
  }
  const [start, end] = timeRange.value;
  if (!start || !end) return;

  segmentLoading.value = true;
  try {
    segments.value = await previewSegmentsApi({
      loopId: loopId.value,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
    });
    if (segments.value.totalSegments === 0) {
      message.warning('该时间窗口内无可辨识片段');
    } else {
      message.success(
        `发现 ${segments.value.totalSegments} 个片段（充分激励 ${segments.value.sufficientCount} 个）`,
      );
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    segmentLoading.value = false;
  }
}

/** 渲染拟合曲线图 */
function renderFittedCurve() {
  const data = currentResult.value;
  if (!data || !data.fittedCurve || data.fittedCurve.timestamps.length === 0) {
    // 历史辨识结果可能无 fittedCurve，显示提示
    if (currentResult.value && !isStepOnly.value) {
      renderChart({
        title: { left: 'center', text: '历史辨识结果（无拟合曲线）' },
      });
    } else {
      renderChart({
        title: { left: 'center', text: '暂无拟合曲线数据' },
      });
    }
    return;
  }

  const { timestamps, pv, fitted } = data.fittedCurve;
  const enableDataZoom = timestamps.length > 1000;

  renderChart({
    backgroundColor: 'transparent',
    dataZoom: enableDataZoom
      ? [
          { end: 100, start: 0, type: 'inside' },
          {
            end: 100,
            handleSize: '100%',
            start: 0,
            type: 'slider',
          },
        ]
      : [],
    grid: {
      bottom: enableDataZoom ? 60 : 30,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['原始 PV', '拟合曲线'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: pv,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        name: '原始 PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: fitted,
        itemStyle: { color: themeColors.value.WARNING },
        lineStyle: { type: 'dashed', width: 2 },
        name: '拟合曲线',
        showSymbol: false,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(4),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          // 强制北京时间（UTC+8）：+8h 后用 getUTC* 方法
          const d = new Date(Number(val) + 8 * 3600 * 1000);
          const hh = String(d.getUTCHours()).padStart(2, '0');
          const mm = String(d.getUTCMinutes()).padStart(2, '0');
          const dd = String(d.getUTCDate()).padStart(2, '0');
          const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: timestamps,
      type: 'category',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

/** 跳转整定算法页，传递模型参数 */
function handleUseForTuning() {
  const result = currentResult.value;
  if (!result) return;
  const provenance = buildProvenanceQuery();
  if (!provenance) {
    message.warning(
      modelUsageGate.value.blockedReason || '请完成人工风险确认后再进行整定',
    );
    return;
  }
  // P1-019：同步模型来源门禁字段到 store，支撑 flow 可恢复
  tuningStore.setModelSource(
    provenance.modelSource ?? '',
    provenance.sourceRecordId ?? '',
    provenance.riskConfirmed === 'true',
  );
  tuningStore.currentStep = 1;
  router.push({
    path: '/tuning/flow/algorithm',
    query: {
      modelType: result.modelType,
      modelParams: JSON.stringify(result.params),
      loopId: loopId.value,
      ...provenance,
    },
  });
}

function buildProvenanceQuery(): null | Record<string, string> {
  if (!canEnterRecommendedFlow.value) return null;

  if (modelUsageGate.value.modelSource === 'STEP_EXPERIMENT') {
    const sourceRecordId = stepResult.value?.recordId;
    if (!sourceRecordId) return null;
    return {
      modelSource: 'STEP_EXPERIMENT',
      sourceRecordId,
    };
  }

  const sourceRecordId = historyResult.value?.recordId;
  if (
    modelUsageGate.value.modelSource !== 'IDENTIFICATION_RECORD' ||
    !sourceRecordId
  ) {
    return null;
  }

  return {
    modelSource: 'IDENTIFICATION_RECORD',
    sourceRecordId,
    ...(modelUsageGate.value.requiresRiskConfirmation
      ? { riskConfirmed: 'true' }
      : {}),
  };
}

/** 跳转闭环仿真页，携带模型参数 + 候选 PID */
function handleGoSimulation() {
  const result = currentResult.value;
  if (!result) return;
  const provenance = buildProvenanceQuery();
  if (!provenance) {
    message.warning(
      modelUsageGate.value.blockedReason || '请完成人工风险确认后再进行仿真',
    );
    return;
  }
  // P1-019：同步模型来源门禁字段到 store，支撑 flow 可恢复
  tuningStore.setModelSource(
    provenance.modelSource ?? '',
    provenance.sourceRecordId ?? '',
    provenance.riskConfirmed === 'true',
  );
  tuningStore.currentStep = 2;
  router.push({
    path: '/tuning/flow/simulation',
    query: {
      modelType: result.modelType,
      modelParams: JSON.stringify(result.params),
      loopId: loopId.value,
      ...provenance,
    },
  });
}

/** 深色模式切换时重绘 ECharts 图表 */
watch(isDark, () => {
  nextTick(() => {
    renderFittedCurve();
  });
});

/** 历史结果变化时重绘 */
watch(historyResult, () => {
  riskConfirmed.value = false;
  nextTick(() => renderFittedCurve());
});

watch(stepResult, () => {
  riskConfirmed.value = false;
});

watch(
  () => filter.identifyStrategy,
  () => {
    riskConfirmed.value = false;
  },
);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="模型辨识"
      subtitle="选择回路、时间窗和辨识策略，产出用于整定的过程对象模型。"
    />
    <Alert
      type="warning"
      show-icon
      banner
      :closable="false"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
      style="margin-bottom: 12px"
    />

    <ClpmDataCanvas class="mb-4 mt-4" title="辨识筛选条件">
      <Form layout="inline">
        <FormItem label="辨识策略">
          <Select
            v-model:value="filter.identifyStrategy"
            style="width: 280px"
            :options="strategyOptions"
          />
        </FormItem>
        <template v-if="isStepOnly">
          <FormItem label="模型类型">
            <Select
              v-model:value="filter.modelType"
              style="width: 200px"
              :options="modelTypeOptions"
            />
          </FormItem>
          <FormItem label="辨识方法">
            <Select
              v-model:value="filter.method"
              style="width: 140px"
              :options="methodOptions"
            />
          </FormItem>
        </template>
        <template v-else>
          <!-- P1-022：高级参数仅 ADMIN/EXPERT 可见，IC_ENGINEER 使用默认值 -->
          <Collapse
            v-if="canEditAdvancedParams"
            :bordered="false"
            class="advanced-params-collapse"
          >
            <CollapsePanel key="advanced" header="高级参数">
              <FormItem label="候选模型阶次">
                <Select
                  v-model:value="filter.candidateModelTypes"
                  mode="multiple"
                  style="width: 280px"
                  :options="historyModelTypeOptions"
                  placeholder="并行辨识多种阶次"
                />
              </FormItem>
              <FormItem label="纯滞后预估 θ (秒)">
                <InputNumber
                  v-model:value="filter.thetaEstimate"
                  :min="0"
                  :precision="2"
                  placeholder="留空则采用 2Ts 启发值"
                  style="width: 220px"
                />
              </FormItem>
            </CollapsePanel>
          </Collapse>
          <FormItem>
            <Button :loading="segmentLoading" @click="handlePreviewSegments">
              预览可辨识片段
            </Button>
          </FormItem>
        </template>
        <FormItem>
          <Button type="primary" :loading="loading" @click="handleIdentify">
            开始辨识
          </Button>
        </FormItem>
      </Form>
    </ClpmDataCanvas>

    <Spin :spinning="loading">
      <!-- 异步任务进度条（Phase 2） -->
      <ClpmDataCanvas
        v-if="taskProgress && !isStepOnly"
        title="辨识进度"
        class="mb-4"
      >
        <div class="flex flex-col gap-2">
          <Progress
            :percent="progressPercent"
            :status="progressStatus"
            :stroke-color="
              progressStatus === 'exception'
                ? themeColors.DANGER
                : progressStatus === 'success'
                  ? themeColors.SUCCESS
                  : themeColors.INFO
            "
          />
          <div class="flex items-center justify-between text-sm">
            <span class="text-gray-600">
              阶段：{{ taskProgress.stage || '初始化' }}
              <span v-if="taskProgress.message" class="ml-2 text-gray-400">
                · {{ taskProgress.message }}
              </span>
            </span>
            <Tag
              v-if="taskProgress.status"
              :color="
                taskProgress.status === 'SUCCESS'
                  ? 'green'
                  : taskProgress.status === 'FAILED'
                    ? 'red'
                    : 'blue'
              "
            >
              {{ taskProgress.status }}
            </Tag>
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- 可辨识片段预览（Phase 2） -->
      <ClpmDataCanvas
        v-if="segments && !isStepOnly"
        title="可辨识片段预览"
        class="mb-4"
      >
        <Alert
          v-if="segments.sufficientCount === 0"
          type="warning"
          show-icon
          message="未发现激励充分片段"
          description="该时间窗口内 OP 变化不足以支持历史数据辨识，建议改用阶跃实验路径。"
        />
        <Alert
          v-else
          type="info"
          show-icon
          :message="`共 ${segments.totalSegments} 个片段，其中 ${segments.sufficientCount} 个激励充分`"
          :description="`充分片段占比 ${((segments.sufficientCount / Math.max(segments.totalSegments, 1)) * 100).toFixed(0)}%，可进行历史数据辨识。`"
        />
        <div v-if="segments.segments.length > 0" class="mt-3">
          <div class="mb-2 text-xs text-gray-500">片段明细（索引区间）</div>
          <div class="flex flex-wrap gap-2">
            <Tag
              v-for="(seg, idx) in segments.segments"
              :key="idx"
              :color="seg.isSufficient ? 'green' : 'default'"
            >
              #{{ idx + 1 }} [{{ seg.startIdx }}-{{ seg.endIdx }}]
              <template v-if="seg.excitationScore !== null">
                · 激励 {{ seg.excitationScore?.toFixed(2) }}
              </template>
              <template v-if="seg.isSufficient"> ✓</template>
            </Tag>
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- INCONCLUSIVE 引导卡片（Phase 2） -->
      <ClpmDataCanvas
        v-if="isInconclusive"
        title="辨识结果 — INCONCLUSIVE"
        class="mb-4"
      >
        <Alert
          type="warning"
          show-icon
          banner
          :closable="false"
          message="历史数据激励不足，辨识结果可信度低"
        >
          <template #description>
            <div class="flex flex-col gap-2">
              <span>
                原因：{{
                  historyResult?.confidenceReason ||
                  historyResult?.reason ||
                  'OP 激励不充分或数据质量不足'
                }}
              </span>
              <span>
                建议：切换为「仅阶跃实验」策略进行主动实验，或扩大时间范围重新辨识。
              </span>
              <div class="mt-2">
                <Button
                  type="primary"
                  size="small"
                  @click="filter.identifyStrategy = 'STEP_ONLY'"
                >
                  切换为阶跃实验策略
                </Button>
              </div>
            </div>
          </template>
        </Alert>
      </ClpmDataCanvas>

      <!-- 结果区 -->
      <div
        v-if="currentResult && !isInconclusive"
        class="grid grid-cols-1 gap-4 lg:grid-cols-3"
      >
        <Alert
          v-if="modelUsageGate.blockedReason"
          class="lg:col-span-3"
          type="warning"
          show-icon
          :closable="false"
          message="当前模型仅供审阅"
          :description="modelUsageGate.blockedReason"
        />
        <Alert
          v-else-if="modelUsageGate.requiresRiskConfirmation"
          class="lg:col-span-3"
          type="warning"
          show-icon
          :closable="false"
          message="可信度 C：需人工风险确认"
        >
          <template #description>
            <div class="flex flex-col gap-2">
              <span>
                该结果存在较高不确定性。继续前请复核数据窗口、模型参数、回退方案与实施风险。
              </span>
              <Checkbox v-model:checked="riskConfirmed">
                我已完成专业复核，并确认仅生成建议、不直接下写 DCS
              </Checkbox>
            </div>
          </template>
        </Alert>
        <ClpmDataCanvas title="主模型参数" class="lg:col-span-1">
          <Descriptions :column="1" bordered size="small">
            <DescriptionsItem label="模型类型">
              {{ currentResult.modelType }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="currentResult.params?.K !== undefined"
              label="过程增益 K"
            >
              {{ currentResult.params?.K ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="currentResult.params?.tau !== undefined"
              label="时间常数 τ (秒)"
            >
              {{ currentResult.params?.tau ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="currentResult.params?.T1 !== undefined"
              label="时间常数 T1 (秒)"
            >
              {{ currentResult.params?.T1 ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="currentResult.params?.T2 !== undefined"
              label="时间常数 T2 (秒)"
            >
              {{ currentResult.params?.T2 ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="currentResult.params?.theta !== undefined"
              label="纯滞后 θ (秒)"
            >
              {{ currentResult.params?.theta ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem label="拟合度">
              <Tag :color="fittingScoreColor(currentResult.fittingScore ?? 0)">
                {{ Number(currentResult.fittingScore ?? 0).toFixed(2) }}%
              </Tag>
            </DescriptionsItem>
            <!-- Phase 2：可信度徽章 -->
            <DescriptionsItem
              v-if="!isStepOnly && historyResult?.confidenceLevel"
              label="可信度等级"
            >
              <ConfidenceBadge
                :level="
                  historyResult?.confidenceLevel === 'INCONCLUSIVE'
                    ? 'E'
                    : (historyResult?.confidenceLevel as
                        | 'A'
                        | 'B'
                        | 'C'
                        | 'D'
                        | 'E')
                "
                :valid-rate="historyResult?.validRate ?? undefined"
              />
            </DescriptionsItem>
            <!-- Phase 2：辨识方法 -->
            <DescriptionsItem
              v-if="!isStepOnly && historyResult?.identifyMethod"
              label="辨识方法"
            >
              {{ historyResult?.identifyMethod }}
            </DescriptionsItem>
            <!-- Phase 2：激励得分 -->
            <DescriptionsItem
              v-if="!isStepOnly && historyResult?.excitationScore !== null"
              label="激励充分性"
            >
              {{ Number(historyResult?.excitationScore ?? 0).toFixed(3) }}
            </DescriptionsItem>
            <!-- Phase 2：残差检验 -->
            <DescriptionsItem
              v-if="!isStepOnly && historyResult?.residualTestPassed !== null"
              label="残差白噪声检验"
            >
              <Tag :color="historyResult?.residualTestPassed ? 'green' : 'red'">
                {{ historyResult?.residualTestPassed ? '通过' : '未通过' }}
              </Tag>
            </DescriptionsItem>
            <DescriptionsItem label="数据点数">
              {{ currentResult.dataPoints ?? '—' }}
            </DescriptionsItem>
            <DescriptionsItem label="算法版本">
              {{ currentResult.algorithmVersion ?? '—' }}
            </DescriptionsItem>
          </Descriptions>
        </ClpmDataCanvas>

        <ClpmDataCanvas title="拟合曲线" class="lg:col-span-2">
          <EchartsUI ref="chartRef" height="420px" />
        </ClpmDataCanvas>
      </div>

      <!-- 候选模型对比卡片（Phase 2 历史辨识多阶次并行） -->
      <div
        v-if="candidateModels.length > 0"
        class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3"
      >
        <Card
          v-for="(c, idx) in candidateModels"
          :key="idx"
          size="small"
          :title="`${c.modelType} 候选 #${idx + 1}`"
        >
          <Descriptions :column="1" size="small">
            <DescriptionsItem label="拟合度">
              <Tag :color="fittingScoreColor(c.fittingScore)">
                {{ Number(c.fittingScore).toFixed(2) }}%
              </Tag>
            </DescriptionsItem>
            <DescriptionsItem label="可信度">
              <ConfidenceBadge
                :level="
                  c.confidence === 'INCONCLUSIVE'
                    ? 'E'
                    : (c.confidence as 'A' | 'B' | 'C' | 'D' | 'E')
                "
                size="small"
              />
            </DescriptionsItem>
            <DescriptionsItem v-if="c.identifyMethod" label="辨识方法">
              {{ c.identifyMethod }}
            </DescriptionsItem>
            <DescriptionsItem
              v-if="c.residualTestPassed !== null"
              label="残差检验"
            >
              <Tag :color="c.residualTestPassed ? 'green' : 'red'">
                {{ c.residualTestPassed ? '通过' : '未通过' }}
              </Tag>
            </DescriptionsItem>
            <DescriptionsItem v-if="c.reason" label="备注">
              {{ c.reason }}
            </DescriptionsItem>
          </Descriptions>
        </Card>
      </div>

      <!-- 空状态 -->
      <ClpmDataCanvas
        v-if="!currentResult && !isInconclusive && !loading"
        title="模型辨识结果"
      >
        <div class="flex h-64 items-center justify-center text-gray-400">
          请选择回路和时间范围，点击「开始辨识」进行模型辨识
        </div>
      </ClpmDataCanvas>

      <!-- 下一步动作 -->
      <ClpmDataCanvas
        v-if="currentResult && !isInconclusive && !modelUsageGate.blockedReason"
        class="mt-4"
        title="下一步动作"
      >
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-500">
            {{
              modelUsageGate.requiresRiskConfirmation && !riskConfirmed
                ? '完成风险复核并勾选确认后，方可进入整定或推荐仿真。'
                : '辨识完成，可使用此模型进行 PID 整定或闭环仿真。'
            }}
          </span>
          <div class="flex gap-2">
            <Button
              :disabled="!canEnterRecommendedFlow"
              @click="handleGoSimulation"
            >
              进行闭环仿真
            </Button>
            <Button
              type="primary"
              size="large"
              :disabled="!canEnterRecommendedFlow"
              @click="handleUseForTuning"
            >
              使用此模型进行整定 →
            </Button>
          </div>
        </div>
      </ClpmDataCanvas>
    </Spin>
  </Page>
</template>
