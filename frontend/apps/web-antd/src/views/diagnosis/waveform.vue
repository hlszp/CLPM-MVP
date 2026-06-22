<script lang="ts" setup>
/**
 * S4-DIAG-009 波形查看页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 顶部筛选栏（回路选择/时间范围选择/降采样开关）
 * - ECharts 波形图展示 PV/SP/OP 趋势
 * - PV 线按质量码断线渲染：
 *   - Good: 实线（工业蓝 #1890ff）
 *   - Bad: 灰色虚线（null 值自动断线）
 *   - Uncertain: 黄色虚线
 * - SP 线：绿色实线
 * - OP 线：橙色实线
 * - 支持 dataZoom 缩放
 * - 散点图模式（PV-OP 拟合，阀门粘滞检测）—— 可切换的 Tab
 * - 诊断标签卡片区域（显示当前回路的诊断结果）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi, DiagnosisLabel, Quality } from '#/api/diagnosis';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Card,
  DatePicker,
  message,
  Select,
  Spin,
  Switch,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi, getWaveformApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';

defineOptions({ name: 'DiagnosisWaveform' });

const route = useRoute();

const loading = ref(false);
const detailLoading = ref(false);
const waveform = ref<DiagnosisApi.WaveformResult | null>(null);
const diagnosisDetail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const loopOptions = ref<{ label: string; value: string }[]>([]);

const filter = reactive({
  loopId: (route.query.loopId as string) || '',
  timeRange: [dayjs().subtract(24, 'hour'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  downsample: true,
});

const activeTab = ref<'scatter' | 'waveform'>('waveform');

/** 8 类诊断标签颜色映射 */
const labelColorMap: Record<DiagnosisLabel, string> = {
  OSCILLATION: 'red',
  VALVE_STICTION: 'orange',
  OVERAGGRESSIVE: 'purple',
  OVERCONSERVATIVE: 'blue',
  EXTERNAL_DISTURBANCE: 'cyan',
  QUALITY_ABNORMAL: 'default',
  OUTPUT_SATURATION: 'gold',
  MANUAL_REVIEW: 'default',
};

const labelNameMap: Record<DiagnosisLabel, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  EXTERNAL_DISTURBANCE: '外扰频繁',
  QUALITY_ABNORMAL: 'PV 质量异常',
  OUTPUT_SATURATION: '输出饱和',
  MANUAL_REVIEW: '人工复核',
};

// ECharts refs
const waveformChartRef = ref<EchartsUIType>();
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderWaveform } = useEcharts(waveformChartRef);
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

