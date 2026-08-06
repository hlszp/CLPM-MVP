import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断中心路由模块（v6.1 IA 调整：7 页面 → 5 页面 + 诊断任务）
 *
 * 对齐 UI/UX v6.1 §4.2 + PRD §4.4 + IDS v3.2 §2.4
 * - 诊断总览 / 诊断任务 / 诊断记录 / 诊断详情（隐藏） / 异常跟踪
 * - 诊断配置已迁入配置模块（/config/diagnosis），见 config.ts
 *
 * v6.1 IA 调整说明：
 * - 新增 DiagnosisOverview 作为诊断中心默认着陆页（替代原直接进入列表）
 * - 新增 DiagnosisTasks 页面（未归档诊断任务管理 + 触发诊断 + 结果查看 + 归档）
 * - 原 DiagnosisList 改造为 DiagnosisRecords（仅显示已归档数据）
 * - 移除 DiagnosisWaveform 路由（能力已合并入 DiagnosisDetail）
 * - 移除 DiagnosisABCompare 路由（能力已合并入 DiagnosisTracker 的 Drawer 模式）
 * - 移除 DiagnosisStatistics 路由（能力已合并入 DiagnosisOverview）
 * - 原 ab-compare.vue 文件保留，便于回退（list.vue/waveform.vue/statistics.vue 已清理）
 *
 * V62-P1-018/020 IA 减负：
 * - DiagnosisTasks → task-center.vue（Tabs 合并进行中/历史）
 * - DiagnosisRecords → redirect /diagnosis/tasks?tab=history（hideInMenu，兼容旧书签）
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部（含异常跟踪编辑）
 * - PE_ENGINEER：查看 + 异常跟踪
 * - SPONSOR：仅总览与诊断任务/记录汇总，不可进入单回路诊断详情
 * - EXPERT：查看 + 异常跟踪
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:stethoscope',
      order: 4,
      title: '诊断',
    },
    name: 'Diagnose',
    path: '/diagnosis',
    redirect: '/diagnosis/overview',
    children: [
      {
        name: 'DiagnosisOverview',
        path: '/diagnosis/overview',
        component: () => import('#/views/diagnosis/overview.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:layout-dashboard',
          title: '诊断总览',
        },
      },
      {
        name: 'DiagnosisLoopAnalysis',
        path: '/diagnosis/loop-analysis',
        component: () => import('#/views/diagnosis/loop-analysis.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:flask-conical',
          title: '回路分析',
        },
      },
      {
        name: 'DiagnosisTasks',
        path: '/diagnosis/tasks',
        component: () => import('#/views/diagnosis/task-center.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:clipboard-list',
          title: '诊断任务',
        },
      },
      // V62-P1-018/020: 诊断记录合并到诊断任务 Tabs，旧路由重定向兼容
      {
        name: 'DiagnosisRecords',
        path: '/diagnosis/records',
        redirect: '/diagnosis/tasks?tab=history',
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          hideInMenu: true,
          title: '诊断记录',
        },
      },
      {
        name: 'DiagnosisDetail',
        path: '/diagnosis/detail/:loopId',
        component: () => import('#/views/diagnosis/detail.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '诊断详情',
        },
      },
      {
        name: 'DiagnosisVisualization',
        path: '/diagnosis/visualization',
        component: () => import('#/views/diagnosis/visualization.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true, // F12: 功能已迁移到 loop-analysis.vue，从菜单隐藏
          icon: 'lucide:bar-chart-2',
          title: '诊断可视化',
        },
      },
      {
        name: 'DiagnosisTracker',
        path: '/diagnosis/tracker',
        component: () => import('#/views/diagnosis/tracker-page.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          icon: 'lucide:clipboard-check',
          title: '异常跟踪',
        },
      },
      // 诊断配置已迁入配置模块（/config/diagnosis），legacy redirect 由 config.ts 接管
    ],
  },
];

export default routes;
