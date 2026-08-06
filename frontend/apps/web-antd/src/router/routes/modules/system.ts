import type { RouteRecordRaw } from 'vue-router';

/**
 * 系统管理路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + §5.2 + PRD §4.6
 * - 用户管理 / 审计日志 / 权限矩阵 / 自动报表
 *
 * 角色权限（PRD §3 + 实现契约 §5 + UI/UX §4.2）：
 * - 用户管理 / 审计日志 / 算法参数配置：仅 ADMIN
 * - 权限矩阵：所有角色可查看
 * - 自动报表：仅 ADMIN（后端 reports.py 全端点仅 ADMIN，2026-07-28 收紧对齐）
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
          // 实现契约 §5：后端 reports.py 全端点仅 ADMIN，前端同步收紧
          authority: ['ADMIN'],
          icon: 'lucide:file-text',
          title: '自动报表',
        },
      },
      {
        // P3-04：LLM 配置（自然语言诊断解读服务配置）
        name: 'SystemLlmConfig',
        path: '/system/llm-config',
        component: () => import('#/views/system/llm-config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:bot',
          title: 'LLM 配置',
        },
      },
      {
        // 已迁移至性能评估-指标配置（KPI 算法参数 Tab）
        // 保留重定向以兼容旧 URL 和书签
        name: 'SystemAlgorithmParamsDeprecated',
        path: '/system/algorithm-params',
        redirect: '/metric/config',
        meta: {
          authority: ['ADMIN'],
          hideInMenu: true,
          title: '算法参数配置',
        },
      },
      {
        name: 'SystemPidTemplateDeprecated',
        path: '/system/pid-template',
        redirect: '/loop/aas-sync',
        meta: {
          authority: ['ADMIN'],
          hideInMenu: true,
          title: 'PID 结构模板',
        },
      },
    ],
  },
];

export default routes;
