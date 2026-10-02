import type { RouteRecordRaw } from 'vue-router';


function monitorHome() {
  // 0930 用户口径：监控模块默认页统一落工作台（回路监视仍为菜单首项）
  return '/workbench';
}

/**
 * 监控路由模块（0929 总览收敛：4 子页）
 *
 * 定位：运行驾驶舱与单回路处置入口。
 * 菜单顺序：回路监视 → 预警事件 → 关注队列 → 回路工作台。
 * （装置总览 /dashboard/workbench 已按 2026-09-29 裁决下线，
 *   总览收敛为驾驶舱 + 工作台 + 性能总览三屏）
 *
 * 角色权限（实现契约 §5）：
 * - 回路监视/预警事件/关注队列：全部角色（Sponsor 只读）
 * - 回路工作台：ADMIN / IC_ENGINEER / PE_ENGINEER / EXPERT
 *
 * 注：高密度回路实时表仍保留为隐藏视图，服务于批量巡检/导出与旧书签；
 *     关注队列聚合五类当前行动项，预警记录（/monitor/alerts）承载
 *     历史/审计/导出，二者互补不替代。
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
      module: 'monitor',
    },
    children: [
      {
        // 面点分离：回路列表独立成页（页型 B），全角色可见（含 EXPERT/SPONSOR）
        // /loop/monitor 旧书签重定向到本页
        name: 'MonitorLoops',
        path: '/monitor/loops',
        component: () => import('#/views/monitor/loops.vue'),
        meta: {
          authority: [
            'ADMIN',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
            'EXPERT',
          ],
          icon: 'lucide:list',
          title: '回路监视',
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
      {
        name: 'MonitorAttention',
        path: '/monitor/attention',
        component: () => import('#/views/monitor/attention.vue'),
        meta: {
          authority: [
            'ADMIN',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
            'EXPERT',
          ],
          icon: 'lucide:list-checks',
          title: '关注队列',
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
        // 回路工作台新版（P1 壳层与趋势基座，2026-10-02 起）：
        // 单页全生命周期工作台（监视-评估-诊断-整定-处置，零跳转全内嵌）。
        // 验收期与旧 /monitor/loop-workbench 菜单项并存，便于对照；
        // 验收通过后由用户决定隐藏/删除旧菜单（设计方案 v3 §2）。
        name: 'LoopWorkbench360',
        path: '/loop/workbench360',
        component: () => import('#/views/loop/workbench360/index.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          fullPathKey: false,
          icon: 'lucide:panel-top-close',
          module: 'monitor',
          title: '回路工作台（新版）',
        },
      },
      {
        name: 'MonitorLoopRealtime',
        path: '/loop/monitor',
        // 面点分离：旧 /loop/monitor 重定向到独立回路列表页（不再进工作台 table 模式）
        redirect: (to) => ({
          path: '/monitor/loops',
          query: { ...to.query },
        }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          icon: 'lucide:gauge',
          title: '回路实时表格',
        },
      },
      {
        // 旧组件保留至少一个发布周期（MW-P4-04）
        name: 'MonitorLoopRealtimeLegacy',
        path: '/loop/monitor/legacy',
        component: () => import('#/views/loop/monitor.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '回路实时表格（旧版）',
        },
      },
    ],
  },
  // 旧 /dashboard 父路径兼容 redirect（保护书签/E2E）
  // 0929：装置总览下线后改落回路监视
  {
    name: 'DashboardLegacy',
    path: '/dashboard',
    redirect: '/monitor/loops',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      hideInMenu: true,
      title: '工作台',
    },
  },
  {
    name: 'DashboardWorkbenchLegacy',
    path: '/dashboard/workbench',
    redirect: '/monitor/loops',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      hideInMenu: true,
      title: '装置总览（已下线）',
    },
  },
];

export default routes;
