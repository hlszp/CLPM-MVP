import { StatusMetric } from '../../components/ui';
import type { RiskLevel } from '../../types';

export function HomeMissionStrip({
  loopCount,
  loopId,
  risk,
  packageStatus,
  nextStep,
}: {
  loopCount: number;
  loopId: string;
  risk: RiskLevel;
  packageStatus: string;
  nextStep: string;
}) {
  return (
    <section className="mission-strip" aria-label="当前治理任务">
      <StatusMetric label="当前样本" value={`${loopCount} 回路`} tone="neutral" />
      <StatusMetric label="优先对象" value={loopId} tone={risk === 'high' ? 'danger' : risk === 'medium' ? 'warning' : 'ok'} />
      <StatusMetric label="证据包" value={packageStatus} tone="warning" />
      <StatusMetric label="下一步" value={nextStep} tone="warning" />
    </section>
  );
}
