/**
 * 诊断→整定剖面联动预填（workbench360 P4；v3 §6.2 诊断抽屉「基于此结论发起整定」）
 *
 * 动线：诊断行详情抽屉 footer「基于此结论发起整定 →（页内）」→ 切整定剖面，
 * 预填辨识时间窗（诊断 run timeWindow）与上下文说明（主分类/置信度），不发新请求。
 * 整定剖面在辨识步骤消费该状态（唯一事实源，对齐 use-diag-prefill 模式）；
 * 发起辨识或用户手动清除后清空（下钻契约：携带参数必须消费并回显、可清除）。
 */
import { reactive, readonly } from 'vue';

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

const prefill = reactive<TuningPrefill>({
  categoryLabel: null,
  from: null,
  primaryConfidence: null,
  tsEnd: null,
  tsStart: null,
});

/** 设置整定预填（诊断抽屉「基于此结论发起整定」调用） */
export function setTuningPrefill(payload: {
  categoryLabel?: null | string;
  primaryConfidence?: null | number;
  tsEnd: string;
  tsStart: string;
}) {
  prefill.from = 'diag';
  prefill.categoryLabel = payload.categoryLabel ?? null;
  prefill.primaryConfidence = payload.primaryConfidence ?? null;
  prefill.tsStart = payload.tsStart;
  prefill.tsEnd = payload.tsEnd;
}

/** 清除预填（发起辨识后或用户手动清除时调用） */
export function clearTuningPrefill() {
  prefill.from = null;
  prefill.categoryLabel = null;
  prefill.primaryConfidence = null;
  prefill.tsStart = null;
  prefill.tsEnd = null;
}

/** 只读预填状态（整定剖面消费） */
export function useTuningPrefill() {
  return readonly(prefill);
}
