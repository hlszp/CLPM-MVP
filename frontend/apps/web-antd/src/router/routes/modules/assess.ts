import type { RouteRecordRaw } from 'vue-router';

/**
 * 评估路由模块（IA 重构 Phase A·职能轴）
 *
 * 子菜单：性能总览 / 回路性能 / 评估任务 / KPI报表
 * 指标配置已迁入配置模块（/config/metric），见 config.ts
 *
 * 角色权限（PRD §3 + 实现契约 §5）：
 * - ADMIN：全部
 * - IC_ENGINEER：全部（含评估任务）
 * - PE_ENGINEER / SPONSOR：查看（看板/回路性能/报表）
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
        name: 'AssessTasks',
        path: '/metric/tasks',
        component: () => import('#/views/metric/tasks.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER'],
          icon: 'lucide:list-checks',
          title: '评估任务',
        },
      },
      {
        name: 'AssessKpiReport',
        path: '/metric/kpi-report',
        component: () => import('#/views/metric/kpi-report.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:file-bar-chart',
          title: 'KPI报表',
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
];

export default routes;
