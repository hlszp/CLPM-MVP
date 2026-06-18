import { useAppSession } from '../app/session/AppSessionContext';

export function ContextSummaryBar() {
  const { role, currentSample, currentPackage, workflow } = useAppSession();

  return (
    <div className="context-summary-bar">
      <span>角色：{role}</span>
      <span>样本：{currentSample?.name}</span>
      <span>当前回路：{workflow.selectedLoopId}</span>
      <span>证据包：{currentPackage?.packageStatus}</span>
    </div>
  );
}
