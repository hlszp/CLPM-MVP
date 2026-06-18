import type { LoopLedger, MappingFieldStatus, Reevaluation, ReviewRecord } from '../types';
import { demoDataLineage, demoEvidencePackage, demoEvidenceWindows, demoFindings, demoKpis, demoLoops, demoSampleBatch, demoTuningCase } from './demoData.generated';

export const currentBatch = demoSampleBatch;

export const sampleScaleNote = '当前 24 回路为开发 smoke 数据，用于验证主链与契约表达；P0 评审目标样本规模仍按 50-100/72 回路口径保留，扩样后需重新生成 EvidencePackage。';

export const loops = demoLoops;

export const findings = demoFindings;

export const loopLedgers: LoopLedger[] = demoLoops.map((loop) => {
  const isDataLimited = loop.status === '数据不足';
  const needsFieldCheck = ['需现场核实', '不可判定'].includes(loop.status);
  return {
    loopId: loop.id,
    device: loop.device,
    pvMapping: 'mapped',
    spMapping: 'mapped',
    opMapping: 'mapped',
    modeMapping: needsFieldCheck ? 'partial' : 'mapped',
    dataAvailability: isDataLimited || needsFieldCheck ? 'partial' : 'available',
    versionRefs: ['ledger.freeze.v0.1', 'mapping.demo-data.v0.1', 'quality-rule.v0.1', 'mode-mapping.v0.1'],
    blockingItems: isDataLimited ? ['质量码片段需补齐'] : needsFieldCheck ? ['现场阀位或 MODE 定义需确认'] : [],
  };
});

export const evidenceWindows = demoEvidenceWindows;

export const primaryLoopId = evidenceWindows[0]?.loopId ?? loops.find((loop) => loop.status === '可诊断')?.id ?? loops[0]?.id ?? 'TIC-1115';
export const valveCheckLoopId = loops.find((loop) => loop.status === '需现场核实')?.id ?? primaryLoopId;

export const kpis = demoKpis;

export const reviews: ReviewRecord[] = [
  { id: 'R-001', loopId: primaryLoopId, role: '工艺', decision: '通过', note: '认可低效判断，建议小窗口实施。' },
  { id: 'R-002', loopId: primaryLoopId, role: '仪表', decision: '需补证据', note: '需补一次阀门行程核实记录。' },
  { id: 'R-003', loopId: valveCheckLoopId, role: '安全', decision: '通过', note: '回退条件明确，可进入观察。' },
];

export const evidencePackage = demoEvidencePackage;

export const dataLineage = demoDataLineage;

export const tuningCase = demoTuningCase;

export const ledgerMappings: MappingFieldStatus[] = [
  { source: 'pv', target: 'LoopLedger.pvTag', coverage: '100%', status: '已映射', note: '过程量进入趋势、偏差与质量校核' },
  { source: 'sp', target: 'LoopLedger.spTag', coverage: '100%', status: '已映射', note: '设定值用于偏差与振荡判断' },
  { source: 'op', target: 'LoopLedger.opTag', coverage: '100%', status: '已映射', note: '输出量用于动作频繁、阀门粘滞证据' },
  { source: 'mode', target: 'LoopLedger.modeTag', coverage: '91%', status: '缺失需确认', note: '缺 MODE 不进入有效自控率强结论' },
  { source: 'quality', target: 'LoopLedger.qualityRule', coverage: '91%', status: '部分可用', note: 'BAD/FROZEN 触发数据不足或排除规则' },
];

export const sampleImportMethods = [
  { id: 'historian', label: 'Historian 导出', detail: '适合离线导出后导入样本窗口。', availability: 'ready' },
  { id: 'csv', label: 'CSV 模拟数据', detail: '当前 demo-data 已接入并可用于工作流演示。', availability: 'active' },
  { id: 'opc', label: 'OPC 只读连接', detail: '只读接入，不写 DCS，不改变现场参数。', availability: 'ready' },
] as const;

export const sampleMappingMatrix = ledgerMappings;

export const mappingGaps = [
  { field: 'MODE', scope: 'manual_mode 场景', action: '工艺确认手动原因与投用定义' },
  { field: 'quality', scope: 'data_quality_issue 场景', action: '数据治理补质量码连续性' },
  { field: 'valve_position', scope: valveCheckLoopId, action: '仪表补现场阀位反馈' },
];

