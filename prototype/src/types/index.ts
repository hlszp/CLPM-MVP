export type VersionTag = 'P0' | 'P1' | 'P2' | 'P3' | 'P0/P1' | 'P0/P2' | 'P1/P2' | 'P2/P3';
export type PrototypeDepth = 'deep' | 'sample' | 'basic' | 'structure' | 'vision';
export type UserRole = 'engineer' | 'reviewer' | 'sponsor' | 'implementer' | 'admin';
export type NavigationPageLevel = 'core' | 'supporting' | 'structure';
export type NavigationStage = 'foundation' | 'workflow' | 'reporting' | 'system';
export type LoopStatus = '可评估' | '可诊断' | '可整定' | '需现场核实' | '数据不足' | '不可判定';
export type RiskLevel = 'high' | 'medium' | 'low';
export type StateKind = 'loading' | 'empty' | 'error' | 'success' | 'partial';

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  version: VersionTag;
  depth: PrototypeDepth;
  description: string;
  children?: NavigationItem[];
  parentId?: string;
  pageLevel?: NavigationPageLevel;
  stage?: NavigationStage;
  roles?: UserRole[];
  defaultEntry?: boolean;
  isDeepPage?: boolean;
}

export interface SampleBatch {
  id: string;
  name: string;
  source: string;
  window: string;
  loopCount: number;
  mappedRate: number;
  goodValueRate: number;
  readiness: 'ready' | 'partial' | 'blocked';
  risks: string[];
}

export interface LoopRecord {
  id: string;
  device: string;
  type: string;
  status: LoopStatus;
  risk: RiskLevel;
  score: number;
  autoRate: number;
  smoothRate: number;
  nextAction: string;
}

export interface LoopLedger {
  loopId: string;
  device: string;
  pvMapping: 'mapped' | 'missing' | 'partial';
  spMapping: 'mapped' | 'missing' | 'partial';
  opMapping: 'mapped' | 'missing' | 'partial';
  modeMapping: 'mapped' | 'missing' | 'partial';
  dataAvailability: 'available' | 'partial' | 'blocked';
  versionRefs: string[];
  blockingItems: string[];
}

export interface KpiResult {
  key: string;
  label: string;
  value: string;
  delta: string;
  state: LoopStatus;
}

export interface DiagnosisFinding {
  id: string;
  loopId: string;
  findingType: 'pid_oscillation' | 'valve_instrument' | 'data_condition' | 'manual_mode' | 'disturbance' | 'tuning_candidate';
  severity: 'high' | 'medium' | 'low';
  title: string;
  confidence: number;
  evidence: string;
  evidenceRefs: string[];
  action: string;
  ownerRole: string;
}

export interface EvidenceWindow {
  loopId: string;
  title: string;
  summary: string;
  points: Array<{ time: string; pv: number; sp: number; op: number }>;
  rules: string[];
  events: string[];
}

export interface ReviewRecord {
  id: string;
  loopId: string;
  role: string;
  decision: '通过' | '驳回' | '需补证据';
  note: string;
}

export interface EvidencePackage {
  id: string;
  sampleId: string;
  manifestVersion: string;
  manifestHash: string;
  packageStatus: 'PACKAGE_READY' | 'PACKAGE_PARTIAL' | 'PACKAGE_BLOCKED';
  validityStatus: 'VALID' | 'VALID_WITH_MISSING_REFS' | 'INVALID';
  conclusion: string;
  completeness: number;
  references: string[];
  includedRefs: Array<{ name: string; status: '已纳入' | '缺失' }>;
  missingRefs: string[];
  riskSummary: string[];
}

export interface Reevaluation {
  loopId: string;
  beforeWindow: string;
  afterWindow: string;
  status: 'success' | 'partial' | 'blocked';
  kpis: Array<{ label: string; before: string; after: string; delta: string; status: 'improved' | 'worse' | 'pending' }>;
  missingRefs: string[];
  conclusion: string;
}

export interface TuningCase {
  loopId: string;
  current: string;
  suggested: string;
  confidence: number;
  boundary: string;
}
