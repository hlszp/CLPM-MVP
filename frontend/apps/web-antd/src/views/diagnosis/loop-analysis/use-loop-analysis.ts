/**
 * 回路分析页状态管理组合式函数（Batch 4 F2c）
 *
 * 管理 4 步引导式工作流的状态与任务轮询：
 *  Step 1 选回路 → Step 2 KPI 评估 → Step 3 诊断分析 → Step 4 A/B 对比
 *
 * 轮询：KPI 评估走 /tasks 体系（getTaskDetailApi + getTaskResultsApi），
 *      诊断走 /diagnosis/tasks 体系（getDiagnosisTaskDetailApi）。
 * 终态后清理定时器，组件卸载兜底清理。
 */
import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';

import { onMounted, reactive, ref } from 'vue';

import { message } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getAlgorithmMetaApi,
  getDiagnosisDetailApi,
  getDiagnosisTaskDetailApi,
  getDiagnosisVisualizationApi,
  getRecommendationsApi,
  getWaveformApi,
  triggerDiagnosisApi,
} from '#/api/diagnosis';
import {
  getTaskDetailApi,
  getTaskResultsApi,
  TaskApi,
  triggerCustomEvaluateApi,
} from '#/api/task';
import { usePolling } from '#/composables/use-polling';

/** 12 个 metric code（对齐 metric_calculator.CALCULATOR_REGISTRY，前端固化） */
export const ALL_METRIC_CODES = [
  'accuracy_rate',
  'fast_rate',
  'stability_rate',
  'effective_auto_rate',
  'good_value_rate',
  'oscillation_rate',
  'saturation_rate',
  'stiction_index',
  'output_trip_index',
  'auto_mode_rate',
  'settling_time',
  'ideal_settling_time',
] as const;

/** 12 KPI 展示元数据（key → 名称/单位） */
export const METRIC_DISPLAY: Record<string, { label: string; unit: string }> = {
  accuracy_rate: { label: '准确率', unit: '%' },
  auto_mode_rate: { label: '自控率', unit: '%' },
  effective_auto_rate: { label: '有效自控率', unit: '%' },
  fast_rate: { label: '快速率', unit: '%' },
  good_value_rate: { label: '良值率', unit: '%' },
  ideal_settling_time: { label: '理想稳定时间', unit: 's' },
  oscillation_rate: { label: '振荡率', unit: '%' },
  output_trip_index: { label: '输出跳变指数', unit: '' },
  saturation_rate: { label: '饱和率', unit: '%' },
  settling_time: { label: '稳定时间', unit: 's' },
  stability_rate: { label: '平稳率', unit: '%' },
  stiction_index: { label: '粘滞指数', unit: '' },
};

const POLL_INTERVAL = 2000;

/** 工作流配置 */
export interface AnalysisConfig {
  loopId: string;
  tagName: string;
  /** YYYY-MM-DD HH:mm:ss */
  startTime: string;
  /** YYYY-MM-DD HH:mm:ss */
  endTime: string;
  /** 诊断标签子集，空=全部 */
  labels: DiagnosisLabel[];
}

interface KpiState {
  taskId: string;
  status: '' | TaskApi.TaskStatus;
  progress: number;
  currentStage: string;
  errorMessage: string;
  results: TaskApi.TaskResultItem[];
}

interface DiagState {
  taskId: string;
  status: '' | DiagnosisApi.TaskStatus;
  errorMessage: string;
  detail: DiagnosisApi.DiagnosisDetail | null;
  visualization: DiagnosisApi.DiagnosisVisualizationData | null;
  recommendations: DiagnosisApi.RecommendationItem[];
  waveform: DiagnosisApi.WaveformResult | null;
}

function defaultKpiState(): KpiState {
  return {
    currentStage: '',
    errorMessage: '',
    progress: 0,
    results: [],
    status: '',
    taskId: '',
  };
}

function defaultDiagState(): DiagState {
  return {
    detail: null,
    errorMessage: '',
    recommendations: [],
    status: '',
    taskId: '',
    visualization: null,
    waveform: null,
  };
}

