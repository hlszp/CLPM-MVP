<script lang="ts" setup>
/**
 * S7-TUNE-004 闭环仿真页（Phase 2 重构）
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5 + 实现契约 v2.1
 * - 顶部工具栏：运行仿真/重置/对比模式切换
 * - 常驻风险提示横幅
 * - 主区域左 70%：仿真对比图 + 性能指标表（改善/退化标识）
 * - 主区域右 30%：参数配置表单 + 多 PID 候选管理 + 保存
 *
 * Phase 2 变更：
 * - 支持多组 PID 参数对比（≥2 组，上限 5 组）
 * - 预设组合：当前 PID + IMC + Lambda + SIMC + 用户自定义
 * - 多曲线叠加可视化 + 性能指标对比表格（高亮最优项）
 * - 支持用户手动添加/移除候选 PID
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';
import type { SummaryAction, SummaryItem } from '#/components/clpm';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  InputNumber,
  message,
  Select,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  comparePidsApi,
  createTuningTaskApi,
  simulateTuningApi,
} from '#/api/tuning';
import {
  ClpmDataCanvas,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmStateOverlay,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'TuningSimulation' });

const route = useRoute();
const { isDark, themeColors, chartTextColor } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
/** 是否为多 PID 对比模式 */
const compareMode = ref(false);
const simulationResult = ref<null | TuningApi.SimulationResult>(null);
const loopId = ref<string>((route.query.loopId as string) || '');

/** P1-023：错误状态（仿真失败时持久展示，带重试） */
const errorState = ref<{ detail: string; message: string } | null>(null);

/** 模型类型选项 */
const modelTypeOptions: { label: string; value: TuningApi.ModelType }[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
  { label: 'IPDT 积分加纯滞后', value: 'IPDT' },
];

/** 扰动类型选项 */
const disturbanceOptions: {
  label: string;
  value: TuningApi.DisturbanceType;
}[] = [
  { label: '无扰动', value: 'none' },
  { label: '阶跃扰动', value: 'step' },
];

/** 表单参数 */
const form = reactive({
  modelType: 'FOPDT' as TuningApi.ModelType,
  modelParams: {
    K: 1,
    tau: 10,
    theta: 2,
    T1: 10,
    T2: 5,
  } as TuningApi.ModelParams,
  currentPid: { kp: 1, ti: 10, td: 0 } as TuningApi.PidParams,
  recommendedPid: { kp: 1.2, ti: 8, td: 0.5 } as TuningApi.PidParams,
  simDuration: 600,
  simStep: 1,
  setpointStep: 1,
  disturbanceType: 'none' as TuningApi.DisturbanceType,
});

/** 候选 PID 列表（多 PID 对比模式） */
const pidCandidates = ref<TuningApi.PidParamsWithLabel[]>([
  { label: '当前 PID', kp: 1, ti: 10, td: 0 },
  { label: 'IMC λ=τ', kp: 1.2, ti: 8, td: 0.5 },
]);

/** 新增候选 PID 表单 */
const newCandidate = reactive({
  label: '',
  kp: 1.5,
  ti: 8,
  td: 0.5,
});

/** 多 PID 对比色板（最多 5 组） */
const candidateColors = computed(() =>
  isDark.value
    ? ['#60a5fa', '#34d399', '#fbbf24', '#fb7185', '#a78bfa']
    : ['#1890ff', '#52c41a', '#fa8c16', '#f5222d', '#722ed1'],
);

/** 根据模型类型返回需要显示的模型参数字段 */
const modelParamFields = computed<
  { key: keyof TuningApi.ModelParams; label: string }[]
>(() => {
  switch (form.modelType) {
    case 'FOPDT': {
      return [
        { key: 'K', label: 'K (增益)' },
        { key: 'tau', label: 'τ (时间常数)' },
        { key: 'theta', label: 'θ (死区时间)' },
      ];
    }
    case 'IPDT': {
      return [
        { key: 'K', label: 'K (增益)' },
        { key: 'theta', label: 'θ (死区时间)' },
      ];
    }
    case 'SOPDT': {
      return [
        { key: 'K', label: 'K (增益)' },
        { key: 'T1', label: 'T1 (时间常数1)' },
        { key: 'T2', label: 'T2 (时间常数2)' },
        { key: 'theta', label: 'θ (死区时间)' },
      ];
    }
    default: {
      return [];
    }
  }
});

