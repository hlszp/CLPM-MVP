import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import { LoopCardList, LoopTable, MetricCard, StatusBadge, StatusMetric } from '../components/ui';
import { TrendChart } from '../components/TrendChart';
import { currentBatch, dataLineage, evidencePackageView, evidenceWindows, kpis, ledgerVersions, loops, primaryLoopId, sampleScaleNote, valveCheckLoopId } from '../data/mockData';
import { deliveryVerification } from '../data/deliveryStatus';
import type { NavigationItem } from '../types';
import { ActionList, PageHeader } from './pageShared';

export function HomePage({ route }: { route: NavigationItem }) {
  const priorityLoops = loops.filter((loop) => ['可诊断', '需现场核实', '可整定', '数据不足'].includes(loop.status)).slice(0, 6);
  const [selectedId, setSelectedId] = useState(primaryLoopId);
  const selected = loops.find((loop) => loop.id === selectedId) ?? loops.find((loop) => loop.id === primaryLoopId) ?? loops[0];
  const evidence = evidenceWindows.find((item) => item.loopId === selected.id) ?? evidenceWindows[0];
  return (
    <>
      <PageHeader route={route} />
      <section className="mission-strip" aria-label="当前治理任务">
        <StatusMetric label="当前样本" value={`${currentBatch.loopCount} 回路`} tone="neutral" />
        <StatusMetric label="优先对象" value={selected.id} tone={selected.risk === 'high' ? 'danger' : selected.risk === 'medium' ? 'warning' : 'ok'} />
        <StatusMetric label="证据包" value={evidencePackageView.packageStatus} tone="warning" />
        <StatusMetric label="下一步" value={selected.status === '需现场核实' ? '现场核实' : '提交审核'} tone="warning" />
      </section>
      <section className="workspace-layout">
        <section className="panel task-queue" aria-label="低性能优先级清单">
          <div className="section-heading">
            <div>
              <h2>今日优先处理队列</h2>
              <p>选中回路后，证据与动作在同屏更新，不打断工程师判断。</p>
            </div>
            <Link className="text-link" to="/performance/ranking">完整排行</Link>
          </div>
          <LoopCardList loops={priorityLoops} selectedId={selected.id} onSelect={(loop) => setSelectedId(loop.id)} />
        </section>
        <section className="panel evidence-workspace" aria-label="选中回路证据工作区">
          <div className="object-header">
            <div>
              <span className="eyebrow">当前回路</span>
              <h2>{selected.id} 证据摘要</h2>
            </div>
            <div className="object-badges">
              <StatusBadge value={selected.status} />
              <span className={`risk-badge risk-${selected.risk}`}>评分 {selected.score}</span>
            </div>
          </div>
          {evidence ? <TrendChart evidence={evidence} /> : <p>当前回路暂无趋势证据，不会伪装为完整证据链。</p>}
          <div className="evidence-rules">
            {(evidence?.rules ?? ['当前回路暂无规则命中']).map((rule) => <span key={rule}>✓ {rule}</span>)}
          </div>
          <Link className="button" to={`/diagnosis/loop/${selected.id}`}>进入证据链 <ArrowRight size={16}/></Link>
        </section>
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
            <strong>选择“需补证据”会保持 partial</strong>
            <p>实施、复评和 Sponsor 汇报不会被伪装为完成闭环。</p>
          </div>
          <Link className="button secondary" to="/closure/review">进入闭环治理</Link>
          <Link className="button ghost" to="/samples/readiness">进入样本验证</Link>
        </aside>
      </section>
    </>
  );
}

export function RiskOverviewPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid three"><article className="panel warning-panel"><h2>数据不足</h2><p>3 个回路缺 MODE 或有效历史窗口，当前只展示可判定部分。</p><Link className="button ghost" to="/samples/readiness">查看缺失项</Link></article><article className="panel"><h2>需现场核实</h2><p>{valveCheckLoopId} 阀位反馈需现场确认，不能直接归因为 PID 参数问题。</p><Link className="button ghost" to="/samples/radar">进入数据雷达</Link></article><article className="panel"><h2>结果过期</h2><p>暂无过期结果；若样本版本变化，EvidencePackage 将标记需重算。</p><Link className="button ghost" to="/evidence">查看证据包</Link></article></section><section className="panel"><h2>不可证明事项</h2><p>当前不能证明所有低效均来自控制器参数，也不能证明批量整定可自动实施；这些事项必须在 Sponsor 视图中显式保留。</p></section></>; }

