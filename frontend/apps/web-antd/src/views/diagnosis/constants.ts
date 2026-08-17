/**
 * 诊断模块展示常量（纯数据，无请求依赖，便于复用与测试）。
 *
 * 色彩对齐《色彩约定表 v1.0》工业语义色。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

export interface CategoryMeta {
  label: string;
  color: string;
  direction: string;
}

export const CATEGORY_META: Record<DiagnosisApi.Category, CategoryMeta> = {
  TUNING: { label: '参数问题', color: '#0d6efd', direction: '重新整定参数' },
  VALVE: { label: '阀门/执行机构问题', color: '#b45309', direction: '检修/更换配件' },
  INSTRUMENT: { label: '仪表/测量问题', color: '#6f42c1', direction: '校验/维护' },
  COMMUNICATION: {
    label: '通信链路问题',
    color: '#0284c7',
    direction: '检查通信链路',
  },
  PROCESS: { label: '工艺/外扰问题', color: '#0d9488', direction: '工艺分析/前馈/解耦' },
  UTILIZATION: { label: '投用/操作问题', color: '#ca8a04', direction: '恢复自动投用' },
  DESIGN: { label: '组态/设计问题', color: '#795548', direction: '重新组态/改造' },
  DATA_INSUFFICIENT: { label: '数据不足', color: '#6c757d', direction: '先补齐数据' },
};

/** 分类筛选下拉选项 */
export const CATEGORY_OPTIONS: Array<{ label: string; value: DiagnosisApi.Category }> = (
  Object.entries(CATEGORY_META) as Array<[DiagnosisApi.Category, CategoryMeta]>
).map(([value, meta]) => ({ label: meta.label, value }));

export const SEVERITY_TEXT: Record<string, string> = {
  HIGH: '高',
  LOW: '低',
  MEDIUM: '中',
};

export const SEVERITY_COLOR: Record<string, string> = {
  HIGH: 'red',
  LOW: 'default',
  MEDIUM: 'orange',
};

export const RUN_STATUS_TEXT: Record<string, string> = {
  FAILED: '失败',
  PARTIAL: '部分完成',
  RUNNING: '进行中',
  SUCCESS: '完成',
};

/** 触发类型标签（§12 三层自动诊断：手动 / 分级定时 / 预警事件） */
export const TRIGGER_TYPE_TEXT: Record<string, string> = {
  EVENT: '事件触发',
  MANUAL: '手动诊断',
  SCHEDULED: '定期诊断',
};

/** 触发类型色（工业语义：定期=青、事件=橙、手动=中性） */
export const TRIGGER_TYPE_COLOR: Record<string, string> = {
  EVENT: '#ea580c',
  MANUAL: '#6c757d',
  SCHEDULED: '#0891b2',
};
