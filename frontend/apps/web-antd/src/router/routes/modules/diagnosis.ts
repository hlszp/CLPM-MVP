import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断中心路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.4
 * - 诊断配置 / 诊断列表 / 波形查看 / 异常跟踪 / A/B 对比 / 诊断统计
 *
 * 角色权限（PRD §3）：
 * - ADMIN：全部（含配置）
 * - IC_ENGINEER：全部（含异常跟踪编辑）
 * - PE_ENGINEER：查看 + 异常跟踪
 * - SPONSOR：查看
 * - EXPERT：查看 + 异常跟踪
 */
const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:activity',
      order: 4,
      title: '诊断中心',
    },
    name: 'Diagnosis',
    path: '/diagnosis',
    children: [
      {
        name: 'DiagnosisConfig',
        path: '/diagnosis/config',
        component: () => import('#/views/diagnosis/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:sliders-horizontal',
          title: '诊断配置',
        },
      },
      {
        name: 'DiagnosisList',
        path: '/diagnosis/list',
        component: () => import('#/views/diagnosis/list.vue'),
        meta: {
          icon: 'lucide:activity',
          title: '诊断列表',
        },
      },
      {
        name: 'DiagnosisWaveform',
        path: '/diagnosis/waveform',
        component: () => import('#/views/diagnosis/waveform.vue'),
        meta: {
          icon: 'lucide:line-chart',
          title: '波形查看',
        },
      },
      {
        name: 'DiagnosisTracker',
        path: '/diagnosis/tracker',
        component: () => import('#/views/diagnosis/tracker.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
          icon: 'lucide:list-checks',
          title: '异常跟踪',
        },
      },
      {
        name: 'DiagnosisAbCompare',
        path: '/diagnosis/ab-compare',
        component: () => import('#/views/diagnosis/ab-compare.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:git-compare',
          title: 'A/B 对比',
        },
      },
      {
        name: 'DiagnosisStatistics',
        path: '/diagnosis/statistics',
        component: () => import('#/views/diagnosis/statistics.vue'),
        meta: {
          icon: 'lucide:file-bar-chart',
          title: '诊断统计',
        },
      },
    ],
  },
];

export default routes;
