/**
 * WebSocket 实时数据推送客户端
 *
 * 连接后端 /api/v1/ws/realtime 端点，接收实时 Tag 值更新。
 * 支持自动重连、心跳检测、消息回调。
 *
 * 数据链路整改（R15/R18/R19，2026-09-06 S3/C）：
 * - R15 兼容：识别服务端批量帧 `{"type":"batch","items":[...]}`（逐条分发给
 *   消息回调）；未知 `type` 的控制帧（ping/subscribed 等）静默忽略——旧单对象
 *   格式不含 type 字段，行为不变；
 * - R18 连接竞态：connect/reconnect/disconnect 走统一状态机——connect 先清
 *   reconnectTimer、CONNECTING/OPEN 幂等；被替换旧 socket 先解除引用再关闭，
 *   其事件经"当前连接身份"守卫忽略；timer 触发的 _doConnect 同样幂等；
 * - R19 状态通知：每次完整状态迁移（offline/reconnecting/online）后统一
 *   _notifyConnectionChange；close 先置状态（通知 offline）→ 设置重连 timer
 *   后状态变 reconnecting（再次通知）；新订阅者注册时立即回调当前状态一次。
 */

import { useAccessStore } from '@vben/stores';

type RealtimeMessage = {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
};

type MessageHandler = (msg: RealtimeMessage) => void;

/** WS 连接状态三态（Phase 10 UX 包：监控页在线状态栏） */
export type ConnectionStatus = 'offline' | 'online' | 'reconnecting';

const RECONNECT_INTERVAL = 3000; // 重连间隔 3 秒
const MAX_RECONNECT_DELAY = 30_000; // 最大重连延迟 30 秒

class RealtimeWebSocket {
  /**
   * 是否已连接
   */
  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /**
   * 连接状态三态（Phase 10 UX 包：监控页在线状态栏）
   * - online：WebSocket OPEN，实时推送正常
   * - reconnecting：已断开但重连定时器在跑，自动恢复中
   * - offline：尚未连接或手动关闭，无自动重连
   */
  get status(): ConnectionStatus {
    if (this.ws?.readyState === WebSocket.OPEN) return 'online';
    if (this.reconnectTimer !== null) return 'reconnecting';
    return 'offline';
  }
  private baseUrl: string;
  private connectionHandlers = new Set<() => void>();
  private handlers = new Set<MessageHandler>();
  private isManualClose = false;
  private reconnectAttempts = 0;
  private reconnectTimer: null | ReturnType<typeof setTimeout> = null;
  private token: string = '';

  private ws: null | WebSocket = null;

  constructor() {
    // 从环境变量获取后端地址，构造 WebSocket URL
    const apiUrl = import.meta.env.VITE_GLOB_API_URL || '';
    // apiUrl 格式: http://localhost:17101/api/v1 → ws://localhost:17101/api/v1/ws/realtime
    const wsBaseUrl = apiUrl
      .replace(/^http:/, 'ws:')
      .replace(/^https:/, 'wss:')
      .replace(/\/api\/v1$/, '');
    this.baseUrl = `${wsBaseUrl}/api/v1/ws/realtime`;
  }

