<script lang="ts" setup>
/**
 * S7-TUNE-004 闭环仿真页
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部参数输入区（模型类型/模型参数/当前 PID/推荐 PID/仿真参数）
 * - 中部仿真对比图（ECharts 双 Y 轴：左 PV/SP，右 OP）
 * - 性能指标对比表格（上升时间/超调量/稳定时间/ITAE）
 * - 底部保存仿真结果（调用 createTuningTaskApi）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Card,
  Form,
  FormItem,
  InputNumber,
  message,
  Select,
  Spin,
  Table,
} from 'ant-design-vue';

import { createTuningTaskApi, simulateTuningApi } from '#/api/tuning';

defineOptions({ name: 'TuningSimulation' });

const route = useRoute();

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
  { title: '性能指标', dataIndex: 'name', key: 'name', width: 160 },
  { title: '当前 PID', dataIndex: 'current', key: 'current', width: 160 },
  {
    title: '推荐 PID',
    dataIndex: 'recommended',
    key: 'recommended',
    width: 160,
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
    },
    {
      name: '超调量',
      current: formatMetric('overshoot', currentMetrics.overshoot),
      recommended: formatMetric('overshoot', recommendedMetrics.overshoot),
      improvement: formatImprovement(improvement.overshoot),
      improved: isImproved(improvement.overshoot),
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
    },
    {
      name: 'ITAE',
      current: formatMetric('itae', currentMetrics.itae),
      recommended: formatMetric('itae', recommendedMetrics.itae),
      improvement: formatImprovement(improvement.itae),
      improved: isImproved(improvement.itae),
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
        itemStyle: { color: '#52c41a' },
        lineStyle: { width: 1.5 },
        name: 'SP 设定值',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: currentResponse.pv,
        itemStyle: { color: '#1890ff' },
        lineStyle: { width: 2 },
        name: '当前 PID PV',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: recommendedResponse.pv,
        itemStyle: { color: '#ff4d4f' },
        lineStyle: { width: 2 },
        name: '推荐 PID PV',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: currentResponse.op,
        itemStyle: { color: '#1890ff' },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: '当前 PID OP',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 1,
      },
      {
        data: recommendedResponse.op,
        itemStyle: { color: '#ff4d4f' },
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
        nameTextStyle: { color: '#1890ff' },
        type: 'value',
      },
      {
        axisLabel: { formatter: '{value}' },
        name: 'OP',
        nameTextStyle: { color: '#fa8c16' },
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
});
</script>

<template>
  <Page title="闭环仿真">
    <!-- 顶部参数输入区 -->
    <Card class="mb-4" title="参数配置">
      <Form layout="vertical">
        <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
          <!-- 左列：模型配置 -->
          <div>
            <div class="mb-2 text-sm font-medium text-gray-700">模型配置</div>
            <FormItem label="模型类型">
              <Select
                v-model:value="form.modelType"
                :options="modelTypeOptions"
                style="width: 100%"
              />
            </FormItem>
            <div class="grid grid-cols-3 gap-2">
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
          </div>

          <!-- 右列：PID 参数 -->
          <div>
            <div class="mb-2 text-sm font-medium text-gray-700">PID 参数</div>
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
          </div>
        </div>

        <div class="mt-2 border-t border-gray-100 pt-3">
          <div class="mb-2 text-sm font-medium text-gray-700">仿真参数</div>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
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
        </div>

        <div class="mt-2 flex justify-end">
          <Button type="primary" :loading="loading" @click="handleSimulate">
            执行仿真
          </Button>
        </div>
      </Form>
    </Card>

    <!-- 仿真结果区 -->
    <Card v-if="simulationResult" class="mb-4" title="仿真对比图">
      <Spin :spinning="loading">
        <EchartsUI ref="chartRef" height="420px" />
      </Spin>
    </Card>

    <!-- 性能指标对比表格 -->
    <Card v-if="simulationResult" class="mb-4" title="性能指标对比">
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
              :style="{
                color:
                  record.improved === null
                    ? ''
                    : record.improved
                      ? '#52c41a'
                      : '#ff4d4f',
                fontWeight: 500,
              }"
            >
              {{ record.improvement }}
            </span>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 底部操作按钮 -->
    <Card v-if="simulationResult" title="保存仿真结果">
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-gray-500">回路 ID：</span>
        <InputNumber
          v-model:value="loopId"
          style="width: 240px"
          placeholder="请输入回路 ID"
        />
        <Button type="primary" :loading="saving" @click="handleSave">
          保存仿真结果
        </Button>
      </div>
    </Card>
  </Page>
</template>
