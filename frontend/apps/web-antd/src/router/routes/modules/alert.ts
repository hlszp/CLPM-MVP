import type { RouteRecordRaw } from 'vue-router';

/**
 * 智能预警规则引擎路由模块（PRD v6.2 §4.4.6）
 *
 * 独立于诊断中心的实时预警系统：
 * - 预警事件：日常运维操作（查看/确认/处置/误报标记/归档），面向所有工程师
 * - 预警规则：结构性配置（规则定义/订阅/启停），仅 ADMIN
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
      icon: 'lucide:bell-ring',
      order: 5,
      title: '预警',
    },
    name: 'Alert',
    path: '/alert',
    redirect: '/alert/events',
    children: [
      {
        name: 'AlertEvents',
        path: '/alert/events',
        component: () => import('#/views/alert/events.vue'),
        meta: {
          authority: [
            'ADMIN',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
            'EXPERT',
          ],
          icon: 'lucide:bell',
          title: '预警事件',
        },
      },
      {
        name: 'AlertRules',
        path: '/alert/rules',
        component: () => import('#/views/alert/rules.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:shield-alert',
          title: '预警规则',
        },
      },
    ],
  },
];

export default routes;
