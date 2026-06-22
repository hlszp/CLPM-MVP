import type { RouteRecordRaw } from 'vue-router';

/**
 * 工作台（门户）路由模块
 *
 * 对齐 UI/UX v4.1 §6.1 + PRD §4.1 + IDS v3.2 §2.1
 * - 性能总览首页：全角色日常作业入口
 * - 聚合性能评估、诊断中心、Action Tracker 多模块数据
 *
 * 角色权限：全部角色可见
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Dashboard',
    path: '/dashboard',
    redirect: '/dashboard/workbench',
    meta: {
      icon: 'lucide:layout-dashboard',
      order: 1,
      title: '工作台',
    },
    children: [
      {
        name: 'DashboardWorkbench',
        path: '/dashboard/workbench',
        component: () => import('#/views/dashboard/workbench.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:layout-dashboard',
          title: '性能总览',
        },
      },
    ],
  },
];

export default routes;
