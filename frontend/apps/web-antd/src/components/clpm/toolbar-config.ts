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
  | 'import'
  | 'more'
  | 'pdf'
  | 'refresh'
  | 'run'
  | 'save'
  | 'search'
  | 'setting'
  | 'stop'
  | 'track'
  | 'tuning';

/**
 * 工具栏按钮图标映射（Iconify 图标名）
 *
 * 统一使用 ant-design 图标集，与项目现有 confidence-badge.vue 用法一致
 */
export const TOOLBAR_ICON_MAP: Record<ToolbarAction, string> = {
  'auto-refresh': 'ant-design:sync-outlined',
  back: 'ant-design:arrow-left-outlined',
  cancel: 'ant-design:close-circle-outlined',
  create: 'ant-design:plus-outlined',
  delete: 'ant-design:delete-outlined',
  diagnosis: 'ant-design:medicine-box-outlined',
  edit: 'ant-design:edit-outlined',
  export: 'ant-design:download-outlined',
  filter: 'ant-design:filter-outlined',
  fullscreen: 'ant-design:fullscreen-outlined',
  import: 'ant-design:upload-outlined',
  more: 'ant-design:ellipsis-outlined',
  pdf: 'ant-design:file-pdf-outlined',
  refresh: 'ant-design:reload-outlined',
  run: 'ant-design:play-circle-outlined',
  save: 'ant-design:save-outlined',
  search: 'ant-design:search-outlined',
  stop: 'ant-design:pause-circle-outlined',
  track: 'ant-design:flag-outlined',
  tuning: 'ant-design:tool-outlined',
  setting: 'ant-design:setting-outlined',
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
  import: 'default',
  more: 'link',
  pdf: 'export',
  refresh: 'default',
  run: 'primary',
  save: 'primary',
  search: 'primary',
  stop: 'danger',
  track: 'primary',
  tuning: 'default',
  setting: 'default',
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
