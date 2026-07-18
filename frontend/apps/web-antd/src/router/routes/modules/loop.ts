import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路管理路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.2
 * - 回路管理整合页（FE-01）：工厂树 + 回路表格 + 编辑抽屉
 * - 测点清单
 * - 回路监控 / 回路详情（隐藏）
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部
 * - IC_ENGINEER：全部
 * - PE_ENGINEER：查看
 * - SPONSOR / EXPERT：不可见
 *
 * FE-04：loop/factory 与 loop/ledger 已废弃，重定向到 /loop/manage
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
      icon: 'lucide:network',
      order: 2,
      title: '回路管理',
    },
    name: 'Loop',
    path: '/loop',
    children: [
      {
        name: 'LoopAasSync',
        path: '/loop/aas-sync',
        component: () => import('#/views/loop/aas.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:refresh-cw',
          order: 1,
          title: '链路配置',
        },
      },
      {
        name: 'TagList',
        path: '/tag/list',
        component: () => import('#/views/tag/list.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:list',
          order: 2,
          title: '测点配置',
        },
      },
      {
        name: 'LoopManage',
        path: '/loop/manage',
        component: () => import('#/views/loop/manage.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:network',
          order: 3,
          title: '回路配置',
        },
      },
      // FE-04：废弃 loop/factory，重定向到 /loop/manage
      {
        name: 'LoopFactory',
        path: '/loop/factory',
        redirect: '/loop/manage',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '工厂模型',
        },
      },
      // FE-04：废弃 loop/ledger，重定向到 /loop/manage
      {
        name: 'LoopLedger',
        path: '/loop/ledger',
        redirect: '/loop/manage',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '回路台账',
        },
      },
      {
        name: 'LoopMonitor',
        path: '/loop/monitor',
        component: () => import('#/views/loop/monitor.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:gauge',
          order: 4,
          title: '回路监控',
        },
      },
      {
        name: 'LoopData',
        path: '/loop/data',
        component: () => import('#/views/loop/data.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:database',
          order: 5,
          title: '数据管理',
        },
      },
      {
        name: 'LoopDetail',
        path: '/loop/detail/:id',
        component: () => import('#/views/loop/detail.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          hideInTab: false,
          activePath: '/loop/monitor',
          title: '回路详情',
        },
      },
    ],
  },
];

export default routes;
