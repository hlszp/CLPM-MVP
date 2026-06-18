import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dataDir = resolve(__dirname, '../../demo-data/control-loop-second-level');
const outFile = resolve(__dirname, '../src/data/demoData.generated.ts');

const summary = JSON.parse(readFileSync(resolve(dataDir, 'dataset_summary.json'), 'utf8'));
const metadata = JSON.parse(readFileSync(resolve(dataDir, 'loops_metadata.json'), 'utf8'));
const csv = readFileSync(resolve(dataDir, 'control_loop_second_level_24loops_1h.csv'), 'utf8').trim().split('\n');
const eventsCsv = readFileSync(resolve(dataDir, 'events.csv'), 'utf8').trim().split('\n');

const headers = csv[0].split(',');
const rows = csv.slice(1).map((line) => toRecord(headers, line));
const eventHeaders = eventsCsv[0].split(',');
const events = eventsCsv.slice(1).map((line) => toRecord(eventHeaders, line));

const byLoop = groupBy(rows, 'loop_tag');
const eventsByLoop = groupBy(events, 'loop_tag');
const sampleIntervalSeconds = summary.sample_interval_seconds ?? metadata.sample_interval_seconds ?? 1;
const fields = summary.fields ?? metadata.fields ?? headers;
const scenarios = metadata.scenarios ?? [...new Set(metadata.loops.map((loop) => loop.scenario))];
const scenarioStatus = {
  normal: '可评估',
  oscillation: '可诊断',
  valve_stiction: '需现场核实',
  manual_mode: '不可判定',
  data_quality_issue: '数据不足',
  disturbance: '可诊断',
  tuning_candidate: '可整定',
};
const scenarioRisk = {
  normal: 'low',
  oscillation: 'high',
  valve_stiction: 'medium',
  manual_mode: 'medium',
  data_quality_issue: 'medium',
  disturbance: 'medium',
  tuning_candidate: 'medium',
};
const typeMap = { flow: '流量', level: '液位', temperature: '温度', pressure: '压力', composition: '成分' };

const loops = metadata.loops.map((loop) => {
  const loopRows = byLoop[loop.loop_tag] ?? [];
  const goodRows = loopRows.filter((row) => row.quality === 'GOOD').length;
  const autoRows = loopRows.filter((row) => row.mode === 'AUTO' || row.mode === 'CAS').length;
  const goodRate = loopRows.length ? goodRows / loopRows.length : 0;
  const autoRate = loopRows.length ? autoRows / loopRows.length : 0;
  const status = scenarioStatus[loop.scenario] ?? '可评估';
  const baseScore = Math.round(45 + goodRate * 20 + autoRate * 20);
  return {
    id: loop.loop_tag,
    device: loop.unit_name,
    type: typeMap[loop.control_type] ?? loop.control_type,
    status,
    risk: scenarioRisk[loop.scenario] ?? 'low',
    score: status === '可诊断' ? Math.min(baseScore, 58) : status === '数据不足' ? Math.min(baseScore, 62) : baseScore,
    autoRate: Number(autoRate.toFixed(2)),
    smoothRate: Number(goodRate.toFixed(2)),
    nextAction: nextAction(loop.scenario),
  };
});

const primaryTag = pickScenario(metadata.loops, 'oscillation')?.loop_tag ?? metadata.loops[0].loop_tag;
const tuningTag = pickScenario(metadata.loops, 'tuning_candidate')?.loop_tag ?? metadata.loops[7].loop_tag;
const evidenceScenarios = ['oscillation', 'valve_stiction', 'data_quality_issue'];
const evidenceWindows = evidenceScenarios
  .map((scenario) => pickScenario(metadata.loops, scenario))
  .filter(Boolean)
  .map((loop) => buildEvidenceWindow(loop));

const findings = metadata.loops.filter((loop) => loop.scenario !== 'normal').slice(0, 6).map((loop, index) => ({
  id: `DD-${String(index + 1).padStart(3, '0')}`,
  loopId: loop.loop_tag,
  findingType: findingTypeFor(loop.scenario),
  severity: severityFor(loop.scenario),
  title: titleFor(loop.scenario),
  confidence: confidenceFor(loop.scenario),
  evidence: `来自 demo-data 的 ${loop.scenario} 场景，包含 PV/SP/OP/MODE/quality/event_marker 秒级证据。`,
  evidenceRefs: [`EvidenceWindow:${loop.loop_tag}`, `SampleBatch:${summary.dataset_id}`, 'quality-rule.v0.1'],
  action: nextAction(loop.scenario),
  ownerRole: ownerRoleFor(loop.scenario),
}));

