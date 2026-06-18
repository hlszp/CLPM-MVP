import { Link, useParams } from 'react-router-dom';
import { CheckCircle2, FileText, ShieldAlert } from 'lucide-react';
import { TrendChart } from '../components/TrendChart';
import { EvidenceImpactStrip, EvidencePackageHeader, MetricCard } from '../components/ui';
import { dataLineage, evidencePackageView, evidenceWindows, findings, kpis, primaryLoopId, reevaluation, reviews, sampleScaleNote, tuningCaseView } from '../data/mockData';
import type { NavigationItem } from '../types';
import { BatchCard, PageHeader } from './pageShared';

export function DiagnosisPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid three">{findings.map((finding) => <article className="panel" key={finding.id}><h2>{finding.title}</h2><p>{finding.loopId} · {finding.findingType} · {finding.severity} · 置信度 {Math.round(finding.confidence*100)}%</p><p>{finding.evidence}</p><p>负责人：{finding.ownerRole}；证据引用：{finding.evidenceRefs.join(' / ')}</p><Link className="button" to={`/diagnosis/loop/${finding.loopId}`}>查看证据</Link></article>)}</section></>; }
export function LoopEvidencePage({ route }: { route: NavigationItem }) { const { loopId } = useParams(); const requestedLoopId = loopId ?? route.path.split('/').pop(); const evidence = evidenceWindows.find((item) => item.loopId === requestedLoopId); const finding = findings.find((item) => item.loopId === requestedLoopId); if (!evidence) return <><PageHeader route={{...route, label: `${requestedLoopId ?? '未知回路'} 回路证据`}} /><section className="panel warning-panel"><h2>未找到该回路证据</h2><p>当前 demo-data 证据窗口未包含 {requestedLoopId}，不会伪装为真实证据链。</p><Link className="button" to="/diagnosis">返回诊断清单</Link></section></>; return <><PageHeader route={{...route, label: `${evidence.loopId} 回路证据`}} /><section className="grid evidence-layout"><TrendChart evidence={evidence}/><section className="panel"><h2>规则命中</h2>{evidence.rules.map((rule) => <p key={rule}>✓ {rule}</p>)}<h2>事件线</h2>{evidence.events.map((event) => <p key={event}>{event}</p>)}{finding ? <><h2>诊断契约</h2><p>{finding.findingType} · {finding.severity} · 负责人：{finding.ownerRole}</p><p>证据引用：{finding.evidenceRefs.join(' / ')}</p></> : null}<Link className="button" to="/closure/review">提交建议审核</Link></section></section></>; }
export function ReviewPage({ route }: { route: NavigationItem }) {
  const decisions = [
    { label: '通过（模拟）', tone: 'approve', pressed: true, impact: '进入人工实施记录，但仍需检查仪表补证据。' },
    { label: '驳回（模拟）', tone: 'reject', pressed: false, impact: '返回诊断补证据，当前建议不进入实施。' },
    { label: '需补证据（模拟）', tone: 'evidence', pressed: false, impact: 'EvidencePackage 保持 partial，实施和复评不能关闭归档。' },
  ];
  return (
    <>
      <PageHeader route={route} />
      <section className="panel notice-panel">
        <strong>桌面评审版 / 模拟版</strong>
        <p>当前审核页用于评审角色决策流转与证据状态传播，不代表真实工单系统已接入。</p>
      </section>
      <section className="review-layout">
        <section className="panel">
          <h2>决策样式预览</h2>
          <div className="decision-options semantic" aria-label="模拟审核决策">
            {decisions.map((decision) => (
              <button key={decision.label} type="button" className={`decision-button ${decision.tone}`} aria-pressed={decision.pressed}>
                <strong>{decision.label}</strong>
                <span>{decision.impact}</span>
              </button>
            ))}
          </div>
          <h2>审核结论</h2>
          {reviews.filter((review) => review.loopId === primaryLoopId).map((review) => (
            <article className="decision" key={review.id}>
              <strong>{review.role}：{review.decision}</strong>
              <p>{review.note}</p>
            </article>
          ))}
          <Link className="button" to="/closure/multi-review">进入多方审核</Link>
        </section>
        <aside className="action-drawer">
          <h2>右侧总结</h2>
          <div className="state-machine">
            <span className="done">诊断</span>
            <span className="active">审核</span>
            <span className="blocked">实施</span>
            <span className="blocked">复评</span>
            <span className="partial">证据包 partial</span>
          </div>
          <p>通过会进入人工实施记录；驳回会返回诊断补证据；需补证据会标记 EvidencePackage 为 partial。</p>
          <EvidenceImpactStrip />
          <Link className="button ghost" to={`/diagnosis/loop/${primaryLoopId}`}>查看证据</Link>
        </aside>
      </section>
    </>
  );
}
export function MultiReviewPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="panel notice-panel"><strong>桌面评审版 / 模拟版</strong><p>多方审核意见用于展示会签冲突如何影响下游状态；补证据前不允许把流程视为真实完成。</p></section><section className="grid three"><article className="panel"><h2>工艺意见</h2><p>认可低效判断，建议小窗口实施。</p><strong>状态：已同意</strong></article><article className="panel"><h2>仪表意见</h2><p>需补一次阀门行程核实记录。</p><strong>状态：待补证据</strong></article><article className="panel"><h2>安全意见</h2><p>回退条件明确，观察窗口可接受。</p><strong>状态：已同意</strong></article></section><section className="panel warning-panel"><h2>冲突提示</h2><p>仪表角色要求补证据，当前不能直接进入“已完成实施”。</p><Link className="button" to="/closure/implementation">查看实施记录模板</Link></section></>; }
export function ImplementationPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="panel notice-panel"><strong>桌面评审版 / 模拟版</strong><p>当前页面用于评审授权实施链路，不代表系统真实写入 DCS；补证据前只允许展示模板与边界。</p></section><section className="grid two"><section className="panel"><h2>人工实施记录</h2><dl className="detail-list"><div><dt>回路</dt><dd>{primaryLoopId}</dd></div><div><dt>实施窗口</dt><dd>2026-06-18 09:30 — 10:00</dd></div><div><dt>实施人</dt><dd>授权仪表工程师</dd></div><div><dt>DCS 实际动作</dt><dd>人工调整参数，系统不写入</dd></div></dl><Link className="button ghost" to="/closure/reevaluation">进入观察复评</Link></section><section className="panel warning-panel"><h2>实施前门禁</h2><p>因仪表审核为需补证据，当前只能查看实施记录模板，不能标记为已完成实施。</p><p>EvidencePackage 状态：{evidencePackageView.status}</p><Link className="button ghost" to="/closure/rollback">查看回退条件</Link></section></section></>; }
export function RollbackPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid two"><section className="panel"><h2>回退条件</h2><ul><li>PV 偏差超过实施前 P95 超过 30 分钟</li><li>报警次数高于实施前基线 20%</li><li>现场工艺或安全角色要求立即恢复</li></ul></section><section className="panel"><h2>原始参数</h2><code>Kp=1.8, Ti=120s, Td=0s</code><p>回退由授权人员人工恢复，平台只提供记录和提醒。</p></section></section><section className="panel warning-panel"><h2>观察要求</h2><dl className="detail-list"><div><dt>观察窗口</dt><dd>{reevaluation.afterWindow}</dd></div><div><dt>观察指标</dt><dd>有效自控率 / 平稳率 / 报警次数 / 操作频次</dd></div><div><dt>责任人</dt><dd>控制工程师复评，仪表补现场阀位反馈，安全确认回退边界</dd></div><div><dt>复评入口</dt><dd>InstrumentCheckRecord 与 PostImplementationObservation 补齐前保持 partial</dd></div></dl></section></>; }
export function ReevaluationPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="panel notice-panel"><strong>桌面评审版 / 模拟版</strong><p>复评结果用于证明闭环判定规则；缺失现场补证据或观察记录时，仅展示 partial，不生成完整成功结论。</p></section><section className="grid four">{reevaluation.kpis.map((item) => <MetricCard key={item.label} label={item.label} value={item.after} delta={`${item.before} → ${item.after} · ${item.delta}`}/>)}</section><section className="grid two"><section className="panel"><h2>Reevaluation 契约</h2><dl className="detail-list"><div><dt>回路</dt><dd>{reevaluation.loopId}</dd></div><div><dt>前窗口</dt><dd>{reevaluation.beforeWindow}</dd></div><div><dt>后窗口</dt><dd>{reevaluation.afterWindow}</dd></div><div><dt>状态</dt><dd>{reevaluation.status}</dd></div></dl></section><section className="panel warning-panel"><h2>复评结论</h2><p>{reevaluation.conclusion}</p><p>缺失引用：{reevaluation.missingRefs.join(' / ')}；EvidencePackage 保持 partial，缺失引用：{evidencePackageView.riskSummary[2]}。</p><Link className="button" to="/evidence">查看 partial 证据包</Link></section></section></>; }
export function EvidencePage({ route }: { route: NavigationItem }) {
  return (
    <>
      <PageHeader route={route} state={evidencePackageView.status === 'success' ? 'success' : 'partial'} />
      <section className="panel notice-panel">
        <strong>桌面评审版 / 模拟版</strong>
        <p>当前证据包用于评审 manifest-first、状态传播与审计留痕；缺失现场记录时保持 partial，不代表真实项目已闭环。</p>
        <div className="top-actions"><Link className="button ghost" to="/delivery/acceptance">查看交付验收</Link></div>
      </section>
      <EvidencePackageHeader
        id={evidencePackageView.id}
        packageStatus={evidencePackageView.packageStatus}
        validityStatus={evidencePackageView.validityStatus}
        completeness={evidencePackageView.completeness}
        manifestHash={evidencePackageView.manifestHash}
        generatedAt={evidencePackageView.generatedAt}
        missingCount={evidencePackageView.missingRefs.length}
      />
      <section className="grid two">
        <section className="panel warning-panel">
          <h2>结论边界</h2>
          <p>{evidencePackageView.conclusion}</p>
          <dl className="detail-list">
            <div><dt>状态</dt><dd>{evidencePackageView.status}</dd></div>
            <div><dt>版本</dt><dd>{evidencePackageView.manifestVersion}</dd></div>
            <div><dt>缺失 refs</dt><dd>{evidencePackageView.missingRefs.join(' / ')}</dd></div>
          </dl>
        </section>
        <section className="panel manifest">
          <h2>Included refs</h2>
          {evidencePackageView.includedRefs.map((ref) => <code className={ref.status === '缺失' ? 'missing-ref' : ''} key={ref.name}>{ref.name} · {ref.status}</code>)}
        </section>
      </section>
      <section className="grid two">
        <section className="panel warning-panel"><h2>风险摘要</h2><ul>{evidencePackageView.riskSummary.map((risk) => <li key={risk}>{risk}</li>)}</ul></section>
        <section className="panel warning-panel"><h2>不可证明事项</h2><ul>{evidencePackageView.unprovenItems.map((item) => <li key={item}>{item}</li>)}</ul><p>manifest-first：证据包引用对象与版本，不用截图堆叠替代审计链。</p></section>
      </section>
      <section className="panel">
        <h2>demo-data 溯源</h2>
        <dl className="detail-list"><div><dt>数据集 ID</dt><dd>{dataLineage.datasetId}</dd></div><div><dt>采样窗口</dt><dd>{dataLineage.sampleWindow}</dd></div><div><dt>采样间隔</dt><dd>{dataLineage.sampleIntervalSeconds}s</dd></div><div><dt>行数 / 回路数</dt><dd>{dataLineage.rowCount} / {dataLineage.loopCount}</dd></div></dl>
        <p>场景分布：normal {dataLineage.scenarioSummary.normal}，oscillation {dataLineage.scenarioSummary.oscillation}，valve_stiction {dataLineage.scenarioSummary.valve_stiction}，tuning_candidate {dataLineage.scenarioSummary.tuning_candidate}。</p>
      </section>
    </>
  );
}
export function SampleReportPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="grid two"><section className="panel"><h2>样本报告摘要</h2><BatchCard/><p>数据集 {dataLineage.datasetId} 覆盖 {dataLineage.loopCount} 回路、{dataLineage.rowCount} 行秒级数据，支持 P0 主链演示。{sampleScaleNote}</p><p>安全边界：{dataLineage.safetyBoundary}</p></section><section className="panel"><h2>场景分布</h2><dl className="detail-list"><div><dt>normal</dt><dd>{dataLineage.scenarioSummary.normal}</dd></div><div><dt>oscillation</dt><dd>{dataLineage.scenarioSummary.oscillation}</dd></div><div><dt>valve_stiction</dt><dd>{dataLineage.scenarioSummary.valve_stiction}</dd></div><div><dt>manual_mode</dt><dd>{dataLineage.scenarioSummary.manual_mode}</dd></div><div><dt>data_quality_issue</dt><dd>{dataLineage.scenarioSummary.data_quality_issue}</dd></div><div><dt>tuning_candidate</dt><dd>{dataLineage.scenarioSummary.tuning_candidate}</dd></div></dl></section></section></>; }
export function ExportCenterPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid three"><article className="panel"><h2>PDF 评审包</h2><p>用于 Sponsor 汇报，包含结论、风险摘要、样本口径和安全边界。</p></article><article className="panel"><h2>JSON Manifest</h2><p>导出 EvidencePackage 引用、included refs、package_status、validity_status 和 hash。</p></article><article className="panel"><h2>Excel 台账</h2><p>导出 LoopRecord、字段映射、排除规则和版本链，便于人工复核。</p></article></section><section className="panel warning-panel"><h2>不可导出为结论的内容</h2><p>缺失 InstrumentCheckRecord 或 PostImplementationObservation 时，导出包必须标记 partial，不得生成“已完成闭环”结论。</p><p>当前 manifest：{evidencePackageView.manifestHash} · {evidencePackageView.packageStatus}</p></section></>; }
export function DataSourcePage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="grid two"><section className="panel"><h2>当前数据源</h2><dl className="detail-list"><div><dt>数据集</dt><dd>{dataLineage.datasetId}</dd></div><div><dt>CSV</dt><dd>{dataLineage.csvFile}</dd></div><div><dt>Metadata</dt><dd>{dataLineage.metadataFile}</dd></div><div><dt>Events</dt><dd>{dataLineage.eventsFile}</dd></div></dl></section><section className="panel"><h2>接入边界</h2><p>{dataLineage.safetyBoundary}</p><p>当前原型通过 `npm run import:demo-data` 派生样本、回路、KPI、证据包和整定样例，不直接连接真实 DCS。</p><p>关键字段：{dataLineage.fields.slice(0, 8).join(' / ')} …</p></section></section></>; }
export function SafetyPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} state="success"/><section className="grid three"><article className="panel"><ShieldAlert/><h2>不能做</h2><p>不直接写 DCS，不自动下发 P/I/D。</p></article><article className="panel"><CheckCircle2/><h2>能做</h2><p>输出建议、证据、风险、回退方案。</p></article><article className="panel"><FileText/><h2>必须留痕</h2><p>审核、实施、复评、导出进入审计链。</p></article></section></>; }
export function TuningPage({ route }: { route: NavigationItem }) { return <><PageHeader route={route} /><section className="grid two"><section className="panel"><h2>{tuningCaseView.loopId}</h2><dl className="detail-list"><div><dt>当前参数</dt><dd>{tuningCaseView.current}</dd></div><div><dt>建议参数</dt><dd>{tuningCaseView.suggested}</dd></div><div><dt>可信度</dt><dd>{Math.round(tuningCaseView.confidence*100)}%</dd></div><div><dt>负责人</dt><dd>{tuningCaseView.ownerRole}</dd></div></dl></section><section className="panel warning-panel"><h2>风险与回退</h2><p>{tuningCaseView.risk}</p><p>{tuningCaseView.fallback}</p><p>{tuningCaseView.boundary}</p></section></section></>; }
