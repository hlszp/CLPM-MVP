/**
 * 失分→诊断剖面联动预填（workbench360 P2，P2-4；v3 §6.1 评估历史行「诊断此窗」）
 *
 * 动线：评估剖面历史行「诊断此窗」→ 前端切诊断剖面并预填时间窗（不发新请求，
 * 契约 §1.3）。P2 落地状态承载与诊断剖面占位显式回显；P3 诊断剖面实现时
 * 在发起表单消费该状态（唯一事实源，避免第二套预填通道）。
 */
import { reactive, readonly } from 'vue';

export interface DiagPrefill {
  /** 预填来源标识（评估快照窗口） */
  from: 'assess' | null;
  /** 目标时间窗（ISO 本地字符串，评估快照 tsStart/tsEnd 原样透传） */
  tsEnd: null | string;
  tsStart: null | string;
}

const prefill = reactive<DiagPrefill>({
  from: null,
  tsEnd: null,
  tsStart: null,
});

/** 设置诊断预填时间窗（评估行「诊断此窗」调用） */
export function setDiagPrefill(tsStart: string, tsEnd: string) {
  prefill.from = 'assess';
  prefill.tsStart = tsStart;
  prefill.tsEnd = tsEnd;
}

/** 清除预填（诊断剖面发起后或用户手动清除时调用；P3 接管） */
export function clearDiagPrefill() {
  prefill.from = null;
  prefill.tsStart = null;
  prefill.tsEnd = null;
}

/** 只读预填状态（诊断剖面消费） */
export function useDiagPrefill() {
  return readonly(prefill);
}
