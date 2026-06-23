import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断中心路由模块
 *
 * 对齐 UI/UX v4.1 §4.2 + PRD §4.4 + IDS v3.2 §2.4
 * - 诊断配置 / 诊断列表 / 诊断详情 / 波形查看 / 异常跟踪 / A/B 对比 / 诊断统计
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
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:stethoscope',
      order: 4,
      title: '诊断中心',
    },
    name: 'Diagnosis',
    path: '/diagnosis',
    children: [
      {
        name: 'DiagnosisList',
        path: '/diagnosis/list',
        component: () => import('#/views/diagnosis/list.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:list',
          title: '诊断列表',
        },
      },
      {
        name: 'DiagnosisDetail',
        path: '/diagnosis/detail/:loopId',
        component: () => import('#/views/diagnosis/detail.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          hideInMenu: true,
          title: '诊断详情',
        },
      },
      {
        name: 'DiagnosisWaveform',
        path: '/diagnosis/waveform',
        component: () => import('#/views/diagnosis/waveform.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:activity',
          title: '波形分析',
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
        name: 'DiagnosisABCompare',
        path: '/diagnosis/ab-compare',
        component: () => import('#/views/diagnosis/ab-compare.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          hideInMenu: true,
          icon: 'lucide:git-compare',
          title: 'A/B 对比',
        },
      },
      {
        name: 'DiagnosisStatistics',
        path: '/diagnosis/statistics',
        component: () => import('#/views/diagnosis/statistics.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:bar-chart-3',
          title: '统计报表',
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
