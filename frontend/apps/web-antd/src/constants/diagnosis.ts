/**
 * CLPM 诊断中心共享常量（IDS v3.2 §2.4）
 *
 * 集中定义 8 类诊断标签的展示映射，消除各视图组件中重复定义的
 * 标签名称、颜色、下拉选项等常量。
 */
import type { DiagnosisLabel } from '#/api/diagnosis';

/** 诊断标签中文名映射 */
export const DIAGNOSIS_LABEL_NAME_MAP: Record<DiagnosisLabel, string> = {
  EXTERNAL_DISTURBANCE: '外扰频繁',
  MANUAL_REVIEW: '人工复核',
  OSCILLATION: '振荡',
  OUTPUT_SATURATION: '输出饱和',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  QUALITY_ABNORMAL: 'PV 质量异常',
  VALVE_STICTION: '阀门粘滞',
};

/** 诊断标签下拉选项（用于 Select 组件） */
export const DIAGNOSIS_LABEL_OPTIONS: {
  label: string;
  value: DiagnosisLabel;
}[] = [
  { label: '振荡', value: 'OSCILLATION' },
  { label: '阀门粘滞', value: 'VALVE_STICTION' },
  { label: '参数过激', value: 'OVERAGGRESSIVE' },
  { label: '参数过保守', value: 'OVERCONSERVATIVE' },
  { label: '外扰频繁', value: 'EXTERNAL_DISTURBANCE' },
  { label: 'PV 质量异常', value: 'QUALITY_ABNORMAL' },
  { label: '输出饱和', value: 'OUTPUT_SATURATION' },
  { label: '人工复核', value: 'MANUAL_REVIEW' },
];

/** 诊断标签颜色映射（Ant Design Vue Tag 颜色名） */
export const DIAGNOSIS_LABEL_COLOR_MAP: Record<DiagnosisLabel, string> = {
  OSCILLATION: 'red',
  VALVE_STICTION: 'orange',
  OVERAGGRESSIVE: 'purple',
  OVERCONSERVATIVE: 'blue',
  EXTERNAL_DISTURBANCE: 'cyan',
  QUALITY_ABNORMAL: 'default',
  OUTPUT_SATURATION: 'gold',
  MANUAL_REVIEW: 'default',
};

/** 诊断标签十六进制颜色映射（用于 ECharts 图表） */
export const DIAGNOSIS_LABEL_COLOR_HEX_MAP: Record<DiagnosisLabel, string> = {
  OSCILLATION: '#ff4d4f',
  VALVE_STICTION: '#fa8c16',
  OVERAGGRESSIVE: '#722ed1',
  OVERCONSERVATIVE: '#1890ff',
  EXTERNAL_DISTURBANCE: '#13c2c2',
  QUALITY_ABNORMAL: '#8c8c8c',
  OUTPUT_SATURATION: '#faad14',
  MANUAL_REVIEW: '#d9d9d9',
};

/**
 * 诊断标签码转中文名称
 * @param label 诊断标签枚举值
 */
export function getDiagnosisLabelName(label: DiagnosisLabel): string {
  return DIAGNOSIS_LABEL_NAME_MAP[label] || label;
}
