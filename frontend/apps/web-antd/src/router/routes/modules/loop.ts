import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路路由模块（IA 重构 Phase B·§4.1 实体轴）
 *
 * 回路菜单主页 = 回路工作台（单回路 360° 一站式处置，6 Tab）。
 * - /loop              → redirect /loop/workbench
 * - /loop/workbench    → 工作台主页（菜单可见，支持 ?loopId= 预选）
 * - /loop/detail/:id   → redirect /loop/workbench?loopId=:id（兼容旧书签/E2E/monitor 行点击）
 *
 * 注：/loop/monitor（回路实时列表）仍暂留监控组（monitor.ts），
 *    Phase B 工作台上线后作为实体轴主页，monitor 维持运行驾驶舱定位。
 *    原结构性配置子路由（aas-sync/tag/manage/factory/ledger/data）
 *    已迁入 config.ts 为 /config/* 新路径 + legacy redirect。
 *
 * 角色权限（PRD §3 / 实现契约 §5）：
 * - ADMIN / IC_ENGINEER / EXPERT：可编辑
 * - PE_ENGINEER：只读
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Loop',
    path: '/loop',
    redirect: '/loop/workbench',
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
      icon: 'lucide:network',
      order: 2,
      title: '回路',
    },
    children: [
      {
        name: 'LoopWorkbench',
        path: '/loop/workbench',
        component: () => import('#/views/loop/workbench.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          icon: 'lucide:layout-panel-top',
          title: '回路工作台',
          // 工作台左侧切换回路仅更新 URL query（?loopId=），不应新增 tab/面包屑。
          // fullPathKey:false 使 tab key 退化为 route.path，query 变化复用同一 tab。
          fullPathKey: false,
        },
      },
      {
        name: 'LoopDetail',
        path: '/loop/detail/:id',
        // 兼容旧书签 / monitor 行点击 / E2E：重定向到工作台并预选回路
        redirect: (to) => ({
          path: '/loop/workbench',
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
