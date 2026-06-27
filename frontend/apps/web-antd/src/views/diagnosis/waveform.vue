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

import type { DiagnosisApi, Quality } from '#/api/diagnosis';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  DatePicker,
  message,
  Select,
  Spin,
  Switch,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  ClpmDataCanvas,
  ClpmPageToolbar,
} from '#/components/clpm';
import { getDiagnosisDetailApi, getWaveformApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

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
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

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
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
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
      case 'BAD': {
        goodData.push(null);
        badData.push(v);
        uncertainData.push(null);

        break;
      }
      case 'GOOD': {
        goodData.push(v);
        badData.push(null);
        uncertainData.push(null);

        break;
      }
      case 'UNCERTAIN': {
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

/**
 * LTTB（Largest Triangle Three Buckets）降采样算法
 * 对齐 FDS §5.4.3 前端二次降采样要求
 *
 * 算法要点：
 * - 保留首尾两个点
 * - 将数据分桶，每个桶内选择与前后点构成最大三角形面积的点
 * - 时间复杂度 O(n)
 *
 * @param data 时序数据数组，每项为 [x, y]（x 通常为时间戳，y 为数值）
 * @param targetPoints 目标点数，默认 2000
 * @returns 降采样后保留点的原始索引数组（按时间升序）
 */
function lttbDownsample(
  data: [number, number][],
  targetPoints = 2000,
): number[] {
  const n = data.length;
  // 数据量未超过目标点数或目标点数过小，无需降采样
  if (n <= targetPoints || targetPoints < 3) {
    return Array.from({ length: n }, (_, i) => i);
  }

  const sampled: number[] = [];
  // 除首尾两点外，中间数据按桶划分，每个桶选取一个代表点
  const bucketSize = (n - 2) / (targetPoints - 2);
  // a 为上一个被选中点的索引，初始为首点
  let a = 0;
  sampled.push(0);

  for (let i = 0; i < targetPoints - 2; i++) {
    // 当前桶的索引范围（左闭右开）
    const bucketStart = Math.floor((i + 1) * bucketSize) + 1;
    const bucketEnd = Math.min(Math.floor((i + 2) * bucketSize) + 1, n - 1);

    // 计算下一个桶的平均点，作为三角形第三个顶点的参考
    let avgX = 0;
    let avgY = 0;
    let avgCount = 0;
    for (let j = bucketStart; j < bucketEnd; j++) {
      const point = data[j];
      if (!point) {
        continue;
      }
      const [x, y] = point;
      avgX += x;
      avgY += y;
      avgCount++;
    }

    const pointA = data[a];
    // 下一个桶为空或上一个选中点缺失时，退化为取当前桶起始点
    if (avgCount === 0 || !pointA) {
      const fallback = Math.min(bucketStart, n - 1);
      sampled.push(fallback);
      a = fallback;
      continue;
    }

    avgX /= avgCount;
    avgY /= avgCount;
    const [ax, ay] = pointA;

    // 在当前桶内寻找与点 a、下一桶平均点构成最大三角形面积的点
    let maxArea = -1;
    let maxIdx = bucketStart;
    for (let j = bucketStart; j < bucketEnd; j++) {
      const point = data[j];
      if (!point) {
        continue;
      }
      const [px, py] = point;
      // 三角形面积 = |x_a*(y - avgY) + x*(avgY - y_a) + avgX*(y_a - y)| / 2
      const area =
        Math.abs(ax * (py - avgY) + px * (avgY - ay) + avgX * (ay - py)) / 2;
      if (area > maxArea) {
        maxArea = area;
        maxIdx = j;
      }
    }
    sampled.push(maxIdx);
    a = maxIdx;
  }

  // 保留尾点
  sampled.push(n - 1);
  return sampled;
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

  let { timestamps, pv, sp, op, pvQuality } = data;

  // 前端 LTTB 二次降采样（FDS §5.4.3）：数据点数超过阈值时降采样，避免渲染卡顿
  const LTTB_THRESHOLD = 2000;
  if (timestamps.length > LTTB_THRESHOLD) {
    // 以 PV 为主信号构建 LTTB 输入，null 值用 0 占位（仅用于选点，不影响原始数据）
    const lttbData: [number, number][] = timestamps.map((t, i) => [
      t,
      pv[i] ?? 0,
    ]);
    const sampledIndices = lttbDownsample(lttbData, LTTB_THRESHOLD);
    // 按选中的索引重建各序列，保持时间对齐
    timestamps = sampledIndices
      .map((i) => timestamps[i])
      .filter((v): v is number => v !== undefined);
    pv = sampledIndices.map((i) => pv[i] ?? null);
    sp = sampledIndices.map((i) => sp[i] ?? null);
    op = sampledIndices.map((i) => op[i] ?? null);
    pvQuality = sampledIndices.map((i) => pvQuality[i] ?? null);
  }

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
  <Page>
    <ClpmPageToolbar :title="pageTitle" subtitle="波形趋势与 PV-OP 散点用于查看诊断证据细节。" />
    <ClpmDataCanvas class="mb-4 mt-4" title="筛选条件">
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
    </ClpmDataCanvas>

    <!-- 诊断标签卡片区域 -->
    <ClpmDataCanvas v-if="diagnosisDetail" class="mb-4" title="诊断结果">
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
    </ClpmDataCanvas>

    <!-- 波形/散点图 Tab -->
    <ClpmDataCanvas title="证据波形与散点图">
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
    </ClpmDataCanvas>
  </Page>
</template>
