/**
 * usePageToolbar — 声明式页面工具栏配置（UI/UX v6.1 §5.1 统一工具栏）
 *
 * 将「本页启用哪些标准工具 + 各工具的点击/禁用/权限」声明为配置对象，
 * composable 输出带分组分隔符的渲染描述数组，页面在 ClpmPageToolbar 的
 * #actions 槽用 v-for 渲染，避免每页手写 N 个 ClpmToolbarButton + 分隔符。
 *
 * 规范对齐：
 * - 9 标准工具分 3 组（数据组 / 智能组 / 帮助组），组间自动插入分隔符
 * - 权限不足时自动置灰（disabled + disabledReason），占位保留（Poka-Yoke 灰而不藏）
 * - 仅「标准工具」享受语义彩色图标（toolbar-config.ts TOOLBAR_ICON_COLOR）
 *
 * 用法：
 * ```ts
 * const { toolbarItems } = usePageToolbar(() => ({
 *   refresh: { onClick: loadList, loading: loading.value },
 *   export: { onClick: exportCsv, permission: 'alert:read' },
 *   help: { onClick: () => showPageHelp({ title: '预警事件', content: '...' }) },
 * }));
 * ```
 *
 * 反应性：传入 getter 函数，其内部读取的 ref/computed 变化会自动重算。
 */
import type { ToolbarAction } from '#/components/clpm/toolbar-config';

import { computed } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

import { Modal } from 'ant-design-vue';

/** 标准 9 工具（与 TOOLBAR_ICON_COLOR 对齐） */
export type StandardTool =
  | 'ai'
  | 'export'
  | 'filter'
  | 'help'
  | 'import'
  | 'print'
  | 'refresh'
  | 'setting'
  | 'time-window';

/** 单个工具的配置 */
export interface ToolConfig {
  /** 点击回调 */
  onClick?: (event: MouseEvent) => void;
  /** 业务禁用（如接口未就绪）；与权限禁用取并集 */
  disabled?: boolean;
  /** 禁用原因，用于 tooltip 说明 */
  disabledReason?: string;
  /** 加载态（spinner + 禁用） */
  loading?: boolean;
  /** 激活态（如筛选区已展开、自动刷新已开启），仅中性变体生效 */
  active?: boolean;
  /** 覆盖默认中文标签 */
  label?: string;
  /** 自定义 tooltip（默认启用态=标签，禁用态=disabledReason） */
  tooltip?: string;
  /**
   * 权限要求：角色名（ADMIN/IC_ENGINEER 等）或权限码（alert:read），
   * 并集命中即放行；无权限自动灰显，占位保留（不删除按钮）。
   */
  permission?: string | string[];
}

/** 页面工具配置：仅声明本页启用的工具，未声明的工具不渲染 */
export type PageToolbarTools = Partial<Record<StandardTool, ToolConfig>>;

/** 工具默认中文标签 */
const DEFAULT_LABEL: Record<StandardTool, string> = {
  refresh: '刷新',
  'time-window': '时间窗',
  filter: '筛选',
  import: '导入',
  export: '导出',
  setting: '列设置',
  print: '打印',
  ai: 'AI 洞察',
  help: '帮助',
};

/**
 * 分组顺序：数据组 → 智能组 → 帮助组
 * 组内顺序即工具在工具栏中的排列顺序；仅渲染页面已声明的工具，
 * 空组不渲染、不插分隔符。
 */
const GROUP_ORDER: StandardTool[][] = [
  ['refresh', 'time-window', 'filter', 'import', 'export', 'setting', 'print'],
  ['ai'],
  ['help'],
];

export interface ToolbarButtonItem {
  kind: 'button';
  action: ToolbarAction;
  label: string;
  onClick: (event: MouseEvent) => void;
  disabled: boolean;
  disabledReason: string;
  loading: boolean;
  active: boolean;
  tooltip: string;
}

export interface ToolbarDividerItem {
  kind: 'divider';
}

