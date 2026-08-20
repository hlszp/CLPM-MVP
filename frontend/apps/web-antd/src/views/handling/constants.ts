/**
 * 处置模块展示常量
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md
 * - v2.0（§4 双状态机 / §8.1 工作台双 Tab）：建议 4 态 + 工单 6 态常量
 * - v1.x（Phase 1）：STATUS_* / STATUS_TAB_OPTIONS 等仅供存量页面引用，勿删
 */

import type { HandlingApi } from '#/api/handling';

/** 状态中文名（§4.1） */
export const STATUS_TEXT: Record<HandlingApi.Status, string> = {
  PENDING: '待处置',
  HANDLING: '处置中',
  VERIFYING: '验证中',
  CLOSED: '已闭环',
  REOPENED: '重开',
  IGNORED: '已忽略',
};

/**
 * 状态色（§8.2 对齐项目工业色约定）：
 * 待处置=橙 / 处置中=蓝 / 验证中=青 / 已闭环=绿 / 重开=红 / 已忽略=灰
 */
export const STATUS_COLOR: Record<HandlingApi.Status, string> = {
  PENDING: 'orange',
  HANDLING: 'blue',
  VERIFYING: 'cyan',
  CLOSED: 'green',
  REOPENED: 'red',
  IGNORED: 'default',
};

