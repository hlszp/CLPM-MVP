import type { RouteRecordRaw } from 'vue-router';

/**
 * 性能评估路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.3 + IDS v3.2 §2.3
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
      icon: 'lucide:gauge',
      order: 3,
      title: '性能评估',
    },
    name: 'Metric',
    path: '/metric',
    redirect: '/metric/dashboard',
    children: [
      {
        name: 'MetricDashboard',
        path: '/metric/dashboard',
        component: () => import('#/views/metric/dashboard.vue'),
        meta: {
          icon: 'lucide:layout-dashboard',
          title: '性能看板',
        },
      },
      {
        name: 'MetricRanking',
        path: '/metric/ranking',
        component: () => import('#/views/metric/ranking.vue'),
        meta: {
          icon: 'lucide:arrow-down-narrow-wide',
          title: '低效排行',
        },
      },
      {
        name: 'MetricStatistics',
        path: '/metric/statistics',
        component: () => import('#/views/metric/statistics.vue'),
        meta: {
          icon: 'lucide:bar-chart-3',
          title: '统计报表',
        },
      },
      {
        name: 'MetricConfig',
        path: '/metric/config',
        component: () => import('#/views/metric/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings-2',
          title: '指标配置',
        },
      },
      {
        name: 'MetricEngineConfig',
        path: '/metric/engine-config',
        component: () => import('#/views/metric/engine-config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:cog',
          title: '引擎配置',
        },
      },
    ],
  },
];

export default routes;
