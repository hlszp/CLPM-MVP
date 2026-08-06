import type { RouteRecordRaw } from 'vue-router';

/**
 * 监控路由模块（IA 重构 Phase A）
 *
 * 定位：运行驾驶舱（系统概览/待办/数据链路健康/异常预测）。
 * 对齐 IA 重构方案 §3.2/§4.5。
 *
 * 角色权限（实现契约 §5）：
 * - ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR 可见
 * - EXPERT 默认首页 /diagnosis，不进监控
 *
 * 注：回路实时列表 /loop/monitor 暂留监控组（Phase B 回路工作台上线后回归实体轴）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Monitor',
    path: '/monitor',
    redirect: '/dashboard/workbench',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:activity',
      order: 1,
      title: '监控',
    },
    children: [
      {
        name: 'MonitorOverview',
        path: '/dashboard/workbench',
        component: () => import('#/views/dashboard/workbench.vue'),
        meta: {
          affixTab: true,
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:layout-dashboard',
          title: '系统概览',
        },
      },
      {
        name: 'MonitorLoopRealtime',
        path: '/loop/monitor',
        component: () => import('#/views/loop/monitor.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:gauge',
          title: '回路实时',
        },
      },
    ],
  },
  // 旧 /dashboard 父路径兼容 redirect（保护书签/E2E）
  {
    name: 'DashboardLegacy',
    path: '/dashboard',
    redirect: '/dashboard/workbench',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      hideInMenu: true,
      title: '工作台',
    },
  },
];

export default routes;