  /**
   * 连接 WebSocket（统一建连入口，R18）
   *
   * - 先清除挂起的重连 timer（页面 connect 与自动重连不再交错创建第二条连接）；
   * - 幂等保护：连接建立中/已连接且 token 未变化时直接复用，
   *   避免 layout 全局建连与 monitor/tag 页面级建连产生重复 WebSocket；
   * - token 变化或连接已关闭：经 _startConnection 替换旧连接（旧 socket 的
   *   事件经"当前连接身份"守卫忽略）。
   */
  connect(token: string) {
    this._clearReconnectTimer();
    if (
      this.token === token &&
      (this.ws?.readyState === WebSocket.OPEN ||
        this.ws?.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this._startConnection(token);
  }

  /**
   * 断开连接（显式断开：停止重连、关闭当前连接并通知 offline）
   */
  disconnect() {
    this.isManualClose = true;
    this._clearReconnectTimer();
    this.reconnectAttempts = 0;
    const wasActive = this.ws !== null || this.reconnectTimer !== null;
    this._teardownSocket();
    if (wasActive) {
      this._notifyConnectionChange();
    }
  }

  /**
   * 注册连接状态变化回调（P2 #38 UX14 / R19）
   *
   * 每次完整状态迁移（offline/reconnecting/online）后触发；
   * 注册时立即回调一次当前状态（新订阅者可同步初始态，覆盖初始非 online）。
   */
  onConnectionChange(handler: () => void): () => void {
    this.connectionHandlers.add(handler);
    // R19：注册即回调，订阅方无需等待下一次迁移就能拿到当前状态
    handler();
    return () => {
      this.connectionHandlers.delete(handler);
    };
  }

  /**
   * 注册消息回调
   */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  /**
   * P2-08：手动重连（清除自动重连定时器后立即重连）
   *
   * 供断线提示 Banner 的"重连"按钮调用。
   */
  reconnect() {
    this._clearReconnectTimer();
    this.reconnectAttempts = 0;
    this._startConnection(this.token);
  }

  // -------------------------------------------------------------------------
  // 内部状态机（R18：所有建连路径收敛到 _startConnection）
  // -------------------------------------------------------------------------

  private _clearReconnectTimer() {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private _doConnect() {
    // R18：timer 触发 / 手动重连等路径同样幂等——已存在活跃连接时不重建
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    // 重连前实时读取 store token：accessToken 30min 过期后由 REST 401
    // → doRefreshToken 静默换新，若仍用固化的旧 token 会陷入 403 重连
    // 死循环（实时数据推送从此断流）。store 取不到时回退上次 token。
    this.token = useAccessStore().accessToken || this.token;
    if (!this.token) return;

    try {
      this.ws = new WebSocket(
        `${this.baseUrl}?token=${encodeURIComponent(this.token)}`,
      );
    } catch {
      this._scheduleReconnect();
      return;
    }

    // 连接身份：事件回调仅对"当前生效连接"生效，被替换的旧连接事件全部忽略
    const activeWs = this.ws;

    activeWs.addEventListener('open', () => {
      if (this.ws !== activeWs) return;
      this.reconnectAttempts = 0;
      // P3 #57: 控制台日志环境守卫，生产环境不输出
      if (import.meta.env.DEV) {
        console.warn('[RealtimeWS] 已连接');
      }
      this._notifyConnectionChange();
    });

    activeWs.addEventListener('message', (event) => {
      if (this.ws !== activeWs) return;
      try {
        const data = JSON.parse(event.data);
        if (data?.type === 'ping') return;
        // R15：服务端高频合并的批量帧——逐条分发给消息回调
        if (data?.type === 'batch' && Array.isArray(data.items)) {
          for (const item of data.items) {
            this.handlers.forEach((h) => h(item));
          }
          return;
        }
        // 其他带 type 的服务端控制帧（subscribed 等）：静默忽略
        if (data?.type) return;
        // 通知所有回调
        this.handlers.forEach((h) => h(data));
      } catch {
        // 非 JSON 消息，忽略
      }
    });

    activeWs.addEventListener('close', (event) => {
      if (this.ws !== activeWs) return;
      // P3 #57: 控制台日志环境守卫，生产环境不输出
      if (import.meta.env.DEV) {
        console.warn(`[RealtimeWS] 连接关闭 (code=${event.code})`);
      }
      this.ws = null;
      // R19：先置状态（offline）再通知
      this._notifyConnectionChange();
      if (!this.isManualClose) {
        this._scheduleReconnect();
        // R19：设置重连 timer 后状态变为 reconnecting，再次通知
        this._notifyConnectionChange();
      }
    });

    activeWs.addEventListener('error', () => {
      if (this.ws !== activeWs) return;
      // P3 #57: 控制台日志环境守卫，生产环境不输出
      if (import.meta.env.DEV) {
        console.warn('[RealtimeWS] 连接错误');
      }
    });
  }

  private _notifyConnectionChange() {
    this.connectionHandlers.forEach((h) => h());
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    // P2-08：指数退避（3s * 2^attempts，与 alert-ws.ts 一致）
    const delay = Math.min(
      RECONNECT_INTERVAL * 2 ** this.reconnectAttempts,
      MAX_RECONNECT_DELAY,
    );
    this.reconnectAttempts++;
    // P3 #57: 控制台日志环境守卫，生产环境不输出
    if (import.meta.env.DEV) {
      console.warn(
        `[RealtimeWS] ${delay}ms 后重连 (第${this.reconnectAttempts}次)`,
      );
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._doConnect();
    }, delay);
  }

  private _startConnection(token: string) {
    this.token = token;
    this.isManualClose = false;
    // 关闭被替换的旧连接（先解除引用 → 旧 socket 事件被身份守卫忽略）
    this._teardownSocket();
    this._doConnect();
  }

  private _teardownSocket() {
    const old = this.ws;
    if (!old) return;
    this.ws = null;
    try {
      old.close();
    } catch {
      // 旧 socket 关闭失败（已断/中途态）不影响新连接
    }
  }
}

// 全局单例
export const realtimeWs = new RealtimeWebSocket();

export type { RealtimeMessage };
