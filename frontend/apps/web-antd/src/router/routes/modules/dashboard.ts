import type { RouteRecordRaw } from 'vue-router';

/**
 * 工作台（门户）路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.1
 * - 性能总览首页：全角色日常作业入口
 * - 聚合性能评估、诊断中心、Action Tracker 多模块数据
 *
 * 角色权限：全部角色可见
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Dashboard',
    path: '/dashboard',
    component: () => import('#/views/dashboard/index.vue'),
    meta: {
      affixTab: true,
      icon: 'lucide:layout-dashboard',
      order: 1,
      title: '工作台',
    },
  },
];

export default routes;
