import type { RouteRecordRaw } from 'vue-router';

/**
 * 性能评估路由模块
 *
 * 对齐 UIUX 改造方案：以"性能看板 / 低效排行 / 统计报表 / 评估任务 / 指标配置"作为主结构。
 * 评估任务模块整合手动重算、自动任务记录与任务策略配置。
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部（含评估任务）
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
        name: 'MetricPidDashboard',
        path: '/metric/pid-dashboard',
        component: () => import('#/views/metric/pid-dashboard.vue'),
        meta: {
          icon: 'lucide:activity',
          title: 'PID评估看板',
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
        name: 'MetricSnapshots',
        path: '/metric/snapshots',
        component: () => import('#/views/metric/snapshots.vue'),
        meta: {
          icon: 'lucide:table-properties',
          title: '指标明细',
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
        name: 'MetricWeightConfig',
        path: '/metric/weight-config',
        component: () => import('#/views/metric/weight-config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:scale',
          title: '权重配置',
        },
      },
      {
        name: 'MetricEngineConfig',
        path: '/metric/engine-config',
        component: () => import('#/views/metric/engine-config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:cog',
          title: '引擎规则',
        },
      },
      {
        name: 'MetricGradingThreshold',
        path: '/metric/grading-threshold',
        component: () => import('#/views/metric/grading-threshold.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:scale',
          title: '定级阈值',
        },
      },
      {
        name: 'MetricVersionHistory',
        path: '/metric/version-history',
        component: () => import('#/views/metric/version-history.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:history',
          title: '版本管理',
        },
      },
    ],
  },
];

export default routes;
