import type { RouteRecordRaw } from 'vue-router';

import { useUserStore } from '@vben/stores';

function monitorHome() {
  const roles = useUserStore().userInfo?.roles ?? [];
  return roles.includes('EXPERT')
    ? '/monitor/loop-workbench'
    : '/dashboard/workbench';
}

/**
 * 监控路由模块（IA 重构 Phase A）
 *
 * 定位：运行驾驶舱与单回路处置入口。
 * 对齐 IA 收敛方案：监控负责跨回路扫视、预警结果与单回路闭环处置。
 *
 * 角色权限（实现契约 §5）：
 * - 系统概览：ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR
 * - 回路工作台：ADMIN / IC_ENGINEER / PE_ENGINEER / EXPERT
 * - 预警事件：全部角色
 *
 * 注：高密度回路实时表仍保留为隐藏视图，服务于批量巡检/导出与旧书签；
 *     用户主入口统一为回路工作台。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Monitor',
    path: '/monitor',
    redirect: monitorHome,
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT'],
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
          hideInMenu: true,
          icon: 'lucide:gauge',
          title: '回路实时表格',
        },
      },
      {
        name: 'MonitorLoopWorkbench',
        path: '/monitor/loop-workbench',
        component: () => import('#/views/loop/workbench.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          fullPathKey: false,
          icon: 'lucide:layout-panel-top',
          title: '回路工作台',
        },
      },
      {
        name: 'MonitorAlerts',
        path: '/monitor/alerts',
        component: () => import('#/views/alert/events.vue'),
        meta: {
          authority: [
            'ADMIN',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
            'EXPERT',
          ],
          icon: 'lucide:bell-ring',
          title: '预警事件',
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
