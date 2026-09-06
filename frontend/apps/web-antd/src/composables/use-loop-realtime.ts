/**
 * 回路实时数据 composable（MW-P1-04）
 *
 * 从旧监控页 monitor.vue 迁移 tagCode 解析、PV/SP/OP/MODE 更新和质量码映射，
 * 供工作台和批量表格共用。复用全局 realtimeWs 单例，禁止创建第二连接。
 *
 * 核心能力：
 * - applyMessage：解析 WS 消息，局部更新匹配回路的 currentValues
 * - connectionStatus：响应式三态（online/reconnecting/offline）
 * - startFallback / stopFallback：WS 断连时的 30 秒轮询降级
 * - MODE 解析链（R17）：回路 modeMapping（REST 下发）→ 默认映射 → Unknown，
 *   删除旧"所有正数=Auto"硬编码；权威映射见 services/monitor.py
 * - 数值解析（R06）：utils/numeric.parseFiniteNumber，全字符串校验，
 *   无效值不整条丢弃——数值置 null、质量按消息独立更新
 *
 * 对齐整改方案 §9.2 与数据链路整改 S0 契约 §3/§6。
 */
import type { Ref } from 'vue';

import { onBeforeUnmount, ref } from 'vue';

import { useAccessStore } from '@vben/stores';

import { parseFiniteNumber } from '#/utils/numeric';
import { mapQualityToLabel } from '#/utils/quality-code';
import { type ConnectionStatus, realtimeWs } from '#/utils/realtime-ws';

/** WS 实时消息结构（对齐 realtime-ws.ts RealtimeMessage） */
export interface RealtimeMessage {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
  /** S0 契约 §6 增量字段（发布侧 S1/A 添加，可能尚未就绪）：值经共享契约解析是否有效 */
  valueValid?: boolean;
  /** S0 契约 §6 增量字段：接收时刻（消费侧容错缺省） */
  recvAt?: string;
  /** S0 契约 §6 增量字段：该值是否 last-known 标旧（消费侧容错缺省） */
  stale?: boolean;
}

/** 回路实时值（7 个：PV/SP/OP/MODE/P/I/D，全量 WS 实时推送） */
export interface LoopRealtimeValues {
  mode: null | number;
  modeLabel: null | string;
  op: null | number;
  pidD: null | number;
  pidI: null | number;
  pidP: null | number;
  pv: null | number;
  pvQuality: null | string;
  readAt: null | string;
  sp: null | number;
}

/** MODE 数值 → 控制模式标签映射（REST 下发，键为字符串） */
export type ModeMapping = Record<string, string>;

/** 可被 applyMessage 更新的回路项（鸭子类型，兼容 MonitorListItem） */
export interface RealtimeUpdatable {
  controlMode?: string;
  currentValues: LoopRealtimeValues;
  loopId: string;
  /** R17：该回路的 MODE 数值映射（REST 列表/详情下发；缺省用默认映射） */
  modeMapping?: ModeMapping | null;
  tagName: string;
}

/** useLoopRealtime 返回值 */
export interface UseLoopRealtimeReturn {
  /** WS 连接状态三态（响应式镜像） */
  connectionStatus: Ref<ConnectionStatus>;
  /** 最近一次 WS 消息到达时间 */
  lastMessageAt: Ref<Date | null>;
  /**
   * 将 WS 消息应用到匹配回路。
   * - 按 tagName 匹配；不匹配则跳过
   * - 7 个实时值全部支持：PV/SP/OP/MODE + PID_P/PID_I/PID_D
   * - MODE 按「modeMapping → 默认映射 → Unknown」解析（R17），未知值显式 Unknown
   * - 无效数值不整条丢弃：字段置 null，质量按消息独立更新（R06）
   * - PV 质量码统一走 mapQualityToLabel
   */
  applyMessage: (msg: RealtimeMessage, items: RealtimeUpdatable[]) => boolean;
  /** 注册页面级消息回调（返回取消订阅函数） */
  onMessage: (handler: (msg: RealtimeMessage) => void) => () => void;
  /** 启动 WS 连接（如尚未连接）并注册连接状态回调 */
  start: () => void;
  /** 停止页面级消息回调（不断开全局单例） */
  stop: () => void;
  /** 启动轮询降级（幂等：已在运行时不重复创建 interval） */
  startFallback: (pollFn: () => Promise<void>, intervalMs?: number) => void;
  /** 停止轮询降级 */
  stopFallback: () => void;
}

