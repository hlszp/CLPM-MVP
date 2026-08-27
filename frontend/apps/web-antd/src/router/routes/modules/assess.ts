import type { RouteRecordRaw } from 'vue-router';

/**
 * 评估路由模块（IA 重构 Phase A·职能轴）
 *
 * 子菜单：性能总览 / 指标分析 / 回路性能 / 指标矩阵 / 评估记录 / 评估任务
 * KPI 报表已迁入「统计报告-绩效报告」（/reports/performance），旧路径保留 redirect。
 * 指标配置已迁入配置模块（/config/metric），见 config.ts
 * 评估记录（KPI 快照明细）由 Tab 提升为二级菜单（/metric/history，IA 重构二期）。
 * 指标分析（指标维度横切，docs/MVP设计/12-指标分析页设计方案.md，2026-08-25）。
 * 指标矩阵（全回路 × 全指标集中查看，docs/MVP设计/15-回路指标矩阵页设计方案.md，2026-08-27）。
 *
 * 角色权限（PRD §3 + 实现契约 §5）：
 * - ADMIN：全部
 * - IC_ENGINEER：全部（含评估记录/评估任务）
 * - PE_ENGINEER / SPONSOR：查看（性能总览/指标分析/回路性能/指标矩阵）
 * - EXPERT：不可见
 *
 * leaf 路径保留 /metric/* 绝对路径（路径稳定策略，仅重命名高价值配置项）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Assess',
    path: '/assess',
    redirect: '/metric/pid-dashboard',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:gauge',
      order: 2,
      title: '评估',
      module: 'assess',
    },
    children: [
      {
        name: 'AssessOverview',
        path: '/metric/pid-dashboard',
        component: () => import('#/views/metric/pid-dashboard.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:layout-dashboard',
          title: '性能总览',
        },
      },
      {
        name: 'AssessIndicatorAnalysis',
        path: '/metric/indicator-analysis',
        component: () => import('#/views/metric/indicator-analysis.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          // URL query 为真相源（metric/window/plantNodeId），控件切换 replace query；
          // fullPathKey=false 使 query 变化不重建组件实例（同 loop-workbench 先例），
          // 避免“切换筛选条件整页重挂载”导致的排版抖动与重复请求
          fullPathKey: false,
          icon: 'lucide:bar-chart-3',
          title: '指标分析',
        },
      },
      {
        name: 'AssessLoopPerformance',
        path: '/metric/loop-performance',
        component: () => import('#/views/metric/loop-performance.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:git-branch',
          title: '回路性能',
        },
      },
      {
        name: 'AssessMatrix',
        path: '/metric/matrix',
        component: () => import('#/views/metric/matrix.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          // URL query 为真相源（tab/window/plantNodeId/loopId），控件切换 replace query
          fullPathKey: false,
          icon: 'lucide:table-2',
          title: '指标矩阵',
        },
      },
      {
        name: 'AssessHistory',
        path: '/metric/history',
        component: () => import('#/views/metric/history-snapshots.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER'],
          icon: 'lucide:history',
          title: '评估记录',
        },
      },
      {
        name: 'AssessTasks',
        path: '/metric/tasks',
        component: () => import('#/views/metric/tasks.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER'],
          icon: 'lucide:list-checks',
          title: '评估任务',
        },
      },
    ],
  },
  // 旧 /metric 父路径兼容 redirect（保护书签/E2E，/metric/config 由 config.ts 接管）
  {
    name: 'MetricLegacy',
    path: '/metric',
    redirect: '/metric/pid-dashboard',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      hideInMenu: true,
      title: '性能评估',
    },
  },
  // KPI 报表已迁入统计报告-绩效报告（IA 优化 P0，2026-08-22）
  {
    name: 'LegacyKpiReport',
    path: '/metric/kpi-report',
    redirect: '/reports/performance',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      hideInMenu: true,
      title: '绩效报告',
    },
  },
];

export default routes;
