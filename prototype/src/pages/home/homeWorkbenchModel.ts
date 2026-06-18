import { evidencePackageView, evidenceWindows, loops, primaryLoopId } from '../../data/mockData';

const PRIORITY_STATUSES = ['可诊断', '需现场核实', '可整定', '数据不足'];

export function getPriorityLoops() {
  return loops.filter((loop) => PRIORITY_STATUSES.includes(loop.status)).slice(0, 6);
}

export function getSelectedLoop(loopId: string) {
  return loops.find((loop) => loop.id === loopId) ?? loops.find((loop) => loop.id === primaryLoopId) ?? loops[0];
}

export function getEvidenceWindow(loopId: string) {
  return evidenceWindows.find((item) => item.loopId === loopId) ?? evidenceWindows[0];
}

export function getWorkbenchSummary(loopId: string) {
  const selected = getSelectedLoop(loopId);

  return {
    selected,
    evidence: getEvidenceWindow(selected.id),
    packageStatus: evidencePackageView.packageStatus,
  };
}
