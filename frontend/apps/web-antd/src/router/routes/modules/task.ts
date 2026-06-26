import type { RouteRecordRaw } from 'vue-router';

/**
 * 任务管理路由模块（v5.0 新增）
 *
 * 对齐 UI/UX §6.8 + IDS v3.2 §2.7.6 + PRD §4.3.7
 * - 任务列表（标准任务 + 自定义任务双轨 Tab）
 * - 任务详情（进度 / 阶段时间线 / 错误信息 / 通知）
 *
 * 角色权限（PRD §3 + UI/UX §6.8.5）：
 * - 查看标准任务：全角色
 * - 查看自定义任务：创建人 + ADMIN
 * - 新建/取消自定义任务：IC_ENGINEER / PE_ENGINEER / ADMIN
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
      icon: 'lucide:list-checks',
      order: 3.5,
      title: '任务管理',
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
