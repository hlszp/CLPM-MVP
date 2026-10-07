import type { RouteRecordRaw } from 'vue-router';

/**
 * 驾驶舱路由模块（方案 11 §4；2026-10-05 驾驶舱整合裁决 D1/D4 扩为六页签）
 *
 * 满屏布局实现：`meta.noBasicLayout: true` —— vben generateAccessible 对携带
 * 该标记的动态路由跳过 BasicLayout 包裹，直接 addRoute 为顶层路由，页面自行
 * 渲染 cockpit-header 顶栏；菜单仍由 accessibleRoutes 正常生成（驾驶舱入口
 * 出现在侧边菜单），authority 过滤照常生效，对后台其它页面零污染。
 *
 * 菜单只暴露「驾驶舱」单入口（/cockpit 总览）；其余五页由舱内顶栏 Tab 导航，
 * hideInMenu 不进菜单。性能/诊断/整定/处置四页吸收原运维工作台对应 Tab
 * （P2~P5 逐批迁移，迁移期旧 /workbench 并存，P7 退役）。
 */
const COCKPIT_ROLES = [
  'ADMIN',
  'EXPERT',
  'IC_ENGINEER',
  'PE_ENGINEER',
  'SPONSOR',
];

const routes: RouteRecordRaw[] = [
  {
    name: 'CockpitOverview',
    path: '/cockpit',
    component: () => import('#/views/cockpit/overview.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      icon: 'lucide:gauge',
      noBasicLayout: true,
      order: 0,
      title: '驾驶舱',
    },
  },
  {
    name: 'CockpitLoops',
    path: '/cockpit/loops',
    component: () => import('#/views/cockpit/loops.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-回路',
    },
  },
  {
    name: 'CockpitPerformance',
    path: '/cockpit/performance',
    component: () => import('#/views/cockpit/performance.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-性能',
    },
  },
  {
    name: 'CockpitDiagnosis',
    path: '/cockpit/diagnosis',
    component: () => import('#/views/cockpit/diagnosis.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-诊断',
    },
  },
  {
    name: 'CockpitTuning',
    path: '/cockpit/tuning',
    component: () => import('#/views/cockpit/tuning.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-整定',
    },
  },
  {
    name: 'CockpitHandling',
    path: '/cockpit/handling',
    component: () => import('#/views/cockpit/handling.vue'),
    meta: {
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-处置',
    },
  },
];

export default routes;