const pageTitle = computed(() => {
  if (waveform.value?.tagName) {
    return `波形分析 - ${waveform.value.tagName}`;
  }
  return '波形分析';
});

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 1000 });
    const list = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    // 若未指定 loopId，默认选第一个
    if (!filter.loopId && list.length > 0) {
      const first = list[0];
      if (first) {
        filter.loopId = first.loopId;
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载波形数据 */
async function loadWaveform() {
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
  loading.value = true;
  try {
    const data = await getWaveformApi(filter.loopId, {
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      downsample: filter.downsample,
      maxPoints: 2000,
    });
    waveform.value = data;
    renderWaveformChart();
    renderScatterChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 加载诊断详情 */
async function loadDiagnosisDetail() {
  if (!filter.loopId) return;
  detailLoading.value = true;
  try {
    const data = await getDiagnosisDetailApi(filter.loopId, 'last_24_hours');
    diagnosisDetail.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    detailLoading.value = false;
  }
}

/**
 * 构建按质量码分段的 PV 数据
 * - Good: 实线（工业蓝 #1890ff）
 * - Bad: null（自动断线）
 * - Uncertain: 黄色虚线
 */
function buildPvSeriesByQuality(
  timestamps: number[],
  pv: (null | number)[],
  pvQuality: Quality[],
) {
  const goodData: (null | number)[] = [];
  const badData: (null | number)[] = [];
  const uncertainData: (null | number)[] = [];

  for (let i = 0; i < timestamps.length; i++) {
    const q = pvQuality[i];
    const v = pv[i] ?? null;
    switch (q) {
      case 'Bad': {
        goodData.push(null);
        badData.push(v);
        uncertainData.push(null);

        break;
      }
      case 'Good': {
        goodData.push(v);
        badData.push(null);
        uncertainData.push(null);

        break;
      }
      case 'Uncertain': {
        goodData.push(null);
        badData.push(null);
        uncertainData.push(v);

        break;
      }
      default: {
        goodData.push(null);
        badData.push(null);
        uncertainData.push(null);
      }
    }
  }

  return { badData, goodData, uncertainData };
}

/** 渲染波形图 */
function renderWaveformChart() {
  const data = waveform.value;
  if (!data || !data.timestamps || data.timestamps.length === 0) {
    renderWaveform({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  const { timestamps, pv, sp, op, pvQuality } = data;
  const { goodData, badData, uncertainData } = buildPvSeriesByQuality(
    timestamps,
    pv,
    pvQuality,
  );
  const enableDataZoom = timestamps.length > 1000;

  renderWaveform({
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
      data: ['PV (Good)', 'PV (Bad)', 'PV (Uncertain)', 'SP', 'OP'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: goodData,
        itemStyle: { color: '#1890ff' },
        lineStyle: { width: 2 },
        name: 'PV (Good)',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: badData,
        itemStyle: { color: '#d9d9d9' },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: 'PV (Bad)',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: uncertainData,
        itemStyle: { color: '#faad14' },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: 'PV (Uncertain)',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: sp,
        itemStyle: { color: '#52c41a' },
        lineStyle: { width: 1.5 },
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: op,
        itemStyle: { color: '#fa8c16' },
        lineStyle: { width: 1.5 },
        name: 'OP',
        showSymbol: false,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(3),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          const d = new Date(Number(val));
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const mo = String(d.getMonth() + 1).padStart(2, '0');
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

/** 渲染散点图（PV-OP 拟合，阀门粘滞检测） */
function renderScatterChart() {
  const data = waveform.value;
  if (!data || !data.pv || !data.op || data.pv.length === 0) {
    renderScatter({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  // 构建 PV-OP 散点数据
  const scatterData: [number, number][] = [];
  for (let i = 0; i < data.pv.length; i++) {
    const pv = data.pv[i];
    const op = data.op[i];
    if (pv !== null && pv !== undefined && op !== null && op !== undefined) {
      scatterData.push([op, pv]);
    }
  }

  renderScatter({
    backgroundColor: 'transparent',
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 40,
    },
    series: [
      {
        data: scatterData,
        itemStyle: {
          color: '#1890ff',
          opacity: 0.5,
        },
        name: 'PV-OP',
        symbolSize: 5,
        type: 'scatter',
      },
    ],
    tooltip: {
      formatter: (params: any) => {
        return `OP: ${Number(params.value[0]).toFixed(3)}<br/>PV: ${Number(
          params.value[1],
        ).toFixed(3)}`;
      },
      trigger: 'item',
    },
    xAxis: {
      name: 'OP',
      nameGap: 30,
      nameLocation: 'middle',
      type: 'value',
    },
    yAxis: {
      name: 'PV',
      nameGap: 40,
      nameLocation: 'middle',
      type: 'value',
    },
  });
}

function handleSearch() {
  loadWaveform();
  loadDiagnosisDetail();
}

watch(activeTab, (val) => {
  if (val === 'scatter') {
    renderScatterChart();
  } else {
    renderWaveformChart();
  }
});

watch(
  () => filter.loopId,
  (val) => {
    if (val) {
      handleSearch();
    }
  },
);

onMounted(() => {
  loadLoopOptions().then(() => {
    if (filter.loopId) {
      handleSearch();
    }
  });
});
</script>

<template>
  <Page :title="pageTitle">
    <!-- 筛选栏 -->
    <Card class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">回路：</span>
          <Select
            v-model:value="filter.loopId"
            placeholder="选择回路"
            style="width: 240px"
            show-search
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) => option.label.includes(input)
            "
            @change="handleSearch"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">时间范围：</span>
          <DatePicker.RangePicker
            v-model:value="filter.timeRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始时间', '结束时间']"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">降采样：</span>
          <Switch v-model:checked="filter.downsample" />
        </div>
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>
    </Card>

    <!-- 诊断标签卡片区域 -->
    <Card v-if="diagnosisDetail" class="mb-4" title="诊断结果">
      <Spin :spinning="detailLoading">
        <div class="flex flex-wrap items-center gap-3">
          <div class="text-sm text-gray-500">回路位号：</div>
          <div class="font-medium">{{ diagnosisDetail.tagName }}</div>
          <div class="ml-4 text-sm text-gray-500">综合评分：</div>
          <div class="font-medium text-blue-600">
            {{ Number(diagnosisDetail.compositeScore).toFixed(2) }}
          </div>
          <div class="ml-4 text-sm text-gray-500">融合置信度：</div>
          <div class="font-medium">
            {{ Number(diagnosisDetail.fusedConfidence).toFixed(2) }}
          </div>
          <div class="ml-4 text-sm text-gray-500">诊断标签：</div>
          <Tag
            v-for="item in diagnosisDetail.diagnosisLabels"
            :key="item.label"
            :color="labelColorMap[item.label]"
          >
            {{ item.labelName || labelNameMap[item.label] }} ({{
              Number(item.confidence).toFixed(2)
            }})
          </Tag>
        </div>
      </Spin>
    </Card>

    <!-- 波形/散点图 Tab -->
    <Card>
      <Tabs v-model:active-key="activeTab">
        <Tabs.TabPane key="waveform" tab="波形趋势">
          <Spin :spinning="loading">
            <EchartsUI ref="waveformChartRef" height="420px" />
          </Spin>
        </Tabs.TabPane>
        <Tabs.TabPane key="scatter" tab="PV-OP 散点（粘滞检测）">
          <Spin :spinning="loading">
            <EchartsUI ref="scatterChartRef" height="420px" />
          </Spin>
        </Tabs.TabPane>
      </Tabs>
    </Card>
  </Page>
</template>
