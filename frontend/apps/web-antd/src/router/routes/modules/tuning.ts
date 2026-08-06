import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块（Phase D 单页整合）
 *
 * 对齐 UI/UX v6.1 §4.2 + PRD v6.1 §4.5 + 实现契约 v2.1
 * - 整定工作台 / 整定任务详情（单页 4 锚点）/ 整定知识库 / 效果统计
 *
 * Phase D（IA 重构）：原 3 页向导（model→algorithm→simulation）整合为
 * /tuning/detail 单页 + 4 锚点导航（①过程辨识 ②PID推荐 ③闭环仿真 ④方案确认）。
 * 旧 /tuning/flow/* 路由重定向到 /tuning/detail，兼容书签与 E2E。
 *
 * 角色权限（PRD §3）：
 * - ADMIN / IC_ENGINEER / EXPERT：全部
 * - PE_ENGINEER / SPONSOR：不可见
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
      icon: 'lucide:settings-2',
      order: 5,
      title: '整定',
    },
    name: 'Tune',
    path: '/tuning',
    children: [
      {
        name: 'TuningWorkbench',
        path: '/tuning/workbench',
        component: () => import('#/views/tuning/workbench.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:settings-2',
          title: '整定工作台',
        },
      },
      {
        name: 'TuningDetail',
        path: '/tuning/detail',
        component: () => import('#/views/tuning/detail.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '整定任务详情',
        },
      },
      {
        name: 'TuningKnowledgeBase',
        path: '/tuning/knowledge-base',
        component: () => import('#/views/tuning/knowledge-base.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:book-open',
          title: '整定知识库',
        },
      },
      {
        name: 'TuningStats',
        path: '/tuning/stats',
        component: () => import('#/views/tuning/stats.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:file-bar-chart',
          title: '效果统计',
        },
      },
      // ===== Phase D 旧路由兼容重定向 =====
      // /tuning/flow/* → /tuning/detail（保留 query 参数）
      {
        path: '/tuning/flow',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '整定流程',
        },
      },
      {
        path: '/tuning/flow/model',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '模型辨识',
        },
      },
      {
        path: '/tuning/flow/algorithm',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '整定算法',
        },
      },
      {
        path: '/tuning/flow/simulation',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '闭环仿真',
        },
      },
      // 原始旧路由（P1-019 前的路径）
      {
        name: 'TuningModelLegacy',
        path: '/tuning/model',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '模型辨识',
        },
      },
      {
        name: 'TuningAlgorithmLegacy',
        path: '/tuning/algorithm',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '整定算法',
        },
      },
      {
        name: 'TuningSimulationLegacy',
        path: '/tuning/simulation',
        redirect: (to) => ({ path: '/tuning/detail', query: to.query }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '闭环仿真',
        },
      },
    ],
  },
];

export default routes;
