import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路旧路由兼容模块（IA 收敛后不再作为一级菜单）
 *
 * - /loop              → redirect /monitor/loop-workbench
 * - /loop/workbench    → redirect /monitor/loop-workbench
 * - /loop/detail/:id   → redirect /monitor/loop-workbench?loopId=:id
 *
 * 注：/loop/monitor 由 monitor.ts 保留为隐藏的高密度实时表；
 *    监控模块的主入口是 /monitor/loop-workbench。
 *    原结构性配置子路由（aas-sync/tag/manage/factory/ledger/data）
 *    已迁入 config.ts 为 /config/* 新路径 + legacy redirect。
 *
 * 角色权限（PRD §3 / 实现契约 §5）：
 * - ADMIN / IC_ENGINEER / EXPERT：可编辑
 * - PE_ENGINEER：只读
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'LoopLegacy',
    path: '/loop',
    redirect: (to) => ({ path: '/monitor/loop-workbench', query: to.query }),
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
      hideInMenu: true,
      title: '回路',
    },
    children: [
      {
        name: 'LoopWorkbenchLegacy',
        path: '/loop/workbench',
        redirect: (to) => ({
          path: '/monitor/loop-workbench',
          query: to.query,
        }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '回路工作台',
        },
      },
      {
        name: 'LoopDetail',
        path: '/loop/detail/:id',
        // 兼容旧书签 / monitor 行点击 / E2E：重定向到工作台并预选回路
        redirect: (to) => ({
          path: '/monitor/loop-workbench',
          query: { loopId: String(to.params.id) },
        }),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          title: '回路详情',
        },
      },
    ],
  },
];

export default routes;
