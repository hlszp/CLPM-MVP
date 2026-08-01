import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块（Phase 2 已落地 + v6.2 P1-019 stepper 合并）
 *
 * 对齐 UI/UX v6.1 §4.2 + PRD v6.1 §4.5 + 实现契约 v2.1
 * - 整定工作台 / 整定流程（stepper）/ 效果统计
 *
 * v6.2 P1-019：model→algorithm→simulation 三页合并为 /tuning/flow 嵌套 stepper
 * - /tuning/flow/{model,algorithm,simulation} 子路由复用现有三页组件
 * - 旧 /tuning/{model,algorithm,simulation} 重定向到 flow 子路由 + hideInMenu（兼容书签）
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
        name: 'TuningFlow',
        path: '/tuning/flow',
        redirect: '/tuning/flow/model',
        component: () => import('#/views/tuning/flow.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:git-branch',
          title: '整定流程',
        },
        children: [
          {
            name: 'TuningFlowModel',
            path: '/tuning/flow/model',
            component: () => import('#/views/tuning/model.vue'),
            meta: {
              authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
              hideInMenu: true,
              title: '模型辨识',
            },
          },
          {
            name: 'TuningFlowAlgorithm',
            path: '/tuning/flow/algorithm',
            component: () => import('#/views/tuning/algorithm.vue'),
            meta: {
              authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
              hideInMenu: true,
              title: '整定算法',
            },
          },
          {
            name: 'TuningFlowSimulation',
            path: '/tuning/flow/simulation',
            component: () => import('#/views/tuning/simulation.vue'),
            meta: {
              authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
              hideInMenu: true,
              title: '闭环仿真',
            },
          },
        ],
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
      // 旧路由兼容重定向（P1-019，至少保留一个版本，与 P1-020 一致）
      {
        name: 'TuningModelLegacy',
        path: '/tuning/model',
        redirect: '/tuning/flow/model',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '模型辨识',
        },
      },
      {
        name: 'TuningAlgorithmLegacy',
        path: '/tuning/algorithm',
        redirect: '/tuning/flow/algorithm',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '整定算法',
        },
      },
      {
        name: 'TuningSimulationLegacy',
        path: '/tuning/simulation',
        redirect: '/tuning/flow/simulation',
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