export function useLoopAnalysis() {
  /** Steps 当前步（1-4） */
  const current = ref(1);

  const config = reactive<AnalysisConfig>({
    endTime: dayjs().format('YYYY-MM-DD HH:mm:ss'),
    labels: [],
    loopId: '',
    startTime: dayjs().subtract(24, 'hour').format('YYYY-MM-DD HH:mm:ss'),
    tagName: '',
  });

  const kpi = reactive<KpiState>(defaultKpiState());
  const diag = reactive<DiagState>(defaultDiagState());
  const algorithmMeta = ref<DiagnosisApi.AlgorithmMetaList | null>(null);
  const loading = ref(false);

  /**
   * KPI 评估轮询（usePolling：递归 setTimeout 防堆积 + 页面隐藏暂停 +
   * 连续 3 次失败才熔断；熔断时提示用户，避免一次网络抖动永久卡死进度条）
   */
  const kpiPolling = usePolling(pollKpiTask, {
    interval: POLL_INTERVAL,
    onGiveUp: () => {
      kpi.errorMessage =
        '任务进度刷新已停止（连续获取状态失败），请检查网络后重新触发评估';
      message.warning('KPI 评估进度刷新中断，请检查网络后重新触发评估');
    },
  });

  /** 诊断任务轮询（同 KPI 轮询约定） */
  const diagPolling = usePolling(pollDiagTask, {
    interval: POLL_INTERVAL,
    onGiveUp: () => {
      diag.errorMessage =
        '任务进度刷新已停止（连续获取状态失败），请检查网络后重新触发诊断';
      message.warning('诊断任务进度刷新中断，请检查网络后重新触发诊断');
    },
  });

  /** 加载算法元数据（onMounted 时调一次） */
  async function loadAlgorithmMeta() {
    try {
      algorithmMeta.value = await getAlgorithmMetaApi();
    } catch {
      // 错误已由拦截器处理，降级为不带算法说明的卡片
    }
  }

  /** Step 2：触发 KPI 评估 */
  async function triggerKpiEvaluation() {
    if (!config.loopId) return;
    kpiPolling.stop();
    Object.assign(kpi, defaultKpiState());
    loading.value = true;
    try {
      const task = await triggerCustomEvaluateApi({
        loopIds: [config.loopId],
        metrics: [...ALL_METRIC_CODES],
        tsEnd: config.endTime,
        tsStart: config.startTime,
      });
      kpi.taskId = task.taskId;
      kpi.status = task.status;
      kpiPolling.start();
    } catch {
      // 错误已由拦截器处理
    } finally {
      loading.value = false;
    }
  }

  /**
   * 轮询 KPI 任务状态；错误不捕获，交由 usePolling 计入连续失败熔断。
   * 到达终态时主动停止轮询。
   */
  async function pollKpiTask() {
    if (!kpi.taskId) return;
    const task = await getTaskDetailApi(kpi.taskId);
    kpi.status = task.status;
    kpi.progress = task.progress ?? 0;
    kpi.currentStage = task.currentStage ?? '';
    if (TaskApi.TERMINAL_STATUSES.includes(task.status)) {
      kpiPolling.stop();
      if (task.status === 'SUCCESS') {
        await loadKpiResults();
      } else {
        kpi.errorMessage = task.errorMessage || '评估未成功完成';
      }
    }
  }

  async function loadKpiResults() {
    if (!kpi.taskId) return;
    try {
      const res = await getTaskResultsApi(kpi.taskId);
      kpi.results = res.items || [];
    } catch {
      // 错误已由拦截器处理
    }
  }

  /** Step 3：触发诊断 */
  async function triggerDiagnosis() {
    if (!config.loopId) return;
    diagPolling.stop();
    Object.assign(diag, defaultDiagState());
    loading.value = true;
    try {
      const result = await triggerDiagnosisApi({
        endTime: config.endTime,
        labels: config.labels.length > 0 ? config.labels : undefined,
        loopIds: [config.loopId],
        startTime: config.startTime,
      });
      const first = result?.tasks?.[0];
      if (first) {
        diag.taskId = first.taskId;
        diag.status = first.status;
        diagPolling.start();
      }
    } catch {
      // 错误已由拦截器处理
    } finally {
      loading.value = false;
    }
  }

  /**
   * 轮询诊断任务状态；错误不捕获，交由 usePolling 计入连续失败熔断。
   * 到达终态时主动停止轮询。
   */
  async function pollDiagTask() {
    if (!diag.taskId) return;
    const task = await getDiagnosisTaskDetailApi(diag.taskId);
    diag.status = task.status;
    if (TaskApi.TERMINAL_STATUSES.includes(task.status)) {
      diagPolling.stop();
      if (task.status === 'SUCCESS') {
        await loadDiagnosisResults();
      } else {
        diag.errorMessage = task.errorMessage || '诊断未成功完成';
      }
    }
  }

  /** 诊断完成后并行拉详情/可视化/推荐/波形 */
  async function loadDiagnosisResults() {
    if (!config.loopId) return;
    try {
      const [detail, visualization, recommendations, waveform] =
        await Promise.all([
          getDiagnosisDetailApi(config.loopId).catch(() => null),
          getDiagnosisVisualizationApi(config.loopId).catch(() => null),
          getRecommendationsApi(config.loopId).catch(() => null),
          getWaveformApi(config.loopId, {
            endTime: config.endTime,
            startTime: config.startTime,
          }).catch(() => null),
        ]);
      diag.detail = detail;
      diag.visualization = visualization;
      diag.recommendations = recommendations?.recommendations || [];
      diag.waveform = waveform;
    } catch {
      // 错误已由拦截器处理
    }
  }

  /** 切换回路时清空所有结果 */
  function resetResults() {
    kpiPolling.stop();
    diagPolling.stop();
    Object.assign(kpi, defaultKpiState());
    Object.assign(diag, defaultDiagState());
  }

  onMounted(() => {
    loadAlgorithmMeta();
  });

  // 轮询清理由 usePolling 的 onScopeDispose 兜底（组件卸载自动停止）

  // 用 reactive 包裹返回值，使 ref 在父模板与子组件 props 中自动解包，
  // 子组件可直接用 state.current / state.algorithmMeta 访问，无需 .value。
  return reactive({
    ALL_METRIC_CODES,
    METRIC_DISPLAY,
    algorithmMeta,
    config,
    current,
    diag,
    kpi,
    loadAlgorithmMeta,
    resetResults,
    triggerDiagnosis,
    triggerKpiEvaluation,
  });
}

export type UseLoopAnalysisReturn = ReturnType<typeof useLoopAnalysis>;
