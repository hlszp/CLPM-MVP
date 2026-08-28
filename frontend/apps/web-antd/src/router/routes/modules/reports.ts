import type { RouteRecordRaw } from 'vue-router';

/**
 * 统计报告路由模块（IA 优化 P0，2026-08-22）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §二
 * 跨域报表与管理决策中心，统一承载绩效/诊断/处置/收益报告的查看、导出与订阅。
 *
 * 二级菜单（8 个，P1 基座补域 2026-08-28：基座在前、闭环在后，与成熟度分层一致）：
 * - 管理总览 /reports/overview     全角色，S1~S3 自适应
 * - 绩效报告 /reports/performance   ADMIN/IC/PE/SPONSOR（由 /metric/kpi-report 迁入）
 * - 数据质量 /reports/data-quality  全角色（P1 新增，基础模块数据自持）
 * - 预警统计 /reports/alert-statistics 全角色（P1 新增，基础模块数据自持）
 * - 诊断报告 /reports/diagnosis     ADMIN/IC/PE/EXPERT/SPONSOR（新建）
 * - 处置报告 /reports/handling      全角色（由 /handling/statistics 迁入）
 * - 收益报告 /reports/benefit       ADMIN/IC/PE/SPONSOR（新建，仅技术指标）
 * - 订阅配置 /reports/subscription  ADMIN（由 /system/reports 迁入，原"自动报表"改名）
 *
 * 所有路由 meta.module='reports'，为 P1 模块热插拔准备。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Reports',
    path: '/reports',
    redirect: '/reports/overview',
    meta: {
      // 父路由 authority 取子路由并集，避免 IC/PE/SPONSOR 看不到菜单
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:file-bar-chart-2',
      order: 6,
      title: '报告',
      module: 'reports',
    },
    children: [
      {
        name: 'ReportsOverview',
        path: '/reports/overview',
        component: () => import('#/views/reports/overview.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:layout-dashboard',
          title: '管理总览',
          module: 'reports',
        },
      },
      {
        name: 'ReportsPerformance',
        path: '/reports/performance',
        component: () => import('#/views/reports/performance.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:gauge',
          title: '绩效报告',
          module: 'reports',
        },
      },
      {
        name: 'ReportsDataQuality',
        path: '/reports/data-quality',
        component: () => import('#/views/reports/data-quality.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:heart-pulse',
          title: '数据质量',
          module: 'reports',
        },
      },
      {
        name: 'ReportsAlertStatistics',
        path: '/reports/alert-statistics',
        component: () => import('#/views/reports/alert-statistics.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:bell-ring',
          title: '预警统计',
          module: 'reports',
        },
      },
      {
        name: 'ReportsDiagnosis',
        path: '/reports/diagnosis',
        component: () => import('#/views/reports/diagnosis.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:stethoscope',
          title: '诊断报告',
          module: 'reports',
        },
      },
      {
        name: 'ReportsHandling',
        path: '/reports/handling',
        component: () => import('#/views/reports/handling.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:clipboard-check',
          title: '处置报告',
          module: 'reports',
        },
      },
      {
        name: 'ReportsBenefit',
        path: '/reports/benefit',
        component: () => import('#/views/reports/benefit.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
          icon: 'lucide:trending-up',
          title: '收益报告',
          module: 'reports',
        },
      },
      {
        name: 'ReportsSubscription',
        path: '/reports/subscription',
        component: () => import('#/views/reports/subscription.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:bell-dot',
          title: '订阅配置',
          module: 'reports',
        },
      },
    ],
  },
];

export default routes;
