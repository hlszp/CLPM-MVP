import { useAppSession } from '../../app/session/AppSessionContext';
import { currentBatch } from '../../data/mockData';
import { HomeActionDrawer } from './HomeActionDrawer';
import { HomeEvidenceWorkspace } from './HomeEvidenceWorkspace';
import { HomeMissionStrip } from './HomeMissionStrip';
import { HomePriorityQueue } from './HomePriorityQueue';
import { getPriorityLoops, getWorkbenchSummary } from './homeWorkbenchModel';

export function HomeWorkbench() {
  const { currentLoopId, selectLoop } = useAppSession();
  const priorityLoops = getPriorityLoops();
  const { selected, evidence, packageStatus } = getWorkbenchSummary(currentLoopId);

  return (
    <>
      <HomeMissionStrip
        loopCount={currentBatch.loopCount}
        loopId={selected.id}
        risk={selected.risk}
        packageStatus={packageStatus}
        nextStep={selected.status === '需现场核实' ? '现场核实' : '提交审核'}
      />
      <section className="workspace-layout home-workbench-layout">
        <HomePriorityQueue loops={priorityLoops} selectedId={selected.id} onSelect={selectLoop} />
        <HomeEvidenceWorkspace selected={selected} evidence={evidence} />
        <HomeActionDrawer currentStatus={selected.status} />
      </section>
    </>
  );
}
