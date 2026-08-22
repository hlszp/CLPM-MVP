import type { RouteRecordRaw } from 'vue-router';

/**
 * 处置路由模块（Phase 1F 三页式，2026-08-19）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.1（v1.1）
 * 三页式：
 * - 处置工作台 /handling/workbench   执行闭环：统计卡 + 清单 + 流转抽屉
 * - 处置档案   /handling/archive     回路维度聚合 + 跨 run 处置全史
 * - 处置统计已迁入「统计报告-处置报告」（/reports/handling），旧路径保留 redirect
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
      order: 5,
      title: '处置',
      module: 'handling',
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
        // 处置统计已迁入统计报告-处置报告（IA 优化 P0，2026-08-22）
        name: 'HandlingStatistics',
        path: '/handling/statistics',
        redirect: '/reports/handling',
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          hideInMenu: true,
          title: '处置报告',
        },
      },
    ],
  },
];

export default routes;