// ECharts refs
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 性能指标表格列（双 PID 模式） */
const metricColumns = computed(() => {
  if (compareMode.value) {
    // 多 PID 对比模式：动态生成列
    const cols: {
      title: string;
      dataIndex: string;
      key: string;
      width?: number;
    }[] = [{ title: '性能指标', dataIndex: 'name', key: 'name', width: 120 }];
    pidCandidates.value.forEach((c, idx) => {
      cols.push({
        title: c.label,
        dataIndex: `candidate_${idx}`,
        key: `candidate_${idx}`,
        width: 140,
      });
    });
    return cols;
  }
  return [
    { title: '性能指标', dataIndex: 'name', key: 'name', width: 120 },
    { title: '当前 PID', dataIndex: 'current', key: 'current', width: 130 },
    {
      title: '推荐 PID',
      dataIndex: 'recommended',
      key: 'recommended',
      width: 130,
    },
    { title: '改善幅度', dataIndex: 'improvement', key: 'improvement' },
  ];
});

/** 指标行数据 */
const metricRows = computed(() => {
  if (compareMode.value) {
    // 多 PID 对比模式
    const candidates = simulationResult.value?.candidateResponses || [];
    const metricNames = [
      { name: '上升时间 (秒)', key: 'riseTime' as const },
      { name: '超调量 (%)', key: 'overshoot' as const },
      { name: '稳定时间 (秒)', key: 'settlingTime' as const },
      { name: 'ITAE', key: 'itae' as const },
    ];
    return metricNames.map((m) => {
      const row: Record<string, string | number> = { name: m.name };
      candidates.forEach((c, idx) => {
        row[`candidate_${idx}`] = formatMetric(m.key, c.metrics?.[m.key]);
      });
      return row;
    });
  }

  // 双 PID 对比模式（原逻辑）
  if (!simulationResult.value) return [];
  const { currentMetrics, recommendedMetrics, improvement } =
    simulationResult.value;
  return [
    {
      name: '上升时间',
      current: formatMetric('riseTime', currentMetrics.riseTime),
      recommended: formatMetric('riseTime', recommendedMetrics.riseTime),
      improvement: formatImprovement(improvement.riseTime),
      improved: isImproved(improvement.riseTime),
      improvementLabel: getImprovementLabel(improvement.riseTime),
    },
    {
      name: '超调量',
      current: formatMetric('overshoot', currentMetrics.overshoot),
      recommended: formatMetric('overshoot', recommendedMetrics.overshoot),
      improvement: formatImprovement(improvement.overshoot),
      improved: isImproved(improvement.overshoot),
      improvementLabel: getImprovementLabel(improvement.overshoot),
    },
    {
      name: '稳定时间',
      current: formatMetric('settlingTime', currentMetrics.settlingTime),
      recommended: formatMetric(
        'settlingTime',
        recommendedMetrics.settlingTime,
      ),
      improvement: formatImprovement(improvement.settlingTime),
      improved: isImproved(improvement.settlingTime),
      improvementLabel: getImprovementLabel(improvement.settlingTime),
    },
    {
      name: 'ITAE',
      current: formatMetric('itae', currentMetrics.itae),
      recommended: formatMetric('itae', recommendedMetrics.itae),
      improvement: formatImprovement(improvement.itae),
      improved: isImproved(improvement.itae),
      improvementLabel: getImprovementLabel(improvement.itae),
    },
  ];
});

/** 格式化指标值 */
function formatMetric(
  key: 'itae' | 'overshoot' | 'riseTime' | 'settlingTime',
  val: null | number | undefined,
): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  switch (key) {
    case 'itae': {
      return val.toExponential(3);
    }
    case 'overshoot': {
      return `${val?.toFixed(1) ?? '0.0'}%`;
    }
    case 'riseTime':
    case 'settlingTime': {
      return `${val?.toFixed(1) ?? '0.0'} 秒`;
    }
    default: {
      return String(val);
    }
  }
}

