/**
 * Mock 数据：诊断结果（DDS diagnosis_results）
 *
 * 诊断预诊标签（UI/UX §7.2.5）：
 * 振荡 / 粘滞阀 / 参数过激 / 参数过保守 / 外扰频繁 / PV 质量异常 / 人工复核
 */

import type { DiagnosisResult, DiagnosisLabel } from './types';

/** 根据回路评分生成诊断结果（低分回路有诊断） */
function makeDiagnosis(
  loopId: string,
  loopName: string,
  nodeName: string,
  label: DiagnosisLabel,
  confidence: number,
  detail: string,
  suggestion: string,
  hasTracker: boolean,
): DiagnosisResult {
  return {
    resultId: `D-${loopId}`,
    loopId,
    loopName,
    nodeName,
    diagnosisTime: '2026-06-21 09:30:00',
    label,
    confidence,
    detail,
    suggestion,
    hasTracker,
  };
}

export const diagnosisResults: DiagnosisResult[] = [
  makeDiagnosis('L003', 'F-101 加热炉出口温度', '反应系统', '振荡', 0.92,
    'PV 时序存在周期约 120s 的持续振荡，FFT 频谱在 0.0083Hz 处有明显峰值。振荡幅值 ±3.5°C，超出允许波动带 ±2°C。',
    '建议降低 PID_P 至 0.8，增加 PID_I 至 45s，或检查阀门是否存在粘滞。',
    true),
  makeDiagnosis('L004', 'C-101 塔顶压力', '反应系统', 'PV 质量异常', 0.98,
    'PV tag 质量码持续为 Bad 超过 2 小时，数据不可信。KPI 计算无法执行，回路处于盲控状态。',
    '请检查 AAS Tag 同步状态与现场变送器，恢复 PV 数据质量后重新触发评估。',
    true),
  makeDiagnosis('L009', 'R-201 反应器床层温度', '反应系统', '粘滞阀', 0.85,
    'PV-OP 散点图呈现典型粘滞特征：OP 持续变化但 PV 响应迟滞，拟合曲线 R²=0.42。阀门存在静摩擦导致的粘滞现象。',
    '建议安排阀门检修，或临时增加 PID_I 至 60s 缓解粘滞影响。',
    true),
  makeDiagnosis('L011', 'C-201 塔顶压力', '反应系统', 'PV 质量异常', 0.75,
    'PV tag 质量码间歇性为 Uncertain，近 24 小时内累计 3.2 小时数据不确定。KPI 计算结果标记为 PARTIAL。',
    '请检查变送器信号稳定性与通信链路，必要时更换变送器。',
    false),
  makeDiagnosis('L014', 'C-202 回流量', '分馏系统', '参数过激', 0.88,
    '设定值阶跃响应测试显示 PV 超调量达 18%，首次穿越时间 12s，衰减比 1:1.8。PID 参数偏激进，鲁棒性不足。',
    '建议降低 PID_P 至 0.6，增加 PID_I 至 50s，目标超调量 <10%。',
    true),
  makeDiagnosis('L007', 'C-102 回流量', '分馏系统', '参数过保守', 0.78,
    '设定值阶跃响应缓慢，PV 达到 ±2% 带的时间长达 85s，IAE 偏高。PID 参数过于保守，响应性不足。',
    '建议增加 PID_P 至 1.5，降低 PID_I 至 25s，提升响应速度。',
    false),
  makeDiagnosis('L002', 'R-101 反应器床层温度', '反应系统', '外扰频繁', 0.65,
    '近 7 天 PV 波动率持续偏高，但未检测到明显振荡或粘滞特征。疑似存在未识别的外部扰动源。',
    '建议人工复核扰动来源，检查上游工艺参数变化。',
    false),
];

/** 按 loopId 查询诊断结果 */
export function findDiagnosisByLoop(loopId: string): DiagnosisResult | undefined {
  return diagnosisResults.find((d) => d.loopId === loopId);
}

/** 按预诊标签分组统计 */
export function getDiagnosisStatsByLabel(): Array<{ label: DiagnosisLabel; count: number }> {
  const map = new Map<DiagnosisLabel, number>();
  diagnosisResults.forEach((d) => {
    map.set(d.label, (map.get(d.label) ?? 0) + 1);
  });
  return Array.from(map.entries()).map(([label, count]) => ({ label, count }));
}

/** 未处理的诊断（无 Tracker 或 Tracker 待处理） */
export function getPendingDiagnoses(): DiagnosisResult[] {
  return diagnosisResults.filter((d) => !d.hasTracker);
}
