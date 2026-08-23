import type { RouteRecordRaw } from 'vue-router';

/**
 * 处置路由模块（批次 C 五段式入口，2026-08-23）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.1
 * 五段式（views 不物理拆页，路由别名 + meta.handlingView 预设，由 workbench.vue 消费）：
 * - 诊断建议 /handling/suggestions  建议审核 Tab
 * - 处置任务 /handling/tasks        工单 Tab + status 预设 PENDING,REOPENED（排程/下达；作废段需手动勾选 CANCELLED）
 * - 处置工单 /handling/orders       工单 Tab + status 预设 EXECUTING,VERIFYING（作业/验证）
 * - 处置档案 /handling/archive      回路维度聚合 + 跨 run 处置全史
 * - 处置统计 隐藏 redirect → /reports/handling（不进菜单）
 *
 * 深链接契约（与后端聚合动作产出一致）：
 * - 关注队列 HANDLING 动作 target：/handling/orders?focus={orderId}
 * - 诊断「去处置」：/handling/suggestions?focus={suggestionId}
 *
 * 旧路由兼容：
 * - /handling → /handling/suggestions（透传 query）
 * - /handling/workbench → 按 tab query 分流到对应新路由（透传 focus 等其余 query，
 *   保护既有书签；旧 focus=建议id 直达 orders 的存量链接由 workbench 404 回落兜底）
 *
 * 角色权限（§7）：
 * - 各页查看：全部登录角色（SPONSOR / EXPERT 只读）
 * - 流转操作：ADMIN / IC_ENGINEER / PE_ENGINEER（后端校验）
 */
const HANDLING_AUTHORITY = [
  'ADMIN',
  'EXPERT',
  'IC_ENGINEER',
  'PE_ENGINEER',
  'SPONSOR',
];

const routes: RouteRecordRaw[] = [
  {
    name: 'Handling',
    path: '/handling',
    redirect: (to) => ({ path: '/handling/suggestions', query: { ...to.query } }),
    meta: {
      authority: HANDLING_AUTHORITY,
      icon: 'lucide:clipboard-check',
      order: 5,
      title: '处置',
      module: 'handling',
    },
    children: [
      {
        name: 'HandlingSuggestions',
        path: '/handling/suggestions',
        component: () => import('#/views/handling/workbench.vue'),
        meta: {
          authority: HANDLING_AUTHORITY,
          icon: 'lucide:lightbulb',
          order: 1,
          title: '诊断建议',
          handlingView: 'suggestions',
        },
      },
      {
        name: 'HandlingTasks',
        path: '/handling/tasks',
        component: () => import('#/views/handling/workbench.vue'),
        meta: {
          authority: HANDLING_AUTHORITY,
          icon: 'lucide:list-todo',
          order: 2,
          title: '处置任务',
          handlingView: 'tasks',
        },
      },
      {
        name: 'HandlingOrders',
        path: '/handling/orders',
        component: () => import('#/views/handling/workbench.vue'),
        meta: {
          authority: HANDLING_AUTHORITY,
          icon: 'lucide:clipboard-list',
          order: 3,
          title: '处置工单',
          handlingView: 'orders',
        },
      },
      {
        name: 'HandlingArchive',
        path: '/handling/archive',
        component: () => import('#/views/handling/archive.vue'),
        meta: {
          authority: HANDLING_AUTHORITY,
          icon: 'lucide:archive',
          order: 4,
          title: '处置档案',
        },
      },
      {
        // 旧工作台入口（隐藏）：按 tab query 分流到新路由，透传 focus 等其余 query
        name: 'HandlingWorkbench',
        path: '/handling/workbench',
        redirect: (to) => {
          const query = { ...to.query };
          delete query.tab;
          const tab = to.query.tab;
          const path =
            tab === 'orders'
              ? '/handling/orders'
              : (tab === 'tasks'
                  ? '/handling/tasks'
                  : '/handling/suggestions');
          return { path, query };
        },
        meta: {
          authority: HANDLING_AUTHORITY,
          hideInMenu: true,
          title: '处置工作台',
        },
      },
      {
        // 处置统计已迁入统计报告-处置报告（IA 优化 P0，2026-08-22）
        name: 'HandlingStatistics',
        path: '/handling/statistics',
        redirect: '/reports/handling',
        meta: {
          authority: HANDLING_AUTHORITY,
          hideInMenu: true,
          title: '处置报告',
        },
      },
    ],
  },
];

export default routes;
