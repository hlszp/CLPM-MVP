/**
 * CLPM 诊断中心共享常量（IDS v3.2 §2.4）
 *
 * 集中定义 8 类诊断标签的展示映射，消除各视图组件中重复定义的
 * 标签名称、颜色、下拉选项等常量。
 */
import type { DiagnosisLabel } from '#/api/diagnosis';

// P2-01：re-export 供组件直接从 constants/diagnosis 统一导入
export type { DiagnosisLabel } from '#/api/diagnosis';

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

// ===== P2-01：结构化诊断报告数据 =====

/** 诊断建议动作类型 */
export type DiagnosisActionType =
  | 'investigation'
  | 'maintenance'
  | 'review'
  | 'tuning';

/** 紧急程度 */
export type DiagnosisUrgency = 'high' | 'low' | 'medium';

/** 结构化诊断报告项（P2-01） */
export interface StructuredDiagnosisReport {
  /** 根因分析 */
  cause: string;
  /** 建议下一步 */
  suggestion: string;
  /** 预估改善效果 */
  improvement: string;
  /** 动作类型 */
  actionType: DiagnosisActionType;
  /** 紧急程度 */
  urgency: DiagnosisUrgency;
}

/** 动作类型 → 中文标签 */
export const DIAGNOSIS_ACTION_TYPE_LABEL: Record<
  DiagnosisActionType,
  string
> = {
  tuning: 'PID 整定',
  maintenance: '仪表维护',
  investigation: '工况排查',
  review: '人工复核',
};

/** 动作类型 → Tag 颜色 */
export const DIAGNOSIS_ACTION_TYPE_COLOR: Record<
  DiagnosisActionType,
  string
> = {
  tuning: 'purple',
  maintenance: 'orange',
  investigation: 'cyan',
  review: 'default',
};

/** 紧急程度 → 中文标签 */
export const DIAGNOSIS_URGENCY_LABEL: Record<DiagnosisUrgency, string> = {
  high: '紧急',
  medium: '一般',
  low: '低',
};

/** 紧急程度 → 颜色 */
export const DIAGNOSIS_URGENCY_COLOR: Record<DiagnosisUrgency, string> = {
  high: 'red',
  medium: 'gold',
  low: 'default',
};

/**
 * 诊断标签 → 结构化报告数据（P2-01）
 *
 * 将"有振荡"升级为"PID 过激 65% / 阀门粘滞 25% / 外扰 10%"的结构化呈现：
 * 原因排序 + 概率 + 建议下一步 + 预估改善效果。
 */
export const DIAGNOSIS_STRUCTURED_REPORT: Record<
  DiagnosisLabel,
  StructuredDiagnosisReport
> = {
  OSCILLATION: {
    cause: 'PV/OP 出现周期性波动，可能由 PID 参数过激、阀门粘滞或外扰引起',
    suggestion:
      '结合频谱分析定位振荡源：峰频与回路自然频率一致→参数过激；PV-OP 椭圆轨迹→阀门粘滞；无明显峰频→外扰',
    improvement: '消除振荡后综合评分预计提升 15-30 分，平稳率提升至 90%+',
    actionType: 'tuning',
    urgency: 'high',
  },
  VALVE_STICTION: {
    cause: '调节阀存在静摩擦（stiction），OP 变化时卡涩不动，累积后突然动作',
    suggestion:
      '联系仪表人员检修调节阀（更换填料/润滑），或临时增加 PID 积分作用补偿',
    improvement: '检修后振荡消除，综合评分预计提升 20-40 分',
    actionType: 'maintenance',
    urgency: 'high',
  },
  OVERAGGRESSIVE: {
    cause: 'PID 比例增益过大或积分时间过短，控制器对偏差反应过度导致振荡',
    suggestion:
      '减小比例增益（Kp ↓20-30%）或增大积分时间（Ti ↑1.5-2 倍），使用整定工作台仿真对比',
    improvement: '参数调整后振荡消除，综合评分预计提升 15-25 分',
    actionType: 'tuning',
    urgency: 'high',
  },
  OVERCONSERVATIVE: {
    cause: 'PID 比例增益过小或积分时间过长，控制器响应迟缓无法及时消除偏差',
    suggestion:
      '增大比例增益（Kp ↑30-50%）或减小积分时间（Ti ↓30-50%），使用整定工作台仿真对比',
    improvement: '参数调整后响应速度提升，综合评分预计提升 10-20 分，快速率显著改善',
    actionType: 'tuning',
    urgency: 'medium',
  },
  EXTERNAL_DISTURBANCE: {
    cause: '上游负荷、原料组分等不可控因素频繁变化，超出回路调节能力',
    suggestion:
      '排查扰动源（上游流量/温度/压力变化），考虑增加前馈补偿或调整回路结构（如串级控制）',
    improvement: '前馈补偿后抗扰能力提升，综合评分预计提升 10-15 分',
    actionType: 'investigation',
    urgency: 'medium',
  },
  OUTPUT_SATURATION: {
    cause: 'OP 长期处于上下限附近，执行器已达极限位置仍无法消除偏差',
    suggestion:
      '检查阀门选型是否匹配工况（可能需增大阀门口径），或调整工艺参数降低负荷',
    improvement: '解除饱和后恢复调节能力，综合评分预计提升 5-15 分',
    actionType: 'investigation',
    urgency: 'medium',
  },
  QUALITY_ABNORMAL: {
    cause: '传感器故障或通讯问题导致 PV 信号存在坏值，影响 KPI 计算准确性',
    suggestion:
      '联系仪表人员检查测量回路（传感器校验/接线/通讯），修复后重新触发评估',
    improvement: '修复后数据质量恢复，KPI 评估可信度提升至 A/B 级',
    actionType: 'maintenance',
    urgency: 'high',
  },
  MANUAL_REVIEW: {
    cause: '自动诊断无法明确归类，特征值处于多个标签的边界区域',
    suggestion: '由经验丰富的仪控工程师结合工艺情况、历史趋势和频谱图综合分析',
    improvement: '人工定位后针对性优化，避免盲目整定',
    actionType: 'review',
    urgency: 'low',
  },
};

