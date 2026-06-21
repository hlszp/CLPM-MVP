/**
 * CLPM 主导航菜单结构（单一来源）
 *
 * 权威来源：docs/设计文档/06-UIUX/ui-ux-design-guidelines.md v4.0 §4.2
 *
 * 规则（UI/UX §4.2）：
 * - 当前角色无权限的菜单项隐藏（不置灰）
 * - 隐藏路由（详情页 :loopId）通过列表行点击进入，不在菜单中显示
 * - Phase 2 整定模块在 Phase 1 保留原型页面
 * - Sponsor 默认首页 /performance，不可见诊断中心与回路整定
 * - 外部专家默认首页 /diagnosis，仅可见诊断中心与回路整定
 */

import {
  LayoutDashboard,
  Network,
  BarChart3,
  Activity,
  ListChecks,
  Settings2,
  ShieldCheck,
  ScrollText,
  UserCircle,
  SlidersHorizontal,
  Gauge,
  type LucideIcon,
} from 'lucide-react';

/** 角色（UI/UX §5.1） */
export type Role =
  | '仪控工程师'
  | '工艺/设备工程师'
  | 'Sponsor'
  | '系统管理员'
  | '外部专家';

export const ROLES: Role[] = [
  '仪控工程师',
  '工艺/设备工程师',
  'Sponsor',
  '系统管理员',
  '外部专家',
];

/** 角色默认首页（UI/UX §5.1 + §12.4） */
export const ROLE_DEFAULT_HOME: Record<Role, string> = {
  仪控工程师: '/',
  '工艺/设备工程师': '/performance',
  Sponsor: '/performance',
  系统管理员: '/loop/factory',
  外部专家: '/diagnosis',
};

/** 分期标记 */
export type Phase = 1 | 2 | 3;

/** 菜单项定义 */
export interface MenuItem {
  label: string;
  path: string;
  icon: LucideIcon;
  phase: Phase;
  /** 可见角色；undefined 表示全角色可见 */
  roles?: Role[];
  /** 是否在菜单中隐藏（隐藏路由如详情页） */
  hidden?: boolean;
}

/** 菜单组定义 */
export interface MenuGroup {
  group: string;
  items: MenuItem[];
}

/**
 * 6 模块 + 1 门户 共 25 个页面（UI/UX §4.2）
 *
 * 隐藏路由（/loop/monitor/:loopId、/diagnosis/:loopId）计入页面数但不在菜单显示。
 */
export const MENU_GROUPS: MenuGroup[] = [
  {
    group: '工作台',
    items: [
      { label: '性能总览首页', path: '/', icon: LayoutDashboard, phase: 1 },
    ],
  },
  {
    group: '回路管理',
    items: [
      { label: '工厂层级配置', path: '/loop/factory', icon: Network, phase: 1, roles: ['系统管理员', '仪控工程师'] },
      { label: '回路台账', path: '/loop/ledger', icon: Network, phase: 1, roles: ['系统管理员', '仪控工程师'] },
      { label: 'Tag 关联管理', path: '/loop/mapping', icon: Network, phase: 1, roles: ['系统管理员', '仪控工程师'] },
      { label: '回路监控列表', path: '/loop/monitor', icon: Gauge, phase: 1 },
      { label: '回路运行详情', path: '/loop/monitor/:loopId', icon: Gauge, phase: 1, hidden: true },
    ],
  },
  {
    group: '性能评估',
    items: [
      { label: '全局看板', path: '/performance', icon: BarChart3, phase: 1 },
      { label: '低效回路排行', path: '/performance/ranking', icon: BarChart3, phase: 1 },
      { label: '性能指标配置', path: '/performance/metrics', icon: SlidersHorizontal, phase: 1, roles: ['系统管理员'] },
      { label: '引擎规则配置', path: '/performance/rules', icon: SlidersHorizontal, phase: 1, roles: ['系统管理员'] },
      { label: '性能统计报表', path: '/performance/analytics', icon: BarChart3, phase: 1 },
    ],
  },
  {
    group: '诊断中心',
    items: [
      { label: '诊断列表', path: '/diagnosis', icon: Activity, phase: 1, roles: ['仪控工程师', '工艺/设备工程师', '系统管理员', '外部专家'] },
      { label: '诊断指标配置', path: '/diagnosis/metrics', icon: SlidersHorizontal, phase: 1, roles: ['系统管理员'] },
      { label: '回路诊断详情', path: '/diagnosis/:loopId', icon: Activity, phase: 1, hidden: true, roles: ['仪控工程师', '工艺/设备工程师', '系统管理员', '外部专家'] },
      { label: '异常跟踪', path: '/diagnosis/tracker', icon: ListChecks, phase: 1, roles: ['仪控工程师'] },
      { label: '诊断统计报表', path: '/diagnosis/analytics', icon: BarChart3, phase: 1, roles: ['仪控工程师', '工艺/设备工程师', '系统管理员', '外部专家'] },
    ],
  },
  {
    group: '回路整定',
    items: [
      { label: '整定工作台', path: '/tuning', icon: Settings2, phase: 2, roles: ['仪控工程师', '外部专家'] },
      { label: '模型辨识', path: '/tuning/identification', icon: Settings2, phase: 2, roles: ['仪控工程师', '外部专家'] },
      { label: '整定算法', path: '/tuning/algorithm', icon: Settings2, phase: 2, roles: ['仪控工程师', '外部专家'] },
      { label: '闭环仿真', path: '/tuning/simulation', icon: Settings2, phase: 2, roles: ['仪控工程师', '外部专家'] },
      { label: '整定效果统计', path: '/tuning/analytics', icon: BarChart3, phase: 2, roles: ['仪控工程师', '外部专家'] },
    ],
  },
  {
    group: '系统管理',
    items: [
      { label: '用户与角色', path: '/system/users', icon: UserCircle, phase: 1, roles: ['系统管理员'] },
      { label: '审计日志', path: '/system/audit', icon: ScrollText, phase: 1, roles: ['系统管理员'] },
      { label: '自动报表管理', path: '/system/reports', icon: ScrollText, phase: 1, roles: ['系统管理员'] },
      { label: '安全边界说明', path: '/system/safety', icon: ShieldCheck, phase: 1 },
    ],
  },
];

/**
 * 获取角色可见的菜单组（过滤隐藏项与无权限项）
 */
export function getVisibleMenuGroups(role: Role): MenuGroup[] {
  return MENU_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (item.hidden) return false;
      if (!item.roles) return true;
      return item.roles.includes(role);
    }),
  })).filter((group) => group.items.length > 0);
}

/**
 * 判断角色是否可访问指定路径（用于路由守卫）
 */
export function canAccess(role: Role, pathname: string): boolean {
  for (const group of MENU_GROUPS) {
    for (const item of group.items) {
      const basePath = item.path.split('/:')[0];
      if (pathname === basePath || pathname.startsWith(basePath + '/')) {
        if (!item.roles) return true;
        return item.roles.includes(role);
      }
    }
  }
  return true; // 未定义的路径默认放行（如 404）
}
