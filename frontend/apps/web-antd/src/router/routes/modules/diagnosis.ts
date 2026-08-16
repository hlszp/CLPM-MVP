import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断路由模块（MVP v2 重设计版，2026-08-16）
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 两页式：诊断工作台（发起+结果一体）/ 诊断记录（历史+导出）。
 * 原诊断中心 5 页结构不沿用；旧页面文件已在 MVP 精简时删除。
 *
 * 角色权限：
 * - 工作台发起诊断：ADMIN / IC_ENGINEER / PE_ENGINEER（后端校验）
 * - 查看：全部登录角色（含 SPONSOR / EXPERT）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Diagnosis',
    path: '/diagnosis',
    redirect: '/diagnosis/workbench',
    meta: {
      authority: [
        'ADMIN',
        'EXPERT',
        'IC_ENGINEER',
        'PE_ENGINEER',
        'SPONSOR',
      ],
      icon: 'lucide:stethoscope',
      order: 3,
      title: '诊断',
    },
    children: [
      {
        name: 'DiagnosisWorkbench',
        path: '/diagnosis/workbench',
        component: () => import('#/views/diagnosis/workbench.vue'),
        meta: {
          // 工作台页面选择器对 VIEW 类角色只读展示（发起按钮由后端权限兜底）
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:stethoscope',
          title: '诊断工作台',
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
          icon: 'lucide:history',
          title: '诊断记录',
        },
      },
    ],
  },
];

export default routes;
