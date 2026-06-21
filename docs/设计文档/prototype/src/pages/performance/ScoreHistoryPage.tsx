/**
 * 评估历史页面（UI/UX §6.3.4）
 *
 * 布局结构：
 * 1. 页面标题
 * 2. FilterBar：回路选择（下拉）+ 时间范围（7/30/90 天）
 * 3. 评分历史趋势图（ECharts 折线图，含阈值线与低效红色区域）
 * 4. 评估历史记录表（DataTable）
 * 5. 行点击打开 Drawer 显示该次评估的 KPI 分项详情
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 列表页：FilterBar + DataTable 组合
 */

import { useMemo, useState, useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { Eye } from 'lucide-react';
import { FilterBar, type FilterItem } from '../../components/FilterBar';
import { DataTable, type Column } from '../../components/DataTable';
import { Drawer } from '../../components/Drawer';
import {
  ComputeStatusBadge,
  ScoreBadge,
  type ComputeStatus,
} from '../../components/StatusBadge';
import { kpiDefinitions } from '../../mock/kpi';
import { loops, findLoop } from '../../mock/loops';

/** 历史评估记录 */
interface HistoryRecord {
  /** 评估时间 */
  time: string;
  /** 回路 ID */
  loopId: string;
  /** 回路名 */
  loopName: string;
  /** 评分 */
  score: number;
  /** 计算状态 */
  computeStatus: ComputeStatus;
  /** 触发方式 */
  trigger: '定时' | '手动' | '事件';
}

/** 趋势数据点 */
interface TrendPoint {
  /** 日期（MM-DD） */
  date: string;
  /** 评分 */
  score: number;
}

/** 格式化日期为 YYYY-MM-DD */
function formatDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** 格式化日期为 MM-DD */
function formatShortDate(date: Date): string {
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${m}-${d}`;
}

/** 生成历史评估记录（基于回路当前评分做确定性偏移） */
function generateHistory(loopId: string, days: number): HistoryRecord[] {
  const loop = findLoop(loopId);
  if (!loop) return [];
  const baseScore = loop.score ?? 70;
  const records: HistoryRecord[] = [];
  const today = new Date('2026-06-21T10:00:00');

  for (let i = days - 1; i >= 0; i--) {
    const date = new Date(today);
    date.setDate(date.getDate() - i);
    const dateStr = formatDate(date);

    // 每天一条定时评估记录
    const variation = Math.sin(i * 0.7) * 6 + Math.cos(i * 0.3) * 4;
    const score = Math.max(0, Math.min(100, Math.round(baseScore + variation)));
    records.push({
      time: `${dateStr} 10:00:00`,
      loopId,
      loopName: loop.loopName,
      score,
      computeStatus: 'SUCCESS',
      trigger: '定时',
    });

    // 每周一条手动评估记录
    if (i % 7 === 3) {
      const manualVariation = Math.sin(i * 0.5 + 1) * 8;
      const manualScore = Math.max(
        0,
        Math.min(100, Math.round(baseScore + manualVariation)),
      );
      records.push({
        time: `${dateStr} 14:00:00`,
        loopId,
        loopName: loop.loopName,
        score: manualScore,
        computeStatus: 'SUCCESS',
        trigger: '手动',
      });
    }
  }

  return records.sort((a, b) => b.time.localeCompare(a.time));
}

/** 生成趋势数据（每日评分） */
function generateTrend(loopId: string, days: number): TrendPoint[] {
  const loop = findLoop(loopId);
  if (!loop) return [];
  const baseScore = loop.score ?? 70;
  const trend: TrendPoint[] = [];
  const today = new Date('2026-06-21');

  for (let i = days - 1; i >= 0; i--) {
    const date = new Date(today);
    date.setDate(date.getDate() - i);
    const variation = Math.sin(i * 0.7) * 6 + Math.cos(i * 0.3) * 4;
    const score = Math.max(0, Math.min(100, Math.round(baseScore + variation)));
    trend.push({ date: formatShortDate(date), score });
  }

  return trend;
}

/** 评分历史趋势图 */
function ScoreHistoryChart({
  trend,
  loopName,
}: {
  trend: TrendPoint[];
  loopName: string;
}) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    const option: echarts.EChartsOption = {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = params as Array<{ axisValue: string; value: number; seriesName: string; color: string }>;
          if (!arr || arr.length === 0) return '';
          let html = `<div style="font-size:12px;color:#6C757D">${arr[0].axisValue}</div>`;
          arr.forEach((p) => {
            html += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px">
              <span style="display:inline-block;width:8px;height:8px;background:${p.color};border-radius:50%"></span>
              <span>${p.seriesName}: <strong>${p.value}</strong></span>
            </div>`;
          });
          return html;
        },
      },
      grid: { left: 40, right: 20, top: 30, bottom: 30 },
      xAxis: {
        type: 'category',
        data: trend.map((t) => t.date),
        axisLabel: { fontSize: 11, color: '#6C757D' },
        axisLine: { lineStyle: { color: '#E0E0E0' } },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: { fontSize: 11, color: '#6C757D' },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      },
      series: [
        {
          name: `${loopName} 评分`,
          type: 'line',
          data: trend.map((t) => t.score),
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { color: '#0D6EFD', width: 2 },
          itemStyle: { color: '#0D6EFD' },
          areaStyle: { color: 'rgba(13, 110, 253, 0.08)' },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              {
                yAxis: 80,
                lineStyle: { color: '#198754', type: 'dashed', width: 1 },
                label: {
                  formatter: '优秀 80',
                  position: 'insideEndTop',
                  color: '#198754',
                  fontSize: 10,
                },
              },
              {
                yAxis: 60,
                lineStyle: { color: '#FFC107', type: 'dashed', width: 1 },
                label: {
                  formatter: '警告 60',
                  position: 'insideEndTop',
                  color: '#B8860B',
                  fontSize: 10,
                },
              },
            ],
          },
          markArea: {
            silent: true,
            itemStyle: { color: 'rgba(220, 53, 69, 0.06)' },
            data: [[{ yAxis: 0 }, { yAxis: 60 }]],
          },
        },
      ],
    };

    chartInstance.current.setOption(option, true);
  }, [trend, loopName]);

  useEffect(() => {
    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, []);

  return <div ref={chartRef} style={{ width: '100%', height: '280px' }} />;
}

