/**
 * PV-OP 散点图（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.3.2 + §6.4.3
 *
 * 用于诊断详情页，展示 PV 与 OP 的相关性。
 * 粘滞阀特征：散点呈"鱼骨"或"双带"分布，OP 持续变化但 PV 响应迟滞。
 * 正常控制：散点呈紧凑椭圆分布。
 */

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { ScatterPoint } from '../mock/types';

interface ScatterChartProps {
  data: ScatterPoint[];
  /** PV 轴名称 */
  pvName?: string;
  /** OP 轴名称 */
  opName?: string;
  height?: string;
}

export function ScatterChart({
  data,
  pvName = 'PV',
  opName = 'OP (%)',
  height = '280px',
}: ScatterChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const scatterData = data.map((p) => [p.pv, p.op]);

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'item',
        formatter: (params: unknown) => {
          const p = params as { value: [number, number] };
          return `PV: ${p.value[0].toFixed(2)}<br/>OP: ${p.value[1].toFixed(2)}%`;
        },
      },
      grid: {
        left: 50,
        right: 20,
        top: 20,
        bottom: 40,
      },
      xAxis: {
        type: 'value',
        name: pvName,
        nameLocation: 'middle',
        nameGap: 25,
        nameTextStyle: { fontSize: 11, color: '#6C757D' },
        axisLabel: { fontSize: 11, color: '#6C757D' },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        name: opName,
        nameLocation: 'middle',
        nameGap: 35,
        nameTextStyle: { fontSize: 11, color: '#6C757D' },
        axisLabel: { fontSize: 11, color: '#6C757D', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      },
      series: [
        {
          type: 'scatter',
          data: scatterData,
          symbolSize: 4,
          itemStyle: {
            color: 'rgba(13, 110, 253, 0.3)',
            borderColor: 'rgba(13, 110, 253, 0.6)',
            borderWidth: 0.5,
          },
        },
      ],
    };

    chartInstance.current.setOption(option, true);
  }, [data, pvName, opName]);

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
