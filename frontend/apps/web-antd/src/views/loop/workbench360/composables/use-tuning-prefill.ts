/**
 * 诊断→整定剖面联动预填（workbench360 P4；v3 §6.2 诊断抽屉「基于此结论发起整定」）
 *
 * 动线：诊断行详情抽屉 footer「基于此结论发起整定 →（页内）」→ 切整定剖面，
 * 预填辨识时间窗（诊断 run timeWindow）与上下文说明（主分类/置信度），不发新请求。
 * 整定剖面在辨识步骤消费该状态（唯一事实源，对齐 use-diag-prefill 模式）；
 * 发起辨识或用户手动清除后清空（下钻契约：携带参数必须消费并回显、可清除）。
 *
 * P6-02a（O02-CORE，2026-10-14）：预填状态从模块级 reactive 迁入实例会话
 * （use-wb360-session，含对象身份；对象/证据切换自动失效，双实例互不污染）。
 * 本模块保留原导出签名作会话委托，TuningSection 零改动（读 inject / 写栈顶）。
 */
import { inject, reactive, readonly } from 'vue';

import {
  currentWb360Session,
  WB360_SESSION_KEY,
} from './use-wb360-session';

export interface TuningPrefill {
  /** 主分类文案（上下文提示用） */
  categoryLabel: null | string;
  /** 预填来源标识（诊断结论） */
  from: 'diag' | null;
  /** 置信度（0~1；上下文提示用） */
  primaryConfidence: null | number;
  /** 目标辨识时间窗（ISO 本地字符串，诊断 run timeWindowStart/End 原样透传） */
  tsEnd: null | string;
  tsStart: null | string;
}

/** 无会话兜底（兼容态：仅在会话外误用时承载，不跨实例共享真实预填） */
const legacyFallback = reactive<TuningPrefill>({
  categoryLabel: null,
  from: null,
  primaryConfidence: null,
  tsEnd: null,
  tsStart: null,
});

function readTarget(): TuningPrefill {
  const session = inject(WB360_SESSION_KEY, null);
  return session?.tuningPrefill ?? legacyFallback;
}

function writeTarget(): TuningPrefill {
  return currentWb360Session()?.tuningPrefill ?? legacyFallback;
}

/** 设置整定预填（诊断抽屉「基于此结论发起整定」调用） */
export function setTuningPrefill(payload: {
  categoryLabel?: null | string;
  primaryConfidence?: null | number;
  tsEnd: string;
  tsStart: string;
}) {
  const t = writeTarget();
  t.from = 'diag';
  t.categoryLabel = payload.categoryLabel ?? null;
  t.primaryConfidence = payload.primaryConfidence ?? null;
  t.tsStart = payload.tsStart;
  t.tsEnd = payload.tsEnd;
}

/** 清除预填（发起辨识后或用户手动清除时调用） */
export function clearTuningPrefill() {
  const t = writeTarget();
  t.from = null;
  t.categoryLabel = null;
  t.primaryConfidence = null;
  t.tsStart = null;
  t.tsEnd = null;
}

/** 只读预填状态（整定剖面消费；实例会话内调用即得本实例预填） */
export function useTuningPrefill() {
  return readonly(readTarget());
}
