/**
 * 装置总览（workbench）页内共享的展示层类型
 *
 * 由 workbench.vue（组装/数据加载）产出，经 props 传入 components/ 下各子组件。
 */

/** MODE 分布行（实时口径，来自 /dashboard/auto-rate-rt 的 modeCounts） */
export interface ModeRow {
  label: string;
  count: number;
  pct: number;
  /** 手动模式 >0 红色强调（运行监控核心语义） */
  emphasis: boolean;
}

/** §D 绩效趋势序列（全厂/节点双口径合一；evaluated 仅全厂口径有值） */
export interface TrendLines {
  timestamps: string[];
  score: (null | number)[];
  steady: (null | number)[];
  fast: (null | number)[];
  acc: (null | number)[];
  auto: (null | number)[];
  /** 参评回路数柱（右轴）；节点口径无此数据为 null */
  evaluated: null | number[];
}

/** 装置总览选中节点（排名即导航；null = 全厂） */
export interface WorkbenchSelection {
  id: string;
  name: string;
  type: 'AREA' | 'UNIT';
}
