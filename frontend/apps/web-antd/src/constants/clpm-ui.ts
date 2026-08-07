/**
 * CLPM 工业设计常量（UI/UX v6.1）
 *
 * 集中定义可信度等级、严重度等级、专业术语Tooltip等映射，
 * 供所有列表/详情页统一使用，消除视图内重复硬编码。
 */
import type { ConfidenceLevel } from '#/api/metric';

// ---------------------------------------------------------------------------
// 可信度等级（A/B/C/D/E） — 基于有效数据率 valid_rate
// ---------------------------------------------------------------------------

/** 可信度等级 → 有效数据率阈值映射（GB/T 44693.2-2024） */
export const CONFIDENCE_LEVEL_THRESHOLDS: Record<ConfidenceLevel, number> = {
  A: 0.95, // ≥95% 优秀
  B: 0.8, // ≥80% 良好
  C: 0.6, // ≥60% 一般
  D: 0.2, // ≥20% 较差
  E: 0.0, // <20% 极差
};

/** 可信度等级 → 中文释义 */
export const CONFIDENCE_LEVEL_LABEL: Record<ConfidenceLevel, string> = {
  A: '数据充分',
  B: '数据良好',
  C: '数据一般',
  D: '数据不足',
  E: '数据极差',
};

/** 可信度等级 → 详细说明（用于Tooltip） */
export const CONFIDENCE_LEVEL_DESCRIPTION: Record<ConfidenceLevel, string> = {
  A: '有效数据率≥95%，评估结果高度可信，可直接用于决策',
  B: '有效数据率≥80%，评估结果可信，建议关注缺失数据时段',
  C: '有效数据率≥60%，评估结果仅供参考，建议补齐数据后重新评估',
  D: '有效数据率≥20%，数据缺失严重，评估结果不可靠',
  E: '有效数据率<20%，数据严重缺失，无法给出可信评估',
};

/** 可信度等级 → ZL 工业语义色 */
export const CONFIDENCE_LEVEL_STATUS: Record<
  ConfidenceLevel,
  'info' | 'neutral' | 'ok' | 'warning'
> = {
  A: 'ok',
  B: 'ok',
  C: 'info',
  D: 'warning',
  E: 'neutral',
};

/**
 * 根据 valid_rate 推断可信度等级
 * @param validRate 有效数据率 0~1，或 null/undefined
 */
export function getConfidenceLevel(
  validRate: null | number | undefined,
): ConfidenceLevel | null {
  if (validRate === null || validRate === undefined || Number.isNaN(validRate))
    return null;
  if (validRate >= CONFIDENCE_LEVEL_THRESHOLDS.A) return 'A';
  if (validRate >= CONFIDENCE_LEVEL_THRESHOLDS.B) return 'B';
  if (validRate >= CONFIDENCE_LEVEL_THRESHOLDS.C) return 'C';
  if (validRate >= CONFIDENCE_LEVEL_THRESHOLDS.D) return 'D';
  return 'E';
}

/**
 * 根据 confidence 数值（0~1）和可选 validRate 推断等级
 * 优先使用后端返回的 confidenceLevel；若后端未返回则按 validRate 推断
 */
export function resolveConfidenceLevel(
  confidence: null | number | undefined,
  validRate?: null | number,
  confidenceLevel?: ConfidenceLevel | null,
): ConfidenceLevel | null {
  if (confidenceLevel) return confidenceLevel;
  if (validRate !== null && validRate !== undefined)
    return getConfidenceLevel(validRate);
  // 退化方案：按旧 confidence 数值推断（仅作兼容）
  if (
    confidence === null ||
    confidence === undefined ||
    Number.isNaN(confidence)
  )
    return null;
  if (confidence >= 0.9) return 'A';
  if (confidence >= 0.7) return 'B';
  if (confidence >= 0.5) return 'C';
  if (confidence >= 0.3) return 'D';
  return 'E';
}

// ---------------------------------------------------------------------------
// 严重度等级（CRITICAL/ERROR/WARN/INFO） — 诊断标签/跟踪项
// ---------------------------------------------------------------------------

export type SeverityLevel = 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';

/** 严重度 → 中文名称 */
export const SEVERITY_LABEL: Record<SeverityLevel, string> = {
  CRITICAL: '紧急',
  ERROR: '严重',
  WARN: '警告',
  INFO: '提示',
};

/** 严重度 → ZL 工业语义色 */
export const SEVERITY_STATUS: Record<
  SeverityLevel,
  'error' | 'info' | 'warning'
> = {
  CRITICAL: 'error',
  ERROR: 'error',
  WARN: 'warning',
  INFO: 'info',
};

/** 严重度 → 图标 */
export const SEVERITY_ICON: Record<SeverityLevel, string> = {
  CRITICAL: 'lucide:alert-octagon',
  ERROR: 'lucide:alert-circle',
  WARN: 'lucide:alert-triangle',
  INFO: 'lucide:info',
};

// ---------------------------------------------------------------------------
// 专业术语 Tooltip 解释
// ---------------------------------------------------------------------------

