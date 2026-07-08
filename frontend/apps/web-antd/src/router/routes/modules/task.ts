import type { RouteRecordRaw } from 'vue-router';

/**
 * 评估任务路由模块（已并入性能评估模块）
 *
 * 保留 `/tasks/*` 路由兼容详情跳转，但菜单归属已收敛到性能评估模块下的"评估任务"子菜单。
 * 标准评估任务列表和任务策略配置已迁移至 `/metric/tasks`。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'TaskDetail',
    path: '/tasks/:taskId',
    component: () => import('#/views/task/detail.vue'),
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
      hideInMenu: true,
      title: '任务详情',
    },
  },
];

export default routes;
