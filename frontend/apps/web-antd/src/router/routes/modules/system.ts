import type { RouteRecordRaw } from 'vue-router';

/**
 * 系统管理路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + §5.2 + PRD §4.6
 * - 用户管理 / 审计日志 / 权限矩阵 / 自动报表
 *
 * 角色权限（PRD §3 + UI/UX §5.2）：
 * - 用户管理 / 审计日志：仅 ADMIN
 * - 权限矩阵：所有角色可查看
 * - 自动报表：ADMIN + IC_ENGINEER
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER'],
      icon: 'lucide:settings',
      order: 6,
      title: '系统管理',
    },
    name: 'System',
    path: '/system',
    children: [
      {
        name: 'SystemUsers',
        path: '/system/users',
        component: () => import('#/views/system/users.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:users',
          title: '用户管理',
        },
      },
      {
        name: 'SystemAudit',
        path: '/system/audit',
        component: () => import('#/views/system/audit.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:scroll-text',
          title: '审计日志',
        },
      },
      {
        name: 'SystemPermissions',
        path: '/system/permissions',
        component: () => import('#/views/system/permissions.vue'),
        meta: {
          icon: 'lucide:shield-check',
          title: '权限矩阵',
        },
      },
      {
        name: 'SystemReports',
        path: '/system/reports',
        component: () => import('#/views/system/reports.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER'],
          icon: 'lucide:file-text',
          title: '自动报表',
        },
      },
      {
        name: 'SystemAlgorithmParams',
        path: '/system/algorithm-params',
        component: () => import('#/views/system/algorithm-params/index.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:sliders-horizontal',
          title: '算法参数配置',
        },
      },
      {
        name: 'SystemPidTemplate',
        path: '/system/pid-template',
        component: () => import('#/views/system/pid-template/index.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings-2',
          title: 'PID 结构模板',
        },
      },
    ],
  },
];

export default routes;
