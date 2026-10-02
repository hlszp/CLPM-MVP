/**
 * CLPM 工业设计常量（UI/UX v6.1）
 *
 * 集中定义可信度等级、严重度等级、专业术语Tooltip等映射，
 * 供所有列表/详情页统一使用，消除视图内重复硬编码。
 */
import type { ConfidenceLevel, MetricApi } from '#/api/metric';

// ---------------------------------------------------------------------------
// 可信度等级（A/B/C/D/E） — 基于有效数据率 valid_rate
// ---------------------------------------------------------------------------

/** 可信度等级 → 有效数据率阈值映射（GB/T 44693.2-2024） */
export const CONFIDENCE_LEVEL_THRESHOLDS: Record<ConfidenceLevel, number> = {
  A: 0.95, // ≥95% 优秀
  B: 0.8, // ≥80% 良好
  C: 0.6, // ≥60% 一般
  D: 0.2, // ≥20% 较差
  E: 0, // <20% 极差
};

/** 可信度等级 → 中文释义 */
export const CONFIDENCE_LEVEL_LABEL: Record<ConfidenceLevel, string> = {
  A: '数据充分',
  B: '数据良好',
  C: '数据一般',
  D: '数据较少',
  E: '数据不足',
};

/** 可信度等级 → 详细说明（用于Tooltip） */
export const CONFIDENCE_LEVEL_DESCRIPTION: Record<ConfidenceLevel, string> = {
  A: '有效数据率≥95%，评估结果高度可信，可直接用于决策',
  B: '有效数据率≥80%，评估结果可信，建议关注缺失数据时段',
  C: '有效数据率≥60%，评估结果仅供参考，建议补齐数据后重新评估',
  D: '有效数据率≥20%，数据缺失严重，评估结果不可靠',
  E: '有效数据率<20%，数据严重缺失，无法给出可信评估',
};

// ---------------------------------------------------------------------------
// 综合评分定级档位（GB/T 44693.2-2024 §6.3） — 全站唯一定义（0929 口径收敛）
// ---------------------------------------------------------------------------

/**
 * 定级档位默认值：动态配置（/configs/grading-thresholds）加载失败时降级使用。
 * 此前 use-score-color / cockpit / pid-dashboard / 回路监视 / 指标矩阵 各持一份
 * 硬编码且互有出入（A–E 五档 90/80/70/60、四档 90/80/60 三套并存），已全部收敛到本常量。
 */
export const GRADE_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  { level: 1, name: 'EXCELLENT', label: '优秀', minScore: 90, maxScore: 100 },
  { level: 2, name: 'GOOD', label: '良好', minScore: 80, maxScore: 90 },
  { level: 3, name: 'FAIR', label: '合格', minScore: 60, maxScore: 80 },
  { level: 4, name: 'WARNING', label: '警告', minScore: 40, maxScore: 60 },
  { level: 5, name: 'POOR', label: '不合格', minScore: 0, maxScore: 40 },
];

/** 档位 level → 中文名（1 优秀 … 5 不合格） */
export const GRADE_LEVEL_LABEL: Record<number, string> = Object.fromEntries(
  GRADE_THRESHOLDS.map((t) => [t.level, t.label ?? t.name]),
);

/**
 * score → 等级信息（字母档 + level + 中文名；无评分返回 null）。
 *
 * P2 抽取（workbench360 移交项）：此前 use-wb360-loop / JourneyRail / ThumbStrip
 * 各持一份 90/80/60/40 硬编码，收敛到 GRADE_THRESHOLDS 单源派生。
 * 注意：基于默认阈值；动态阈值配置（/configs/grading-thresholds）场景
 * 仍走 useScoreColor(score, thresholds) 判定链。
 */
export function scoreToGradeInfo(
  score: null | number | undefined,
): null | { label: string; letter: string; level: number } {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  const sorted = [...GRADE_THRESHOLDS].toSorted(
    (a, b) => b.minScore - a.minScore,
  );
  const hit = sorted.find((t) => score >= t.minScore) ?? sorted.at(-1) ?? null;
  if (!hit) return null;
  // 字母档：A–E 对应 level 1–5（左脊柱等级筛选/趋势等级色同口径）
  const letter = String.fromCodePoint(64 + hit.level);
  return { label: hit.label ?? hit.name, letter, level: hit.level };
}

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