export default function ScoreHistoryPage() {
  /** 选中的回路 ID */
  const [selectedLoopId, setSelectedLoopId] = useState<string>(
    loops[0]?.loopId ?? '',
  );
  /** 时间范围（天） */
  const [timeRange, setTimeRange] = useState<number>(7);
  /** 搜索关键词 */
  const [searchValue, setSearchValue] = useState('');
  /** Drawer 状态 */
  const [drawerRecord, setDrawerRecord] = useState<HistoryRecord | null>(null);

  /** 选中的回路 */
  const selectedLoop = useMemo(
    () => findLoop(selectedLoopId),
    [selectedLoopId],
  );

  /** 历史记录（按时间范围生成） */
  const historyRecords = useMemo(
    () => generateHistory(selectedLoopId, timeRange),
    [selectedLoopId, timeRange],
  );

  /** 趋势数据 */
  const trendData = useMemo(
    () => generateTrend(selectedLoopId, timeRange),
    [selectedLoopId, timeRange],
  );

  /** 过滤后的历史记录（按搜索关键词） */
  const filteredRecords = useMemo(() => {
    if (!searchValue.trim()) return historyRecords;
    return historyRecords.filter((r) =>
      r.loopName.toLowerCase().includes(searchValue.toLowerCase()),
    );
  }, [historyRecords, searchValue]);

  /** FilterBar 筛选项 */
  const filters: FilterItem[] = [
    {
      key: 'loop',
      label: '回路',
      type: 'select',
      value: selectedLoopId,
      onChange: (v) => setSelectedLoopId(v),
      options: loops.map((l) => ({ label: l.loopName, value: l.loopId })),
    },
    {
      key: 'range',
      label: '时间范围',
      type: 'select',
      value: String(timeRange),
      onChange: (v) => setTimeRange(Number(v)),
      options: [
        { label: '7天', value: '7' },
        { label: '30天', value: '30' },
        { label: '90天', value: '90' },
      ],
    },
  ];

  /** 表格列定义 */
  const columns: Column<HistoryRecord>[] = useMemo(
    () => [
      {
        key: 'time',
        header: '时间',
        width: '160px',
        sortable: true,
        render: (row) => <span className="mono">{row.time}</span>,
      },
      {
        key: 'loopName',
        header: '回路名',
        sortable: true,
        render: (row) => <span style={{ fontWeight: 500 }}>{row.loopName}</span>,
      },
      {
        key: 'score',
        header: '评分',
        width: '70px',
        align: 'center',
        sortable: true,
        render: (row) => <ScoreBadge score={row.score} />,
      },
      {
        key: 'computeStatus',
        header: '计算状态',
        width: '90px',
        align: 'center',
        render: (row) => <ComputeStatusBadge status={row.computeStatus} />,
      },
      {
        key: 'trigger',
        header: '触发方式',
        width: '80px',
        align: 'center',
        render: (row) => {
          const className =
            row.trigger === '手动'
              ? 'status-info'
              : row.trigger === '事件'
                ? 'status-warning'
                : 'status-neutral';
          return (
            <span className={`badge ${className} badge-sm`}>{row.trigger}</span>
          );
        },
      },
      {
        key: 'action',
        header: '操作',
        width: '80px',
        align: 'center',
        render: (row) => (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '2px 8px', fontSize: '12px' }}
            onClick={(e) => {
              e.stopPropagation();
              setDrawerRecord(row);
            }}
          >
            <Eye size={12} /> 详情
          </button>
        ),
      },
    ],
    [],
  );

  /** Drawer 中显示的 KPI 分项详情（基于评分确定性生成） */
  const drawerKpiItems = useMemo(() => {
    if (!drawerRecord) return [];
    return kpiDefinitions.map((kpi, idx) => {
      const offset = ((idx * 7 + 3) % 30) - 15;
      const itemScore = Math.max(
        0,
        Math.min(100, Math.round(drawerRecord.score + offset)),
      );
      return {
        kpiId: kpi.kpiId,
        kpiName: kpi.kpiName,
        kpiCode: kpi.kpiCode,
        score: itemScore,
        weight: kpi.weight,
        unit: kpi.unit,
      };
    });
  }, [drawerRecord]);

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>评估历史</h1>
          <p className="page-subtitle">
            回路评分历史趋势与评估记录 · 支持按回路与时间范围筛选
          </p>
        </div>
      </div>

      {/* 筛选栏 */}
      <FilterBar
        searchValue={searchValue}
        onSearchChange={setSearchValue}
        searchPlaceholder="搜索回路名..."
        filters={filters}
        showClearAll
        onClearAll={() => {
          setSearchValue('');
          setSelectedLoopId(loops[0]?.loopId ?? '');
          setTimeRange(7);
        }}
      />

      {/* 评分历史趋势图 */}
      <div className="card" style={{ marginBottom: '16px' }}>
        <div className="card-header">
          <h3>
            评分历史趋势 — {selectedLoop?.loopName ?? '未选择'}（{timeRange}天）
          </h3>
        </div>
        <div className="card-body">
          {selectedLoop ? (
            <ScoreHistoryChart trend={trendData} loopName={selectedLoop.loopName} />
          ) : (
            <div className="text-muted" style={{ textAlign: 'center', padding: '40px' }}>
              请选择回路
            </div>
          )}
        </div>
      </div>

      {/* 评估历史记录表 */}
      <div className="card">
        <div className="card-header">
          <h3>评估记录（共 {filteredRecords.length} 条）</h3>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <DataTable
            columns={columns}
            data={filteredRecords}
            rowKey={(row) => `${row.time}-${row.loopId}`}
            onRowClick={(row) => setDrawerRecord(row)}
            initialSortKey="time"
            initialSortDir="desc"
          />
        </div>
      </div>

      {/* 评估详情 Drawer */}
      <Drawer
        open={drawerRecord !== null}
        title="评估详情"
        onClose={() => setDrawerRecord(null)}
      >
        {drawerRecord && (
          <>
            {/* 评估基本信息 */}
            <div className="detail-meta-row" style={{ marginBottom: '16px' }}>
              <div className="detail-meta-item">
                <span className="label">评估时间</span>
                <span className="value mono">{drawerRecord.time}</span>
              </div>
              <div className="detail-meta-item">
                <span className="label">回路名</span>
                <span className="value">{drawerRecord.loopName}</span>
              </div>
              <div className="detail-meta-item">
                <span className="label">综合评分</span>
                <span className="value">
                  <ScoreBadge score={drawerRecord.score} size="md" />
                </span>
              </div>
              <div className="detail-meta-item">
                <span className="label">触发方式</span>
                <span className="value">{drawerRecord.trigger}</span>
              </div>
            </div>

            {/* KPI 分项得分 */}
            <div style={{ marginBottom: '8px', fontWeight: 600, fontSize: '14px' }}>
              KPI 分项得分
            </div>
            <div
              style={{
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-md)',
                overflow: 'hidden',
              }}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>KPI 指标</th>
                    <th style={{ width: '60px', textAlign: 'center' }}>权重</th>
                    <th style={{ width: '70px', textAlign: 'center' }}>得分</th>
                  </tr>
                </thead>
                <tbody>
                  {drawerKpiItems.map((item) => {
                    const color =
                      item.score >= 80
                        ? '#198754'
                        : item.score >= 60
                          ? '#B8860B'
                          : '#DC3545';
                    return (
                      <tr key={item.kpiId}>
                        <td>
                          <div style={{ fontWeight: 500 }}>{item.kpiName}</div>
                          <div
                            className="text-muted mono"
                            style={{ fontSize: '11px' }}
                          >
                            {item.kpiCode}
                          </div>
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          <span className="mono text-muted">{item.weight}%</span>
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          <span
                            className="mono"
                            style={{ color, fontWeight: 700 }}
                          >
                            {item.score}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Drawer>
    </div>
  );
}
