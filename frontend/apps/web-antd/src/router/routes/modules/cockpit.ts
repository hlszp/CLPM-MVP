import type { RouteRecordRaw } from 'vue-router';

/**
 * 驾驶舱路由模块（方案 11 §4，C2 骨架）
 *
 * 满屏布局实现：`meta.noBasicLayout: true` —— vben generateAccessible 对携带
 * 该标记的动态路由跳过 BasicLayout 包裹，直接 addRoute 为顶层路由，页面自行
 * 渲染 cockpit-header 顶栏；菜单仍由 accessibleRoutes 正常生成（驾驶舱入口
 * 出现在侧边菜单），authority 过滤照常生效，对后台其它页面零污染。
 *
 * 菜单只暴露「驾驶舱」单入口（/cockpit 总览）；/cockpit/loops 由舱内
 * 顶栏 Tab 导航，hideInMenu 不进菜单。
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
      order: -1,
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
];

export default routes;
