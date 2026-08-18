import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路整定路由模块（09 设计方案恢复为一级模块，2026-08-19）
 *
 * 设计文档：docs/MVP设计/09-整定模块设计方案.md §6.1
 * 三页式：整定工作台（辨识→矩阵→仿真→确认 单页流程）
 *        / 整定记录（历史追溯）/ 效果验证（前后窗曲线对比）。
 * 前端全新实现（不恢复 git 历史版本）。
 *
 * 角色权限：
 * - 工作台操作（辨识/整定/仿真/保存）：ADMIN / IC_ENGINEER / EXPERT（后端校验）
 * - 查看：全部登录角色（含 PE_ENGINEER / SPONSOR）
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Tuning',
    path: '/tuning',
    redirect: '/tuning/workbench',
    meta: {
      authority: ['ADMIN', 'EXPERT', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR'],
      icon: 'lucide:sliders-horizontal',
      order: 5,
      title: '整定',
    },
    children: [
      {
        name: 'TuningWorkbench',
        path: '/tuning/workbench',
        component: () => import('#/views/tuning/workbench.vue'),
        meta: {
          // 工作台操作按钮由后端权限兜底；VIEW 类角色只读展示
          authority: ['ADMIN', 'IC_ENGINEER', 'EXPERT'],
          icon: 'lucide:sliders-horizontal',
          title: '整定工作台',
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
