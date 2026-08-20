/**
 * CLPM 工具栏按钮图标与色彩规范
 *
 * 对齐 UI/UX 改造方案 §6.3、§7.1 与用户补充要求：
 * "工具栏不同的按钮最好使用图标表示，并根据不同的功能和状态采用不同的颜色"
 *
 * 使用方式：
 * 1. 直接引用常量：<IconifyIcon :icon="TOOLBAR_ICON_MAP.refresh" />
 * 2. 使用 ClpmToolbarButton 组件：<ClpmToolbarButton icon="refresh" @click="..." />
 */

/**
 * 工具栏按钮功能名
 *
 * 命名规范：动词或动名词，小写连字符
 */
export type ToolbarAction =
  | 'ai'
  | 'auto-refresh'
  | 'back'
  | 'cancel'
  | 'create'
  | 'delete'
  | 'diagnosis'
  | 'edit'
  | 'export'
  | 'filter'
  | 'fullscreen'
  | 'help'
  | 'import'
  | 'more'
  | 'pdf'
  | 'print'
  | 'refresh'
  | 'run'
  | 'save'
  | 'search'
  | 'setting'
  | 'stop'
  | 'time-window'
  | 'track'
  | 'tuning';

/**
 * 工具栏按钮图标映射（Iconify 图标名）
 *
 * 标准 9 工具统一使用 lucide 图标集（现代、简洁、线性描边风格，
 * 对齐 Google Material Design 设计语言），体现工业软件专业性与细节质感。
 * 上下文动作（create/delete/save 等）沿用 ant-design 图标集。
 */
export const TOOLBAR_ICON_MAP: Record<ToolbarAction, string> = {
  'auto-refresh': 'lucide:refresh-cw',
  back: 'lucide:arrow-left',
  cancel: 'lucide:x-circle',
  create: 'ant-design:plus-outlined',
  delete: 'ant-design:delete-outlined',
  diagnosis: 'lucide:stethoscope',
  edit: 'lucide:pencil',
  export: 'lucide:upload',
  filter: 'lucide:filter',
  fullscreen: 'lucide:maximize',
  help: 'lucide:circle-help',
  ai: 'lucide:sparkles',
  import: 'lucide:download',
  more: 'lucide:ellipsis',
  pdf: 'lucide:file-text',
  print: 'lucide:printer',
  refresh: 'lucide:refresh-cw',
  run: 'lucide:play',
  save: 'lucide:save',
  search: 'lucide:search',
  setting: 'lucide:columns-3',
  'time-window': 'lucide:clock',
  stop: 'lucide:square',
  track: 'lucide:flag',
  tuning: 'lucide:wrench',
};

/**
 * 工具栏按钮图标语义色映射（UI/UX v6.1 统一工具栏）
 *
 * 启用态：图标套用各自语义色（按钮外壳保持中性灰描边），体现工业软件
 * 功能丰富性与专业辨识度；禁用态统一降饱和灰（见 toolbar-button.vue）。
 * 仅对「标准 9 工具」着色，contextual 动作（create/delete/save 等）沿用
 * 变体色，不在此映射中。
 *
 * 色值采用 mid-tone hsl，明暗主题下均清晰；主题语义 token（primary/
 * success/warning）优先复用。
 */
export const TOOLBAR_ICON_COLOR: Partial<Record<ToolbarAction, string>> = {
  // 数据组
  refresh: 'hsl(217 91% 55%)', // 蓝
  'time-window': 'hsl(189 90% 42%)', // 青
  filter: 'hsl(32 95% 48%)', // 橙
  import: 'hsl(173 70% 40%)', // 蓝绿
  export: 'hsl(var(--success))', // 绿（复用主题）
  setting: 'hsl(262 70% 58%)', // 紫（列设置）
  print: 'hsl(243 60% 58%)', // 靛
  // 智能组
  ai: 'hsl(291 64% 56%)', // 品红（AI 专属）
  // 帮助组
  help: 'hsl(199 85% 47%)', // 天蓝
};

/**
 * 工具栏按钮变体（功能色）
 *
 * 对应 Ant Design Vue Button 的 type + danger 组合，并扩展 export 语义
 * - primary：工业蓝，主操作（新建、查询、执行仿真、运行评估）
 * - default：中性灰，常规操作（刷新、自动刷新、返回）
 * - export：绿色调，导出/下载（导出 CSV、下载 PDF、导出日报）
 * - danger：红色，危险操作（删除、取消任务、清除数据）
 * - link：链接样式，次要操作（更多、查看详情）
 * - dashed：虚线，状态切换（视图切换、时间窗选择）
 */
export type ToolbarVariant =
  | 'danger'
  | 'dashed'
  | 'default'
  | 'export'
  | 'link'
  | 'primary';

/**
 * 功能 → 默认变体映射
 *
 * 页面未显式指定 variant 时使用此默认值
 */
export const TOOLBAR_DEFAULT_VARIANT: Record<ToolbarAction, ToolbarVariant> = {
  'auto-refresh': 'default',
  back: 'default',
  cancel: 'danger',
  create: 'primary',
  delete: 'danger',
  diagnosis: 'default',
  edit: 'default',
  export: 'export',
  filter: 'default',
  fullscreen: 'default',
  help: 'default',
  ai: 'default',
  import: 'default',
  more: 'link',
  pdf: 'export',
  print: 'default',
  refresh: 'default',
  run: 'primary',
  save: 'primary',
  search: 'primary',
  setting: 'default',
  'time-window': 'default',
  stop: 'danger',
  track: 'primary',
  tuning: 'default',
};

/**
 * 按钮状态色规范
 *
 * 用于 ClpmToolbarButton 的 active/disabled/loading 状态
 */
export const TOOLBAR_STATE_COLOR = {
  /** 激活态：主色填充（如自动刷新开启时） */
  active: 'primary',
  /** 禁用态：灰色 + tooltip 显示原因 */
  disabled: 'muted',
  /** 加载态：spinner + 禁用 */
  loading: 'primary',
} as const;
