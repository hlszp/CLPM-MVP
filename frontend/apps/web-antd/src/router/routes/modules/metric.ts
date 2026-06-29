import type { RouteRecordRaw } from 'vue-router';

/**
 * 性能评估路由模块
 *
 * 对齐 UIUX 改造方案：以“性能看板 / 低效排行 / 统计报表 / 指标配置”作为主结构，
 * 将任务能力收口为性能评估执行体系的一部分。
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部
 * - PE_ENGINEER / SPONSOR / EXPERT：查看（看板/排行/报表/任务记录）
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
      // —— 性能分析（全部角色可见）——
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
      // —— 指标配置（仅 ADMIN 可见，折叠子菜单）——
      {
        name: 'MetricConfigGroup',
        path: '/metric/config-group',
        redirect: '/metric/config',
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings',
          title: '指标配置',
        },
        children: [
          {
            name: 'MetricConfig',
            path: '/metric/config',
            component: () => import('#/views/metric/config.vue'),
            meta: {
              authority: ['ADMIN'],
              icon: 'lucide:settings-2',
              title: '指标定义',
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
          // B2.5 新增：任务策略
          {
            name: 'MetricTaskStrategy',
            path: '/metric/task-strategy',
            component: () => import('#/views/metric/task-strategy.vue'),
            meta: {
              authority: ['ADMIN'],
              icon: 'lucide:calendar-clock',
              title: '任务策略',
            },
          },
          // 执行记录（任务列表）
          {
            name: 'MetricTaskRecords',
            path: '/metric/tasks',
            component: () => import('#/views/task/list.vue'),
            meta: {
              authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
              icon: 'lucide:list-checks',
              title: '执行记录',
            },
          },
        ],
      },
    ],
  },
];

export default routes;
