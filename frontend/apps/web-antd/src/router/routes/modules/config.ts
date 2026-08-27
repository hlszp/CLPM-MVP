import type { RouteRecordRaw } from 'vue-router';

/**
 * 配置路由模块（IA 重构 Phase A·结构性配置集中）
 *
 * 来源：原散落于 loop/tag/metric/diagnosis/system 的结构性配置页。
 * 对齐 IA 重构方案 §3.3/§4.6。
 *
 * 权限：仅 ADMIN（测点/回路/数据源配置允许 IC_ENGINEER/PE_ENGINEER 查看，对齐原口径）
 *
 * 工程/操作分离原则：
 * - 结构性配置（一次性/低频工程活动）→ 集中于此
 * - 操作性调参（阈值微调/算法参数/时间窗/列设置）→ 保留在各业务页内联，不迁入
 * - 历史数据导入 → 保留在监控/评估工具栏"导入"按钮
 *
 * legacy redirect 段保护旧书签与 E2E（route-compat.spec.ts 守护）。
 */
const routes: RouteRecordRaw[] = [
  {
    name: 'Config',
    path: '/config',
    redirect: '/config/loop',
    meta: {
      // FP-P0-05：父路由 authority 取子路由并集，避免 filterTree 不递归导致
      // IC_ENGINEER/PE_ENGINEER 永远看不到"配置"菜单（子页查看权死配置）。
      // 仅 ADMIN 可访问的子页（链路/指标/诊断/预警规则配置）由各自 authority 守卫。
      authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
      icon: 'lucide:settings-2',
      order: 7,
      title: '配置',
      module: 'config',
    },
    children: [
      {
        name: 'ConfigLink',
        path: '/config/link',
        component: () => import('#/views/loop/aas.vue'),
        meta: {
          // 实现契约 §5：datasource.py/dcs.py 写端点仅 ADMIN
          authority: ['ADMIN'],
          icon: 'lucide:refresh-cw',
          order: 1,
          title: '链路配置',
        },
      },
      {
        name: 'ConfigTag',
        path: '/config/tag',
        component: () => import('#/views/tag/list.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:list',
          order: 2,
          title: '测点配置',
        },
      },
      {
        name: 'ConfigLoop',
        path: '/config/loop',
        component: () => import('#/views/loop/manage.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:network',
          order: 3,
          title: '回路配置',
        },
      },
      // 回路台账已并入回路配置（FE-04），保留 redirect 兼容
      // （工厂模型 /config/factory 已恢复为独立页面「工厂配置」）
      {
        name: 'ConfigLedger',
        path: '/config/ledger',
        redirect: '/config/loop',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '回路台账',
        },
      },
      {
        name: 'ConfigFactory',
        path: '/config/factory',
        component: () => import('#/views/factory/config.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:factory',
          order: 6,
          title: '工厂配置',
        },
      },
      {
        name: 'ConfigDatasource',
        path: '/config/datasource',
        component: () => import('#/views/loop/data.vue'),
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          icon: 'lucide:database',
          order: 7,
          title: '数据检查',
        },
      },
      {
        name: 'ConfigMetric',
        path: '/config/metric',
        component: () => import('#/views/metric/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:settings',
          order: 8,
          title: '指标配置',
        },
      },
      {
        name: 'ConfigDiagnosis',
        path: '/config/diagnosis',
        component: () => import('#/views/diagnosis/config.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:stethoscope',
          order: 9,
          title: '诊断配置',
        },
      },
      {
        name: 'ConfigAlertRules',
        path: '/config/alert-rules',
        component: () => import('#/views/alert/rules.vue'),
        meta: {
          authority: ['ADMIN'],
          icon: 'lucide:bell-ring',
          order: 10,
          title: '预警规则',
        },
      },
      // PID 结构模板无独立页，redirect 到链路配置（原行为）
      {
        name: 'ConfigPidTemplate',
        path: '/config/pid-template',
        redirect: '/config/link',
        meta: {
          authority: ['ADMIN'],
          hideInMenu: true,
          title: 'PID 结构模板',
        },
      },
      // ===== legacy redirect：旧路径 → 新路径（保护书签/E2E） =====
      {
        name: 'LegacyLoopAasSync',
        path: '/loop/aas-sync',
        redirect: '/config/link',
        meta: { authority: ['ADMIN'], hideInMenu: true, title: '链路配置' },
      },
      {
        name: 'LegacyTagList',
        path: '/tag/list',
        redirect: '/config/tag',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '测点配置',
        },
      },
      {
        name: 'LegacyTagRoot',
        path: '/tag',
        redirect: '/config/tag',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '测点配置',
        },
      },
      {
        name: 'LegacyLoopManage',
        path: '/loop/manage',
        redirect: '/config/loop',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '回路配置',
        },
      },
      {
        name: 'LegacyLoopFactory',
        path: '/loop/factory',
        redirect: '/config/loop',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '工厂模型',
        },
      },
      {
        name: 'LegacyLoopLedger',
        path: '/loop/ledger',
        redirect: '/config/loop',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '回路台账',
        },
      },
      {
        name: 'LegacyLoopData',
        path: '/loop/data',
        redirect: '/config/datasource',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          hideInMenu: true,
          title: '数据管理',
        },
      },
      {
        name: 'LegacyMetricConfig',
        path: '/metric/config',
        redirect: '/config/metric',
        meta: { authority: ['ADMIN'], hideInMenu: true, title: '指标配置' },
      },
      {
        name: 'LegacyDiagnosisConfig',
        path: '/diagnosis/config',
        redirect: '/config/diagnosis',
        meta: { authority: ['ADMIN'], hideInMenu: true, title: '诊断配置' },
      },
      {
        name: 'LegacySystemPidTemplate',
        path: '/system/pid-template',
        redirect: '/config/link',
        meta: { authority: ['ADMIN'], hideInMenu: true, title: 'PID 结构模板' },
      },
    ],
  },
];

export default routes;
