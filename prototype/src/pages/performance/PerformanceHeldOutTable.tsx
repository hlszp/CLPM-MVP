import type { LoopRecord } from '../../types';

export function PerformanceHeldOutTable({ loops }: { loops: LoopRecord[] }) {
  return (
    <section className="panel warning-panel">
      <h2>未参与真实排序对象</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>回路</th>
              <th>状态</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {loops.map((loop) => (
              <tr key={loop.id}>
                <th scope="row">{loop.id}</th>
                <td>{loop.status}</td>
                <td>{loop.nextAction}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