/** 状态 tabs（全部置空前端不传 status 参数） */
export const STATUS_TAB_OPTIONS: Array<{
  label: string;
  value: '' | HandlingApi.Status;
}> = [
  { label: '全部', value: '' },
  { label: '待处置', value: 'PENDING' },
  { label: '处置中', value: 'HANDLING' },
  { label: '验证中', value: 'VERIFYING' },
  { label: '已闭环', value: 'CLOSED' },
  { label: '重开', value: 'REOPENED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 处置类型中文名（§5.1） */
export const ACTION_TYPE_TEXT: Record<HandlingApi.ActionType, string> = {
  TUNING: '参数整定',
  VALVE: '阀门检修',
  INSTRUMENT: '仪表校验',
  LINK: '链路修复',
  PROCESS: '工艺调整',
  UTILIZATION: '恢复投用',
  RECONFIG: '组态改造',
  OTHER: '其他',
};

export const ACTION_TYPE_OPTIONS = (
  Object.keys(ACTION_TYPE_TEXT) as HandlingApi.ActionType[]
).map((v) => ({ label: ACTION_TYPE_TEXT[v], value: v }));

/** 来源中文名 */
export const SOURCE_TEXT: Record<HandlingApi.Source, string> = {
  SYSTEM: '系统建议',
  MANUAL: '人工新增',
};

/** 验证结论中文名 */
export const VERIFY_RESULT_TEXT: Record<HandlingApi.VerifyResult, string> = {
  EFFECTIVE: '有效',
  INEFFECTIVE: '无效',
};

/**
 * 结构化处置详情字段 schema（§5.2，前端常量 + 后端轻校验）。
 * 开始处置/提交验证两阶段共用的字段定义；TUNING 的 P/I/D 组由抽屉单独渲染。
 */
export interface DetailField {
  key: string;
  label: string;
  type: 'number' | 'text';
  placeholder?: string;
}

export const ACTION_DETAIL_FIELDS: Record<
  HandlingApi.ActionType,
  DetailField[]
> = {
  TUNING: [
    {
      key: 'method',
      label: '整定方法',
      type: 'text',
      placeholder: '如 Lambda 整定法',
    },
  ],
  VALVE: [
    {
      key: 'parts',
      label: '检修内容',
      type: 'text',
      placeholder: '如 更换填料函',
    },
    { key: 'downtimeHours', label: '停工时长(h)', type: 'number' },
    { key: 'vendor', label: '检修单位', type: 'text' },
  ],
  INSTRUMENT: [
    {
      key: 'instrument',
      label: '仪表位号',
      type: 'text',
      placeholder: '如 变送器 PT-5121',
    },
    { key: 'calibrationResult', label: '校验结果', type: 'text' },
    {
      key: 'nextDue',
      label: '下次校验日期',
      type: 'text',
      placeholder: 'YYYY-MM-DD',
    },
  ],
  LINK: [
    {
      key: 'action',
      label: '修复动作',
      type: 'text',
      placeholder: '如 更换交换机端口',
    },
    { key: 'rootCause', label: '根因', type: 'text' },
  ],
  PROCESS: [
    { key: 'measure', label: '调整措施', type: 'text' },
    { key: 'operator', label: '执行班组', type: 'text' },
  ],
  UTILIZATION: [
    {
      key: 'autoRateBefore',
      label: '投用前自控率',
      type: 'number',
      placeholder: '0~1',
    },
    {
      key: 'autoRateAfter',
      label: '投用后自控率',
      type: 'number',
      placeholder: '0~1',
    },
  ],
  RECONFIG: [
    {
      key: 'change',
      label: '改造内容',
      type: 'text',
      placeholder: '如 量程 0-2.5MPa 改 0-1.6MPa',
    },
    { key: 'dcEngineer', label: 'DCS 工程师', type: 'text' },
  ],
  OTHER: [{ key: 'note', label: '说明', type: 'text' }],
};

// ===========================================================================
// v2.0 双实体常量（§4 状态机 / §8.1 工作台双 Tab）
// ===========================================================================

/** 建议状态中文名（§4.1，4 态 + 忽略终态） */
export const SUGGESTION_STATUS_TEXT: Record<
  HandlingApi.SuggestionStatus,
  string
> = {
  PENDING: '待审核',
  ACCEPTED: '已接受',
  CONVERTED: '已转工单',
  REJECTED: '已驳回',
  IGNORED: '已忽略',
};

/** 建议状态色（§8.1）：待审核=橙 / 已接受=蓝 / 已转工单=绿 / 已驳回=红 / 已忽略=灰 */
export const SUGGESTION_STATUS_COLOR: Record<
  HandlingApi.SuggestionStatus,
  string
> = {
  PENDING: 'orange',
  ACCEPTED: 'blue',
  CONVERTED: 'green',
  REJECTED: 'red',
  IGNORED: 'default',
};

/** 建议状态筛选（工作台 Tab1；全部置空前端不传 status 参数） */
export const SUGGESTION_TAB_OPTIONS: Array<{
  label: string;
  value: '' | HandlingApi.SuggestionStatus;
}> = [
  { label: '全部', value: '' },
  { label: '待审核', value: 'PENDING' },
  { label: '已接受', value: 'ACCEPTED' },
  { label: '已转工单', value: 'CONVERTED' },
  { label: '已驳回', value: 'REJECTED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 工单状态中文名（§4.2，6 态） */
export const ORDER_STATUS_TEXT: Record<HandlingApi.OrderStatus, string> = {
  PENDING: '待执行',
  EXECUTING: '执行中',
  VERIFYING: '验证中',
  CLOSED: '已闭环',
  REOPENED: '重开',
  CANCELLED: '已作废',
};

/** 工单状态色（§8.1）：待执行=橙 / 执行中=蓝 / 验证中=青 / 已闭环=绿 / 重开=红 / 已作废=灰 */
export const ORDER_STATUS_COLOR: Record<HandlingApi.OrderStatus, string> = {
  PENDING: 'orange',
  EXECUTING: 'blue',
  VERIFYING: 'cyan',
  CLOSED: 'green',
  REOPENED: 'red',
  CANCELLED: 'default',
};

/** 工单状态筛选（工作台 Tab2；全部置空前端不传 status 参数） */
export const ORDER_TAB_OPTIONS: Array<{
  label: string;
  value: '' | HandlingApi.OrderStatus;
}> = [
  { label: '全部', value: '' },
  { label: '待执行', value: 'PENDING' },
  { label: '执行中', value: 'EXECUTING' },
  { label: '验证中', value: 'VERIFYING' },
  { label: '已闭环', value: 'CLOSED' },
  { label: '重开', value: 'REOPENED' },
  { label: '已作废', value: 'CANCELLED' },
];

/** 工单来源中文名（§3.2 source：建议转化 / 手动新建） */
export const ORDER_SOURCE_TEXT: Record<HandlingApi.OrderSource, string> = {
  DIAGNOSIS: '建议转化',
  MANUAL: '手动新建',
};