export interface TermExplanation {
  term: string;
  short: string;
  detail?: string;
}

/** KPI 指标名称 → 解释 */
export const KPI_TERM_EXPLANATIONS: Record<string, TermExplanation> = {
  compositeScore: {
    term: '综合评分',
    short: '加权综合得分，0-100分',
    detail:
      '基于3个核心KPI（利用率、准确率、快速性）加权计算，权重可在指标配置中调整',
  },
  utilizationRate: {
    term: '利用率',
    short: '回路有效运行时间占比',
    detail: '反映回路在自动模式下正常运行的时间比例',
  },
  accuracyScore: {
    term: '准确率',
    short: '控制偏差综合评分',
    detail: '基于IAE、稳态误差等指标综合评估控制精度',
  },
  responseScore: {
    term: '快速性',
    short: '设定值跟踪响应速度评分',
    detail: '评估回路响应设定值变化和扰动的快速程度',
  },
  steadyScore: {
    term: '稳定性',
    short: '振荡和波动程度评分',
    detail: '评估控制过程的平稳性，振荡越严重得分越低',
  },
  effectiveAutoRate: {
    term: '有效投自动率',
    short: '高质量自动运行时间占比',
    detail: '自动模式下且控制质量合格的时间比例，剔除异常工况',
  },
};

/** 诊断标签 → 解释 */
export const DIAGNOSIS_TERM_EXPLANATIONS: Record<string, TermExplanation> = {
  OSCILLATION: {
    term: '振荡',
    short: 'PV/OP出现周期性波动',
    detail: '可能由参数过激、阀门粘滞或外扰引起，建议结合频谱分析进一步定位',
  },
  VALVE_STICTION: {
    term: '阀门粘滞',
    short: '调节阀存在静摩擦问题',
    detail: '阀门卡涩导致PV-OP出现特征性椭圆轨迹，需联系仪表人员检修',
  },
  OVERAGGRESSIVE: {
    term: '参数过激',
    short: 'PID参数过于敏感',
    detail:
      '比例增益过大或积分时间过短导致振荡，建议适当减小增益或增大积分时间',
  },
  OVERCONSERVATIVE: {
    term: '参数过保守',
    short: 'PID参数响应迟缓',
    detail:
      '比例增益过小或积分时间过长导致响应缓慢，建议适当增大增益或减小积分时间',
  },
  EXTERNAL_DISTURBANCE: {
    term: '外扰频繁',
    short: '存在不可控外部扰动',
    detail:
      '上游负荷、原料组分等频繁变化影响回路稳定，建议排查扰动源并考虑前馈补偿',
  },
  QUALITY_ABNORMAL: {
    term: 'PV质量异常',
    short: '测量信号存在坏值',
    detail: '传感器故障或通讯问题导致PV信号异常，需联系仪表人员检查测量回路',
  },
  OUTPUT_SATURATION: {
    term: '输出饱和',
    short: 'OP长期处于上下限',
    detail:
      '执行器已达极限位置仍无法消除偏差，可能是阀门选型不当或工况超出设计范围',
  },
  MANUAL_REVIEW: {
    term: '人工复核',
    short: '需工程师结合经验判断',
    detail: '自动诊断无法明确归类，建议由经验丰富的仪控工程师结合工艺情况分析',
  },
};

/** 控制类型 → 解释 */
export const CONTROL_TYPE_EXPLANATIONS: Record<string, TermExplanation> = {
  FAST: { term: '快速回路', short: '流量、压力等快响应回路' },
  SLOW: { term: '慢速回路', short: '温度、成分等慢响应回路' },
  STABLE: { term: '平稳回路', short: '液位等需平稳控制的回路' },
  LOGIC: { term: '逻辑回路', short: '顺控、联锁等逻辑控制' },
};

/** 重要等级 → 解释 */
export const IMPORTANCE_EXPLANATIONS: Record<string, TermExplanation> = {
  CRITICAL: { term: '关键', short: '直接影响安全或产品质量' },
  IMPORTANT: { term: '重要', short: '影响装置平稳运行' },
  GENERAL: { term: '一般', short: '辅助回路，影响较小' },
};

/**
 * Tag 7 槽位 → 解释（P1-01 回路配置向导化）
 *
 * 对齐 AAS 数据模型：回路由用户创建并关联 7 个 OPC tag。
 * 必填槽位（PV/SP/OP/MODE）缺一则回路状态为 PARTIAL，无法进入评估；
 * 可选槽位（PID_P/PID_I/PID_D）缺一仅影响 PID 参数只读展示，不影响评估。
 */
