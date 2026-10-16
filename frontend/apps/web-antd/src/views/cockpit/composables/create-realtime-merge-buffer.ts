/**
 * 回路墙 WS 实时消息短时合并缓冲（P5-04，03 号文 §8 实时更新节流）
 *
 * - 短时合并：窗口内同位号（tagCode）只保留最新一条，窗口到期批量应用
 *   ——30 卡 × 4 位号的高频推送合并为每窗口一轮响应式更新；
 * - 后台页停更：isHidden()（默认 document.hidden）为真时窗口到期不应用、
 *   仅继续保留各位号最新值（缓冲按位号去重，天然有界）；恢复可见时调
 *   flush() 立即冲刷；
 * - 定时器/可见性判定可注入（测试与调优）。
 */
export interface RealtimeMergeMessage {
  tagCode: string;
}

export interface RealtimeMergeBufferOptions {
  /** 合并窗口毫秒（P5-04 定稿 500，登记交接） */
  windowMs?: number;
  /** 是否处于后台/隐藏（默认 document.hidden） */
  isHidden?: () => boolean;
  /** 定时器实现（默认 setTimeout/clearTimeout，测试用假定时器注入） */
  schedule?: (
    fn: () => void,
    ms: number,
  ) => { clear: () => void };
}

export interface RealtimeMergeBuffer<
  T extends RealtimeMergeMessage = RealtimeMergeMessage,
> {
  /** 收到消息：进缓冲并（重新）安排窗口到期应用 */
  push: (msg: T) => void;
  /** 立即冲刷（恢复可见时调用；隐藏中调用为 no-op） */
  flush: () => void;
  /** 释放：清定时器与缓冲（组件卸载） */
  dispose: () => void;
}

export function createRealtimeMergeBuffer<
  T extends RealtimeMergeMessage,
>(
  apply: (messages: T[]) => void,
  options: RealtimeMergeBufferOptions = {},
): RealtimeMergeBuffer<T> {
  const {
    windowMs = 500,
    isHidden = () => document.hidden,
    schedule = (fn: () => void, ms: number) => {
      const timer = setTimeout(fn, ms);
      return { clear: () => clearTimeout(timer) };
    },
  } = options;

  const pending = new Map<string, T>();
  let timer: null | { clear: () => void } = null;

  function flush() {
    // 后台页停止高频更新：隐藏期间只缓冲不应用（可见时由调用方 flush）
    if (isHidden() || pending.size === 0) return;
    const batch = [...pending.values()];
    pending.clear();
    apply(batch);
  }

  function push(msg: T) {
    pending.set(msg.tagCode, msg);
    if (timer) return;
    timer = schedule(() => {
      timer = null;
      flush();
    }, windowMs);
  }

  function dispose() {
    timer?.clear();
    timer = null;
    pending.clear();
  }

  return { push, flush, dispose };
}