const tuningMeta = metadata.loops.find((loop) => loop.loop_tag === tuningTag);
const qualityGoodRate = rows.filter((row) => row.quality === 'GOOD').length / rows.length;
const autoRows = rows.filter((row) => row.mode === 'AUTO' || row.mode === 'CAS').length;
const autoRate = autoRows / rows.length;
const effectiveAutoRate = loops.filter((loop) => ['可评估', '可诊断', '可整定'].includes(loop.status)).length / loops.length;
const smoothRate = loops.filter((loop) => loop.score >= 70).length / loops.length;
const closureReadyRate = loops.filter((loop) => ['可诊断', '需现场核实', '可整定'].includes(loop.status)).length / loops.length;
const evidenceCompleteness = round((qualityGoodRate * 0.35) + (autoRate * 0.25) + ((events.length / Math.max(loops.length, 1) > 1 ? 1 : 0.75) * 0.2) + (findings.length / Math.max(loops.length, 1) * 0.2));
const kpis = [
  { key: 'auto', label: '样本自控率', value: percent(autoRate), delta: `${summary.loop_count} 回路 / 1h 窗口 / ${sampleIntervalSeconds}s 采样`, state: '可评估' },
  { key: 'effective', label: '有效自控率', value: percent(effectiveAutoRate), delta: `${findings.length} 条诊断样例`, state: '可诊断' },
  { key: 'smooth', label: '平稳率', value: percent(smoothRate), delta: `${loops.filter((loop) => loop.score < 70).length} 条低效`, state: '可评估' },
  { key: 'closure', label: '闭环候选率', value: percent(closureReadyRate), delta: `${loops.filter((loop) => loop.status === '需现场核实').length} 条需现场核实`, state: '需现场核实' },
];
const evidencePackage = {
  id: `EP-${summary.dataset_id}`,
  sampleId: summary.dataset_id,
  manifestVersion: 'manifest.demo-data.v0.1',
  manifestHash: 'sha256:demo-7f4c9c1b8e2a',
  packageStatus: 'PACKAGE_PARTIAL',
  validityStatus: 'VALID_WITH_MISSING_REFS',
  conclusion: 'demo-data 秒级样本可支撑从回路绩效、诊断证据到闭环治理的原型演示，但不代表真实算法有效性。',
  completeness: evidenceCompleteness,
  references: ['SampleBatch', 'LoopLedger', 'KpiResult', 'DiagnosisFinding', 'ReviewRecord', 'EvidenceWindow', 'InstrumentCheckRecord', 'PostImplementationObservation', 'events.csv'],
  includedRefs: ['SampleBatch', 'LoopLedger', 'KpiResult', 'DiagnosisFinding', 'ReviewRecord', 'EvidenceWindow', 'InstrumentCheckRecord', 'PostImplementationObservation', 'events.csv'].map((name) => ({
    name,
    status: ['InstrumentCheckRecord', 'PostImplementationObservation'].includes(name) ? '缺失' : '已纳入',
  })),
  missingRefs: ['InstrumentCheckRecord', 'PostImplementationObservation'],
  riskSummary: ['阀门粘滞样例需现场核实', '24 回路为开发 smoke 数据，P0 评审需保留样本规模口径', '闭环审核存在需补证据项，当前包为 partial'],
};
const demoDataLineage = {
  datasetId: summary.dataset_id,
  csvFile: summary.csv_file,
  metadataFile: summary.metadata_file,
  eventsFile: summary.events_file,
  sampleWindow: `${summary.sample_start_time} — ${summary.sample_end_time}`,
  sampleIntervalSeconds,
  rowCount: summary.row_count,
  loopCount: summary.loop_count,
  fields,
  scenarios,
  scenarioSummary: summary.scenario_summary,
  safetyBoundary: metadata.safety_boundary ?? 'Demo data only. No DCS read/write.',
};
const generated = `// Auto-generated by prototype/scripts/import-demo-data.mjs. Do not edit by hand.\nimport type { DiagnosisFinding, EvidencePackage, EvidenceWindow, KpiResult, LoopRecord, SampleBatch, TuningCase } from '../types';\n\nexport const demoSampleBatch: SampleBatch = ${JSON.stringify({
  id: summary.dataset_id,
  name: 'demo-data 秒级控制回路样本',
  source: `CSV:${summary.csv_file} + Metadata:${summary.metadata_file} + Events:${summary.events_file}`,
  window: `${summary.sample_start_time} — ${summary.sample_end_time}`,
  loopCount: summary.loop_count,
  mappedRate: 1,
  goodValueRate: Number((rows.filter((row) => row.quality === 'GOOD').length / rows.length).toFixed(2)),
  readiness: 'partial',
  risks: [
    `manual_mode ${summary.scenario_summary.manual_mode} 条`,
    `data_quality_issue ${summary.scenario_summary.data_quality_issue} 条`,
    `valve_stiction ${summary.scenario_summary.valve_stiction} 条需现场核实`,
  ],
}, null, 2)};\n\nexport const demoDataLineage = ${JSON.stringify(demoDataLineage, null, 2)};\n\nexport const demoKpis: KpiResult[] = ${JSON.stringify(kpis, null, 2)};\n\nexport const demoEvidencePackage: EvidencePackage = ${JSON.stringify(evidencePackage, null, 2)};\n\nexport const demoLoops: LoopRecord[] = ${JSON.stringify(loops, null, 2)};\n\nexport const demoEvidenceWindows: EvidenceWindow[] = ${JSON.stringify(evidenceWindows, null, 2)};\n\nexport const demoFindings: DiagnosisFinding[] = ${JSON.stringify(findings, null, 2)};\n\nexport const demoTuningCase: TuningCase = ${JSON.stringify({
  loopId: tuningTag,
  current: `Kp=${tuningMeta?.p}, Ti=${tuningMeta?.i}s, Td=${tuningMeta?.d}s`,
  suggested: `Kp=${round((tuningMeta?.p ?? 1) * 0.82)}, Ti=${Math.round((tuningMeta?.i ?? 100) * 0.9)}s, Td=${tuningMeta?.d ?? 0}s`,
  confidence: 0.76,
  boundary: '来自 demo-data tuning_candidate 场景；仅用于 P0 单条可信整定样例，不代表批量整定，平台不写 DCS。',
}, null, 2)};\n`;

