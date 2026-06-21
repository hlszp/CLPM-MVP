/**
 * 时序波形图（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.3 + §3.1.5 + §10.5
 *
 * PV 质量码分段渲染规则（§3.1.5）：
 * - Good：青绿实线，正常显示
 * - Bad：灰色虚线断线（PV 值为 null，自然断线）+ 背景红色半透明标记
 * - Uncertain：琥珀虚线
 * - SP/OP 不受 PV 质量码影响，始终正常显示
 *
 * 交互（§10.5）：
 * - 十字准星 tooltip 联动
 * - 时间轴 dataZoom 缩放
 * - 图例可切换显示/隐藏
 * - LTTB 降采样已在数据层处理（maxPoints=2000）
 */

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { TimeseriesDataset, TimeseriesPoint } from '../mock/types';

interface WaveformChartProps {
  dataset: TimeseriesDataset;
  /** 高度（默认 320px） */
  height?: string;
  /** 是否显示 dataZoom（默认 true） */
  showDataZoom?: boolean;
  /** 是否显示 OP 线（默认 true） */
  showOp?: boolean;
}

/** 颜色常量（对齐 CSS 变量） */
const COLORS = {
  pvGood: '#198754',
  pvUncertain: '#FFC107',
  pvBad: '#6C757D',
  sp: '#0D6EFD',
  op: '#FD7E14',
  badArea: 'rgba(220, 53, 69, 0.08)',
  uncertainArea: 'rgba(255, 193, 7, 0.08)',
};

/**
 * 将时序数据按 PV 质量码拆分为 Good / Uncertain 两条线
 * Bad 段 PV 值为 null，自然断线
 */
function splitByQuality(points: TimeseriesPoint[]) {
  const pvGood: Array<[number, number | null]> = [];
  const pvUncertain: Array<[number, number | null]> = [];
  const sp: Array<[number, number | null]> = [];
  const op: Array<[number, number | null]> = [];

  // 质量码变更区间（用于 markArea）
  const badAreas: Array<[number, number]> = [];
  const uncertainAreas: Array<[number, number]> = [];

  let badStart: number | null = null;
  let uncertainStart: number | null = null;

  for (const p of points) {
    const ts = p.timestamp;

    // PV 按质量码分线
    if (p.pvQuality === 'Good' && p.pv !== null) {
      pvGood.push([ts, p.pv]);
      pvUncertain.push([ts, null]);
    } else if (p.pvQuality === 'Uncertain' && p.pv !== null) {
      pvGood.push([ts, null]);
      pvUncertain.push([ts, p.pv]);
    } else {
      // Bad 或 pv 为 null
      pvGood.push([ts, null]);
      pvUncertain.push([ts, null]);
    }

    sp.push([ts, p.sp]);
    op.push([ts, p.op]);

    // 质量码区间标记
    if (p.pvQuality === 'Bad') {
      if (badStart === null) badStart = ts;
    } else {
      if (badStart !== null) {
        badAreas.push([badStart, ts]);
        badStart = null;
      }
    }
    if (p.pvQuality === 'Uncertain') {
      if (uncertainStart === null) uncertainStart = ts;
    } else {
      if (uncertainStart !== null) {
        uncertainAreas.push([uncertainStart, ts]);
        uncertainStart = null;
      }
    }
  }
  // 收尾
  if (badStart !== null) badAreas.push([badStart, points[points.length - 1].timestamp]);
  if (uncertainStart !== null) uncertainAreas.push([uncertainStart, points[points.length - 1].timestamp]);

  return { pvGood, pvUncertain, sp, op, badAreas, uncertainAreas };
}

