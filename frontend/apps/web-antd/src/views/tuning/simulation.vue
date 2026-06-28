<script lang="ts" setup>
/**
 * S7-TUNE-004 闭环仿真页
 *
 * C6 改造：仿真图优先布局
 * - 顶部工具栏：运行仿真/重置/导出（ClpmToolbarButton 图标化）
 * - 常驻风险提示横幅
 * - ObjectSummaryBar：PID 对比 + 改善幅度主指标
 * - 主区域左 70%：风险提示卡 + 仿真对比图 + 性能指标表（改善/退化标识）
 * - 主区域右 30%：参数配置表单 + 保存仿真结果
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { Page } from '@vben/common-ui';
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
  ClpmDataCanvas,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmToolbarButton,
  type SummaryAction,
  type SummaryItem,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { createTuningTaskApi, simulateTuningApi } from '#/api/tuning';

defineOptions({ name: 'TuningSimulation' });

const route = useRoute();
const { isDark, themeColors, chartTextColor } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
const simulationResult = ref<null | TuningApi.SimulationResult>(null);
const loopId = ref<string>((route.query.loopId as string) || '');

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

/** 性能指标表格列 */
const metricColumns = [
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

/** 指标行数据 */
const metricRows = computed(() => {
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
  // improvement > 0 表示减小（改善）
  return val > 0;
}

/** 改善/退化/持平语义标签 */
function getImprovementLabel(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '持平';
  if (val > 0) return '改善';
  if (val < 0) return '退化';
  return '持平';
}

/** ObjectSummaryBar 主指标：综合改善幅度 */
const primarySummaryItem = computed<SummaryItem | null>(() => {
  if (!simulationResult.value) return null;
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
    status: pct > 0 ? 'success' : pct < 0 ? 'danger' : 'neutral',
  };
});

/** ObjectSummaryBar items：当前 PID vs 推荐 PID（6 个字段） */
const pidSummaryItems = computed<SummaryItem[]>(() => {
  if (!simulationResult.value) return [];
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
  },
]);

/** 摘要条动作分发 */
function onSummaryAction(key: string) {
  if (key === 'apply') {
    handleSave();
  } else if (key === 'recalculate') {
    handleSimulate();
  } else if (key === 'export') {
    handleExport();
  }
}

/** 风险等级：根据 PID 参数变化幅度推导（后端字段待补，前端先行计算） */
const riskLevel = computed<'HIGH' | 'LOW' | 'MEDIUM' | null>(() => {
  if (!simulationResult.value) return null;
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

/** 风险等级颜色映射 */
const riskLevelColorMap: Record<'HIGH' | 'LOW' | 'MEDIUM', string> = {
  HIGH: 'red',
  MEDIUM: 'orange',
  LOW: 'green',
};

/** 风险等级中文映射 */
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
  }
  const qRecommendedPid = parseJsonQuery('recommendedPid');
  if (qRecommendedPid && typeof qRecommendedPid === 'object') {
    form.recommendedPid = { ...form.recommendedPid, ...qRecommendedPid };
  }
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
  // 校验 PID 参数
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
    message.success('仿真完成');
  } catch {
    // 错误已由拦截器处理
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
  renderChart();
  message.info('已重置参数');
}

/** 导出报告（占位，待导出接口接入） */
function handleExport() {
  if (!simulationResult.value) {
    message.warning('请先执行仿真');
    return;
  }
  message.info('导出功能开发中');
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

  const { timestamps, currentResponse, recommendedResponse } = data;
  const enableDataZoom = timestamps.length > 500;

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
    // 构造模型参数（仅包含当前模型类型所需字段）
    const modelParams: TuningApi.ModelParams = {};
    for (const f of modelParamFields.value) {
      modelParams[f.key] = form.modelParams[f.key];
    }

    await createTuningTaskApi({
      loopId: loopId.value,
      modelType: form.modelType,
      modelParams,
      algorithm: 'IMC', // 仿真保存默认算法
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
      subtitle="比较当前 PID 与推荐 PID 的响应曲线和性能指标。"
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
          @click="handleExport"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 常驻风险提示横幅：不可关闭 -->
    <Alert
      class="mt-3"
      type="warning"
      show-icon
      banner
      :closable="false"
      message="平台只输出整定建议、证据和风险，不直接修改 DCS 参数。参数由授权人员人工实施并留痕。"
    />

    <!-- 主区域：仿真图优先（左 70%）+ 参数表单（右 30%）-->
    <div class="mt-4 flex flex-col gap-4 lg:flex-row">
      <!-- 左侧：仿真图 + 指标（主体） -->
      <div class="flex flex-1 flex-col gap-4" style="min-width: 0">
        <!-- PID 对比摘要条 -->
        <ClpmObjectSummaryBar
          v-if="simulationResult"
          title="PID 整定对比"
          subtitle="当前 PID vs 推荐 PID"
          :primary-item="primarySummaryItem"
          :items="pidSummaryItems"
          :actions="summaryActions"
          @action="onSummaryAction"
        />

        <!-- 风险提示区 -->
        <Card v-if="simulationResult" size="small" title="风险提示">
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

        <!-- 仿真对比图（主体，优先展示） -->
        <ClpmDataCanvas
          title="仿真对比图"
          description="双 Y 轴：左 PV/SP，右 OP。蓝色为当前 PID，红色为推荐 PID。"
        >
          <Spin :spinning="loading">
            <EchartsUI ref="chartRef" height="500px" />
          </Spin>
        </ClpmDataCanvas>

        <!-- 性能指标对比表格（改善/退化语义标识） -->
        <ClpmDataCanvas v-if="simulationResult" title="性能指标对比">
          <Table
            :columns="metricColumns"
            :data-source="metricRows"
            :pagination="false"
            :row-key="(record: { name: string }) => record.name"
            size="middle"
          >
            <template #bodyCell="{ column, record }">
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
                  <IconifyIcon
                    v-else
                    icon="ant-design:minus-outlined"
                  />
                  <span>{{ record.improvementLabel }}</span>
                  <span class="text-gray-500">{{ record.improvement }}</span>
                </span>
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>
      </div>

      <!-- 右侧：参数表单（30%） -->
      <div
        class="flex flex-col gap-4 lg:w-1/3"
        style="min-width: 320px"
      >
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
            <Button
              type="primary"
              :loading="saving"
              block
              @click="handleSave"
            >
              保存仿真结果
            </Button>
          </div>
        </ClpmDataCanvas>
      </div>
    </div>
  </Page>
</template>
