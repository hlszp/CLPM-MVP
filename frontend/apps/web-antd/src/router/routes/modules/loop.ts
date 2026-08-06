import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路路由模块（IA 重构 Phase A·实体轴占位）
 *
 * 对齐 IA 重构方案 §3.2/§4.1。
 * Phase A：组 redirect 到 /loop/monitor（防空菜单），/loop/detail/:id 靠 loopId 跳转进入。
 * Phase B：升级为 /loop/workbench 6 Tab 工作台（概览/评估/诊断/整定/效果对比/时间线）。
 *
 * 注：原 loop.ts 下的结构性配置子路由（aas-sync/tag/manage/factory/ledger/data）
 *    已迁入 config.ts 为 /config/* 新路径 + legacy redirect。
 *
 * 角色权限（PRD §3）：
 * - ADMIN / IC_ENGINEER / EXPERT：可编辑
 * - PE_ENGINEER：只读
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Loop',
    path: '/loop',
    redirect: '/loop/monitor',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
      icon: 'lucide:network',
      order: 2,
      title: '回路',
    },
    children: [
      {
        name: 'LoopDetail',
        path: '/loop/detail/:id',
        component: () => import('#/views/loop/detail.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          hideInTab: false,
          // 详情页打开时高亮"回路"菜单（而非 /loop/monitor 所在的"监控"）
          activePath: '/loop',
          title: '回路详情',
        },
      },
    ],
  },
];

export default routes;
