import type { RouteRecordRaw } from 'vue-router';

/**
 * 处置路由模块（Phase 1，2026-08-18）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.1 一级菜单
 * 单页式：处置清单（/handling）+ 行内详情抽屉（含流转操作）。
 *
 * 角色权限（§7）：
 * - 清单/详情/统计查看：全部登录角色（SPONSOR / EXPERT 只读）
 * - 流转操作（start/submit/verify/ignore）：ADMIN / IC_ENGINEER / PE_ENGINEER（后端校验）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Handling',
    path: '/handling',
    component: () => import('#/views/handling/index.vue'),
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:clipboard-check',
      order: 4,
      title: '处置',
    },
  },
];

export default routes;