/**
 * 预警等级（预制规则三级阈值口径）→ 中文名称。
 * 与 SEVERITY_LABEL（诊断/跟踪模块共用）区分：预警事件列表专用。
 */
export const ALERT_LEVEL_LABEL: Record<SeverityLevel, string> = {
  CRITICAL: '紧急',
  ERROR: '重要',
  WARN: '一般',
  INFO: '提示',
};

// ---------------------------------------------------------------------------
// 适用性原因标签（fitness tags，loop_fitness.py 枚举）
// ---------------------------------------------------------------------------

/** 适用性原因标签 → 中文描述（各模块统一映射，禁止页面内重复定义） */
export const FITNESS_TAG_LABEL: Record<string, string> = {
  // H1 修复（2026-10-01）：后端 loop_fitness.py 实际产出以下 7 标签
  // （T_* 系为历史标签，保留兼容旧快照；文案与后端 TAG_HUMAN_REASON 一致）
  DATA_INSUFFICIENT: '数据严重不足',
  MANUAL_DOMINANT: '手动模式占比过高',
  LOW_AUTO_RATE: '自控率极低',
  OP_SATURATED: 'OP 长期处于饱和限位附近',
  SP_PV_DEVIATION: 'SP-PV 长期偏离设定',
  NO_EXCITATION: 'OP 无有效激励',
  WEAK_RESPONSE: 'PV 对 OP 响应极弱',
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};

/** 适用性原因标签转中文（未知标签原样返回） */
export function fitnessTagToLabel(tag: string): string {
  return FITNESS_TAG_LABEL[tag] ?? tag;
}

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
  'COMPOSITE' | 'CONFIDENCE' | 'DRIFT' | 'METRIC_THRESHOLD' | 'THRESHOLD',
  string
