import type { RouteRecordRaw } from 'vue-router';

/**
 * 智能预警规则引擎旧路由兼容模块（PRD v6.2 §4.4.6）
 *
 * IA 收敛后：
 * - 预警事件归入监控，作为运行结果处理；
 * - 预警规则归入配置，作为结构性配置维护；
 * - 本文件只负责旧书签/旧 E2E 的重定向，API 路径不变。
 *
 * 角色权限（PRD §3 + IDS v2.7）：
 * - ADMIN：全部（含规则配置、全局开关）
 * - IC_ENGINEER：全部操作（含确认/处置/抑制，不含规则配置）
 * - PE_ENGINEER/SPONSOR/EXPERT：仅查看事件
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT'],
      hideInMenu: true,
      title: '预警',
    },
    name: 'Alert',
    path: '/alert',
    redirect: '/monitor/alerts',
    children: [
      {
        name: 'AlertEvents',
        path: '/alert/events',
        redirect: '/monitor/alerts',
        meta: {
          authority: [
            'ADMIN',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
            'EXPERT',
          ],
          hideInMenu: true,
          title: '预警事件',
        },
      },
      {
        name: 'AlertRules',
        path: '/alert/rules',
        redirect: '/config/alert-rules',
        meta: {
          authority: ['ADMIN'],
          hideInMenu: true,
          title: '预警规则',
        },
      },
    ],
  },
];

export default routes;