export const ledgerVerificationItems = [
  { name: '字段完整性', result: '通过', detail: `${dataLineage.fields.length} 个关键字段可读，采样间隔 ${dataLineage.sampleIntervalSeconds}s` },
  { name: '人工修正记录', result: '待确认', detail: 'MODE 定义、阀位反馈和质量码连续性需人工签字后冻结' },
  { name: '冻结前阻断项', result: '需补证据', detail: 'InstrumentCheckRecord 与 PostImplementationObservation 缺失时不能关闭归档' },
  { name: '现场核实项', result: '需补证据', detail: `${valveCheckLoopId} 等阀门粘滞样例需补阀位反馈` },
];

export const ledgerExclusions = [
  { loopId: 'manual_mode', reason: '长时间 MAN 模式', window: '2026-06-16 08:00—09:00', approval: '待工艺确认', impact: '不计入有效自控率', owner: '工艺确认' },
  { loopId: 'data_quality_issue', reason: 'BAD/FROZEN 质量片段', window: '2026-06-16 08:00—09:00', approval: '待数据治理确认', impact: '不进入诊断结论', owner: '数据治理' },
  { loopId: valveCheckLoopId, reason: '疑似阀门粘滞', window: '2026-06-16 08:00—09:00', approval: '待仪表确认', impact: '需现场核实后才能整定', owner: '仪表' },
];

export const ledgerVersions = [
  { version: 'ledger.freeze.v0.1', type: 'ledger', time: '2026-06-16 09:20', change: '冻结字段映射、状态口径和排除规则，允许 P0 演示复现' },
  { version: 'mapping.demo-data.v0.1', type: 'mapping', time: '2026-06-16 09:22', change: 'PV/SP/OP/MODE/quality 映射矩阵通过基础校核' },
  { version: 'formula.kpi.v0.1', type: 'formula', time: '2026-06-16 09:24', change: '自控率、有效自控率、平稳率和闭环候选率公式固定' },
  { version: 'threshold.demo.v0.1', type: 'threshold', time: '2026-06-16 09:26', change: '低效评分与风险阈值用于原型评审' },
  { version: 'quality-rule.v0.1', type: 'quality rule', time: '2026-06-16 09:28', change: 'BAD/FROZEN 与缺失字段触发 partial 或数据不足' },
  { version: 'mode-mapping.v0.1', type: 'mode mapping', time: '2026-06-16 09:30', change: 'AUTO/CAS 计入自控，MAN 不进入有效自控强结论' },
  { version: 'evidence.partial.v0.1', type: 'rule version', time: '2026-06-16 09:40', change: '仪表角色要求补证据，证据包状态降级为 partial' },
];

export const closureState: { state: 'success' | 'partial'; blocker: ReviewRecord | undefined; missingRefs: string[] } = {
  state: reviews.some((review) => review.decision === '需补证据') ? 'partial' : 'success',
  blocker: reviews.find((review) => review.decision === '需补证据'),
  missingRefs: ['InstrumentCheckRecord', 'PostImplementationObservation'],
};

export const evidencePackageView = {
  ...evidencePackage,
  status: closureState.state,
  generatedAt: '2026-06-16 09:40 +08:00',
  includedRefs: evidencePackage.includedRefs,
  missingRefs: evidencePackage.missingRefs,
  unprovenItems: ['不能证明所有低效均来自 PID 参数', '不能证明批量整定可自动实施', '不能在补证据前关闭复评归档'],
  riskSummary: [`${valveCheckLoopId} 需现场核实`, ...evidencePackage.riskSummary.filter((risk) => !risk.includes('阀门粘滞样例'))],
};

export const reevaluation: Reevaluation = {
  loopId: primaryLoopId,
  beforeWindow: '2026-06-16 08:00—09:00',
  afterWindow: '2026-06-18 10:00—11:00（待现场观察记录）',
  status: closureState.state,
  kpis: [
    { label: '有效自控率', before: '62.5%', after: '72.6%', delta: '+10.1pp（待确认）', status: 'pending' },
    { label: '平稳率', before: '62.5%', after: '78.1%', delta: '+15.6pp（待确认）', status: 'pending' },
    { label: '报警次数', before: '5', after: '3', delta: '-2（需复核）', status: 'pending' },
    { label: '操作频次', before: '18', after: '12', delta: '-6（需复核）', status: 'pending' },
  ],
  missingRefs: closureState.missingRefs,
  conclusion: '缺失现场核实与观察记录时仅能显示 partial 复评，不能关闭归档。',
};

export const tuningCaseView = {
  ...tuningCase,
  risk: '仅 P0 单条样例；建议参数必须经授权人员复核，不能自动扩展到批量回路。',
  fallback: '保留原始参数快照；若 PV 偏差或报警超过回退条件，由授权人员人工恢复。',
  ownerRole: '授权仪表工程师',
};
