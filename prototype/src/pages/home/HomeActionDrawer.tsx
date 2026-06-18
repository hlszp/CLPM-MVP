import { Link } from 'react-router-dom';
import { ActionList } from '../pageShared';

export function HomeActionDrawer({ currentStatus }: { currentStatus: string }) {
  const impactTitle = currentStatus === '数据不足' ? '当前回路存在数据缺口' : '选择“需补证据”会保持 partial';

  return (
    <aside className="action-drawer" aria-label="动作与状态影响">
      <h2>动作与待办</h2>
      <ActionList />
      <div className="state-machine-mini">
        <span className="active">诊断</span>
        <span>审核</span>
        <span>实施</span>
        <span>复评</span>
        <span>证据包</span>
      </div>
      <div className="impact-note">
        <strong>{impactTitle}</strong>
        <p>实施、复评和 Sponsor 汇报不会被伪装为完成闭环。</p>
      </div>
      <Link className="button secondary" to="/closure/review">
        进入闭环治理
      </Link>
      <Link className="button ghost" to="/samples/readiness">
        进入样本验证
      </Link>
    </aside>
  );
}
