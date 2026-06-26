import {
  defineOverridesPreferences,
  definePreferencesExtension,
} from '@vben/preferences';

interface WebAntdPreferencesExtension {
  defaultTableSize: number;
  enableFormFullscreen: boolean;
  reportTitle: string;
  tenantMode: 'multi' | 'single';
}

/**
 * 全局色彩语义规范（对齐 UI/UX v4.1 §3.3 设计令牌）
 *
 * 统一全系统的状态色彩编码，确保 KPI 卡片、徽章、图表、标签等元素
 * 使用一致的色彩语义。所有页面应优先引用这些常量，避免硬编码颜色值。
 */
export const THEME_COLORS = {
  /** 成功 / 优秀 / 已完成（Ant Design green-6） */
  SUCCESS: '#52c41a',
  /** 警告 / 待处理 / 需关注（Ant Design gold-6） */
  WARNING: '#faad14',
  /** 错误 / 差 / 失败 / 危险操作（Ant Design red-5） */
  DANGER: '#ff4d4f',
  /** 信息 / 进行中 / 品牌主色（工业蓝 #0D6EFD） */
  INFO: '#0d6efd',
  /** 中性 / 未知 / 未分类（Ant Design gray-5） */
  NEUTRAL: '#8c8c8c',
} as const;

/**
 * KPI 状态 → 色彩映射
 * 用于性能等级（优秀/良好/合格/差）及综合评估结果的色彩编码
 */
export const KPI_COLOR_MAP = {
  EXCELLENT: THEME_COLORS.SUCCESS,
  GOOD: '#73d13d',
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
    // 默认浅色模式
    mode: 'light',
    semiDarkHeader: false,
    semiDarkSidebar: false,
  },
  widget: {
    fullscreen: true,
    globalSearch: true,
    languageToggle: true,
    lockScreen: false,
    notification: true,
    refresh: true,
    sidebarToggle: true,
    themeToggle: true,
  },
});

export const preferencesExtension =
  definePreferencesExtension<WebAntdPreferencesExtension>({
    tabLabel: 'preferences.antd.tabLabel',
    title: 'preferences.antd.title',
    fields: [
      {
        component: 'switch',
        defaultValue: true,
        key: 'enableFormFullscreen',
        label: 'preferences.antd.fields.enableFormFullscreen.label',
        tip: 'preferences.antd.fields.enableFormFullscreen.tip',
      },
      {
        component: 'select',
        defaultValue: 'single',
        key: 'tenantMode',
        label: 'preferences.antd.fields.tenantMode.label',
        options: [
          {
            label: 'preferences.antd.fields.tenantMode.options.single.label',
            value: 'single',
          },
          {
            label: 'preferences.antd.fields.tenantMode.options.multi.label',
            value: 'multi',
          },
        ],
      },
      {
        component: 'number',
        componentProps: {
          max: 200,
          min: 10,
          step: 10,
        },
        defaultValue: 20,
        key: 'defaultTableSize',
        label: 'preferences.antd.fields.defaultTableSize.label',
      },
      {
        component: 'input',
        defaultValue: '',
        key: 'reportTitle',
        label: 'preferences.antd.fields.reportTitle.label',
        placeholder: 'preferences.antd.fields.reportTitle.placeholder',
      },
    ],
  });
