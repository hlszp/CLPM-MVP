import { defineOverridesPreferences } from '@vben/preferences';

/**
 * CLPM Industrial Light 语义色（基于 vue-vben-admin 主题能力扩展）
 *
 * 这些常量只描述业务语义；真正的主题切换、主色、圆角、明暗模式仍交给
 * vben preferences + Ant Design Vue ConfigProvider 管理。
 */
export const CLPM_INDUSTRIAL_TOKENS = {
  BORDER_STRONG: '#cbd5e1',
  CONTROL_PRIMARY: '#0d6efd',
  DATA_LINE_MODE: '#722ed1',
  DATA_LINE_OP: '#fa8c16',
  DATA_LINE_PV: '#0d6efd',
  DATA_LINE_SP: '#52c41a',
  SURFACE_CANVAS: '#f4f7fb',
  SURFACE_PANEL: '#ffffff',
  TEXT_PRIMARY: '#0f172a',
  TEXT_SECONDARY: '#475569',
} as const;

/**
 * 全局色彩语义规范（对齐 UI/UX v4.1 §3.3 设计令牌）
 *
 * 统一全系统的状态色彩编码，确保 KPI 卡片、徽章、图表、标签等元素
 * 使用一致的色彩语义。所有页面应优先引用这些常量，避免硬编码颜色值。
 *
 * @deprecated P1 #19: 此为静态常量，不响应深色模式切换。
 *   组件内请改用 `useClpmTheme()` 获取响应式 themeColors。
 *   本常量仅保留用于非响应式场景（如模块顶层常量定义）。
 */
export const THEME_COLORS = {
  /** 成功 / 优秀 / 已完成（约定表 --status-ok） */
  SUCCESS: '#198754',
  /** 警告 / 待处理 / 需关注（约定表 --status-warning，深琥珀文字态） */
  WARNING: '#b45309',
  /** 错误 / 差 / 失败 / 危险操作（约定表 --status-error） */
  DANGER: '#dc3545',
  /** 信息 / 进行中 / 品牌主色（工业蓝 --status-info） */
  INFO: '#0d6efd',
  /** 中性 / 未知 / 未分类 / 零值（约定表 --status-neutral） */
  NEUTRAL: '#6c757d',
} as const;

/**
 * KPI 状态 → 色彩映射
 * 用于性能等级（优秀/良好/合格/差）及综合评估结果的色彩编码
 * 整改 A-01：对齐 UI/UX §3.1.4 分级配色（优良青绿/良好深蓝/关注琥珀/低效红/数据不足灰）
 */
export const KPI_COLOR_MAP = {
  EXCELLENT: THEME_COLORS.SUCCESS,
  GOOD: THEME_COLORS.INFO,
  PASS: THEME_COLORS.WARNING,
  FAIL: THEME_COLORS.DANGER,
  UNKNOWN: THEME_COLORS.NEUTRAL,
} as const;

/**
 * 行动状态 → 色彩映射
 * 用于诊断建议的 actionStatus 字段色彩编码
 */
export const ACTION_STATUS_COLOR_MAP = {
  PENDING: THEME_COLORS.WARNING,
  IN_PROGRESS: THEME_COLORS.INFO,
  IMPLEMENTED: THEME_COLORS.SUCCESS,
  IGNORED: THEME_COLORS.NEUTRAL,
} as const;

/**
 * @description CLPM 项目配置文件
 * 对齐 UI/UX v4.1 §2 设计基调与 §3 设计令牌
 * - 品牌主色：工业蓝（#0D6EFD / hsl(211 98% 52%)）
 * - 反 AI Slop：无装饰性渐变、无 Emoji 图标
 * - 启用 Refresh Token 自动续期
 * !!! 更改配置后请清空缓存，否则可能不生效
 */
export const overridesPreferences = defineOverridesPreferences({
  app: {
    // 启用 Refresh Token 自动续期（对齐 IDS v3.2 §5.2）
    enableRefreshToken: true,
    // 默认首页路径
    defaultHomePath: '/dashboard',
    // 应用名称
    name: import.meta.env.VITE_APP_TITLE,
    // 登录过期模式：页面跳转
    loginExpiredMode: 'page',
  },
  breadcrumb: {
    enable: true,
    showHome: true,
    showIcon: true,
  },
  copyright: {
    companyName: 'CLPM',
    companySiteLink: '',
    date: '2026',
    enable: true,
    settingShow: false,
  },
  header: {
    enable: true,
    mode: 'fixed',
  },
  logo: {
    enable: true,
    source: '',
  },
  navigation: {
    accordion: true,
    split: false,
    styleType: 'plain',
  },
  sidebar: {
    collapsed: false,
    collapsedButton: true,
    enable: true,
    width: 224,
  },
  tabbar: {
    enable: true,
    persist: true,
    showIcon: true,
    styleType: 'chrome',
  },
  theme: {
    // 品牌主色：工业蓝 #0D6EFD → hsl(211 98% 52%)
    colorPrimary: 'hsl(211 98% 52%)',
    // 圆角：克制（对齐 UI/UX §3.5 --radius-sm 4px）
    radius: '0.25',
    // 整改 E2：不要在 overrides 里写死 mode——initPreferences 合并时
    // overrides 优先于用户缓存，写死 light 会导致暗色选择刷新后丢失。
    // 默认浅色由 defaultPreferences 承担；用户切换暗色可持久化。
    // 默认保留浅色头部，避免与既有 vben 顶栏功能冲突
    semiDarkHeader: false,
    // 半深色侧栏增强工业桌面端质感，同时仍允许用户在设置中切换
    semiDarkSidebar: true,
  },
  widget: {
    fullscreen: true,
    globalSearch: true,
    // 整改 E3（D4 签认）：业务文案未覆盖 i18n，隐藏语言切换消除半英文残态
    languageToggle: false,
    lockScreen: false,
    notification: true,
    refresh: true,
    sidebarToggle: true,
    themeToggle: true,
  },
});

/**
 * 整改 E1（2026-08-08）：preferencesExtension 已整体移除——
 * 4 个自定义字段（defaultTableSize/tenantMode/reportTitle/
 * enableFormFullscreen）全库零消费且 locale 键未定义（抽屉显示裸键），
 * tenantMode 为模板残留（单租户工业产品无此概念）。
 */
