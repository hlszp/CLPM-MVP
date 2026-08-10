/**
 * WebSocket 实时数据推送客户端
 *
 * 连接后端 /api/v1/ws/realtime 端点，接收实时 Tag 值更新。
 * 支持自动重连、心跳检测、消息回调。
 */

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
    // apiUrl 格式: http://localhost:7101/api/v1 → ws://localhost:7101/api/v1/ws/realtime
    const wsBaseUrl = apiUrl
      .replace(/^http:/, 'ws:')
      .replace(/^https:/, 'wss:')
      .replace(/\/api\/v1$/, '');
    this.baseUrl = `${wsBaseUrl}/api/v1/ws/realtime`;
  }

  /**
   * 连接 WebSocket（需先传入 token）
   */
  connect(token: string) {
    this.token = token;
    this.isManualClose = false;
    this._doConnect();
  }

  /**
   * 断开连接
   */
  disconnect() {
    this.isManualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  /**
   * P2-08：手动重连（清除自动重连定时器后立即重连）
   *
   * 供断线提示 Banner 的"重连"按钮调用。
   */
  reconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    this.isManualClose = false;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._doConnect();
  }

  /**
   * 注册连接状态变化回调（P2 #38 UX14）
   *
   * WS 连接成功 / 断开 / 重连成功时触发，调用方可据此切换轮询策略
   */
  onConnectionChange(handler: () => void): () => void {
    this.connectionHandlers.add(handler);
    return () => this.connectionHandlers.delete(handler);
  }

  /**
   * 注册消息回调
   */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private _doConnect() {
    if (!this.token) return;

    try {
      this.ws = new WebSocket(
        `${this.baseUrl}?token=${encodeURIComponent(this.token)}`,
      );
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.addEventListener('open', () => {
      this.reconnectAttempts = 0;
      // P3 #57: 控制台日志环境守卫，生产环境不输出
      if (import.meta.env.DEV) {
        console.warn('[RealtimeWS] 已连接');
      }
      this._notifyConnectionChange();
    });

    this.ws.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data);
        // 跳过心跳消息
        if (data.type === 'ping') return;
        // 通知所有回调
        this.handlers.forEach((h) => h(data));
      } catch {
        // 非 JSON 消息，忽略
      }
    });

    this.ws.addEventListener('close', (event) => {
      // P3 #57: 控制台日志环境守卫，生产环境不输出
      if (import.meta.env.DEV) {
        console.warn(`[RealtimeWS] 连接关闭 (code=${event.code})`);
      }
      this.ws = null;
      this._notifyConnectionChange();
      if (!this.isManualClose) {
        this._scheduleReconnect();
      }
    });

    this.ws.addEventListener('error', () => {
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
}

// 全局单例
export const realtimeWs = new RealtimeWebSocket();

export type { RealtimeMessage };
