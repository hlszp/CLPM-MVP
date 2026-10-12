/**
 * 回路性能详情/可信度抽屉共享件（P2-3 抽取自 loop-performance.vue）
 *
 * 抽取背景：回路工作台新版（workbench360）评估详情抽屉复用本页
 * 详情/可信度内容（禁分叉第二套实现）。本文件承载两抽屉共用的
 * 类型/映射/格式化函数，原页面与新工作台均从本文件导入。
 * 抽取原则：逐字搬运原页面既有定义，行为不变。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';
import type { KpiSnapshotItem } from '#/api/metric';

import dayjs from 'dayjs';

import { GRADE_LEVEL_LABEL } from '#/constants/clpm-ui';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

/** 合并行类型：快照 + 回路元数据（原页面 LoopPerformanceRow 原样搬移） */
export interface LoopPerformanceRow extends KpiSnapshotItem {
  /** 关联的回路元数据（来自 loops 列表） */
  loopMeta?: LoopApi.LoopListItem;
  /** 回路描述（来自 loopMeta.description） */
  description?: string;
  /** 回路类型（来自 loopMeta.loopType） */
  loopType?: string;
  /** 控制类型（来自 loopMeta.controlType） */
  controlType?: string;
  /** 控制方式（来自 loopMeta.controlMode） */
  controlMode?: string;
  /** P2 IA优化：适用性等级（L0/L1/L2/L3 等），L0/L1=不适用，走中性灰 */
  fitnessLevel?: null | string;
  /** P2 IA优化：适用性原因标签，不适用时 Tooltip 用 */
  fitnessTags?: null | string[];
}

/** 控制类型映射（原样搬移） */
export const CONTROL_TYPE_MAP: Record<string, string> = {
  STABLE: '稳定型',
  SLOW: '慢速型',
  FAST: '快速型',
  LOGIC: '逻辑型',
};

/** 评估状态映射（原样搬移） */
export const STATUS_COLOR_MAP: Record<string, string> = {
  SUCCESS: 'success',
  PARTIAL: 'warning',
  INCONCLUSIVE: 'default',
};

export const STATUS_LABEL_MAP: Record<string, string> = {
  SUCCESS: '成功',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

/** 可信度徽章颜色（原样搬移） */
export const CONFIDENCE_COLOR_MAP: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
};

export const CONFIDENCE_LABEL_MAP: Record<string, string> = {
  A: 'A 优秀',
  B: 'B 良好',
  C: 'C 一般',
  D: 'D 较差',
  E: 'E 不足',
};

/** 评估等级颜色（优秀/良好/合格/警告/不合格；原样搬移） */
export const GRADE_COLOR_MAP: Record<number, string> = {
  1: 'green',
  2: 'blue',
  3: 'gold',
  4: 'orange',
  5: 'red',
};

/** 0929 口径收敛：等级中文名走 constants/clpm-ui 唯一档位定义 */
export const GRADE_LABEL_MAP: Record<number, string> = GRADE_LEVEL_LABEL;

