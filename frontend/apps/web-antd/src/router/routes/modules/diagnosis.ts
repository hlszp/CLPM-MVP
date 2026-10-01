import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断路由模块（MVP v2 重设计版，2026-08-16；2026-10-01 UX 重构）
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2
 * 四页式（2026-10-01 用户口径）：
 * - 诊断工作台：左脊柱单选回路 + 上发起 / 下结论证据 Tabs
 * - 诊断概览：每回路最新一条诊断结论（原工作台概览区独立成页）
 * - 诊断记录：历史 + 导出
 * - 诊断任务：诊断类任务执行列表（从评估任务列表切分）
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
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:stethoscope',
      order: 3,
      title: '诊断',
      module: 'diagnosis',
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
          icon: 'lucide:list-checks',
          title: '诊断概览',
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
      {
        name: 'DiagnosisTasks',
        path: '/diagnosis/tasks',
        component: () => import('#/views/diagnosis/tasks.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:loader-circle',
          title: '诊断任务',
        },
      },
    ],
  },
];

export default routes;
