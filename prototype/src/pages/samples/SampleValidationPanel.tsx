import { MetricCard } from '../../components/ui';
import { currentBatch, dataLineage, primaryLoopId, tuningCase, valveCheckLoopId } from '../../data/mockData';
import type { SampleReadinessState } from '../../types';

export function SampleValidationPanel({ readinessState }: { readinessState: SampleReadinessState }) {
  return (
    <>
      <section className="grid four">
        <MetricCard label="批次映射率" value={`${Math.round(currentBatch.mappedRate * 100)}%`} delta="字段已映射" />
        <MetricCard label="批次好值率" value={`${Math.round(currentBatch.goodValueRate * 100)}%`} delta="来自 demo-data" />
        <MetricCard label="评审就绪率" value="94%" delta="缺 MODE 3 条" />
        <MetricCard label="当前状态" value={readinessState} delta="状态由 session 驱动" />
      </section>
      <section className="grid two">
        <section className="panel">
          <h2>质量规则</h2>
          <ul>
            <li>GOOD 进入评价</li>
            <li>BAD/FROZEN 降级为数据不足</li>
            <li>MAN 不进入有效自控强结论</li>
          </ul>
          <p>事件可用性：{dataLineage.eventsFile} 已接入，可用于扰动与边界追溯。</p>
        </section>
        <section className="panel">
          <h2>下一步</h2>
          <p>
            {primaryLoopId}、{valveCheckLoopId}、{tuningCase.loopId} 可进入 P0 主链。
          </p>
          <p>冻结前请确认字段缺口和现场核实项已显性留痕。</p>
        </section>
      </section>
    </>
  );
}
