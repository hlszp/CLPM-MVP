import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块（09 设计方案恢复为一级模块，2026-08-19；2026-10-04 工作台规整）
 *
 * 设计文档：docs/MVP设计/09-整定模块设计方案.md §6.1；docs/设计文档/工作台规整方案-2026-10-04.md
 * 三页式：整定总览（全回路可整定性 + 在途整定建议；单回路四步流程在
 *        回路工作台整定剖面 /loop/workbench360?loopId=&section=tuning，D3）
 *        / 整定记录（历史追溯）/ 效果验证（前后窗曲线对比）。
 *
 * 角色权限：
 * - 整定操作（辨识/整定/仿真/保存）：回路工作台剖面四角色 ADMIN/IC/PE/EXPERT
 *   （后端校验，D1 对齐）；总览页「调参优化」入口按角色渲染
 * - 查看：全部登录角色（含 SPONSOR）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Tuning',
    path: '/tuning',
    redirect: '/tuning/overview',
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:sliders-horizontal',
      order: 4,
      title: '整定',
      module: 'tuning',
    },
    children: [
      {
        name: 'TuningOverview',
        path: '/tuning/overview',
        component: () => import('#/views/tuning/overview.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:sliders-horizontal',
          title: '整定总览',
        },
      },
      {
        // 旧「整定工作台」书签兼容（2026-10-04 D3 转型）：query 透传（loopId 高亮定位）
        name: 'TuningWorkbench',
        path: '/tuning/workbench',
        redirect: (to) => ({ path: '/tuning/overview', query: { ...to.query } }),
        meta: {
          hideInMenu: true,
          title: '整定总览',
        },
      },
      {
        name: 'TuningRecords',
        path: '/tuning/records',
        component: () => import('#/views/tuning/records.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:history',
          title: '整定记录',
        },
      },
      {
        name: 'TuningVerification',
        path: '/tuning/verification',
        component: () => import('#/views/tuning/verification.vue'),
        meta: {
          authority: [
            'ADMIN',
            'EXPERT',
            'IC_ENGINEER',
            'PE_ENGINEER',
            'SPONSOR',
          ],
          icon: 'lucide:git-compare-arrows',
          title: '效果验证',
        },
      },
    ],
  },
];

export default routes;