export const TAG_SLOT_TERM_EXPLANATIONS: Record<string, TermExplanation> = {
  pv: {
    term: 'PV 过程变量',
    short: '过程变量测量值，回路控制的被控量',
    detail: '如温度、压力、流量、液位等现场测量值；KPI 计算的核心输入',
  },
  sp: {
    term: 'SP 设定值',
    short: '回路控制目标值，操作员设定的工艺参数',
    detail: 'PV 追踪的目标；准确率指标基于 PV 与 SP 的偏差计算',
  },
  op: {
    term: 'OP 控制器输出',
    short: 'PID 控制器输出值，驱动执行机构',
    detail: '如阀门开度、电机转速等；饱和率/输出跳变率基于 OP 计算',
  },
  mode: {
    term: 'MODE 控制模式',
    short: '回路当前控制模式（自动/手动/串级等）',
    detail: '自控率/有效自控率基于 MODE 判定；需关联 DCS 型号做值映射',
  },
  pid_p: {
    term: 'PID_P 比例增益',
    short: '比例参数，可选（仅只读展示）',
    detail: '从关联 Tag 实时读取，平台不回写 DCS；缺省不影响 KPI 评估',
  },
  pid_i: {
    term: 'PID_I 积分时间',
    short: '积分参数，可选（仅只读展示）',
    detail: '从关联 Tag 实时读取，平台不回写 DCS；缺省不影响 KPI 评估',
  },
  pid_d: {
    term: 'PID_D 微分时间',
    short: '微分参数，可选（仅只读展示）',
    detail: '从关联 Tag 实时读取，平台不回写 DCS；缺省不影响 KPI 评估',
  },
};

// ---------------------------------------------------------------------------
// 智能预警规则引擎 — 枚举值中文映射
// 说明：DSL 技术配置（JSON 编辑框）保留英文键名以对齐后端校验；
//       列表 Tag、筛选下拉、详情展示等面向用户的文本统一显示中文。
// ---------------------------------------------------------------------------

/** 规则类型 → 中文 */
export const ALERT_RULE_TYPE_LABEL: Record<
  'COMPOSITE' | 'CONFIDENCE' | 'DRIFT' | 'THRESHOLD',
  string
> = {
  THRESHOLD: '阈值',
  DRIFT: '漂移',
  COMPOSITE: '组合',
  CONFIDENCE: '可信度',
};

/** 监控指标 → 中文（对齐 7 个 Tag 角色） */
export const ALERT_METRIC_LABEL: Record<string, string> = {
  PV: '过程变量 PV',
  SP: '设定值 SP',
  OP: '控制器输出 OP',
  MODE: '控制模式 MODE',
  PID_P: '比例增益 P',
  PID_I: '积分时间 I',
  PID_D: '微分时间 D',
};

/** 订阅范围类型 → 中文 */
export const ALERT_SCOPE_TYPE_LABEL: Record<
  'ALL' | 'CONTROL_TYPE' | 'LOOP' | 'PLANT',
  string
> = {
  ALL: '全部回路',
  LOOP: '指定回路',
  PLANT: '按装置',
  CONTROL_TYPE: '按控制类型',
};

/** 统计量 → 中文（DRIFT 规则 condition.statistic） */
export const ALERT_STATISTIC_LABEL: Record<
  'MAX' | 'MEAN' | 'MIN' | 'P95' | 'P99' | 'STDDEV',
  string
> = {
  MEAN: '均值',
  STDDEV: '标准差',
  P95: '95 分位',
  P99: '99 分位',
  MIN: '最小值',
  MAX: '最大值',
};

/** 偏差类型 → 中文（DRIFT 规则 condition.deviationType） */
export const ALERT_DEVIATION_TYPE_LABEL: Record<
  'ABSOLUTE' | 'RELATIVE' | 'SIGMA',
  string
> = {
  ABSOLUTE: '绝对偏差',
  RELATIVE: '相对偏差',
  SIGMA: '标准差倍数',
};

/** 基线类型 → 中文（DRIFT 规则 condition.baseline.type） */
export const ALERT_BASELINE_TYPE_LABEL: Record<
  'HISTORICAL' | 'RULE_BASED' | 'STATIC',
  string
> = {
  STATIC: '静态值',
  HISTORICAL: '历史基线',
  RULE_BASED: '规则推导',
};

/** 组合逻辑 → 中文（COMPOSITE 规则 condition.logic） */
export const ALERT_LOGIC_LABEL: Record<
  'AND' | 'NOT' | 'OR' | 'SEQUENCE',
  string
> = {
  AND: '全部满足',
  OR: '任一满足',
  NOT: '取反',
  SEQUENCE: '时序',
};

/** 动作类型 → 中文（DSL actions[].type） */
export const ALERT_ACTION_TYPE_LABEL: Record<
  'CREATE_EVENT' | 'CREATE_TRACKER' | 'NOTIFY',
  string
> = {
  CREATE_EVENT: '生成事件',
  CREATE_TRACKER: '创建工单',
  NOTIFY: '通知',
};

/** 比较运算符 → 中文（THRESHOLD 规则 condition.operator） */
export const ALERT_OPERATOR_LABEL: Record<string, string> = {
  '>': '大于',
  '>=': '大于等于',
  '<': '小于',
  '<=': '小于等于',
  '==': '等于',
  '!=': '不等于',
  IN: '属于',
  NOT_IN: '不属于',
  RATE_OF_CHANGE: '变化率',
};
