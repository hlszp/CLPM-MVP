import type { RouteRecordRaw } from 'vue-router';

/**
 * 评估任务路由模块（归入性能评估执行体系）
 *
 * 对齐 UIUX 改造方案：任务能力与性能评估引擎规则、指标配置、执行记录强关联，
 * 保留 `/tasks/*` 路由兼容详情跳转，但菜单归属后续收敛到性能评估。
 *
 * 角色权限（PRD §3 + UI/UX §6.8.5）：
 * - 查看标准评估任务：全角色
 * - 查看自定义评估任务：创建人 + ADMIN
 * - 新建/取消自定义评估任务：IC_ENGINEER / PE_ENGINEER / ADMIN
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
      icon: 'lucide:list-checks',
      order: 3.5,
      title: '评估任务',
    },
    name: 'Task',
    path: '/tasks',
    redirect: '/tasks/list',
    children: [
      {
        name: 'TaskList',
        path: '/tasks/list',
        component: () => import('#/views/task/list.vue'),
        meta: {
          icon: 'lucide:list-checks',
          title: '任务列表',
        },
      },
      {
        name: 'TaskDetail',
        path: '/tasks/:taskId',
        component: () => import('#/views/task/detail.vue'),
        meta: {
          hideInMenu: true,
          title: '任务详情',
        },
      },
    ],
  },
];

export default routes;