/** 12 子指标元数据（3+1+8 体系，键为 DB 列名 snake_case；原样搬移） */
export const CONFIDENCE_METRIC_META: {
  key: string;
  label: string;
  unit: string;
}[] = [
  { key: 'accuracy_rate', label: '准确率', unit: '%' },
  { key: 'fast_rate', label: '快速率', unit: '%' },
  { key: 'steady_rate', label: '平稳率', unit: '%' },
  { key: 'effective_auto_rate', label: '有效自控率', unit: '%' },
  { key: 'good_value_rate', label: '好值率', unit: '%' },
  { key: 'auto_mode_rate', label: '自控率', unit: '%' },
  { key: 'settling_time', label: '稳定时间', unit: 's' },
  { key: 'ideal_settling_time', label: '理想稳定时间', unit: 's' },
  { key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { key: 'saturation_rate', label: '饱和率', unit: '%' },
  { key: 'stiction_index', label: '阀门粘滞指数', unit: '' },
  { key: 'output_trip_index', label: '输出跳变率', unit: '' },
];

/** 详情抽屉历史快照子表列（原页面 diagHistoryColumns 原样搬移） */
export const DETAIL_HISTORY_COLUMNS: TableColumnsType = [
  {
    title: '时间窗',
    key: 'tsRange',
    width: 140,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 80,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 80,
  },
  {
    title: '平稳率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 80,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 100,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 80,
  },
  {
    title: '状态',
    key: 'status',
    dataIndex: 'status',
    width: 90,
  },
];

/** 可信度子指标表列（原页面 confMetricColumns 原样搬移） */
export const CONF_METRIC_COLUMNS: TableColumnsType = [
  { title: '指标', key: 'label', dataIndex: 'label' },
  {
    title: '计算值',
    key: 'value',
    dataIndex: 'value',
    width: 120,
    align: 'right' as const,
  },
];

/** 时间字符串规范化（PostgreSQL timestamp without timezone 假定为 UTC） */
export function normalizeTime(ts: null | string | undefined): null | string {
  if (!ts) return null;
  return normalizeUtcTimestamp(ts);
}

export function formatTsRange(
  start: null | string,
  end: null | string,
): string {
  const s = normalizeTime(start);
  const e = normalizeTime(end);
  if (!s && !e) return '—';
  const fmt = 'MM-DD HH:mm';
  if (s && e) {
    const ds = dayjs(s);
    const de = dayjs(e);
    // 同一天：MM-DD HH:mm~HH:mm（省略第二个日期）
    if (ds.isSame(de, 'day')) {
      return `${ds.format(fmt)}~${de.format('HH:mm')}`;
    }
    return `${ds.format(fmt)} ~ ${de.format(fmt)}`;
  }
  return dayjs(e || s).format(fmt);
}

export function formatFullTime(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'YYYY-MM-DD HH:mm:ss');
}

export function formatNumber(
  val: null | number | undefined,
  suffix = '',
): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

/** 0~1 比率（如 validRate）格式化为百分比 */
export function formatRatio(val: null | number | undefined): string {
  if (val === null || val === undefined) return '—';
  return `${(val * 100).toFixed(2)}%`;
}

export function getMetricValue(
  record: object,
  dataIndex: string,
): null | number | undefined {
  const value = (record as unknown as Record<string, unknown>)[dataIndex];
  return typeof value === 'number' ? value : undefined;
}

/**
 * G3-R03（14 号文 §4）：请求错误 → 用户可读失败原因
 *
 * 两个历史入口（可信度详情/性能详情历史子表）共用，错误态展示显式原因，
 * 不把失败冒充"暂无记录"。错误形状对齐 requestClient 抛出链：
 * HTTP 非 2xx/3xx 为 axios error（response.status / response.data.message）；
 * HTTP 200 但业务 code!=0 时 defaultResponseInterceptor 抛"展开 response
 * 的对象"（status=200 + data.message）。超时无 response（code=ECONNABORTED）。
 * abort 取消不会到达此处——组件侧每次发起前 bump 代次，被取消的响应
 * 先被 guard 拒绝丢弃。
 */
export function describeRequestError(error: unknown): string {
  const err = error as null | {
    code?: unknown;
    message?: unknown;
    response?: {
      data?: { message?: unknown };
      status?: number;
    };
  };
  const status = err?.response?.status;
  const bizMessage =
    typeof err?.response?.data?.message === 'string'
      ? err.response.data.message
      : '';

  if (status === 403) {
    return '无权限访问（403），请确认当前账号权限';
  }
  if (status === 422) {
    return `请求被拒绝（422）${bizMessage ? `：${bizMessage}` : '：参数校验失败'}`;
  }
  if (typeof status === 'number' && status >= 500) {
    return `服务异常（${status}），请稍后重试`;
  }
  if (typeof status === 'number' && status !== 200) {
    return `请求失败（${status}）${bizMessage ? `：${bizMessage}` : ''}`;
  }
  if (status === 200) {
    // 业务错误（HTTP 成功但 code!=0）：展示后端 message
    const msg =
      bizMessage || (typeof err?.message === 'string' ? err.message : '');
    return msg ? `请求失败：${msg}` : '请求失败（业务错误）';
  }
  // 无 HTTP 状态：超时（axios ECONNABORTED + timeout）或网络异常
  if (
    err?.code === 'ECONNABORTED' ||
    /timeout/i.test(typeof err?.message === 'string' ? err.message : '')
  ) {
    return '请求超时，请稍后重试';
  }
  const msg = typeof err?.message === 'string' ? err.message : '';
  return msg ? `请求失败：${msg}` : '网络异常或请求失败，请稍后重试';
}