/** 默认轮询间隔（30 秒，对齐整改方案 §3.2 指标） */
const DEFAULT_FALLBACK_INTERVAL = 30_000;

/**
 * 默认 MODE 值 → 控制模式映射（R17；与后端 services/monitor.py
 * _DEFAULT_MODE_LABELS 一致：0=Manual，1=Auto，2=Cascade，3/4 归并 Auto）
 */
export const DEFAULT_MODE_LABELS: ModeMapping = {
  '0': 'Manual',
  '1': 'Auto',
  '2': 'Cascade',
  '3': 'Auto',
  '4': 'Auto',
};

/**
 * MODE 数值 → 控制模式标签（R17 解析链）。
 *
 * 优先该回路 REST 下发的 modeMapping（自定义 loop_mode_mapping 的生效结果），
 * 未命中回退默认映射，仍未命中显式返回 'Unknown'（不得保留旧标签冒充）。
 */
export function resolveModeLabel(
  mode: null | number | undefined,
  mapping?: ModeMapping | null,
): string {
  if (mode === null || mode === undefined || !Number.isFinite(mode)) {
    return 'Unknown';
  }
  const key = String(Math.trunc(mode));
  const label = mapping?.[key] ?? DEFAULT_MODE_LABELS[key];
  return label ?? 'Unknown';
}

/**
 * 位号角色后缀 → 语义角色映射（下划线风格解析用）。
 * 生产命名中 PID 参数后缀为 KP/TI/TD（如 `90TIC60004_PIDA_KP`），
 * 归一到后端角色模型 PID_P/PID_I/PID_D（对齐 loop_tag_mapping.tag_role）。
 */
const ROLE_SUFFIX_MAP: Record<string, string> = {
  KP: 'PID_P',
  MODE: 'MODE',
  OP: 'OP',
  PID_D: 'PID_D',
  PID_I: 'PID_I',
  PID_P: 'PID_P',
  PV: 'PV',
  SP: 'SP',
  TD: 'PID_D',
  TI: 'PID_I',
};

// 下划线风格角色后缀白名单（用于 tagCode 尾段匹配；PID_* 含下划线，靠正则回溯兼容）
const UNDERSCORE_ROLE_RE =
  /^(.*)_(PV|SP|OP|MODE|KP|TI|TD|PID_P|PID_I|PID_D)$/i;

/**
 * 解析 tagCode 为 { tagName, role }，兼容两种命名风格：
 * - 仿真点号风格：`80FIC11906_PIDA.PV` → { tagName: '80FIC11906_PIDA', role: 'PV' }
 * - 生产下划线风格：`90TIC60004_PIDA_PV` → 同上；`..._KP` → role 'PID_P'
 *
 * 2026-09-05 修复：此前仅支持点号风格，接入生产 AAS（下划线命名）后
 * 所有 WS 实时消息解析失败被丢弃，监控/总览页实时值冻结。
 */
export function parseTagCode(
  tagCode: string,
): null | { role: string; tagName: string } {
  // 点号风格（仿真 signal_sim 命名）
  const dotIdx = tagCode.lastIndexOf('.');
  if (dotIdx !== -1) {
    const tagName = tagCode.slice(0, Math.max(0, dotIdx));
    const role = tagCode.slice(Math.max(0, dotIdx + 1)).toUpperCase();
    if (!tagName || !role) return null;
    return { role, tagName };
  }
  // 下划线风格（生产 AAS 命名）：尾段角色白名单匹配
  const m = UNDERSCORE_ROLE_RE.exec(tagCode);
  if (!m) return null;
  const tagName = m[1] ?? '';
  const role = ROLE_SUFFIX_MAP[(m[2] ?? '').toUpperCase()];
  if (!tagName || !role) return null;
  return { role, tagName };
}

/**
 * 回路实时数据 composable。
 *
 * 注意：本 composable 只注册页面级消息回调和轮询定时器，
 * WS 全局连接由 layouts/basic.vue 管理生命周期，页面卸载只退订 handler。
 */