export function TodoPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid two"><section className="panel"><h2>待办队列</h2><ol className="task-list"><li><strong>审核 {primaryLoopId} 建议</strong><span>负责人：工艺 / 仪表 / 安全 · 截止：2026-06-17</span></li><li><strong>补齐 {valveCheckLoopId} 现场核实</strong><span>负责人：仪表 · 截止：2026-06-18</span></li><li><strong>复评上一轮实施记录</strong><span>负责人：控制工程师 · 截止：2026-06-20</span></li></ol></section><section className="panel"><h2>下一步动作</h2><p>优先完成 {primaryLoopId} 审核，再进入人工实施记录；缺证据任务不会阻塞其他可判定回路。</p><Link className="button" to="/closure/review">进入建议审核</Link></section></section></>; }

export function PerformancePage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="grid four">{kpis.map((kpi) => <MetricCard key={kpi.key} label={kpi.label} value={kpi.value} delta={kpi.delta}/>)}</section><section className="panel"><h2>相关低性能回路</h2><LoopTable loops={loops.slice(0,3)}/><Link className="button" to="/performance/ranking">查看排行</Link><Link className="button ghost" to="/performance/lineage">查看溯源</Link></section></>; }
export function RankingPage({ route }: { route: NavigationItem }) { const navigate = useNavigate(); const rankedLoops = loops.filter((loop) => ['可评估', '可诊断', '可整定', '需现场核实'].includes(loop.status)).sort((a,b)=>a.score-b.score); const heldOutLoops = loops.filter((loop) => ['数据不足', '不可判定'].includes(loop.status)); return <><PageHeader route={route} /><section className="panel"><h2>低效排行</h2><p>仅对可评估、可诊断、可整定、需现场核实对象排序；数据不足与不可判定不会被当作真实 0 分。</p><LoopTable loops={rankedLoops} onSelect={(loop) => navigate(`/diagnosis/loop/${loop.id}`)}/></section><section className="panel warning-panel"><h2>未参与真实排序对象</h2><div className="table-wrap"><table><thead><tr><th>回路</th><th>状态</th><th>原因</th></tr></thead><tbody>{heldOutLoops.map((loop) => <tr key={loop.id}><th scope="row">{loop.id}</th><td>{loop.status}</td><td>{loop.nextAction}</td></tr>)}</tbody></table></div></section></>; }
export function LineagePage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="panel"><h2>KPI 口径来源</h2><dl className="detail-list"><div><dt>样本自控率</dt><dd>AUTO/CAS 行数 ÷ 总行数 = {kpis[0]?.value}</dd></div><div><dt>有效自控率</dt><dd>可评估 / 可诊断 / 可整定回路占比 = {kpis[1]?.value}</dd></div><div><dt>平稳率</dt><dd>评分 ≥ 70 的回路占比 = {kpis[2]?.value}</dd></div><div><dt>闭环候选率</dt><dd>可诊断 / 需现场核实 / 可整定回路占比 = {kpis[3]?.value}</dd></div></dl><p>数据集来源：{dataLineage.datasetId}，采样窗口 {dataLineage.sampleWindow}，采样间隔 {dataLineage.sampleIntervalSeconds}s。</p></section><section className="panel"><h2>公式、阈值与版本引用</h2><div className="table-wrap"><table><thead><tr><th>版本</th><th>类型</th><th>用途</th></tr></thead><tbody>{ledgerVersions.filter((item) => ['formula', 'threshold', 'quality rule', 'mode mapping'].includes(item.type)).map((item) => <tr key={item.version}><th scope="row">{item.version}</th><td>{item.type}</td><td>{item.change}</td></tr>)}</tbody></table></div><p>输入数据：PV/SP/OP/MODE/quality/event_marker；窗口：{dataLineage.sampleWindow}。</p></section></>; }
export function TrendPage({ route }: { route: NavigationItem }) { const windows = evidenceWindows.slice(0, 3); return <><PageHeader route={route} state="success"/><section className="panel"><h2>趋势分析工作台</h2><p>按 demo-data 秒级样本展示 PV/SP/OP/MODE 关键窗口，只用于识别证据，不自动写 DCS 或生成强整定结论。</p><div className="table-wrap"><table><thead><tr><th>回路</th><th>趋势摘要</th><th>诊断边界</th><th>下一步</th></tr></thead><tbody>{windows.map((window) => <tr key={window.loopId}><th scope="row">{window.loopId}</th><td>{window.summary}</td><td>{window.rules.join('；')}</td><td><Link to={`/diagnosis/loop/${window.loopId}`}>查看证据链</Link></td></tr>)}</tbody></table></div></section><section className="grid three"><article className="panel"><h2>PV/SP</h2><p>用于确认偏差是否长期存在，不能单独证明控制器参数错误。</p></article><article className="panel"><h2>OP 动作</h2><p>用于识别频繁动作、饱和或疑似阀门问题，需结合质量码和事件线。</p></article><article className="panel warning-panel"><h2>安全边界</h2><p>趋势分析只读、可追溯、可审计；现场实施必须经过人工审核和回退方案。</p></article></section></>; }
export function SponsorPage({ route }: { route: NavigationItem }) {
  const evidenceCoverage = [
    ['样本可信', '已覆盖', `${Math.round(currentBatch.mappedRate * 100)}% 映射，${Math.round(currentBatch.goodValueRate * 100)}% 好值率`],
    ['三类诊断', '已覆盖', 'PID 疑似 / 阀门仪表 / 数据工况问题'],
    ['闭环治理', 'partial', '仪表角色需补现场核实，不能关闭归档'],
    ['交付验收', '可评审', 'build + smoke 有记录，保留样本规模边界'],
  ];
  return (
    <>
      <PageHeader route={{ ...route, label: 'Sponsor 证据视图', description: '判断本轮样本是否可信、是否可汇报、哪些事项不能证明' }} state="success" />
      <section className="sponsor-hero" aria-label="Sponsor 结论">
        <div>
          <span className="eyebrow">Sponsor verdict</span>
          <h2>可以继续评审，但不能伪装为完整闭环验收</h2>
          <p>{primaryLoopId} 证明从 demo-data 秒级低效识别、证据解释、审核实施到复评的闭环链路可走通。</p>
        </div>
        <div className="sponsor-score">
          <strong>83%</strong>
          <span>证据完整度</span>
          <em>{evidencePackageView.packageStatus}</em>
        </div>
      </section>
      <section className="grid two">
        <section className="panel">
          <h2>证据覆盖矩阵</h2>
          <div className="coverage-list">
            {evidenceCoverage.map(([name, status, detail]) => (
              <div key={name} className="coverage-row">
                <strong>{name}</strong>
                <span className={status === 'partial' ? 'status-warning' : 'status-ok'}>{status}</span>
                <p>{detail}</p>
              </div>
            ))}
          </div>
        </section>
        <section className="panel warning-panel">
          <h2>不可证明事项</h2>
          <ul>{evidencePackageView.unprovenItems.map((item) => <li key={item}>{item}</li>)}</ul>
          <p>这些内容必须保留在 Sponsor 汇报和导出包中，不能被 KPI 成功态覆盖。</p>
        </section>
      </section>
      <section className="panel">
        <h2>代表性样例</h2>
        <p>{primaryLoopId} 是本轮可解释诊断样例；{valveCheckLoopId} 保留现场核实风险；{evidencePackageView.id} 记录最终审计引用。</p>
        <div className="top-actions"><Link className="button" to="/evidence">查看完整证据包</Link><Link className="button ghost" to="/delivery/acceptance">查看交付验收</Link></div>
      </section>
    </>
  );
}
export function QualityRulesPage({ route }: { route: NavigationItem }) { const rules = [{ name: '质量码规则', value: 'GOOD 进入评价；BAD/FROZEN 降级为数据不足' }, { name: '缺失规则', value: 'PV/SP/OP/MODE 任一关键字段缺失时不生成强诊断' }, { name: '冻结规则', value: '样本冻结后字段映射、窗口、排除规则不可漂移' }, { name: '突变规则', value: '突变与扰动事件需进入事件线，不直接归因 PID' }]; return <><PageHeader route={route} /><section className="grid two">{rules.map((rule) => <article className="panel" key={rule.name}><h2>{rule.name}</h2><p>{rule.value}</p></article>)}</section><section className="panel warning-panel"><h2>安全边界</h2><p>质量规则只影响评价状态与 EvidencePackage，不写 DCS、不切模式、不主动激励。</p><Link className="button ghost" to="/samples/freeze">查看样本冻结</Link></section></>; }
export function DeliveryAcceptancePage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="grid four"><MetricCard label="主链验收" value="通过" delta="首页到 Sponsor 可走通"/><MetricCard label="自动测试" value={deliveryVerification.smokeTests} delta={deliveryVerification.verifiedScope}/><MetricCard label="安全边界" value="通过" delta="不写 DCS"/><MetricCard label="证据状态" value="partial" delta="缺证据不伪装 success"/></section><section className="grid two"><section className="panel"><h2>验收清单</h2><ol className="task-list"><li><strong>可信样本</strong><span>{dataLineage.datasetId} · {dataLineage.loopCount} 回路开发 smoke 数据 · {sampleScaleNote}</span></li><li><strong>三类证据链</strong><span>PID 疑似、阀门/仪表疑似、数据/工况问题均可点击展开</span></li><li><strong>治理闭环</strong><span>审核冲突会降级 partial，实施和复评不伪装完成</span></li><li><strong>EvidencePackage</strong><span>manifest-first，含 included refs、hash、状态、风险摘要</span></li></ol></section><section className="panel"><h2>演示路径</h2><div className="timeline"><span>/</span><span>/samples/readiness</span><span>/performance/ranking</span><span>/diagnosis/loop/:loopId</span><span>/closure/review</span><span>/evidence</span><span>/sponsor</span></div><Link className="button" to="/">进入演示起点</Link></section></section><section className="grid two"><section className="panel"><h2>门禁记录</h2><dl className="detail-list"><div><dt>构建命令</dt><dd>{deliveryVerification.buildCommand}</dd></div><div><dt>构建状态</dt><dd>{deliveryVerification.buildStatus}</dd></div><div><dt>Smoke 命令</dt><dd>{deliveryVerification.smokeCommand}</dd></div><div><dt>Smoke 状态</dt><dd>{deliveryVerification.smokeStatus} · {deliveryVerification.smokeTests}</dd></div></dl></section><section className="panel"><h2>整改就绪矩阵</h2><div className="table-wrap"><table><thead><tr><th>级别</th><th>问题</th><th>状态</th><th>当前表达</th></tr></thead><tbody><tr><th scope="row">P0</th><td>样本口径一致</td><td>已收口</td><td>24 回路 smoke 数据与 50-100/72 评审口径并行展示</td></tr><tr><th scope="row">P0</th><td>三类证据链</td><td>已收口</td><td>TIC-1115 / PIC-1122 / FIC-1136 可分别展开</td></tr><tr><th scope="row">P0</th><td>EvidencePackage manifest</td><td>已收口</td><td>included refs、hash、package/validity 状态、风险摘要可见</td></tr><tr><th scope="row">P1</th><td>partial 状态传播</td><td>已收口</td><td>审核冲突影响实施、复评与导出包</td></tr><tr><th scope="row">P2</th><td>页面可维护性</td><td>已收口</td><td>已拆为 shared / overview / sample-ledger / closure-evidence 模块</td></tr></tbody></table></div></section></section></>; }
