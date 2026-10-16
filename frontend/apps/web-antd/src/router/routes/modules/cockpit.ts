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
 *
 * 五组导航（IAR-D1，2026-10-14）：我的工作=个人责任组（navGroup: 'personal'），
 * 驾驶舱=全局观察组（navGroup: 'observe'）；组登记见 ../nav-groups.ts。
 */
const COCKPIT_ROLES = [
  'ADMIN',
  'EXPERT',
  'IC_ENGINEER',
  'PE_ENGINEER',
  'SPONSOR',
];

const routes: RouteRecordRaw[] = [
  /**
   * 我的工作（P6-06 O06-NAV；IAR-D1 五组导航第一组，工程师默认首页）
   *
   * 四视图（分配给我/系统建议/我的关注/最近继续）+ 三分区互斥的个人工作
   * 入口页；标准 BasicLayout（非驾驶舱满屏）。order=-1 位于驾驶舱(0)之前。
   * 五角色可见：观察角色（SPONSOR）页内不出现认领/移交/处置等可执行入口
   * （操作按钮按角色显隐，后端权限每次查询/操作仍重新校验）。
   *
   * permCode=page:my-work.main：后端权限目录/种子下发由主协调（O-INTEGRATE）
   * 整合——未下发前页面码过滤会移除本入口（与 P4-02 过滤口径一致）。
   */
  {
    name: 'MyWork',
    path: '/my-work',
    component: () => import('#/views/my-work/index.vue'),
    meta: {
      permCode: 'page:my-work.main',
      authority: COCKPIT_ROLES,
      icon: 'lucide:list-checks',
      navGroup: 'personal',
      order: -1,
      title: '我的工作',
    },
  },
  {
    name: 'CockpitOverview',
    path: '/cockpit',
    component: () => import('#/views/cockpit/overview.vue'),
    meta: {
      permCode: 'page:cockpit.overview',
      authority: COCKPIT_ROLES,
      icon: 'lucide:gauge',
      navGroup: 'observe',
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
      permCode: 'page:cockpit.loops',
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
      permCode: 'page:cockpit.performance',
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
      permCode: 'page:cockpit.diagnosis',
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
      permCode: 'page:cockpit.tuning',
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
      permCode: 'page:cockpit.handling',
      authority: COCKPIT_ROLES,
      hideInMenu: true,
      noBasicLayout: true,
      title: '驾驶舱-处置',
    },
  },
];

export default routes;
