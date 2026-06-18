import ReactECharts from 'echarts-for-react';
import type { EvidenceWindow } from '../types';

export function TrendChart({ evidence }: { evidence: EvidenceWindow }) {
  const firstPoint = evidence.points[0];
  const lastPoint = evidence.points[evidence.points.length - 1];
  const textSummary = firstPoint && lastPoint
    ? `${evidence.loopId} 趋势摘要：从 ${firstPoint.time} 到 ${lastPoint.time}，PV ${firstPoint.pv}→${lastPoint.pv}，SP ${firstPoint.sp}→${lastPoint.sp}，OP ${firstPoint.op}→${lastPoint.op}。`
    : `${evidence.loopId} 趋势摘要：暂无可绘制点。`;
  const option = {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['PV', 'SP', 'OP'], top: 0 },
    grid: { left: 36, right: 16, bottom: 28, top: 40 },
    xAxis: { type: 'category', data: evidence.points.map((point) => point.time) },
    yAxis: { type: 'value' },
    series: [
      { name: 'PV', type: 'line', data: evidence.points.map((point) => point.pv), color: '#2563EB', smooth: true },
      { name: 'SP', type: 'line', data: evidence.points.map((point) => point.sp), color: '#0F766E', smooth: true },
      { name: 'OP', type: 'line', data: evidence.points.map((point) => point.op), color: '#B45309', smooth: true },
    ],
  };

  return (
    <figure className="chart-card" role="img" aria-label={`${evidence.loopId} PV SP OP 趋势图，包含过程量、设定值和输出量三条曲线`}>
      <ReactECharts option={option} style={{ height: 280 }} aria-hidden="true" />
      <figcaption>{evidence.summary}</figcaption>
      <p className="chart-summary">{textSummary}</p>
    </figure>
  );
}
