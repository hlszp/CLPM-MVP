import type { RouteRecordRaw } from 'vue-router';

/**
 * 系统管理路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + §5.2 + PRD §4.6
 * - 用户管理 / 审计日志 / 权限矩阵 / 字典管理
 * - 自动报表已迁入「统计报告-订阅配置」（/reports/subscription），旧路径保留 redirect
 *
 * 角色权限（PRD §3 + 实现契约 §5 + UI/UX §4.2）：
 * - 用户管理 / 审计日志 / 算法参数配置：仅 ADMIN
 * - 权限矩阵：所有角色可查看
 * - 订阅配置（原自动报表）：仅 ADMIN（后端 reports.py 全端点仅 ADMIN，2026-07-28 收紧对齐）
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER'],
      icon: 'lucide:settings',
      order: 8,
      title: '系统',
      module: 'system',
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
        // 通用字典项（可配置枚举：测点类型等），业务下拉与导入校验自动生效
        name: 'SystemDict',
        path: '/system/dict',
        component: () => import('#/views/system/dict.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:book-marked',
          title: '字典管理',
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
        name: 'SystemModules',
        path: '/system/modules',
        component: () => import('#/views/system/modules.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:blocks',
          title: '模块管理',
        },
      },
      {
        // 自动报表已迁入统计报告-订阅配置（IA 优化 P0，2026-08-22）
        name: 'SystemReports',
        path: '/system/reports',
        redirect: '/reports/subscription',
        meta: {
          // 实现契约 §5：后端 reports.py 全端点仅 ADMIN，前端同步收紧
          authority: ['ADMIN'],
          hideInMenu: true,
          title: '订阅配置',
        },
      },
      // MVP 精简：已屏蔽诊断模块 → 移除「LLM 配置」（自然语言诊断解读服务配置）
      // {
      //   // P3-04：LLM 配置（自然语言诊断解读服务配置）
      //   name: 'SystemLlmConfig',
      //   path: '/system/llm-config',
      //   component: () => import('#/views/system/llm-config.vue'),
      //   meta: {
      //     authority: ['ADMIN'],
      //     icon: 'lucide:bot',
      //     title: 'LLM 配置',
      //   },
      // },
      {
        // 已迁移至配置模块-指标配置（KPI 算法参数 Tab）
        // 保留重定向以兼容旧 URL 和书签
        name: 'SystemAlgorithmParamsDeprecated',
        path: '/system/algorithm-params',
        redirect: '/config/metric',
        meta: {
          authority: ['ADMIN'],
          hideInMenu: true,
          title: '算法参数配置',
        },
      },
      // PID 结构模板 redirect 已由 config.ts 的 LegacySystemPidTemplate 接管（→ /config/link）
    ],
  },
];

export default routes;
