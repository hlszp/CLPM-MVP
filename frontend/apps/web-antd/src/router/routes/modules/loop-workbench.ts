import type { RouteRecordRaw } from 'vue-router';

/**
 * 回路工作台（一级菜单，2026-10-03 用户裁决）
 *
 * 以回路为对象的单页全生命周期工作台（监视-评估-诊断-整定-处置，零跳转
 * 全内嵌）。原挂监控模块子菜单（P1 起验收期），验收通过后升为一级菜单，
 * 菜单名去掉"（新版）"后缀——回路维度的主战场入口。
 *
 * order=1.5：位于 监控(1) 与 评估(2) 之间——先在监控发现（哪里有问题），
 * 再进回路工作台处理（单回路深钻）。
 *
 * meta.module='monitor'：剖面内容仍按模块热插拔过滤（诊断/整定/处置
 * 禁用时对应剖面不可用），菜单本体随 monitor 模块（monitor 不可禁）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'LoopWorkbench360',
    path: '/loop/workbench360',
    component: () => import('#/views/loop/workbench360/index.vue'),
    meta: {
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'EXPERT'],
      fullPathKey: false,
      icon: 'lucide:panel-top-close',
      module: 'monitor',
      order: 1.5,
      title: '回路工作台',
    },
  },
];

export default routes;