/** 格式化改善幅度 */
function formatImprovement(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  const pct = val * 100;
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct?.toFixed(1) ?? '0.0'}%`;
}

/** 判断是否改善（减小为改善） */
function isImproved(val: null | number | undefined): boolean | null {
  if (val === null || val === undefined || Number.isNaN(val)) return null;
  return val > 0;
}

/** 改善/退化/持平语义标签 */
function getImprovementLabel(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '持平';
  if (val > 0) return '改善';
  if (val < 0) return '退化';
  return '持平';
}

function getImprovementStatus(
  value: number,
): NonNullable<SummaryItem['status']> {
  if (value > 0) return 'success';
  if (value < 0) return 'danger';
  return 'neutral';
}

/** ObjectSummaryBar 主指标：综合改善幅度 */
const primarySummaryItem = computed<null | SummaryItem>(() => {
  if (!simulationResult.value || compareMode.value) return null;
  const imp = simulationResult.value.improvement || {};
  const values = Object.values(imp).filter(
    (v): v is number => v !== null && v !== undefined && !Number.isNaN(v),
  );
  if (values.length === 0) {
    return {
      key: 'improvement',
      label: '综合改善幅度',
      value: '—',
      status: 'neutral',
    };
  }
  const avg = values.reduce((a, b) => a + b, 0) / values.length;
  const pct = avg * 100;
  const sign = pct >= 0 ? '+' : '';
  return {
    key: 'improvement',
    label: '综合改善幅度',
    value: `${sign}${pct.toFixed(1)}%`,
    status: getImprovementStatus(pct),
  };
});

/** ObjectSummaryBar items：当前 PID vs 推荐 PID */
const pidSummaryItems = computed<SummaryItem[]>(() => {
  if (compareMode.value || !simulationResult.value) return [];
  const fmt = (v: number | undefined) =>
    v === null || v === undefined || Number.isNaN(v)
      ? '—'
      : Number(v).toFixed(3);
  return [
    {
      key: 'cur_kp',
      label: '当前 Kp',
      value: fmt(form.currentPid.kp),
      status: 'neutral',
    },
    {
      key: 'cur_ti',
      label: '当前 Ti',
      value: fmt(form.currentPid.ti),
      status: 'neutral',
    },
    {
      key: 'cur_td',
      label: '当前 Td',
      value: fmt(form.currentPid.td),
      status: 'neutral',
    },
    {
      key: 'rec_kp',
      label: '推荐 Kp',
      value: fmt(form.recommendedPid.kp),
      status: 'primary',
    },
    {
      key: 'rec_ti',
      label: '推荐 Ti',
      value: fmt(form.recommendedPid.ti),
      status: 'primary',
    },
    {
      key: 'rec_td',
      label: '推荐 Td',
      value: fmt(form.recommendedPid.td),
      status: 'primary',
    },
  ];
});

/** ObjectSummaryBar actions */
const summaryActions = computed<SummaryAction[]>(() => [
  {
    key: 'apply',
    label: '应用建议',
    icon: 'ant-design:check-outlined',
    type: 'primary',
  },
  {
    key: 'recalculate',
    label: '重新计算',
    icon: 'ant-design:reload-outlined',
    type: 'default',
  },
  {
    key: 'export',
    label: '导出报告',
    icon: 'ant-design:download-outlined',
    type: 'default',
    disabled: true,
  },
]);

/** 摘要条动作分发 */
function onSummaryAction(key: string) {
  if (key === 'apply') {
    handleSave();
  } else if (key === 'recalculate') {
    handleSimulate();
  }
}

/** 风险等级 */
const riskLevel = computed<'HIGH' | 'LOW' | 'MEDIUM' | null>(() => {
  if (!simulationResult.value || compareMode.value) return null;
  const cur = form.currentPid;
  const rec = form.recommendedPid;
  const kpChange = Math.abs((rec.kp - cur.kp) / (cur.kp || 1));
  const tiChange = Math.abs((rec.ti - cur.ti) / (cur.ti || 1));
  const tdChange = cur.td
    ? Math.abs((rec.td - cur.td) / cur.td)
    : Math.abs(rec.td);
  const maxChange = Math.max(kpChange, tiChange, tdChange);
  if (maxChange >= 0.5) return 'HIGH';
  if (maxChange >= 0.2) return 'MEDIUM';
  return 'LOW';
});

const riskLevelColorMap: Record<'HIGH' | 'LOW' | 'MEDIUM', string> = {
  HIGH: 'red',
  MEDIUM: 'orange',
  LOW: 'green',
};

const riskLevelLabelMap: Record<'HIGH' | 'LOW' | 'MEDIUM', string> = {
  HIGH: '高风险',
  MEDIUM: '中风险',
  LOW: '低风险',
};

/** 从 URL query 解析 JSON 参数 */
function parseJsonQuery(key: string): unknown {
  const raw = route.query[key] as string | undefined;
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** 从 URL query 初始化参数 */
function initFromQuery() {
  const qModelType = route.query.modelType as TuningApi.ModelType | undefined;
  if (qModelType) {
    form.modelType = qModelType;
  }
  const qModelParams = parseJsonQuery('modelParams');
  if (qModelParams && typeof qModelParams === 'object') {
    form.modelParams = { ...form.modelParams, ...qModelParams };
  }
  const qCurrentPid = parseJsonQuery('currentPid');
  if (qCurrentPid && typeof qCurrentPid === 'object') {
    form.currentPid = { ...form.currentPid, ...qCurrentPid };
    if (pidCandidates.value[0]) {
      pidCandidates.value[0] = {
        label: '当前 PID',
        ...form.currentPid,
      };
    }
  }
  const qRecommendedPid = parseJsonQuery('recommendedPid');
  if (qRecommendedPid && typeof qRecommendedPid === 'object') {
    form.recommendedPid = { ...form.recommendedPid, ...qRecommendedPid };
  }
}

/** 切换对比模式 */
function handleToggleCompareMode(checked: boolean) {
  compareMode.value = checked;
  simulationResult.value = null;
  errorState.value = null;
  renderChart();
}

/** 添加候选 PID */
function handleAddCandidate() {
  if (pidCandidates.value.length >= 5) {
    message.warning('最多支持 5 组 PID 候选');
    return;
  }
  if (!newCandidate.label) {
    message.warning('请填写候选 PID 标签');
    return;
  }
  pidCandidates.value.push({
    label: newCandidate.label,
    kp: newCandidate.kp,
    ti: newCandidate.ti,
    td: newCandidate.td,
  });
  newCandidate.label = '';
}

/** 移除候选 PID */
function handleRemoveCandidate(index: number) {
  if (pidCandidates.value.length <= 2) {
    message.warning('对比模式至少需要 2 组 PID');
    return;
  }
  pidCandidates.value.splice(index, 1);
}

/** 执行仿真 */
async function handleSimulate() {
  // 校验模型参数
  const params: TuningApi.ModelParams = {};
  for (const f of modelParamFields.value) {
    const v = form.modelParams[f.key];
    if (v === null || v === undefined || Number.isNaN(v)) {
      message.warning(`请填写模型参数：${f.label}`);
      return;
    }
    params[f.key] = v;
  }

  if (compareMode.value) {
    // 多 PID 对比模式
    if (pidCandidates.value.length < 2) {
      message.warning('对比模式至少需要 2 组候选 PID');
      return;
    }
    loading.value = true;
    errorState.value = null;
    const hide = message.loading(
      `正在进行 ${pidCandidates.value.length} 组 PID 对比仿真…`,
      0,
    );
    try {
      const data = await comparePidsApi({
        modelType: form.modelType,
        modelParams: params,
        currentPid: form.currentPid,
        pidCandidates: pidCandidates.value,
        simDuration: form.simDuration,
        simStep: form.simStep,
        setpointStep: form.setpointStep,
      });
      simulationResult.value = data;
      renderChart();
      hide();
      message.success('多 PID 对比仿真完成');
    } catch (err) {
      hide();
      errorState.value = {
        message: '多 PID 对比仿真失败',
        detail:
          err instanceof Error
            ? err.message
            : '请检查模型参数和候选 PID 后重试',
      };
    } finally {
      loading.value = false;
    }
    return;
  }

  // 双 PID 对比模式（原逻辑）
  if (
    !form.currentPid.kp ||
    !form.currentPid.ti ||
    !form.recommendedPid.kp ||
    !form.recommendedPid.ti
  ) {
    message.warning('请完整填写当前 PID 与推荐 PID 参数');
    return;
  }

  loading.value = true;
  errorState.value = null;
  const hide = message.loading(
    `正在进行 ${form.modelType} 闭环仿真（${form.simDuration}s 时长）…`,
    0,
  );
  try {
    const data = await simulateTuningApi({
      modelType: form.modelType,
      modelParams: params,
      currentPid: {
        kp: form.currentPid.kp,
        ti: form.currentPid.ti,
        td: form.currentPid.td || 0,
      },
      recommendedPid: {
        kp: form.recommendedPid.kp,
        ti: form.recommendedPid.ti,
        td: form.recommendedPid.td || 0,
      },
      simDuration: form.simDuration,
      simStep: form.simStep,
      setpointStep: form.setpointStep,
      disturbanceType: form.disturbanceType,
    });
    simulationResult.value = data;
    renderChart();
    hide();
    message.success('仿真完成');
  } catch (err) {
    hide();
    errorState.value = {
      message: '闭环仿真失败',
      detail:
        err instanceof Error ? err.message : '请检查模型参数和 PID 配置后重试',
    };
  } finally {
    loading.value = false;
  }
}

/** 重置参数 */
function handleReset() {
  form.modelType = 'FOPDT';
  form.modelParams = { K: 1, tau: 10, theta: 2, T1: 10, T2: 5 };
  form.currentPid = { kp: 1, ti: 10, td: 0 };
  form.recommendedPid = { kp: 1.2, ti: 8, td: 0.5 };
  form.simDuration = 600;
  form.simStep = 1;
  form.setpointStep = 1;
  form.disturbanceType = 'none';
  simulationResult.value = null;
  errorState.value = null;
  renderChart();
  message.info('已重置参数');
}

/** 渲染仿真对比图 */
function renderChart() {
  const data = simulationResult.value;
  if (!data || !data.timestamps || data.timestamps.length === 0) {
    renderEcharts({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  const { timestamps } = data;
  const enableDataZoom = timestamps.length > 500;

  if (compareMode.value && data.candidateResponses) {
    // 多 PID 对比模式：叠加所有候选 PV 曲线
    const series: any[] = [
      {
        data: data.recommendedResponse.sp,
        itemStyle: { color: themeColors.value.NEUTRAL },
        lineStyle: { width: 1.5, type: 'dashed' },
        name: 'SP 设定值',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
    ];
    const legendNames = ['SP 设定值'];
    data.candidateResponses.forEach((c, idx) => {
      const color = candidateColors.value[idx % candidateColors.value.length];
      series.push({
        data: c.response.pv,
        itemStyle: { color },
        lineStyle: { width: 2 },
        name: c.label,
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      });
      legendNames.push(c.label);
    });

    renderEcharts({
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
        top: 60,
      },
      legend: {
        data: legendNames,
        top: 5,
      },
      series,
      tooltip: {
        axisPointer: { type: 'cross' },
        trigger: 'axis',
        valueFormatter: (val) =>
          val === null || val === undefined ? '—' : Number(val).toFixed(4),
      },
      xAxis: {
        axisLabel: {
          formatter: (val: string) => `${Number(val).toFixed(0)}s`,
        },
        data: timestamps,
        name: '时间 (秒)',
        nameGap: 30,
        nameLocation: 'middle',
        type: 'category',
      },
      yAxis: [
        {
          axisLabel: { formatter: '{value}' },
          name: 'PV / SP',
          nameTextStyle: { color: chartTextColor.value },
          type: 'value',
        },
      ],
    });
    return;
  }

  // 双 PID 对比模式（原逻辑）
  const { currentResponse, recommendedResponse } = data;

  renderEcharts({
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
      data: [
        'SP 设定值',
        '当前 PID PV',
        '推荐 PID PV',
        '当前 PID OP',
        '推荐 PID OP',
      ],
      top: 5,
    },
    series: [
      {
        data: recommendedResponse.sp,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 1.5 },
        name: 'SP 设定值',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: currentResponse.pv,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        name: '当前 PID PV',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: recommendedResponse.pv,
        itemStyle: { color: themeColors.value.DANGER },
        lineStyle: { width: 2 },
        name: '推荐 PID PV',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: currentResponse.op,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: '当前 PID OP',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 1,
      },
      {
        data: recommendedResponse.op,
        itemStyle: { color: themeColors.value.DANGER },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: '推荐 PID OP',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 1,
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
        formatter: (val: string) => `${Number(val).toFixed(0)}s`,
      },
      data: timestamps,
      name: '时间 (秒)',
      nameGap: 30,
      nameLocation: 'middle',
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { formatter: '{value}' },
        name: 'PV / SP',
        nameTextStyle: { color: chartTextColor.value },
        type: 'value',
      },
      {
        axisLabel: { formatter: '{value}' },
        name: 'OP',
        nameTextStyle: { color: chartTextColor.value },
        splitLine: { show: false },
        type: 'value',
      },
    ],
  });
}

/** 保存仿真结果 */
async function handleSave() {
  if (!simulationResult.value) {
    message.warning('请先执行仿真');
    return;
  }
  if (!loopId.value) {
    message.warning('请输入回路 ID');
    return;
  }

  saving.value = true;
  try {
    const modelParams: TuningApi.ModelParams = {};
    for (const f of modelParamFields.value) {
      modelParams[f.key] = form.modelParams[f.key];
    }

    await createTuningTaskApi({
      loopId: loopId.value,
      modelType: form.modelType,
      modelParams,
      algorithm: 'IMC',
      recommendedPid: {
        kp: form.recommendedPid.kp,
        ti: form.recommendedPid.ti,
        td: form.recommendedPid.td || 0,
      },
      currentPid: {
        kp: form.currentPid.kp,
        ti: form.currentPid.ti,
        td: form.currentPid.td || 0,
      },
      simulationResult: simulationResult.value as TuningApi.SimulationResult,
      status: 'SIMULATED',
      // Phase 2：多 PID 候选元数据
      pidCandidates: compareMode.value
        ? { candidates: pidCandidates.value }
        : undefined,
      candidateResults: compareMode.value
        ? { responses: simulationResult.value.candidateResponses }
        : undefined,
    });
    message.success('仿真结果已保存');
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  initFromQuery();
  nextTick(() => {
    renderChart();
  });
});

/** 深色模式切换时重绘 ECharts 图表 */
watch(isDark, () => {
  nextTick(() => {
    renderChart();
  });
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="闭环仿真"
      subtitle="对比当前 PID 与推荐 PID 的响应曲线和性能指标。"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="run"
          label="运行仿真"
          variant="primary"
          :loading="loading"
          @click="handleSimulate"
        />
        <ClpmToolbarButton
          icon="ant-design:undo-outlined"
          label="重置"
          @click="handleReset"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          disabled
          disabled-reason="导出功能开发中，待后端接口支持"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 常驻风险提示横幅 -->
    <Alert
      class="mt-3"
      type="warning"
      show-icon
      banner
      :closable="false"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
    />

    <!-- 对比模式切换（Phase 2） -->
    <ClpmDataCanvas class="mt-4" title="仿真模式">
      <div class="flex items-center gap-4">
        <span class="text-sm text-gray-600">双 PID 对比</span>
        <a-switch
          v-model:checked="compareMode"
          @change="handleToggleCompareMode"
        />
        <span class="text-sm text-gray-600">多 PID 对比（Phase 2）</span>
        <span class="ml-4 rounded bg-blue-50 px-2 py-1 text-xs text-blue-600">
          {{
            compareMode
              ? `当前：多 PID 对比（${pidCandidates.length} 组候选）`
              : '当前：双 PID 对比（当前 vs 推荐）'
          }}
        </span>
      </div>
    </ClpmDataCanvas>

    <!-- 主区域：仿真图优先（左 70%）+ 参数表单（右 30%）-->
    <div class="mt-4 flex flex-col gap-4 lg:flex-row">
      <!-- 左侧：仿真图 + 指标（主体） -->
      <div class="flex flex-1 flex-col gap-4" style="min-width: 0">
        <!-- PID 对比摘要条（仅双 PID 模式） -->
        <ClpmObjectSummaryBar
          v-if="simulationResult && !compareMode"
          title="PID 整定对比"
          subtitle="当前 PID vs 推荐 PID"
          :primary-item="primarySummaryItem"
          :items="pidSummaryItems"
          :actions="summaryActions"
          @action="onSummaryAction"
        />

        <!-- 风险提示区（仅双 PID 模式） -->
        <Card
          v-if="simulationResult && !compareMode"
          size="small"
          title="风险提示"
        >
          <Descriptions :column="{ xs: 1, sm: 3 }" size="small">
            <DescriptionsItem label="风险等级">
              <Tag v-if="riskLevel" :color="riskLevelColorMap[riskLevel]">
                {{ riskLevelLabelMap[riskLevel] }}（{{ riskLevel }}）
              </Tag>
              <span v-else>—</span>
            </DescriptionsItem>
            <DescriptionsItem label="回退方案">—</DescriptionsItem>
            <DescriptionsItem label="适用边界">—</DescriptionsItem>
          </Descriptions>
        </Card>

        <!-- 仿真对比图 -->
        <ClpmDataCanvas
          title="仿真对比图"
          :description="
            compareMode
              ? '多 PID 候选 PV 响应叠加对比，颜色区分不同 PID 组合。'
              : '双 Y 轴：左 PV/SP，右 OP。蓝色为当前 PID，红色为推荐 PID。'
          "
        >
          <!-- P1-023：错误状态覆盖（仿真失败时持久展示，带重试） -->
          <ClpmStateOverlay
            v-if="errorState"
            status="error"
            :error-message="errorState.message"
            :error-detail="errorState.detail"
            @retry="handleSimulate"
          />
          <!-- P1-023：空状态覆盖（无结果且无错误时） -->
          <ClpmStateOverlay
            v-else-if="!simulationResult"
            status="empty"
            empty-description="请配置模型与 PID 参数后点击「运行仿真」"
          />
          <!-- success：正常展示仿真图 -->
          <Spin v-else :spinning="loading">
            <EchartsUI ref="chartRef" height="500px" />
          </Spin>
        </ClpmDataCanvas>

        <!-- 性能指标对比表格 -->
        <ClpmDataCanvas
          v-if="simulationResult && metricRows.length > 0"
          :title="compareMode ? '多 PID 性能指标对比' : '性能指标对比'"
        >
          <Table
            :columns="metricColumns"
            :data-source="metricRows"
            :pagination="false"
            :row-key="(record: { name: string }) => record.name"
            size="middle"
          >
            <template v-if="!compareMode" #bodyCell="{ column, record }">
              <template v-if="column.key === 'improvement'">
                <span
                  class="inline-flex items-center gap-1 font-medium"
                  :style="{
                    color:
                      record.improved === null
                        ? themeColors.NEUTRAL
                        : record.improved
                          ? themeColors.SUCCESS
                          : themeColors.DANGER,
                  }"
                >
                  <IconifyIcon
                    v-if="record.improved === true"
                    icon="ant-design:rise-outlined"
                  />
                  <IconifyIcon
                    v-else-if="record.improved === false"
                    icon="ant-design:fall-outlined"
                  />
                  <IconifyIcon v-else icon="ant-design:minus-outlined" />
                  <span>{{ record.improvementLabel }}</span>
                  <span class="text-gray-500">{{ record.improvement }}</span>
                </span>
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>
      </div>

      <!-- 右侧：参数表单（30%） -->
      <div class="flex flex-col gap-4 lg:w-1/3" style="min-width: 320px">
        <ClpmDataCanvas title="参数配置">
          <Form layout="vertical">
            <div class="mb-2 text-sm font-medium text-gray-700">模型配置</div>
            <FormItem label="模型类型">
              <Select
                v-model:value="form.modelType"
                :options="modelTypeOptions"
                style="width: 100%"
              />
            </FormItem>
            <div class="grid grid-cols-2 gap-2">
              <FormItem
                v-for="f in modelParamFields"
                :key="f.key"
                :label="f.label"
              >
                <InputNumber
                  v-model:value="(form.modelParams as any)[f.key]"
                  style="width: 100%"
                  :step="0.1"
                />
              </FormItem>
            </div>

            <div
              class="mt-2 mb-2 border-t border-gray-100 pt-3 text-sm font-medium text-gray-700"
            >
              PID 参数
            </div>

            <!-- 双 PID 模式：当前 + 推荐 -->
            <template v-if="!compareMode">
              <div class="grid grid-cols-3 gap-2">
                <FormItem label="当前 Kp">
                  <InputNumber
                    v-model:value="form.currentPid.kp"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
                <FormItem label="当前 Ti">
                  <InputNumber
                    v-model:value="form.currentPid.ti"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
                <FormItem label="当前 Td">
                  <InputNumber
                    v-model:value="form.currentPid.td"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
                <FormItem label="推荐 Kp">
                  <InputNumber
                    v-model:value="form.recommendedPid.kp"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
                <FormItem label="推荐 Ti">
                  <InputNumber
                    v-model:value="form.recommendedPid.ti"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
                <FormItem label="推荐 Td">
                  <InputNumber
                    v-model:value="form.recommendedPid.td"
                    style="width: 100%"
                    :step="0.1"
                  />
                </FormItem>
              </div>
            </template>

            <!-- 多 PID 对比模式：候选列表 + 新增表单 -->
            <template v-else>
              <div class="mb-2 text-xs text-gray-500">
                候选 PID 列表（2-5 组）
              </div>
              <div class="flex flex-col gap-2">
                <div
                  v-for="(c, idx) in pidCandidates"
                  :key="idx"
                  class="flex items-center gap-2 rounded border border-gray-200 p-2"
                >
                  <span
                    class="inline-block h-3 w-3 rounded-full"
                    :style="{
                      backgroundColor:
                        candidateColors[idx % candidateColors.length],
                    }"
                  ></span>
                  <span class="flex-1 text-sm font-medium">{{ c.label }}</span>
                  <span class="font-mono text-xs text-gray-600">
                    Kp={{ c.kp }} Ti={{ c.ti }} Td={{ c.td }}
                  </span>
                  <Button
                    type="link"
                    size="small"
                    danger
                    @click="handleRemoveCandidate(idx)"
                  >
                    移除
                  </Button>
                </div>
              </div>

              <!-- 新增候选 -->
              <div
                class="mt-3 rounded border border-dashed border-gray-300 p-3"
              >
                <div class="mb-2 text-xs text-gray-500">添加新候选 PID</div>
                <FormItem label="标签" class="mb-2">
                  <a-input
                    v-model:value="newCandidate.label"
                    placeholder="如 SIMC λ=θ"
                    style="width: 100%"
                  />
                </FormItem>
                <div class="grid grid-cols-3 gap-2">
                  <FormItem label="Kp" class="mb-0">
                    <InputNumber
                      v-model:value="newCandidate.kp"
                      style="width: 100%"
                      :step="0.1"
                    />
                  </FormItem>
                  <FormItem label="Ti" class="mb-0">
                    <InputNumber
                      v-model:value="newCandidate.ti"
                      style="width: 100%"
                      :step="0.1"
                    />
                  </FormItem>
                  <FormItem label="Td" class="mb-0">
                    <InputNumber
                      v-model:value="newCandidate.td"
                      style="width: 100%"
                      :step="0.1"
                    />
                  </FormItem>
                </div>
                <Button
                  type="dashed"
                  size="small"
                  class="mt-2"
                  block
                  @click="handleAddCandidate"
                >
                  + 添加候选
                </Button>
              </div>
            </template>

            <div
              class="mt-2 mb-2 border-t border-gray-100 pt-3 text-sm font-medium text-gray-700"
            >
              仿真参数
            </div>
            <div class="grid grid-cols-2 gap-2">
              <FormItem label="仿真时长 (秒)">
                <InputNumber
                  v-model:value="form.simDuration"
                  style="width: 100%"
                  :min="1"
                  :step="10"
                />
              </FormItem>
              <FormItem label="仿真步长 (秒)">
                <InputNumber
                  v-model:value="form.simStep"
                  style="width: 100%"
                  :min="0.1"
                  :step="0.1"
                />
              </FormItem>
              <FormItem label="设定值阶跃">
                <InputNumber
                  v-model:value="form.setpointStep"
                  style="width: 100%"
                  :step="0.1"
                />
              </FormItem>
              <FormItem label="扰动类型">
                <Select
                  v-model:value="form.disturbanceType"
                  :options="disturbanceOptions"
                  style="width: 100%"
                />
              </FormItem>
            </div>
          </Form>
        </ClpmDataCanvas>

        <!-- 保存仿真结果 -->
        <ClpmDataCanvas v-if="simulationResult" title="保存仿真结果">
          <div class="flex flex-col gap-3">
            <FormItem label="回路 ID">
              <InputNumber
                v-model:value="loopId"
                style="width: 100%"
                placeholder="请输入回路 ID"
              />
            </FormItem>
            <Button type="primary" :loading="saving" block @click="handleSave">
              保存仿真结果
            </Button>
          </div>
        </ClpmDataCanvas>
      </div>
    </div>
  </Page>
</template>
