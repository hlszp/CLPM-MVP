/**
 * 失分→诊断剖面联动预填（workbench360 P2，P2-4；v3 §6.1 评估历史行「诊断此窗」）
 *
 * 动线：评估剖面历史行「诊断此窗」→ 前端切诊断剖面并预填时间窗（不发新请求，
 * 契约 §1.3）。诊断剖面发起表单消费该状态（唯一事实源，避免第二套预填通道）。
 *
 * P6-02a（O02-CORE，2026-10-14）：预填状态从模块级 reactive 迁入实例会话
 * （use-wb360-session，含对象身份；对象/证据切换自动失效，双实例互不污染）。
 * 本模块保留原导出签名作会话委托：剖面组件（DiagSection/DiagTriggerModal）
 * 零改动——读经 inject 解析本实例会话，写经会话栈顶（当前可见实例）解析；
 * 无会话兜底为惰性兼容态（真实消费方均处于会话内，不会到达）。
 */
import { inject, reactive, readonly } from 'vue';

import {
  currentWb360Session,
  WB360_SESSION_KEY,
} from './use-wb360-session';

export interface DiagPrefill {
  /** 预填来源标识（评估快照窗口） */
  from: 'assess' | null;
  /** 目标时间窗（ISO 本地字符串，评估快照 tsStart/tsEnd 原样透传） */
  tsEnd: null | string;
  tsStart: null | string;
}

function emptyPrefill(): DiagPrefill {
  return { from: null, tsEnd: null, tsStart: null };
}

/** 无会话兜底（兼容态：仅在会话外误用时承载，不跨实例共享真实预填） */
const legacyFallback = reactive<DiagPrefill>(emptyPrefill());

/** 读目标：优先本组件注入的实例会话（setup 同步路径调用方有效） */
function readTarget(): DiagPrefill {
  const session = inject(WB360_SESSION_KEY, null);
  return session?.diagPrefill ?? legacyFallback;
}

/** 写目标：当前可见实例会话（栈顶；事件只可能来自可见实例） */
function writeTarget(): DiagPrefill {
  return currentWb360Session()?.diagPrefill ?? legacyFallback;
}

/** 设置诊断预填时间窗（评估行「诊断此窗」调用） */
export function setDiagPrefill(tsStart: string, tsEnd: string) {
  const t = writeTarget();
  t.from = 'assess';
  t.tsStart = tsStart;
  t.tsEnd = tsEnd;
}

/** 清除预填（诊断剖面发起后或用户手动清除时调用） */
export function clearDiagPrefill() {
  const t = writeTarget();
  t.from = null;
  t.tsStart = null;
  t.tsEnd = null;
}

/** 只读预填状态（诊断剖面消费；实例会话内调用即得本实例预填） */
export function useDiagPrefill() {
  return readonly(readTarget());
}
