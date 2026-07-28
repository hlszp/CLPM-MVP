import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块（Phase 2 已落地）
 *
 * 对齐 UI/UX v6.1 §4.2 + PRD v6.1 §4.5 + 实现契约 v2.1
 * - 整定工作台 / 模型辨识 / 整定算法 / 闭环仿真 / 效果统计
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部
 * - IC_ENGINEER：全部
 * - EXPERT：全部
 * - PE_ENGINEER / SPONSOR：不可见
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
      badge: 'Beta',
      badgeVariants: 'default',
      icon: 'lucide:settings-2',
      order: 5,
      title: '回路整定',
    },
    name: 'Tuning',
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
        name: 'TuningModel',
        path: '/tuning/model',
        component: () => import('#/views/tuning/model.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:git-branch',
          title: '模型辨识',
        },
      },
      {
        name: 'TuningAlgorithm',
        path: '/tuning/algorithm',
        component: () => import('#/views/tuning/algorithm.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:cpu',
          title: '整定算法',
        },
      },
      {
        name: 'TuningSimulation',
        path: '/tuning/simulation',
        component: () => import('#/views/tuning/simulation.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:play-circle',
          title: '闭环仿真',
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
    ],
  },
];

export default routes;
