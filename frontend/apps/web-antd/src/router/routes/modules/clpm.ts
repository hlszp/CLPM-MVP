import type { RouteRecordRaw } from 'vue-router';

/**
 * 旧「运维工作台」退役兼容层（2026-10-05 驾驶舱整合裁决 D5/D6）
 *
 * 运维工作台五 Tab 已被驾驶舱六页签吸收（性能/诊断/整定/处置四页迁移
 * 至 /cockpit/*），本模块仅保留旧书签/深链重定向。全局总览入口=驾驶舱
 * /cockpit（cockpit.ts，order 0）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Workbench',
    path: '/workbench',
    redirect: '/cockpit',
    meta: {
      authority: [
        'ADMIN',
        'EXPERT',
        'IC_ENGINEER',
        'PE_ENGINEER',
        'SPONSOR',
      ],
      hideInMenu: true,
      title: '运维工作台（已并入驾驶舱）',
    },
  },
];

export default routes;
