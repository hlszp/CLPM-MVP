/**
 * UI-01 工业语义色映射工具（v6.1 §3.1.3 / §14 C-01 C-02）
 *
 * 将 CLPM 业务状态枚举统一映射到 ZL 工业语义色 token：
 * - Emerald (--status-ok)        → 运行/成功/在线/实施完成
 * - Amber   (--status-warning)   → 待机/预警/部分/处理中
 * - Rose    (--status-error)     → 故障/严重/不可逆/失败
 * - Blue    (--status-info)      → 主操作/信息/待处理
 * - Slate   (--status-neutral)   → 中性/忽略/无数据/未知
 *
 * 替代业务页面散落的 Ant Design 字符串色与局部 hex，统一收敛到 --status-* token。
 */
import { computed } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';

/** 业务状态语义分类（严格对齐 ZL IndustrialDesignSystem.md §3 色彩系统） */
export type IndustrialStatus =
  | 'ok' // 运行/成功/在线/EXCELLENT/GOOD/IMPLEMENTED/SUCCESS
  | 'warning' // 待机/预警/部分/GOOD/IN_PROGRESS/PARTIAL/WARNING
  | 'error' // 故障/严重/不可逆/POOR/BAD/IGNORED/FAILED
  | 'info' // 主操作/待处理/PENDING/FAIR
  | 'neutral'; // 中性/忽略/无数据/INCONCLUSIVE/UNCERTAIN/UNKNOWN

/** 单条状态映射项 */
export interface IndustrialStatusMeta {
  /** 状态语义 */
  status: IndustrialStatus;
  /** CSS 变量 token 名（如 --status-ok） */
  tokenVar: string;
  /** 文本颜色（响应式，跟随主题） */
  color: string;
  /** 背景色（带透明度，用于 tag/badge 背景） */
  bgColor: string;
  /** 边框色（带透明度） */
  borderColor: string;
  /** 默认中文文案（具体业务可覆盖） */
  defaultText: string;
  /** 默认图标名（Iconify 图标名） */
  icon: string;
}

/** CLPM 状态枚举 → IndustrialStatus 映射表（覆盖所有 v6.0 状态机枚举值） */
const STATUS_MAP: Record<string, IndustrialStatus> = {
  // 评分状态（§8.2.2）
  SUCCESS: 'ok',
  PARTIAL: 'warning',
  INCONCLUSIVE: 'neutral',
  // 性能定级（§7.2.1）
  EXCELLENT: 'ok',
  GOOD: 'ok',
  FAIR: 'info',
  WARNING: 'warning',
  POOR: 'error',
  // PV 质量码（§7.2.4 / §8.2.5）
  GOOD_QUALITY: 'ok',
  BAD: 'error',
  UNCERTAIN: 'warning',
  // Action Tracker 状态（§7.2.2 / §8.2.3，对齐实现契约 v2.0）
  PENDING: 'info',
  IN_PROGRESS: 'warning',
  IMPLEMENTED: 'ok',
  IGNORED: 'neutral',
  // Loop 就绪状态（§8.2.1）
  READY: 'ok',
  PARTIAL_READY: 'warning',
  INACTIVE: 'neutral',
  // Tuning 状态（§8.2.6）
  DRAFT: 'neutral',
  TUNING_RUNNING: 'info',
  COMPLETED: 'ok',
  ROLLED_BACK: 'warning',
  // 通用业务状态
  RUNNING: 'ok',
  ONLINE: 'ok',
  OFFLINE: 'neutral',
  IDLE: 'warning',
  PAUSED: 'warning',
  ERROR: 'error',
  CRITICAL: 'error',
  STOPPED: 'error',
  ALARM: 'error',
  ACTIVE: 'ok',
  RESOLVED: 'ok',
  SUPPRESSED: 'neutral',
  UNKNOWN: 'neutral',
};

/** 浅色模式状态元数据 */
const LIGHT_STATUS_META: Record<IndustrialStatus, IndustrialStatusMeta> = {
  ok: {
    status: 'ok',
    tokenVar: '--status-ok',
    color: 'hsl(var(--status-ok))',
    bgColor: 'hsl(var(--status-ok) / 0.12)',
    borderColor: 'hsl(var(--status-ok) / 0.4)',
    defaultText: '正常',
    icon: 'lucide:check-circle',
  },
  warning: {
    status: 'warning',
    tokenVar: '--status-warning',
    color: 'hsl(var(--status-warning))',
    bgColor: 'hsl(var(--status-warning) / 0.12)',
    borderColor: 'hsl(var(--status-warning) / 0.4)',
    defaultText: '警告',
    icon: 'lucide:alert-triangle',
  },
  error: {
    status: 'error',
    tokenVar: '--status-error',
    color: 'hsl(var(--status-error))',
    bgColor: 'hsl(var(--status-error) / 0.12)',
    borderColor: 'hsl(var(--status-error) / 0.4)',
    defaultText: '故障',
    icon: 'lucide:x-circle',
  },
  info: {
    status: 'info',
    tokenVar: '--status-info',
    color: 'hsl(var(--status-info))',
    bgColor: 'hsl(var(--status-info) / 0.12)',
    borderColor: 'hsl(var(--status-info) / 0.4)',
    defaultText: '处理中',
    icon: 'lucide:info',
  },
  neutral: {
    status: 'neutral',
    tokenVar: '--status-neutral',
    color: 'hsl(var(--status-neutral))',
    bgColor: 'hsl(var(--status-neutral) / 0.12)',
    borderColor: 'hsl(var(--status-neutral) / 0.4)',
    defaultText: '未知',
    icon: 'lucide:minus-circle',
  },
};

/**
 * 获取业务状态对应的工业语义色元数据（响应式，跟随主题切换）
 *
 * @example
 * ```ts
 * const { getStatusMeta } = useIndustrialStatus();
 * const meta = getStatusMeta('INCONCLUSIVE'); // → neutral 元数据
 * // 在模板中：
 * // <Tag :color="meta.color" :style="{ background: meta.bgColor }">{{ meta.defaultText }}</Tag>
 * ```
 */
export function useIndustrialStatus() {
  const { isDark } = useClpmTheme();

  /** 暗色模式色值与浅色相同（基于 HSL 变量），但 --status-* 已在 dark 作用域覆盖 */
  const statusMetaMap = computed(() => LIGHT_STATUS_META);

  /** 根据业务枚举值获取状态元数据 */
  function getStatusMeta(status: string): IndustrialStatusMeta {
    const industrial = STATUS_MAP[status] ?? 'neutral';
    return statusMetaMap.value[industrial];
  }

  /** 直接根据 IndustrialStatus 获取元数据 */
  function getMetaByStatus(status: IndustrialStatus): IndustrialStatusMeta {
    return statusMetaMap.value[status];
  }

  /** 业务枚举 → IndustrialStatus 转换 */
  function toIndustrialStatus(status: string): IndustrialStatus {
    return STATUS_MAP[status] ?? 'neutral';
  }

  return {
    isDark,
    statusMetaMap,
    getStatusMeta,
    getMetaByStatus,
    toIndustrialStatus,
  };
}

/** 状态映射表导出（供静态场景使用） */
export { STATUS_MAP, LIGHT_STATUS_META };
export type { IndustrialStatus as IndustrialStatusType };
