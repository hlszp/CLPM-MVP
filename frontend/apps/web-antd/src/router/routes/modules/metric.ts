import type { RouteRecordRaw } from 'vue-router';

/**
 * 性能评估路由模块
 *
 * 子菜单顺序：装置性能 / 回路性能 / 评估任务 / 指标配置 / KPI报表
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部（含评估任务）
 * - PE_ENGINEER / SPONSOR / EXPERT：查看（看板/回路性能/报表）
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:gauge',
      order: 3,
      title: '性能评估',
    },
    name: 'Metric',
    path: '/metric',
    redirect: '/metric/pid-dashboard',
    children: [
      {
        name: 'MetricPidDashboard',
        path: '/metric/pid-dashboard',
        component: () => import('#/views/metric/pid-dashboard.vue'),
        meta: {
          icon: 'lucide:layout-dashboard',
          title: '装置性能',
        },
      },
      {
        name: 'MetricLoopPerformance',
        path: '/metric/loop-performance',
        component: () => import('#/views/metric/loop-performance.vue'),
        meta: {
          icon: 'lucide:git-branch',
          title: '回路性能',
        },
      },
      {
        name: 'MetricTasks',
        path: '/metric/tasks',
        component: () => import('#/views/metric/tasks.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER'],
          icon: 'lucide:list-checks',
          title: '评估任务',
        },
      },
      {
        name: 'MetricConfig',
        path: '/metric/config',
        component: () => import('#/views/metric/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings',
          title: '指标配置',
        },
      },
      {
        name: 'MetricKpiReport',
        path: '/metric/kpi-report',
        component: () => import('#/views/metric/kpi-report.vue'),
        meta: {
          icon: 'lucide:file-bar-chart',
          title: 'KPI报表',
        },
      },
    ],
  },
];

export default routes;
