import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断中心路由模块（v6.1 IA 调整：7 页面 → 5 页面 + 诊断任务）
 *
 * 对齐 UI/UX v6.1 §4.2 + PRD §4.4 + IDS v3.2 §2.4
 * - 诊断总览 / 诊断任务 / 诊断记录 / 诊断详情（隐藏） / 异常跟踪 / 诊断配置
 *
 * v6.1 IA 调整说明：
 * - 新增 DiagnosisOverview 作为诊断中心默认着陆页（替代原直接进入列表）
 * - 新增 DiagnosisTasks 页面（未归档诊断任务管理 + 触发诊断 + 结果查看 + 归档）
 * - 原 DiagnosisList 改造为 DiagnosisRecords（仅显示已归档数据，list.vue 保留便于回退）
 * - 移除 DiagnosisWaveform 路由（能力已合并入 DiagnosisDetail）
 * - 移除 DiagnosisABCompare 路由（能力已合并入 DiagnosisTracker 的 Drawer 模式）
 * - 移除 DiagnosisStatistics 路由（能力已合并入 DiagnosisOverview）
 * - 原 waveform.vue / ab-compare.vue / statistics.vue 文件保留，便于回退
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
      title: '诊断中心',
    },
    name: 'Diagnosis',
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
        name: 'DiagnosisTasks',
        path: '/diagnosis/tasks',
        component: () => import('#/views/diagnosis/tasks.vue'),
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
      {
        name: 'DiagnosisRecords',
        path: '/diagnosis/records',
        component: () => import('#/views/diagnosis/records.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:archive',
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
        path: '/diagnosis/visualization/:loopId?',
        component: () => import('#/views/diagnosis/visualization.vue'),
        meta: {
          authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:bar-chart-2',
          title: '诊断可视化',
        },
      },
      {
        name: 'DiagnosisTracker',
        path: '/diagnosis/tracker',
        component: () => import('#/views/diagnosis/tracker.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          icon: 'lucide:clipboard-check',
          title: '异常跟踪',
        },
      },
      {
        name: 'DiagnosisConfig',
        path: '/diagnosis/config',
        component: () => import('#/views/diagnosis/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings-2',
          title: '诊断配置',
        },
      },
    ],
  },
];

export default routes;
