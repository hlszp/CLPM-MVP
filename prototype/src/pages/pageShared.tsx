import { Link } from 'react-router-dom';
import { StateBlock } from '../components/ui';
import { currentBatch, primaryLoopId, sampleScaleNote, valveCheckLoopId } from '../data/mockData';
import type { NavigationItem } from '../types';

export function PageHeader({ route, state = 'partial' }: { route: NavigationItem; state?: 'partial' | 'success' }) {
  return (
    <header className="page-header">
      <div>
        <span className="eyebrow">{route.version} · {route.depth}</span>
        <h1>{route.label}</h1>
        <p>{route.description}</p>
      </div>
      <StateBlock state={state} />
    </header>
  );
}

export function PlaceholderPage({ route }: { route: NavigationItem }) {
  const structureGroups = {
    trend: {
      title: '趋势分析规划能力',
      bullets: ['按班次/周/月对自控率、平稳率、报警与操作频次做周期对比', '支持版本冻结后的趋势回放，避免不同口径混算', '后续与项目交付视图联动，展示整改前后收益曲线'],
    },
    coupling: {
      title: '耦合分析规划能力',
      bullets: ['展示扰动源、受影响回路与传播路径的结构视图', '区分真实耦合、共同工况变化和证据不足三种状态', '与单回路证据页配合，避免把耦合问题误判为 PID 单点问题'],
    },
    'interactive-tuning': {
      title: '交互整定规划能力',
      bullets: ['保留专家逐步确认整定建议的入口，不自动写入 DCS', '后续会接入特征点法、仿真验证和回退条件联动', '与当前 P0 样例页区分：这里只展示未来交互流程，不承诺批量整定'],
    },
    'device-overview': {
      title: '装置总览规划能力',
      bullets: ['面向装置级 KPI、回路分布和整改状态聚合展示', '后续承接 1200 回路项目交付视图，而非当前 P0 主链', '保持版本标签，避免被误解为已交付能力'],
    },
    'delivery-scope': {
      title: '项目范围规划能力',
      bullets: ['用于展示装置批次、实施窗口、角色分工和交付边界', '与验收包页互补：这里看范围，验收包页看验收标准', '当前仍为结构页，不替代 P0 治理闭环原型'],
    },
    'similar-loops': {
      title: '相似回路规划能力',
      bullets: ['后续沉淀跨项目相似回路分组、推荐参数区间和典型诊断模板', '当前只保留知识资产方向，不引入未验证算法结论', '不会在 P0 评审中被当作已可用生产能力'],
    },
    'industry-template': {
      title: '行业模板规划能力',
      bullets: ['沉淀行业阈值、诊断模板和治理动作建议', '与知识资产联动，但不会覆盖当前 demo-data 的显式证据链', '保持 P3 vision 标签，避免误读为首版交付范围'],
    },
  }[route.id] ?? {
    title: '结构展示页',
    bullets: ['该页面保留正式产品导航和版本标签', '不做深交互，避免扩大 P0 范围', '后续按版本路线逐步深化'],
  };

  return <><PageHeader route={route} /><section className="panel"><h2>{structureGroups.title}</h2><ul>{structureGroups.bullets.map((bullet) => <li key={bullet}>{bullet}</li>)}</ul><Link className="button" to="/">返回 P0 主链</Link></section></>;
}

export function NotFoundPage() {
  const route: NavigationItem = { id: 'not-found', label: '页面不存在', path: '*', version: 'P0', depth: 'basic', description: '未知路由不会被伪装为正常页面。' };
  return <><PageHeader route={route} /><section className="panel warning-panel"><h2>未知路由</h2><p>当前路径未命中原型路由清单，不会自动回到首页伪装成功。</p><div className="top-actions"><Link className="button" to="/">返回首页</Link><Link className="button ghost" to="/delivery/acceptance">查看交付验收</Link></div></section></>;
}

export function BatchCard() {
  return <section className="panel"><h2>{currentBatch.name}</h2><p>{currentBatch.window}</p><p>{currentBatch.loopCount} 回路 · 映射率 {Math.round(currentBatch.mappedRate*100)}% · 好值率 {Math.round(currentBatch.goodValueRate*100)}%</p><p>{sampleScaleNote}</p></section>;
}

export function ActionList() {
  return <ul className="action-list"><li>审核 {primaryLoopId} 诊断建议</li><li>补齐 {valveCheckLoopId} 阀门现场核实</li><li>复评上一轮人工实施记录</li></ul>;
}
