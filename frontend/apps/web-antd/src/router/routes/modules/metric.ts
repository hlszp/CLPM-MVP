import type { RouteRecordRaw } from 'vue-router';

/**
 * 性能评估路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.3
 * - 指标配置 / 引擎规则 / 性能看板 / 低效排行 / 统计报表
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部
 * - PE_ENGINEER / SPONSOR / EXPERT：查看（看板/排行/报表）
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:bar-chart-3',
      order: 3,
      title: '性能评估',
    },
    name: 'Metric',
    path: '/metric',
    children: [
      {
        name: 'MetricConfig',
        path: '/metric/config',
        component: () => import('#/views/metric/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:sliders-horizontal',
          title: '指标配置',
        },
      },
      {
        name: 'MetricEngine',
        path: '/metric/engine',
        component: () => import('#/views/metric/engine.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings-2',
          title: '引擎规则',
        },
      },
      {
        name: 'MetricDashboard',
        path: '/metric/dashboard',
        component: () => import('#/views/metric/dashboard.vue'),
        meta: {
          icon: 'lucide:bar-chart-3',
          title: '性能看板',
        },
      },
      {
        name: 'MetricRanking',
        path: '/metric/ranking',
        component: () => import('#/views/metric/ranking.vue'),
        meta: {
          icon: 'lucide:list-ordered',
          title: '低效排行',
        },
      },
      {
        name: 'MetricStatistics',
        path: '/metric/statistics',
        component: () => import('#/views/metric/statistics.vue'),
        meta: {
          icon: 'lucide:file-bar-chart',
          title: '统计报表',
        },
      },
    ],
  },
];

export default routes;
