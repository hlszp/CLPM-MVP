<script lang="ts" setup>
/* oxlint-disable typescript/no-non-null-assertion -- chart series indexes are validated against the timestamp series */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { LoopApi } from '#/api/loop';

import { nextTick, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'WaveformChart' });

const props = withDefaults(
  defineProps<{
    /** 是否启用时间点选择（默认 false）。启用后点击图表可选中最近时间点并 emit time-select */
    enableTimeSelect?: boolean;
    height?: string;
    /** Phase 5：逐点异常原因码（如 'FROZEN,JUMP'） */
    outlierReasons?: string[];
    /** 增量追加渲染时保留当前 dataZoom 缩放状态（实时模式逐秒追加用，默认 false=重置为全量） */
    preserveZoom?: boolean;
    /** 外部设置的选中时间点（timestamp 字符串）。传入后在图表上以 markLine 标记 */
    selectedTimestamp?: null | string;
    /** 是否显示图例（默认 true） */
    showLegend?: boolean;
    /** 是否显示 MODE 阶梯线 + 切换标记（默认 true） */
    showMode?: boolean;
    trend: LoopApi.MonitorTrend;
    /** Phase 5：逐点有效性标记（true=有效，false=无效）。存在时优先于 pvQuality */
    validMask?: boolean[];
  }>(),
  {
    enableTimeSelect: false,
    height: '360px',
    outlierReasons: () => [],
    preserveZoom: false,
    selectedTimestamp: null,
    showLegend: true,
    showMode: true,
    validMask: () => [],
  },
);

const emit = defineEmits<{
  (e: 'timeSelect', payload: { index: number; timestamp: string }): void;
  /** 光标悬停时刻的值变化（鼠标移出画布时 payload 为 null，恢复默认） */
  (
    e: 'cursorChange',
    payload: null | {
      index: number;
      mode: null | number;
      op: null | number;
      pv: null | number;
      pvQuality: LoopApi.Quality;
      sp: null | number;
      timestamp: number;
    },
  ): void;
}>();

