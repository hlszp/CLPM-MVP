<script lang="ts" setup>
/**
 * ECharts 波形图组件
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.14 + UIUX §10.5 PV 质量码波形处理：
 * - 展示 PV/SP/OP 趋势线
 * - PV 质量码渲染：
 *   - 无 validMask（向后兼容）：Bad 质量时段置 null 实现断线（connectNulls: false）
 *   - 有 validMask（Phase 5）：无效时段用灰色虚线保留连线 + markArea 标识无效区间
 * - 数据超过 1 万点时启用 dataZoom 平滑渲染
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { LoopApi } from '#/api/loop';

import { ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

defineOptions({ name: 'WaveformChart' });

const props = defineProps<{
  height?: string;
  /** Phase 5：逐点异常原因码（如 'FROZEN,JUMP'） */
  outlierReasons?: string[];
  trend: LoopApi.MonitorTrend;
  /** Phase 5：逐点有效性标记（true=有效，false=无效）。存在时优先于 pvQuality */
  validMask?: boolean[];
}>();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/**
 * 构建 PV 数据（向后兼容模式）：Bad 质量时段置 null，实现断线效果
 */
function buildPvData(
  timestamps: number[],
  pv: (null | number)[],
  pvQuality: LoopApi.Quality[],
): [number, null | number][] {
  return timestamps.map((ts, i) => {
    const q = pvQuality[i];
    const v = pv[i] ?? null;
    // Bad 质量时段断线
    if (q === 'BAD') return [ts, null];
    return [ts, v];
  });
}

/**
 * 构建 PV 分段数据（Phase 5 模式）
 *
 * 将 PV 数据拆为两个 series：
 * - 有效段（蓝色实线）
 * - 无效段（灰色虚线）
 *
 * 为保证视觉连线连续，在有效↔无效的边界点处，将该点同时放入两个 series
 * （即"桥接点"），其余非本段位置置 null，配合 connectNulls: false 形成分段。
 */
function buildPvSegmentedData(
  timestamps: number[],
  pv: (null | number)[],
  validMask: boolean[],
): {
  invalidData: ([number, null | number] | null)[];
  validData: ([number, null | number] | null)[];
} {
  const n = timestamps.length;
  const validData: ([number, null | number] | null)[] = [];
  const invalidData: ([number, null | number] | null)[] = [];

  for (let i = 0; i < n; i++) {
    const ts = timestamps[i]!;
    const v = pv[i] ?? null;
    const isValid = validMask[i] === true;
    const nextIsValid = i + 1 < n && validMask[i + 1] === true;
    const nextIsInvalid = i + 1 < n && validMask[i + 1] === false;
    // 当前点为某段末尾且下一点进入另一段时，当前点作为桥接点同时放入两个 series
    const bridgeToInvalid = isValid && nextIsInvalid;
    const bridgeToValid = !isValid && nextIsValid;

    if (isValid) {
      validData.push([ts, v]);
      invalidData.push(bridgeToInvalid ? [ts, v] : null);
    } else {
      invalidData.push([ts, v]);
      validData.push(bridgeToValid ? [ts, v] : null);
    }
  }

  return { validData, invalidData };
}

/**
 * 收集连续无效区间，用于 markArea 渲染
 *
 * 返回 ECharts markArea.data 所需的二元组数组：
 * `[[{ xAxis: startTs }, { xAxis: endTs }], ...]`
 * 区间终点延伸到下一个有效点的时间戳，避免单点区间零宽度。
 */
function buildInvalidMarkAreas(
  timestamps: number[],
  validMask: boolean[],
): Array<[{ xAxis: number }, { xAxis: number }]> {
  const areas: Array<[{ xAxis: number }, { xAxis: number }]> = [];
  const n = timestamps.length;
  if (n === 0) return areas;
  const avgInterval =
    n > 1 ? (timestamps[n - 1]! - timestamps[0]!) / (n - 1) : 1000;
  let startIdx = -1;

  for (let i = 0; i <= n; i++) {
    const isInvalid = i < n && validMask[i] === false;
    if (isInvalid && startIdx === -1) {
      startIdx = i;
    } else if (!isInvalid && startIdx !== -1) {
      const startTs = timestamps[startIdx]!;
      let endTs: number;
      if (i < n) {
        // 延伸到下一个有效点的时间戳
        endTs = timestamps[i]!;
      } else {
        // 无效段延续到数据末尾
        endTs = timestamps[n - 1]!;
        if (endTs <= startTs) endTs = startTs + avgInterval;
      }
      areas.push([{ xAxis: startTs }, { xAxis: endTs }]);
      startIdx = -1;
    }
  }

  return areas;
}

function buildSimpleData(
  timestamps: number[],
  values: (null | number)[],
): [number, null | number][] {
  return timestamps.map((ts, i) => [ts, values[i] ?? null]);
}

function render() {
  const { timestamps, pv, sp, op, pvQuality } = props.trend;
  if (!timestamps || timestamps.length === 0) return;

  const spData = buildSimpleData(timestamps, sp);
  const opData = buildSimpleData(timestamps, op);
  const enableDataZoom = timestamps.length > 10_000;

  // Phase 5：validMask 存在且长度匹配时优先使用，否则回退到 pvQuality 断线逻辑
  const useValidMask =
    !!props.validMask && props.validMask.length === timestamps.length;
  const hasInvalid = useValidMask && props.validMask!.includes(false);

  let pvSeries: any[];
  if (useValidMask && hasInvalid) {
    // Phase 5 分段渲染：蓝色实线（有效）+ 灰色虚线（无效）+ markArea
    const { validData, invalidData } = buildPvSegmentedData(
      timestamps,
      pv,
      props.validMask!,
    );
    const markAreas = buildInvalidMarkAreas(timestamps, props.validMask!);
    pvSeries = [
      {
        connectNulls: false,
        data: validData,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
        markArea: {
          data: markAreas,
          itemStyle: { color: 'rgba(200,200,200,0.15)' },
          silent: true,
        },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: invalidData,
        itemStyle: { color: '#ccc' },
        lineStyle: { color: '#ccc', type: 'dashed', width: 2 },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
    ];
  } else if (useValidMask) {
    // Phase 5 全部有效：正常蓝实线
    const pvData = buildSimpleData(timestamps, pv);
    pvSeries = [
      {
        connectNulls: false,
        data: pvData,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
    ];
  } else {
    // 向后兼容：Bad 质量断线
    const pvData = buildPvData(timestamps, pv, pvQuality);
    pvSeries = [
      {
        connectNulls: false,
        data: pvData,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
    ];
  }

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
      top: 40,
    },
    legend: {
      data: ['PV', 'SP', 'OP'],
      top: 5,
    },
    series: [
      ...pvSeries,
      {
        connectNulls: false,
        data: spData,
        itemStyle: { color: '#52c41a' },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: opData,
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
        formatter: (val: number) => {
          const d = new Date(val);
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const mo = String(d.getMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      type: 'time',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

watch(
  () => [props.trend, props.validMask, props.outlierReasons],
  () => render(),
  { deep: true, immediate: true },
);
</script>

<template>
  <EchartsUI ref="chartRef" :style="{ height: height || '360px' }" />
</template>