export function WaveformChart({
  dataset,
  height = '320px',
  showDataZoom = true,
  showOp = true,
}: WaveformChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;

    // 初始化或复用实例
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const { pvGood, pvUncertain, sp, op, badAreas, uncertainAreas } = splitByQuality(dataset.points);

    // 构建 markArea
    const markAreas: Array<{ coord: [number, number]; itemStyle: { color: string } }> = [];
    badAreas.forEach(([start, end]) => {
      markAreas.push({
        coord: [start, end],
        itemStyle: { color: COLORS.badArea },
      });
    });
    uncertainAreas.forEach(([start, end]) => {
      markAreas.push({
        coord: [start, end],
        itemStyle: { color: COLORS.uncertainArea },
      });
    });

    const series: echarts.SeriesOption[] = [
      {
        name: 'PV (Good)',
        type: 'line',
        data: pvGood,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: COLORS.pvGood, width: 1.5, type: 'solid' },
        connectNulls: false,
        markArea: markAreas.length > 0 ? {
          silent: true,
          data: markAreas.map((a) => [
            { xAxis: a.coord[0], itemStyle: { color: a.itemStyle.color } },
            { xAxis: a.coord[1] },
          ]),
        } : undefined,
      },
      {
        name: 'PV (Uncertain)',
        type: 'line',
        data: pvUncertain,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: COLORS.pvUncertain, width: 1.5, type: 'dashed' },
        connectNulls: false,
      },
      {
        name: 'SP',
        type: 'line',
        data: sp,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: COLORS.sp, width: 1.2, type: 'solid' },
        connectNulls: true,
      },
    ];

    if (showOp) {
      series.push({
        name: 'OP',
        type: 'line',
        data: op,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: COLORS.op, width: 1.2, type: 'solid' },
        connectNulls: true,
        yAxisIndex: 1,
      });
    }

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'cross',
          label: {
            backgroundColor: '#6C757D',
          },
        },
        formatter: (params: unknown) => {
          const arr = params as Array<{ seriesName: string; value: [number, number | null]; color: string }>;
          if (!arr || arr.length === 0) return '';
          const ts = arr[0].value[0];
          const time = new Date(ts).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
          });
          let html = `<div style="font-size:12px;color:#6C757D">${time}</div>`;
          arr.forEach((p) => {
            if (p.value[1] !== null) {
              html += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px">
                <span style="display:inline-block;width:8px;height:8px;background:${p.color};border-radius:50%"></span>
                <span>${p.seriesName}: <strong>${p.value[1]}</strong></span>
              </div>`;
            }
          });
          return html;
        },
      },
      legend: {
        top: 0,
        right: 10,
        textStyle: { fontSize: 12 },
        data: showOp ? ['PV (Good)', 'PV (Uncertain)', 'SP', 'OP'] : ['PV (Good)', 'PV (Uncertain)', 'SP'],
      },
      grid: {
        left: 50,
        right: showOp ? 60 : 20,
        top: 35,
        bottom: showDataZoom ? 60 : 30,
      },
      xAxis: {
        type: 'time',
        axisLabel: {
          fontSize: 11,
          color: '#6C757D',
        },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: 'value',
          name: 'PV/SP',
          nameTextStyle: { fontSize: 11, color: '#6C757D' },
          axisLabel: { fontSize: 11, color: '#6C757D' },
          splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
        },
        ...(showOp ? [{
          type: 'value' as const,
          name: 'OP (%)',
          nameTextStyle: { fontSize: 11, color: '#6C757D' },
          axisLabel: { fontSize: 11, color: '#6C757D', formatter: '{value}%' },
          splitLine: { show: false },
        }] : []),
      ],
      dataZoom: showDataZoom ? [
        {
          type: 'inside',
          start: 0,
          end: 100,
        },
        {
          type: 'slider',
          start: 0,
          end: 100,
          height: 20,
          bottom: 10,
          borderColor: '#E5E5E5',
          textStyle: { fontSize: 10 },
        },
      ] : undefined,
      series,
    };

    chartInstance.current.setOption(option, true);
  }, [dataset, showDataZoom, showOp]);

  /** 响应式调整 */
  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  return <div ref={chartRef} style={{ width: '100%', height }} />;
}
