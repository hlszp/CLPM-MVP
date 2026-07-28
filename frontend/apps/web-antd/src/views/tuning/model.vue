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

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  message,
  Progress,
  Select,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { identifyModelApi, previewSegmentsApi } from '#/api/tuning';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import ConfidenceBadge from '#/components/metric/confidence-badge.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'TuningModel' });

const router = useRouter();
const tuningStore = useTuningStore();
const { isDark, themeColors } = useClpmTheme();

const loading = ref(false);
const segmentLoading = ref(false);
const loopOptions = ref<{ label: string; value: string }[]>([]);

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
  loopId: '' as string,
  timeRange: [dayjs().subtract(24, 'hour'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  identifyStrategy: 'AUTO' as TuningApi.IdentifyStrategy,
  candidateModelTypes: ['FOPDT', 'SOPDT'] as TuningApi.ModelType[],
  // STEP_ONLY 路径仍用 modelType + method
  modelType: 'FOPDT' as TuningApi.ModelType,
  method: 'TWO_POINT' as TuningApi.IdentifyMethod,
});

/** 辨识策略选项 */
const strategyOptions: { label: string; value: TuningApi.IdentifyStrategy }[] = [
  {
    label: '自动（优先历史数据，失败兜底阶跃实验）',
    value: 'AUTO',
  },
  { label: '仅历史数据辨识', value: 'HISTORY_ONLY' },
  { label: '仅阶跃实验（同步）', value: 'STEP_ONLY' },
];

/** 模型类型选项 */
const modelTypeOptions: { label: string; value: TuningApi.ModelType }[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
  { label: 'IPDT 积分加纯滞后', value: 'IPDT' },
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
const progressStatus = computed<
  'active' | 'exception' | 'normal' | 'success'
>(() => {
  if (!taskProgress.value) return 'normal';
  if (taskProgress.value.status === 'FAILED') return 'exception';
  if (taskProgress.value.status === 'SUCCESS') return 'success';
  return 'active';
});

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    const list = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    if (list.length > 0 && !filter.loopId) {
      const first = list[0];
      if (first) {
        filter.loopId = first.loopId;
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 执行模型辨识 */
async function handleIdentify() {
  if (!filter.loopId) {
    message.warning('请选择回路');
    return;
  }
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }

  // 同步当前回路到 store
  const selectedLoop = loopOptions.value.find((l) => l.value === filter.loopId);
  tuningStore.setCurrentLoop(filter.loopId, selectedLoop?.label || '');

  if (isStepOnly.value) {
    // STEP_ONLY 走同步阶跃实验路径（向后兼容）
    loading.value = true;
    const hide = message.loading(
      `正在进行 ${filter.modelType} 阶跃实验辨识（${filter.method}）…`,
      0,
    );
    try {
      const result = await identifyModelApi({
        loopId: filter.loopId,
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
      loopId: filter.loopId,
      startTime: start.toISOString(),
      endTime: end.toISOString(),
      identifyStrategy: filter.identifyStrategy,
      candidateModelTypes: filter.candidateModelTypes.length
        ? filter.candidateModelTypes
        : undefined,
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
  if (!filter.loopId || !filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请先选择回路和时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) return;

  segmentLoading.value = true;
  try {
    segments.value = await previewSegmentsApi({
      loopId: filter.loopId,
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
  router.push({
    path: '/tuning/algorithm',
    query: {
      modelType: result.modelType,
      modelParams: JSON.stringify(result.params),
      loopId: filter.loopId,
    },
  });
}

/** 跳转闭环仿真页，携带模型参数 + 候选 PID */
function handleGoSimulation() {
  const result = currentResult.value;
  if (!result) return;
  router.push({
    path: '/tuning/simulation',
    query: {
      modelType: result.modelType,
      modelParams: JSON.stringify(result.params),
      loopId: filter.loopId,
    },
  });
}

onMounted(() => {
  loadLoopOptions();
});

/** 深色模式切换时重绘 ECharts 图表 */
watch(isDark, () => {
  nextTick(() => {
    renderFittedCurve();
  });
});

/** 历史结果变化时重绘 */
watch(historyResult, () => {
  nextTick(() => renderFittedCurve());
});
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
        <FormItem label="回路选择">
          <Select
            v-model:value="filter.loopId"
            placeholder="请选择回路"
            style="width: 220px"
            show-search
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) =>
                option.label.toLowerCase().includes(input.toLowerCase())
            "
          />
        </FormItem>
        <FormItem label="时间范围">
          <DatePicker.RangePicker
            v-model:value="filter.timeRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始时间', '结束时间']"
          />
        </FormItem>
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
          <FormItem label="候选模型阶次">
            <Select
              v-model:value="filter.candidateModelTypes"
              mode="multiple"
              style="width: 280px"
              :options="modelTypeOptions"
              placeholder="并行辨识多种阶次"
            />
          </FormItem>
          <FormItem>
            <Button
              :loading="segmentLoading"
              @click="handlePreviewSegments"
            >
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
                原因：{{ historyResult?.confidenceReason || historyResult?.reason || 'OP 激励不充分或数据质量不足' }}
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
              <Tag
                :color="historyResult?.residualTestPassed ? 'green' : 'red'"
              >
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
            <DescriptionsItem
              v-if="c.identifyMethod"
              label="辨识方法"
            >
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
        v-if="currentResult && !isInconclusive"
        class="mt-4"
        title="下一步动作"
      >
        <div class="flex items-center justify-between">
          <span class="text-sm text-gray-500">
            辨识完成，可使用此模型进行 PID 整定或闭环仿真。
          </span>
          <div class="flex gap-2">
            <Button @click="handleGoSimulation">进行闭环仿真</Button>
            <Button type="primary" size="large" @click="handleUseForTuning">
              使用此模型进行整定 →
            </Button>
          </div>
        </div>
      </ClpmDataCanvas>
    </Spin>
  </Page>
</template>
