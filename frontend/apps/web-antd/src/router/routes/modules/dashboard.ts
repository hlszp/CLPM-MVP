import type { RouteRecordRaw } from 'vue-router';

/**
 * 工作台（门户）路由模块
 *
 * 对齐 UI/UX v4.1 §6.1 + PRD §4.1 + IDS v3.2 §2.1
 * - V62-P1-017：工作台改造为跨模块待办门户（诊断/异常跟踪/评估/整定待办计数 + 复用聚合卡）
 * - 装置性能完整看板归属 /metric/pid-dashboard，此处不再重复，消除重复心智入口
 *
 * 角色权限（实现契约 §5 + UI/UX §4.2）：
 * - ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR 可见
 * - EXPERT 不可见（EXPERT 仅诊断中心 + 回路整定，默认首页 /diagnosis）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Dashboard',
    path: '/dashboard',
    redirect: '/dashboard/workbench',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
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
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:layout-dashboard',
          title: '工作台',
        },
      },
    ],
  },
];

export default routes;
