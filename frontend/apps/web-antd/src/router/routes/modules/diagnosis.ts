import type { RouteRecordRaw } from 'vue-router';

/**
 * 诊断路由模块（MVP v2 重设计版，2026-08-16；2026-10-01 UX 重构；2026-10-04 工作台规整）
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.2；docs/设计文档/工作台规整方案-2026-10-04.md
 * 三页式（2026-10-04 裁决 D2：诊断工作台并入回路工作台，不保留）：
 * - 诊断概览：每回路最新一条诊断结论 + 可诊断性筛选（仅可诊断/预检徽标）
 * - 诊断记录：历史 + 导出
 * - 诊断任务：诊断类任务执行列表（从评估任务列表切分）
 * - 单回路发起诊断/结论证据查看：回路工作台诊断剖面
 *   （/loop/workbench360?loopId=&section=diagnosis）
 *
 * 角色权限：
 * - 发起诊断：回路工作台剖面四角色 ADMIN/IC/PE/EXPERT（后端校验，D1 对齐）
 * - 查看：全部登录角色（含 SPONSOR）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Diagnosis',
    path: '/diagnosis',
    redirect: '/diagnosis/overview',
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:stethoscope',
      order: 3,
      title: '诊断',
      module: 'diagnosis',
    },
    children: [
      {
        // 旧「诊断工作台」书签兼容（2026-10-04 D2 并入）：
        // 带 loopId 直达回路工作台诊断剖面，否则落诊断概览；其余 query 透传
        name: 'DiagnosisWorkbench',
        path: '/diagnosis/workbench',
        redirect: (to) => {
          const loopId =
            typeof to.query.loopId === 'string' ? to.query.loopId : '';
          return loopId
            ? {
                path: '/loop/workbench360',
                query: { ...to.query, loopId, section: 'diagnosis' },
              }
            : { path: '/diagnosis/overview', query: { ...to.query } };
        },
        meta: {
          hideInMenu: true,
          title: '诊断概览',
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
          // 查看类页面统一全 5 角色（2026-10-04 D1；发起类操作在回路工作台剖面）
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:loader-circle',
          title: '诊断任务',
        },
      },
    ],
  },
];

export default routes;
