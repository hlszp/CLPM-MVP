import type { RouteRecordRaw } from 'vue-router';

/**
 * 处置路由模块（Phase 1F 三页式，2026-08-19）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.1（v1.1）
 * 三页式：
 * - 处置工作台 /handling/workbench   执行闭环：统计卡 + 清单 + 流转抽屉
 * - 处置档案   /handling/archive     回路维度聚合 + 跨 run 处置全史
 * - 处置统计   /handling/statistics  管理报表：月度趋势 / 分布 / Top 回路
 *
 * 旧路由兼容：/handling → /handling/workbench（透传 query，保护
 * 诊断侧「去处置」focus 深链接与既有书签，§8.1）。
 *
 * 角色权限（§7）：
 * - 三页查看：全部登录角色（SPONSOR / EXPERT 只读）
 * - 流转操作（start/submit/verify/ignore）：ADMIN / IC_ENGINEER / PE_ENGINEER（后端校验）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Handling',
    path: '/handling',
    redirect: (to) => ({ path: '/handling/workbench', query: { ...to.query } }),
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:clipboard-check',
      order: 4,
      title: '处置',
    },
    children: [
      {
        name: 'HandlingWorkbench',
        path: '/handling/workbench',
        component: () => import('#/views/handling/workbench.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:clipboard-list',
          title: '处置工作台',
        },
      },
      {
        name: 'HandlingArchive',
        path: '/handling/archive',
        component: () => import('#/views/handling/archive.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:archive',
          title: '处置档案',
        },
      },
      {
        name: 'HandlingStatistics',
        path: '/handling/statistics',
        component: () => import('#/views/handling/statistics.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:chart-column',
          title: '处置统计',
        },
      },
    ],
  },
];

export default routes;