> = {
  METRIC_THRESHOLD: '指标阈值',
  THRESHOLD: '阈值（存量）',
  DRIFT: '漂移（存量）',
  COMPOSITE: '组合（存量）',
  CONFIDENCE: '可信度（存量）',
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

/** 动作类型 → 中文（DSL actions[].type；CREATE_TRACKER 已关停，
 * 保留类型仅为存量规则展示兼容，新建规则模板不再提供） */
export const ALERT_ACTION_TYPE_LABEL: Record<
  'CREATE_EVENT' | 'CREATE_TRACKER' | 'NOTIFY',
  string
> = {
  CREATE_EVENT: '生成事件',
  CREATE_TRACKER: '创建工单（已停用）',
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

// ===========================================================================
// P2-01 状态颜色统一映射（2026-08-10）
//
// 色彩约定表 v1.0 单一来源：industrial-light.css 的 --status-* 变量族。
// 本专章集中所有"业务状态/严重度 → 语义 token"映射，消除各视图散落的
// statusColorMap / PRIORITY_COLOR / IMPORTANCE_LEVEL_TAG 等硬编码。
//
// 三层架构：
//   业务状态（PENDING/URGENT/...）→ StatusToken（ok/warning/...）→ 表现层
//                                                             ├─ antd Tag color
//                                                             ├─ CSS 变量
//                                                             └─ hex（ECharts）
//
// 使用方式：
//   import { TASK_STATUS_TO_STATUS, statusTokenToAntdColor } from '#/constants/clpm-ui';
//   <Tag :color="statusTokenToAntdColor(TASK_STATUS_TO_STATUS[row.status])">
//
// 响应式场景（ECharts/暗色）请用 useClpmTheme().themeColors 直接取 hex。
// ===========================================================================

/**
 * 语义状态 token — 对齐 industrial-light.css 的 --status-* 变量族
 *
 * - ok       → --status-ok      (#198754) 正常/达标/成功
 * - warning  → --status-warning  (#B45309) 警告/需关注
 * - error    → --status-error    (#DC3545) 危险/故障/失败
 * - info     → --status-info     (#0D6EFD) 信息/进行中
 * - neutral  → --status-neutral  (#6C757D) 中性/未知/无数据
 */
export type StatusToken = 'error' | 'info' | 'neutral' | 'ok' | 'warning';

/** 语义 token → Ant Design Vue Tag color 属性值 */
export const STATUS_TOKEN_TO_ANTD_COLOR: Record<StatusToken, string> = {
  ok: 'success',
  warning: 'warning',
  error: 'error',
  info: 'processing',
  neutral: 'default',
};

/** 语义 token → CSS 变量引用（用于内联 style / 自定义组件） */
export const STATUS_TOKEN_TO_CSS_VAR: Record<StatusToken, string> = {
  ok: 'var(--status-ok)',
  warning: 'var(--status-warning)',
  error: 'var(--status-error)',
  info: 'var(--status-info)',
  neutral: 'var(--status-neutral)',
};

/** 语义 token → hex 浅色值（仅用于 ECharts 等无法消费 CSS 变量的场景；暗色请用 useClpmTheme） */
export const STATUS_TOKEN_TO_HEX: Record<StatusToken, string> = {
  ok: '#198754',
  warning: '#b45309',
  error: '#dc3545',
  info: '#0d6efd',
  neutral: '#6c757d',
};

/**
 * 语义 token → Tailwind badge class（浅底+深字+边框+暗色覆盖）
 *
 * 用于自定义 span badge 场景（如回路重要等级），统一语义色表现层。
 * 暗色覆盖对齐 industrial-light.css §10.5 的半透明策略。
 */
export const STATUS_TOKEN_TO_BADGE_CLASS: Record<StatusToken, string> = {
  ok: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30',
  warning:
    'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
  error:
    'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30',
  info: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30',
  neutral:
    'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
};

/** 语义 token → antd Tag color（便捷别名） */
export function statusTokenToAntdColor(token: StatusToken): string {
  return STATUS_TOKEN_TO_ANTD_COLOR[token];
}

/** 语义 token → CSS 变量（便捷别名） */
export function statusTokenToCssVar(token: StatusToken): string {
  return STATUS_TOKEN_TO_CSS_VAR[token];
}

// ---------------------------------------------------------------------------
// 任务状态映射（统一 task/list.vue + task/detail.vue，原 statusColorMap）
// 对齐 TaskApi.TaskStatus: PENDING | RUNNING | SUCCESS | FAILED | CANCELLED
// ---------------------------------------------------------------------------

export const TASK_STATUS_TO_STATUS: Record<string, StatusToken> = {
  PENDING: 'neutral',
  RUNNING: 'info',
  SUCCESS: 'ok',
  FAILED: 'error',
  CANCELLED: 'warning',
};

export const TASK_STATUS_LABEL: Record<string, string> = {
  PENDING: '待执行',
  RUNNING: '执行中',
  SUCCESS: '成功',
  FAILED: '失败',
  CANCELLED: '已取消',
};

// ---------------------------------------------------------------------------
// 关注优先级映射（统一 monitor/attention.vue，原 PRIORITY_COLOR）
// 对齐 MonitorApi.AttentionPriority: URGENT | HIGH | MEDIUM | LOW
// 色彩约定：URGENT=需立即行动(error) / HIGH=需关注(warning) /
//           MEDIUM=待处理(info) / LOW=低优先(neutral)
// ---------------------------------------------------------------------------

export const PRIORITY_TO_STATUS: Record<string, StatusToken> = {
  URGENT: 'error',
  HIGH: 'warning',
  MEDIUM: 'info',
  LOW: 'neutral',
};

export const PRIORITY_LABEL: Record<string, string> = {
  URGENT: '紧急',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
};

// ---------------------------------------------------------------------------
// 回路重要等级映射（统一 use-loop-changes.ts IMPORTANCE_LEVEL_TAG）
// 1 级=关键(error) / 2 级=重要(warning) / 3 级=一般(neutral)
// ---------------------------------------------------------------------------

export const IMPORTANCE_LEVEL_TO_STATUS: Record<number, StatusToken> = {
  1: 'error',
  2: 'warning',
  3: 'neutral',
};

export const IMPORTANCE_LEVEL_LABEL: Record<number, string> = {
  1: '1 级',
  2: '2 级',
  3: '3 级',
};

// ---------------------------------------------------------------------------
// 诊断紧急程度映射（统一 diagnosis.ts DIAGNOSIS_URGENCY_COLOR）
// high=紧急(error) / medium=一般(warning) / low=低(neutral)
// ---------------------------------------------------------------------------

export const URGENCY_TO_STATUS: Record<string, StatusToken> = {
  high: 'error',
  medium: 'warning',
  low: 'neutral',
};

// ---------------------------------------------------------------------------
// 行动状态映射（统一 preferences.ts ACTION_STATUS_COLOR_MAP，P2-01 收敛至此）
// PENDING=待处理(warning) / IN_PROGRESS=进行中(info) /
// IMPLEMENTED=已完成(ok) / IGNORED=已忽略(neutral)
// ---------------------------------------------------------------------------

export const ACTION_STATUS_TO_STATUS: Record<string, StatusToken> = {
  PENDING: 'warning',
  IN_PROGRESS: 'info',
  IMPLEMENTED: 'ok',
  IGNORED: 'neutral',
};

// ---------------------------------------------------------------------------
// 连接/质量状态映射（统一 loop-live-status-bar.vue 等组件）
// ---------------------------------------------------------------------------

export const CONNECTION_STATUS_TO_STATUS: Record<string, StatusToken> = {
  CONNECTED: 'ok',
  DISCONNECTED: 'error',
  RECONNECTING: 'warning',
  UNKNOWN: 'neutral',
};

export const QUALITY_STATUS_TO_STATUS: Record<string, StatusToken> = {
  GOOD: 'ok',
  BAD: 'error',
  UNCERTAIN: 'warning',
  UNKNOWN: 'neutral',
};

// ---------------------------------------------------------------------------
// 整定状态字典（唯一事实源，2026-09-24 收敛）
//
// 背景：同一状态在 4 处各自维护中文标签且已实质分歧 ——
//   COMPLETED  工作台批次卡「已验证」 / 记录页与批次抽屉「已完成」
//   CANCELLED  记录页「已取消」 / 工作台批次卡「已回退」
//   ROLLED_BACK 记录页「已回退」 / 收益报告「已回滚」
// 工程师与管理者对同一件事表述不一致，且新增状态必然漏改某一份。
// 全站（整定记录、批次列表、批次抽屉、收益报告）统一 import 本字典。
// ---------------------------------------------------------------------------

/** 整定任务/记录状态 → 中文标签（后端 tuning_record.status 全量枚举） */
export const TUNING_TASK_STATUS_LABEL: Record<string, string> = {
  APPLIED: '已实施',
  COMPLETED: '已完成',
  DRAFT: '草稿',
  IDENTIFIED: '已辨识',
  INCONCLUSIVE: '无法判定',
  PENDING: '待实施',
  ROLLED_BACK: '已回退',
  RUNNING: '进行中',
  SIMULATED: '已仿真',
  VERIFIED: '已验证',
};

/** 整定任务/记录状态 → Ant Design Tag color（与标签同源，避免两处打架） */
export const TUNING_TASK_STATUS_COLOR: Record<string, string> = {
  APPLIED: 'cyan',
  COMPLETED: 'success',
  DRAFT: 'default',
  IDENTIFIED: 'processing',
  INCONCLUSIVE: 'default',
  PENDING: 'gold',
  ROLLED_BACK: 'warning',
  RUNNING: 'processing',
  SIMULATED: 'processing',
  VERIFIED: 'success',
};

/** 整定批次状态 → 中文标签（后端 tuning_batch.status） */
export const TUNING_BATCH_STATUS_LABEL: Record<string, string> = {
  BLOCKED: '阻塞',
  CANCELLED: '已取消',
  COMPLETED: '已完成',
  PENDING: '待启动',
  READY: '就绪',
  RUNNING: '执行中',
};

/** 状态中文标签查询（未知状态原样返回，避免显示空白） */
export function tuningTaskStatusLabel(status?: null | string): string {
  if (!status) return '—';
  return TUNING_TASK_STATUS_LABEL[status] ?? status;
}

export function tuningBatchStatusLabel(status?: null | string): string {
  if (!status) return '—';
  return TUNING_BATCH_STATUS_LABEL[status] ?? status;
}

/**
 * 处置闭环率构成分段色（2026-09-25 从 views/reports/handling.vue 上移）。
 *
 * 上移原因：scripts/check-hex-whitelist.mjs 的 hex 棘轮门禁不允许页面内新增
 * 硬编码色值；constants/ 是颜色定义型目录（白名单），语义色集中在此维护。
 */
export const CLOSURE_BREAKDOWN_COLORS: Record<string, string> = {
  notDispatched: '#8c8c8c',
  dispatchedTodo: '#faad14',
  executing: '#1890ff',
  verifying: '#722ed1',
  closed: '#52c41a',
  reopened: '#ff4d4f',
  cancelled: '#bfbfbf',
};

/**
 * 工业深蓝体系（与 styles/industrial-light.css 的 --clpm-industrial-* 同值）。
 * 需要以 JS 值参与计算/图表配置的场景用它，class 场景请用 CSS 变量。
 */
export const CLPM_INDUSTRIAL = {
  navy: '#1F4E79',
  navySoft: '#EBF1F8',
  border: '#E4E7ED',
} as const;

/**
 * 位号级数据质量体检：问题类型标签与语义色（2026-09-26）
 *
 * 与后端 app/services/data_quality_audit.py 的常量口径一致；
 * 色值走 Ant Design 语义色名（不使用 hex，遵守 hex 棘轮门禁）。
 */
export const DQ_AUDIT_ISSUE_LABEL: Record<string, string> = {
  no_data: '断流',
  bad_quality: '质量码坏',
  low_density: '密度不足',
  held: '含 HELD 填平',
};

/** 问题类型 → Ant Design Tag 语义色 */
export const DQ_AUDIT_ISSUE_COLOR: Record<string, string> = {
  no_data: 'error',
  bad_quality: 'warning',
  low_density: 'processing',
  held: 'default',
};

/** C（2026-09-28）：排名空态文案的单一事实源（装置排名 / 单元排名 共用；{subject} 由调用方注入） */
export const RANKING_EMPTY_TEMPLATES: Record<string, string> = {
  NO_ORG_NODES:
    '组织树未配置工厂/装置节点，{subject}不可用（请先在系统管理配置组织）',
  NO_PRECALC_ROWS: '{subject}暂无数据：预计算尚未产出，请稍候或检查预计算任务',
};

/** 按 emptyReason 生成排名空态文案；未命中原因时用 fallback */
export function rankingEmptyText(
  reason: null | string | undefined,
  subject: string,
  fallback?: string,
) {
  const tpl = reason ? RANKING_EMPTY_TEMPLATES[reason] : undefined;
  return tpl
    ? tpl.replace('{subject}', subject)
    : (fallback ?? '暂无' + subject);
}

// ---------------------------------------------------------------------------
// 回路工作台新版（workbench360，2026-10-02 P1 起）
//
// 本节常量供 views/loop/workbench360/** 消费；hex 集中在此
// （constants/ 是 hex 棘轮白名单目录，组件内禁 hex）。
// 色值对齐原型 docs/设计文档/原型/回路工作台-原型-2026-10-02.html。
// ---------------------------------------------------------------------------

/** 趋势系列色（浅色模式，原型 §5.2：PV 蓝 / SP 绿 / OP 紫） */
export const WB360_TREND_PALETTE_LIGHT = {
  pv: '#1677ff',
  sp: '#13a876',
  op: '#7b61ff',
  /** 质量码 BAD 段（灰虚线） */
  qualityBad: '#9aa2ad',
  /** 质量码 UNCERTAIN 段（琥珀点划） */
  qualityUncertain: '#d48806',
  /** MANUAL 背景带边界 */
  manualBand: '#d9363e',
  /** 图表网格/坐标轴（浅） */
  grid: '#eef1f4',
  grid2: '#f6f8fa',
  axis: '#8c93a0',
  /** 质量码垫层（断开主线的底色，浅色=白） */
  underlay: '#ffffff',
  /** OP 右轴文字色 */
  opAxis: '#a89ce0',
} as const;

/** 趋势系列色（深色模式，html.dark 时使用） */
export const WB360_TREND_PALETTE_DARK = {
  pv: '#5b9bff',
  sp: '#43d9a4',
  op: '#9d8cff',
  qualityBad: '#6b7484',
  qualityUncertain: '#e8b34b',
  manualBand: '#f2707a',
  grid: '#272f3d',
  grid2: '#202836',
  axis: '#78839a',
  underlay: '#1f2734',
  opAxis: '#9d92e8',
} as const;

/** 包络带填充色（半透明，密集采样时 PV/OP 的 min/max 带） */
export const WB360_ENVELOPE_FILL = {
  pvLight: 'rgba(22,119,255,.26)',
  pvDark: 'rgba(91,155,255,.30)',
  opLight: 'rgba(123,97,255,.20)',
  opDark: 'rgba(157,140,255,.24)',
} as const;

/** MANUAL 背景带填充（半透明红带） */
export const WB360_MANUAL_BAND_FILL = {
  light: 'rgba(217,54,62,.08)',
  dark: 'rgba(217,54,62,.13)',
} as const;

/** 深色状态栏（浅/深主题均为深色应用式底，原型 #sbar） */
export const WB360_STATUSBAR = {
  bg: '#1c2330',
  divider: 'rgba(255,255,255,.08)',
  errDot: '#d9363e',
  okDot: '#13a876',
  text: '#9aa5b8',
  textStrong: '#e6ebf3',
  warnDot: '#d48806',
  warnText: '#e8b34b',
} as const;

/** 事件标注层徽标色（诊断▼/整定◆/验证▮/手动⏸；P1 仅 MANUAL 投入使用，其余 P2-P4 接数据） */
export const WB360_EVENT_MARK_COLORS = {
  diag: '#d9363e',
  tuning: '#7b61ff',
  verify: '#13a876',
  manual: '#d9363e',
} as const;

/** 窗口九档（D12：1H~7D+自定义；D13：每窗恒 ≈3600 采样点） */
export interface WB360WindowPreset {
  /** 档位 key（custom 为自定义占位档） */
  key: string;
  /** 显示名 */
  label: string;
  /** 窗口跨度（秒）；custom 档无固定跨度 */
  spanSeconds?: number;
  /**
   * 后端 trendWindow 预设（GET /loops/{id}/monitor）。
   * 无预设档（12H/7D）走 waveform 自定义起止（API 契约 §1.2）。
   */
  trendWindow?:
    | 'last_1_hour'
    | 'last_2_hours'
    | 'last_4_hours'
    | 'last_8_hours'
    | 'last_24_hours'
    | 'last_72_hours';
  /** 是否为自定义占位档（正式版做起止选择器） */
  custom?: boolean;
}

export const WB360_WINDOW_PRESETS: WB360WindowPreset[] = [
  { key: '1h', label: '1H', spanSeconds: 3600, trendWindow: 'last_1_hour' },
  { key: '2h', label: '2H', spanSeconds: 7200, trendWindow: 'last_2_hours' },
  { key: '4h', label: '4H', spanSeconds: 14_400, trendWindow: 'last_4_hours' },
  { key: '8h', label: '8H', spanSeconds: 28_800, trendWindow: 'last_8_hours' },
  { key: '12h', label: '12H', spanSeconds: 43_200 },
  {
    key: '24h',
    label: '24H',
    spanSeconds: 86_400,
    trendWindow: 'last_24_hours',
  },
  {
    key: '3d',
    label: '3D',
    spanSeconds: 259_200,
    trendWindow: 'last_72_hours',
  },
  { key: '7d', label: '7D', spanSeconds: 604_800 },
  { key: 'custom', label: '自定义', custom: true },
];

/** 默认窗口档（24H，对齐原型默认选中） */
export const WB360_DEFAULT_WINDOW_KEY = '24h';

/** 绘制恒采样点数（D13 定标：每窗 ≈3600 点） */
export const WB360_SAMPLE_POINTS = 3600;

/** 效果验证窗口 7 档（P4；契约 §1.5：windowHours ∈ 1/2/4/8/24/72/168） */
export const WB360_VERIFY_WINDOW_OPTIONS = [1, 2, 4, 8, 24, 72, 168].map(
  (h) => ({ label: `${h}h`, value: h }),
) as Array<{ label: string; value: number }>;

/** 关注抽屉：来源/优先级中文（P4；文案与 monitor/attention.vue 现行口径一致） */
export const WB360_ATTENTION_SOURCE_LABEL: Record<string, string> = {
  ALERT: '活跃预警',
  DATA_QUALITY: '数据质量',
  DEGRADATION: '评分恶化',
  FITNESS_ABNORMAL: '适用性异常',
  HANDLING: '处置工单',
};

export const WB360_ATTENTION_PRIORITY_LABEL: Record<string, string> = {
  URGENT: '紧急',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
};

/** 四剖面 key 与名称（旅程条/缩略卡/工作区共用；v3 §4） */
export const WB360_SECTIONS = [
  { key: 'assess', label: '性能评估', module: 'assess' },
  { key: 'diag', label: '回路诊断', module: 'diagnosis' },
  { key: 'tuning', label: '参数整定', module: 'tuning' },
  { key: 'handling', label: '问题处置', module: 'handling' },
] as const;

export type WB360SectionKey = (typeof WB360_SECTIONS)[number]['key'];
