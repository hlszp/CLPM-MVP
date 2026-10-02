/**
 * 趋势引擎共享类型（workbench360 P1）
 */

/** 单帧趋势样本（ts 毫秒 epoch；pv/sp/op 可缺失；mode 为原始数值） */
export interface TrendFrame {
  ts: number;
  pv: null | number;
  sp: null | number;
  op: null | number;
  mode: null | number;
  quality: 'BAD' | 'GOOD' | 'UNCERTAIN' | null;
}

/** X 数据域 / 视口（毫秒） */
export interface TimeRange {
  t0: number;
  t1: number;
}

/** Y 视口（工程量） */
export interface YRange {
  lo: number;
  hi: number;
}

/** 事件标注徽标（P1 仅 MANUAL 段；诊断/整定/验证 P2-P4 接数据后填） */
export interface TrendEventMark {
  /** 徽标形（▼诊断 / ◆整定 / ▮验证 / ⏸手动） */
  glyph: string;
  key: string;
  label: string;
  /** 点击动作目标剖面（null=无动作） */
  section?: null | 'assess' | 'diag' | 'handling' | 'tuning';
  ts: number;
}

/** 视口 X 变化（EventLane 与滚动条联动） */
export type ViewXChange = TimeRange;
