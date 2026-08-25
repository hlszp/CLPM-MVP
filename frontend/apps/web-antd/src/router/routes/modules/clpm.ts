import type { RouteRecordRaw } from 'vue-router';

/**
 * CLPM 工作台 v2.0 路由模块（方案 §4.2 第 5 条：新增模块，原 8 菜单路由不改）
 *
 * 单屏 100vh 工作台 + 5 Tab 子路由（系统总览/性能评估/回路诊断/参数整定/问题处置）。
 * 范围(scope)+ 时间窗口(window) 跨 Tab 共享，由 stores/workbench.ts 持有。
 *
 * 不设 meta.module：工作台为跨模块总览入口（'clpm' 非业务 module key，
 * 8 业务 key 不含 clpm），需始终可见，故不进模块过滤（filterTreeByModules
 * 对无 meta.module 的路由保留）。
 *
 * M1：骨架可访问；M3：order=0 正式替换首页为工作台。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Workbench',
    path: '/workbench',
    component: () => import('#/views/workbench/index.vue'),
    redirect: '/workbench/overview',
    meta: {
      // 全角色可见（工作台驾驶舱）
      authority: [
        'ADMIN',
        'EXPERT',
        'IC_ENGINEER',
        'PE_ENGINEER',
        'SPONSOR',
      ],
      icon: 'lucide:layout-dashboard',
      order: 0,
      title: '工作台',
    },
    children: [
      {
        name: 'WorkbenchOverview',
        path: '/workbench/overview',
        component: () => import('#/views/workbench/tabs/overview.vue'),
        meta: {
          icon: 'lucide:gauge',
          title: '系统总览',
        },
      },
      {
        name: 'WorkbenchAssessment',
        path: '/workbench/assessment',
        component: () => import('#/views/workbench/tabs/assessment.vue'),
        meta: {
          icon: 'lucide:bar-chart-3',
          title: '性能评估',
        },
      },
      {
        name: 'WorkbenchDiagnosis',
        path: '/workbench/diagnosis',
        component: () => import('#/views/workbench/tabs/diagnosis.vue'),
        meta: {
          icon: 'lucide:stethoscope',
          title: '回路诊断',
        },
      },
      {
        name: 'WorkbenchTuning',
        path: '/workbench/tuning',
        component: () => import('#/views/workbench/tabs/tuning.vue'),
        meta: {
          icon: 'lucide:sliders-horizontal',
          title: '参数整定',
        },
      },
      {
        name: 'WorkbenchHandling',
        path: '/workbench/handling',
        component: () => import('#/views/workbench/tabs/handling.vue'),
        meta: {
          icon: 'lucide:wrench',
          title: '问题处置',
        },
      },
    ],
  },
];

export default routes;
