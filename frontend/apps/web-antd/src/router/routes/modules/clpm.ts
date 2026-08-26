import type { RouteRecordRaw } from 'vue-router';

/**
 * CLPM 工作台 v2.0 路由模块（统一框架页 · 单路由无子路由）
 *
 * /workbench 单路由直显 5 Tabs（button + v-show 组件切换，非子路由）。
 * 范围(scope)+ 时间窗口(window) 跨 Tab 共享，由 stores/workbench.ts 持有。
 * 一级菜单"工作台"无二级菜单，点击 Tab 只切换内容区，不整页刷新。
 *
 * 不设 meta.module：工作台为跨模块总览入口（'clpm' 非业务 module key，
 * 8 业务 key 不含 clpm），需始终可见，故不进模块过滤（filterTreeByModules
 * 对无 meta.module 的路由保留）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Workbench',
    path: '/workbench',
    component: () => import('#/views/workbench/index.vue'),
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
  },
];

export default routes;
