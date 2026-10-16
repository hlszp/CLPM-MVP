/**
 * 五组主导航登记（IAR-D1，P6-01b-a Slice H，2026-10-14）
 *
 * 已采纳口径（09 号文 IAR-D1 + Slice H 任务书契约）：
 * 我的工作、工程助手（P6-07 后续）、驾驶舱、专业作业、配置与管理五组；
 * 本批落地五组为——个人责任（我的工作）/ 全局观察（驾驶舱）/ 回路作业
 * （工作台+监控+评估+诊断+整定+处置）/ 统计分析（报告）/ 系统（配置+系统管理）。
 *
 * 表达形式：各一级菜单路由 meta.navGroup 标记归属，菜单顺序保持组内连续
 * （order 现状即满足，不改既有 path 与 order 数值——旧书签/报告链接回归
 * 由 p6-01ba-nav-groups.test.ts 守护）。「首先是导航分组」（00-IA §3.1）：
 * 不新增空白中转页、不制造菜单层级；侧边栏分组标题渲染依赖布局层能力，
 * 超出本 Slice 独占范围，登记在此供后续布局 Owner 消费。
 *
 * 菜单顺序：个人责任（order=-1，我的工作入口保留现状）→ 全局观察（0）→
 * 回路作业（1~6）→ 统计分析（7）→ 系统（8~9）。
 */

/** 五组导航组键 */
export type NavGroupKey =
  | 'analytics'
  | 'loop-ops'
  | 'observe'
  | 'personal'
  | 'system';

/** 组定义（按菜单出现顺序） */
export interface NavGroupDef {
  /** 组内一级菜单说明（组名采纳口径的组成描述） */
  readonly contains: string;
  /** 组键 */
  readonly key: NavGroupKey;
  /** 用户可见组名（IAR-D1 采纳口径） */
  readonly label: string;
}

/** 五组主导航（菜单出现顺序：个人责任 → 全局观察 → 回路作业 → 统计分析 → 系统） */
export const NAV_GROUPS: readonly NavGroupDef[] = [
  {
    key: 'personal',
    label: '个人责任',
    contains: '我的工作（工程师默认首页）',
  },
  {
    key: 'observe',
    label: '全局观察',
    contains: '驾驶舱（六页签 + 进入工作区统一出口）',
  },
  {
    key: 'loop-ops',
    label: '回路作业',
    contains: '回路工作台 + 回路监控 + 性能评估 + 回路诊断 + 参数整定 + 运维处置',
  },
  {
    key: 'analytics',
    label: '统计分析',
    contains: '统计分析（绩效/数据质量/预警/诊断/处置/收益报告与订阅配置）',
  },
  {
    key: 'system',
    label: '系统',
    contains: '参数配置 + 系统管理',
  },
] as const;

/** 组键 → 组名（校验/展示用） */
export const NAV_GROUP_LABELS: Readonly<Record<NavGroupKey, string>> =
  Object.fromEntries(NAV_GROUPS.map((g) => [g.key, g.label])) as Record<
    NavGroupKey,
    string
  >;
