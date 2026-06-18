import { currentBatch, evidencePackageView, valveCheckLoopId } from '../../data/mockData';
import type { SampleReadinessState } from '../../types';

export function SampleFreezePanel({
  readinessState,
  isFrozen,
  onFreeze,
}: {
  readinessState: SampleReadinessState;
  isFrozen: boolean;
  onFreeze: () => void;
}) {
  return (
    <section className={`panel ${isFrozen ? '' : 'warning-panel'}`}>
      <h2>冻结样本</h2>
      <p>当前状态：{readinessState}</p>
      <ul>
        <li>样本窗口固定：{currentBatch.window}</li>
        <li>证据包状态：{evidencePackageView.status}</li>
        <li>现场核实项：{valveCheckLoopId}</li>
      </ul>
      {isFrozen ? (
        <p>样本已冻结，字段映射只读。</p>
      ) : (
        <button type="button" className="button" onClick={onFreeze}>
          冻结样本
        </button>
      )}
    </section>
  );
}