export type ToolbarItem = ToolbarButtonItem | ToolbarDividerItem;

/**
 * 判断当前用户是否满足指定角色/权限（并集，命中任一即放行）
 *
 * - 角色名：useUserStore().userInfo.roles（精确匹配，大写枚举）
 * - 权限码：useAccessStore().accessCodes（精确 + 模块级通配 loop:* + 超管 *）
 *
 * 对齐 directives/permission.ts 的 checkAccessible 语义，但本 composable
 * 用于「灰而不藏」场景（置 disabled），而非隐藏元素。
 */
function checkAccess(
  accessCodes: Set<string>,
  roles: Set<string>,
  permission?: string | string[],
): boolean {
  if (!permission) return true;
  const values = Array.isArray(permission) ? permission : [permission];
  return values.some((v) => {
    if (roles.has(v)) return true;
    if (accessCodes.has(v) || accessCodes.has('*')) return true;
    const parts = v.split(':');
    if (parts.length > 1) {
      for (let i = parts.length - 1; i > 0; i--) {
        if (accessCodes.has(`${parts.slice(0, i).join(':')}:*`)) return true;
      }
    }
    return false;
  });
}

/**
 * 声明式工具栏配置
 *
 * @param tools 工具配置对象，或返回配置对象的 getter（推荐 getter 形式以
 *   保证 loading/disabled 等响应式状态变化时工具栏自动重算）
 * @returns toolbarItems 渲染描述数组（按钮 + 分组分隔符，已按规范排序）
 */
export function usePageToolbar(
  tools: (() => PageToolbarTools) | PageToolbarTools,
) {
  const accessStore = useAccessStore();
  const userStore = useUserStore();

  const resolved = computed<PageToolbarTools>(() =>
    typeof tools === 'function' ? (tools as () => PageToolbarTools)() : tools,
  );

  const toolbarItems = computed<ToolbarItem[]>(() => {
    const cfg = resolved.value;
    const accessCodes = new Set(accessStore.accessCodes);
    const roles = new Set(userStore.userInfo?.roles);

    const groups: ToolbarButtonItem[][] = [];
    for (const group of GROUP_ORDER) {
      const items: ToolbarButtonItem[] = [];
      for (const action of group) {
        const c = cfg[action];
        // 整改 A-04（图标墙治理）：未声明的工具不渲染——"灰而不藏"仅适用于
        // "页面有此功能但当前角色无权限"的场景；页面本就没有的功能占位渲染
        // 会造成每页 9+ 图标墙，认知负荷过载（审查报告 SYS-P1-03）。
        if (!c) continue;
        const permitted = checkAccess(accessCodes, roles, c.permission);
        const disabled = Boolean(c.disabled) || !permitted;
        const disabledReason =
          c.disabledReason || (permitted ? '' : '当前角色无此操作权限');
        items.push({
          kind: 'button',
          action,
          label: c.label || DEFAULT_LABEL[action],
          onClick: c.onClick ?? noop,
          disabled,
          disabledReason,
          loading: Boolean(c.loading),
          active: Boolean(c.active),
          tooltip: c.tooltip || '',
        });
      }
      if (items.length > 0) groups.push(items);
    }

    const result: ToolbarItem[] = [];
    groups.forEach((g, i) => {
      if (i > 0) result.push({ kind: 'divider' });
      result.push(...g);
    });
    return result;
  });

  return { toolbarItems };
}

function noop() {
  /* 默认无操作 */
}

/**
 * 显示本页帮助（统一帮助入口，配合标准 help 工具使用）
 *
 * @param opts.title 帮助标题（一般为页面名）
 * @param opts.content 帮助正文（支持简单文本/HTML 片段）
 */
export function showPageHelp(opts: { content: string; title: string }): void {
  Modal.info({
    title: opts.title,
    content: opts.content,
    width: 520,
    okText: '知道了',
    class: 'clpm-toolbar-help-modal',
  });
}