writeFileSync(outFile, generated);

function toRecord(headers, line) {
  const cols = line.split(',');
  return Object.fromEntries(headers.map((header, i) => [header, cols[i] ?? '']));
}
function groupBy(items, key) {
  return items.reduce((acc, item) => ((acc[item[key]] ??= []).push(item), acc), {});
}
function sampleRows(items, count) {
  if (items.length <= count) return items;
  const step = Math.floor((items.length - 1) / (count - 1));
  return Array.from({ length: count }, (_, i) => items[Math.min(i * step, items.length - 1)]);
}
function pickScenario(loops, scenario) {
  return loops.find((loop) => loop.scenario === scenario);
}
function buildEvidenceWindow(loop) {
  const loopRows = sampleRows(byLoop[loop.loop_tag] ?? [], 6);
  return {
    loopId: loop.loop_tag,
    title: `${loop.loop_tag} 单回路证据链`,
    summary: `来自秒级 demo-data：${loop.scenario} 场景，采样间隔 ${sampleIntervalSeconds}s，证据窗口 1 小时。`,
    points: loopRows.map((row) => ({ time: row.timestamp.slice(11, 19), pv: Number(row.pv), sp: Number(row.sp), op: Number(row.op) })),
    rules: rulesFor(loop.scenario),
    events: (eventsByLoop[loop.loop_tag] ?? []).slice(0, 8).map((event) => `${event.timestamp.slice(11, 19)} ${event.event_marker} · ${event.mode}/${event.quality}`),
  };
}
function nextAction(scenario) {
  return {
    normal: '保持观察',
    oscillation: '进入证据链并提交审核',
    valve_stiction: '现场核实阀门后复评',
    manual_mode: '确认手动原因与投用定义',
    data_quality_issue: '补齐质量码和历史窗口',
    disturbance: '标记扰动并复核评价窗口',
    tuning_candidate: '查看整定样例',
  }[scenario] ?? '保持观察';
}
function titleFor(scenario) {
  return {
    oscillation: '振荡证据充分',
    valve_stiction: '疑似阀门粘滞',
    manual_mode: '长时间手动模式',
    data_quality_issue: '数据质量片段异常',
    disturbance: '工艺扰动影响控制表现',
    tuning_candidate: '可整定样例候选',
  }[scenario] ?? '诊断样例';
}
function rulesFor(scenario) {
  return {
    oscillation: ['PV 围绕 SP 周期振荡', 'OP 同频动作明显', 'AUTO 状态下控制未收敛'],
    valve_stiction: ['OP 阶梯化变化', 'PV 响应滞后', '需现场核实阀位反馈'],
    disturbance: ['扰动事件后 PV 偏差扩大', '恢复窗口可见', '需剔除特殊时段再评价'],
  }[scenario] ?? ['PV/SP/OP 字段完整', 'MODE 与 quality 可用于状态判定', '事件线可追溯'];
}
function confidenceFor(scenario) {
  return { oscillation: 0.86, valve_stiction: 0.74, manual_mode: 0.68, data_quality_issue: 0.7, disturbance: 0.73, tuning_candidate: 0.78 }[scenario] ?? 0.65;
}
function findingTypeFor(scenario) {
  return {
    oscillation: 'pid_oscillation',
    valve_stiction: 'valve_instrument',
    data_quality_issue: 'data_condition',
    manual_mode: 'manual_mode',
    disturbance: 'disturbance',
    tuning_candidate: 'tuning_candidate',
  }[scenario] ?? 'data_condition';
}
function severityFor(scenario) {
  return { oscillation: 'high', valve_stiction: 'medium', manual_mode: 'medium', data_quality_issue: 'medium', disturbance: 'medium', tuning_candidate: 'low' }[scenario] ?? 'medium';
}
function ownerRoleFor(scenario) {
  return { oscillation: '控制工程师', valve_stiction: '仪表工程师', manual_mode: '工艺工程师', data_quality_issue: '数据治理', disturbance: '工艺工程师', tuning_candidate: '授权仪表工程师' }[scenario] ?? '控制工程师';
}
function round(n) { return Math.round(n * 100) / 100; }
function percent(n) { return `${Math.round(n * 1000) / 10}%`; }