/** 时间戳精度转换：纳秒/微秒级→毫秒级 */
function toMs(ts: number): number {
  const absTs = Math.abs(ts);
  if (absTs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (absTs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

/**
 * 将时间戳转换为北京时间（UTC+8）对应的 Date 对象。
 *
 * 中国无夏令时，固定 UTC+8 偏移。通过 +8h 后取 getUTC* 方法，
 * 确保无论浏览器本地时区如何，均显示北京时间，与后端 Celery Beat 时区一致。
 */
function toCstDate(ts: number): Date {
  return new Date(toMs(ts) + 8 * 3600 * 1000);
}

/** 格式化时间戳为 MM-DD HH:mm（北京时间） */
function fmtTimeShort(ts: number): string {
  const d = toCstDate(ts);
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${mo}-${dd} ${hh}:${mm}`;
}

/** 格式化时间戳为 MM-DD HH:mm:ss（北京时间） */
function fmtTimeLong(ts: number): string {
  const d = toCstDate(ts);
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  const ss = String(d.getUTCSeconds()).padStart(2, '0');
  return `${mo}-${dd} ${hh}:${mm}:${ss}`;
}

/**
 * ECharts 波形图组件（统一版）
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.14 + UIUX §10.5：
 * - 展示 PV/SP/OP/MODE 趋势线（MODE 阶梯线绑右轴）
 * - MODE 切换竖直虚线（markLine）
 * - PV 质量码渲染：
 *   - 无 validMask（向后兼容）：Bad 质量时段置 null 实现断线（connectNulls: false）
 *   - 有 validMask（Phase 5）：无效时段用灰色虚线保留连线 + markArea 标识无效区间
 * - 始终启用 X+Y 双轴 dataZoom（inside + slider）
 */
const chartRef = ref<EchartsUIType>();
const { renderEcharts, resize, getChartInstance } = useEcharts(chartRef);

// ===== 时间点选择：点击事件绑定（D2 多图联动）=====
// 通过 zrender 级别 click 事件捕获画布点击，转换为最近时间点
let boundZr: any = null;
let clickHandler: ((params: any) => void) | null = null;

function bindClickEvent() {
  if (!props.enableTimeSelect) return;
  const chart = getChartInstance();
  if (!chart) return;
  const zr = chart.getZr();
  if (!zr) return;
  // 同一 zr 实例已绑定，避免重复
  if (boundZr === zr && clickHandler) return;
  // 清理旧 zr 上的 handler
  if (boundZr && clickHandler) {
    boundZr.off('click', clickHandler);
  }
  clickHandler = (params: any) => {
    const timestamps = props.trend?.timestamps;
    if (!timestamps || timestamps.length === 0) return;
    const point = [params.offsetX, params.offsetY];
    const xValue = chart.convertFromPixel({ xAxisIndex: 0 }, point[0]);
    if (xValue === null || xValue === undefined || Number.isNaN(xValue)) return;
    // 找到最近的时间点索引
    let nearestIdx = 0;
    let minDist = Infinity;
    for (const [i, timestamp] of timestamps.entries()) {
      const dist = Math.abs(timestamp! - xValue);
      if (dist < minDist) {
        minDist = dist;
        nearestIdx = i;
      }
    }
    emit('timeSelect', {
      timestamp: String(timestamps[nearestIdx]),
      index: nearestIdx,
    });
  };
  zr.on('click', clickHandler);
  boundZr = zr;
}

// ===== 光标悬停联动：mousemove 找最近时间点，emit 当前时刻值 =====
let boundZrForCursor: any = null;
let moveHandler: ((params: any) => void) | null = null;
let outHandler: (() => void) | null = null;

function bindCursorEvent() {
  const chart = getChartInstance();
  if (!chart) return;
  const zr = chart.getZr();
  if (!zr) return;
  if (boundZrForCursor === zr && moveHandler) return;
  if (boundZrForCursor && moveHandler) {
    boundZrForCursor.off('mousemove', moveHandler);
    boundZrForCursor.off('mouseout', outHandler!);
  }
  moveHandler = (params: any) => {
    const { timestamps, pv, sp, op, mode, pvQuality } = props.trend || {};
    if (!timestamps || timestamps.length === 0) return;
    const point = [params.offsetX, params.offsetY];
    const xValue = chart.convertFromPixel({ xAxisIndex: 0 }, point[0]);
    if (xValue === null || xValue === undefined || Number.isNaN(xValue)) return;
    // 找最近时间点索引
    let nearestIdx = 0;
    let minDist = Infinity;
    for (const [i, timestamp] of timestamps.entries()) {
      const dist = Math.abs(timestamp! - xValue);
      if (dist < minDist) {
        minDist = dist;
        nearestIdx = i;
      }
    }
    emit('cursorChange', {
      index: nearestIdx,
      mode: mode?.[nearestIdx] ?? null,
      op: op?.[nearestIdx] ?? null,
      pv: pv?.[nearestIdx] ?? null,
      pvQuality: pvQuality?.[nearestIdx] ?? 'GOOD',
      sp: sp?.[nearestIdx] ?? null,
      timestamp: timestamps[nearestIdx]!,
    });
  };
  outHandler = () => {
    emit('cursorChange', null);
  };
  zr.on('mousemove', moveHandler);
  zr.on('mouseout', outHandler);
  boundZrForCursor = zr;
}

const {
  isDark,
  themeColors,
  chartTextColor,
  chartSplitLineColor,
  chartMutedFillColor,
  chartMarkLineColor,
  chartInvalidColor,
} = useClpmTheme();

/** 暴露 resize 给父组件（全屏切换时调用） */
defineExpose({ resize });

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

/** 查找 MODE 切换时间点（用于 markLine 竖直虚线） */
function findModeChangePoints(
  timestamps: number[],
  modes: (null | number)[],
): number[] {
  const changes: number[] = [];
  let prevMode: null | number = null;
  for (const [i, mode] of modes.entries()) {
    const m = mode ?? null;
    if (m !== prevMode) {
      changes.push(timestamps[i] ?? 0);
      prevMode = m;
    }
  }
  return changes;
}

function render() {
  const { timestamps, pv, sp, op, mode, pvQuality } = props.trend;
  if (!timestamps || timestamps.length === 0) {
    renderEcharts({
      backgroundColor: 'transparent',
      title: {
        text: '暂无趋势数据',
        left: 'center',
        top: 'center',
        textStyle: {
          color: chartTextColor.value,
          fontSize: 14,
          fontWeight: 'normal',
        },
      },
    });
    return;
  }

  const spData = buildSimpleData(timestamps, sp);
  const opData = buildSimpleData(timestamps, op);
  const showMode = props.showMode && mode && mode.length > 0;
  const modeData = showMode ? buildSimpleData(timestamps, mode) : [];
  const modeChanges = showMode ? findModeChangePoints(timestamps, mode) : [];

  // Phase 5：validMask 存在且长度匹配时优先使用，否则回退到 pvQuality 断线逻辑
  const useValidMask =
    !!props.validMask && props.validMask.length === timestamps.length;
  const hasInvalid = useValidMask && props.validMask!.includes(false);

  const pvColor = themeColors.value.INFO;
  const spColor = themeColors.value.SUCCESS;
  const opColor = themeColors.value.WARNING;
  const modeColor = themeColors.value.DANGER;
  const markAreaColor = chartMutedFillColor.value;
  const markLineColor = chartMarkLineColor.value;

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
        itemStyle: { color: pvColor },
        lineStyle: { width: 2 },
        markArea: {
          data: markAreas,
          itemStyle: { color: markAreaColor },
          silent: true,
        },
        markLine: undefined,
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: invalidData,
        itemStyle: { color: chartInvalidColor.value },
        lineStyle: { color: chartInvalidColor.value, type: 'dashed', width: 2 },
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
        itemStyle: { color: pvColor },
        lineStyle: { width: 2 },
        markLine: undefined,
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
        itemStyle: { color: pvColor },
        lineStyle: { width: 2 },
        markLine: undefined,
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
    ];
  }

  // D2 多图联动：在首个 PV series 上叠加选中时间点 markLine
  if (props.selectedTimestamp && pvSeries[0]) {
    const selTs = Number(props.selectedTimestamp);
    if (!Number.isNaN(selTs)) {
      const firstPv = pvSeries[0];
      const selItem = {
        xAxis: selTs,
        label: {
          color: themeColors.value.DANGER,
          fontSize: 11,
          formatter: '选中',
          position: 'end',
          show: true,
        },
        lineStyle: {
          color: themeColors.value.DANGER,
          type: 'solid',
          width: 2,
        },
      };
      if (firstPv.markLine) {
        firstPv.markLine.data = [...(firstPv.markLine.data || []), selItem];
      } else {
        firstPv.markLine = {
          data: [selItem],
          lineStyle: {
            color: themeColors.value.DANGER,
            type: 'solid',
            width: 2,
          },
          silent: true,
          symbol: 'none',
        };
      }
    }
  }

  const series: any[] = [
    ...pvSeries,
    {
      connectNulls: false,
      data: spData,
      itemStyle: { color: spColor },
      lineStyle: { type: 'dashed', width: 1.5 },
      name: 'SP',
      showSymbol: false,
      type: 'line',
    },
    {
      connectNulls: false,
      data: opData,
      itemStyle: { color: opColor },
      lineStyle: { width: 1.5 },
      name: 'OP',
      showSymbol: false,
      type: 'line',
      yAxisIndex: 1,
    },
  ];

  if (showMode) {
    series.push({
      data: modeData,
      itemStyle: { color: modeColor },
      lineStyle: { type: 'dotted', width: 1.5 },
      markLine: {
        data: modeChanges.map((ts) => ({
          label: {
            formatter: () => fmtTimeShort(ts),
            position: 'insideEndTop',
            show: true,
          },
          xAxis: ts,
        })),
        lineStyle: { color: markLineColor, type: 'dashed', width: 1 },
        silent: true,
        symbol: 'none',
      },
      name: 'MODE',
      showSymbol: false,
      step: 'end',
      type: 'line',
      yAxisIndex: 2,
    });
  }

  // Y 轴配置（整改 B3）：PV/SP 主轴按数据自适应（scale:true，不再被 OP 0-100% 压扁），
  // OP 固定副轴 0-100%（UI/UX §7.3），showMode 时 MODE 第三轴右置 offset。
  const yAxis: any[] = [
    {
      axisLabel: { color: chartTextColor.value, formatter: '{value}' },
      name: 'PV/SP',
      nameTextStyle: { color: chartTextColor.value },
      scale: true,
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      type: 'value',
    },
    {
      axisLabel: { color: chartTextColor.value, formatter: '{value}' },
      max: 100,
      min: 0,
      name: 'OP %',
      nameTextStyle: { color: chartTextColor.value },
      position: 'right',
      splitLine: { show: false },
      type: 'value',
    },
  ];
  if (showMode) {
    yAxis.push({
      axisLabel: {
        color: chartTextColor.value,
        formatter: (val: number) => {
          if (val === 0) return 'Manual';
          if (val === 1) return 'Auto';
          if (val === 2) return 'Cascade';
          return '';
        },
      },
      max: 2.5,
      min: -0.5,
      name: 'MODE',
      nameTextStyle: { color: chartTextColor.value },
      offset: 48,
      position: 'right',
      splitLine: { show: false },
      type: 'value',
    });
  }

  // 实时增量追加时保留用户当前缩放状态（避免每秒重渲染重置 dataZoom）
  let zoomX = { end: 100, start: 0 };
  let zoomY = { end: 100, start: 0 };
  if (props.preserveZoom) {
    const opt: any = getChartInstance()?.getOption?.();
    const dz = opt?.dataZoom;
    if (Array.isArray(dz)) {
      for (const d of dz) {
        if (!d || typeof d.start !== 'number') continue;
        if (d.xAxisIndex !== undefined) zoomX = { end: d.end, start: d.start };
        else if (d.yAxisIndex !== undefined)
          zoomY = { end: d.end, start: d.start };
      }
    }
  }

  // 采样间隔提示（sampleInterval > 1 或触发降采样时显示）
  const sampleInterval = props.trend.sampleInterval;
  const downsampled = props.trend.downsampled;
  const hintParts: string[] = [];
  if (sampleInterval && sampleInterval > 1)
    hintParts.push(`采样间隔: ${sampleInterval}s`);
  if (downsampled) hintParts.push('已降采样');
  const samplingHint = hintParts.join('，');
  const showSamplingHint = samplingHint.length > 0;

  renderEcharts({
    backgroundColor: 'transparent',
    graphic: showSamplingHint
      ? {
          elements: [
            {
              right: 10,
              style: {
                fill: chartTextColor.value,
                fontSize: 11,
                text: samplingHint,
              },
              top: 28,
              type: 'text',
              z: 100,
            },
          ],
        }
      : undefined,
    dataZoom: showMode
      ? [
          // X 轴：滚轮 + 滑块
          { ...zoomX, type: 'inside', xAxisIndex: 0 },
          {
            ...zoomX,
            type: 'slider',
            xAxisIndex: 0,
            bottom: 8,
            height: 20,
            labelFormatter: (val: number) => fmtTimeShort(val),
          },
          // Y 轴：滚轮 + 滑块（量程缩放）
          { ...zoomY, type: 'inside', yAxisIndex: 0 },
          {
            ...zoomY,
            type: 'slider',
            yAxisIndex: 0,
            right: 8,
            width: 20,
          },
        ]
      : [
          { ...zoomX, type: 'inside', xAxisIndex: 0 },
          {
            ...zoomX,
            type: 'slider',
            xAxisIndex: 0,
            bottom: 8,
            height: 20,
            labelFormatter: (val: number) => fmtTimeShort(val),
          },
        ],
    grid: {
      bottom: 50,
      containLabel: true,
      left: '3%',
      right: showMode ? 110 : 60,
      top: 60,
    },
    legend: {
      data: showMode ? ['PV', 'SP', 'OP', 'MODE'] : ['PV', 'SP', 'OP'],
      right: 10,
      show: props.showLegend,
      textStyle: { color: chartTextColor.value },
      top: 5,
    },
    series,
    tooltip: {
      axisPointer: { type: 'cross' },
      formatter: (params: any) => {
        if (!Array.isArray(params) || params.length === 0) return '';
        const p0 = params[0];
        const title = fmtTimeLong(p0.axisValue);
        const lines = params.map((p: any) => {
          // time 轴 series data: value 可能是 [timestamp, yValue] 数组
          const raw = Array.isArray(p.value) ? p.value[1] : p.value;
          const v =
            raw === null || raw === undefined || Number.isNaN(Number(raw))
              ? '—'
              : Number(raw).toFixed(3);
          return `${p.marker} ${p.seriesName}: ${v}`;
        });
        return [title, ...lines].join('<br/>');
      },
      trigger: 'axis',
    },
    xAxis: {
      axisLabel: {
        color: chartTextColor.value,
        formatter: (val: number) => fmtTimeShort(val),
      },
      splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      type: 'time',
    },
    yAxis,
  }).then(() => {
    bindClickEvent();
    bindCursorEvent();
  });
}

watch(
  () => [
    props.trend,
    props.validMask,
    props.outlierReasons,
    props.preserveZoom,
    props.showMode,
    props.showLegend,
    props.selectedTimestamp,
    props.enableTimeSelect,
  ],
  () => render(),
  { deep: true, immediate: true },
);

// 主题切换时重新渲染，确保 ECharts 配色跟随深/浅色模式
watch(isDark, () => {
  nextTick(() => render());
});

// useEcharts 的 isActiveRef 在 onMounted 才置 true，
// watch immediate 在 setup 阶段触发时 renderEcharts 会跳过，
// 需在 onMounted 后重新渲染一次
onMounted(() => render());
</script>

<template>
  <EchartsUI ref="chartRef" :style="{ height: height || '360px' }" />
</template>