export function useLoopRealtime(): UseLoopRealtimeReturn {
  const connectionStatus = ref<ConnectionStatus>(realtimeWs.status);
  const lastMessageAt = ref<Date | null>(null);

  let messageUnsub: (() => void) | null = null;
  let connectionUnsub: (() => void) | null = null;
  let fallbackTimer: null | ReturnType<typeof setTimeout> = null;
  let fallbackRunning = false;

  /**
   * 将 WS 消息应用到匹配回路列表。
   * @returns true 表示有回路被更新（可用于触发 UI 刷新）
   */
  function applyMessage(
    msg: RealtimeMessage,
    items: RealtimeUpdatable[],
  ): boolean {
    const parsed = parseTagCode(msg.tagCode);
    if (!parsed) return false;

    const item = items.find((l) => l.tagName === parsed.tagName);
    if (!item) return false;

    const cv = item.currentValues;

    // R06：共享数值契约解析——"-1.#QNAN0"/"nan"/"Infinity"/"1e999"/空串 → null；
    // 发布侧增量字段 valueValid=false 同样视为无效（字段可能尚未就绪，容错缺省）
    const value =
      msg.valueValid === false ? null : parseFiniteNumber(msg.value);

    switch (parsed.role) {
      case 'MODE': {
        // R17：modeMapping → 默认映射 → Unknown；未知值显式 Unknown
        const label = resolveModeLabel(value, item.modeMapping);
        cv.mode = value;
        cv.modeLabel = label;
        if (item.controlMode !== undefined) item.controlMode = label;
        break;
      }
      case 'OP': {
        cv.op = value;
        break;
      }
      case 'PID_D': {
        cv.pidD = value;
        break;
      }
      case 'PID_I': {
        cv.pidI = value;
        break;
      }
      case 'PID_P': {
        cv.pidP = value;
        break;
      }
      case 'PV': {
        // R06：数值无效 → 置 null（页面显示不可用），质量仍按本条消息更新，
        // 不得保留旧值 + 旧 GOOD 标签冒充有效读数
        cv.pv = value;
        cv.pvQuality = mapQualityToLabel(msg.quality);
        break;
      }
      case 'SP': {
        cv.sp = value;
        break;
      }
      default: {
        return false;
      }
    }

    cv.readAt = msg.collectTime;
    lastMessageAt.value = new Date();
    return true;
  }

  /** 注册消息回调（页面级 handler），返回取消订阅函数 */
  function onMessage(handler: (msg: RealtimeMessage) => void): () => void {
    if (messageUnsub) {
      messageUnsub();
    }
    messageUnsub = realtimeWs.onMessage(handler);
    return () => {
      if (messageUnsub) {
        messageUnsub();
        messageUnsub = null;
      }
    };
  }

  /** 启动 WS 连接（如尚未连接）并注册连接状态回调 */
  function start(): void {
    const accessStore = useAccessStore();
    const token = accessStore.accessToken;
    if (!token) return;

    if (!realtimeWs.isConnected) {
      realtimeWs.connect(token);
    }

    // 注册连接状态变化回调（同步三态到响应式 ref；R19 注册即回调当前状态）
    if (!connectionUnsub) {
      connectionUnsub = realtimeWs.onConnectionChange(() => {
        connectionStatus.value = realtimeWs.status;
      });
    }
  }

  /** 停止页面级回调（不断开全局单例） */
  function stop(): void {
    stopFallback();
    if (messageUnsub) {
      messageUnsub();
      messageUnsub = null;
    }
    if (connectionUnsub) {
      connectionUnsub();
      connectionUnsub = null;
    }
  }

  /** 启动轮询降级（幂等：已在运行时不重复创建 interval） */
  function startFallback(
    pollFn: () => Promise<void>,
    intervalMs = DEFAULT_FALLBACK_INTERVAL,
  ): void {
    if (fallbackRunning) return;
    fallbackRunning = true;

    async function tick() {
      if (!fallbackRunning) return;
      try {
        await pollFn();
      } catch {
        // 静默失败，下次轮询继续
      }
      if (fallbackRunning) {
        fallbackTimer = setTimeout(() => void tick(), intervalMs);
      }
    }

    void tick();
  }

  /** 停止轮询降级 */
  function stopFallback(): void {
    fallbackRunning = false;
    if (fallbackTimer) {
      clearTimeout(fallbackTimer);
      fallbackTimer = null;
    }
  }

  onBeforeUnmount(() => {
    stop();
  });

  return {
    applyMessage,
    connectionStatus,
    lastMessageAt,
    onMessage,
    start,
    startFallback,
    stop,
    stopFallback,
  };
}
