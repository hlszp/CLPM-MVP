/**
 * WebSocket 实时数据推送客户端
 *
 * 连接后端 /api/v1/ws/realtime 端点，接收实时 Tag 值更新。
 * 支持自动重连、心跳检测、消息回调。
 */

type RealtimeMessage = {
  tagCode: string;
  value: string;
  quality: number;
  collectTime: string;
};

type MessageHandler = (msg: RealtimeMessage) => void;

const RECONNECT_INTERVAL = 3000; // 重连间隔 3 秒
const MAX_RECONNECT_DELAY = 30000; // 最大重连延迟 30 秒

class RealtimeWebSocket {
  private ws: WebSocket | null = null;
  private token: string = '';
  private handlers = new Set<MessageHandler>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private isManualClose = false;
  private baseUrl: string;

  constructor() {
    // 从环境变量获取后端地址，构造 WebSocket URL
    const apiUrl = import.meta.env.VITE_GLOB_API_URL || '';
    // apiUrl 格式: http://localhost:8001/api/v1 → ws://localhost:8001/api/v1/ws/realtime
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
   * 注册消息回调
   */
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /**
   * 是否已连接
   */
  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private _doConnect() {
    if (!this.token) return;

    try {
      this.ws = new WebSocket(`${this.baseUrl}?token=${encodeURIComponent(this.token)}`);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      console.log('[RealtimeWS] 已连接');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // 跳过心跳消息
        if (data.type === 'ping') return;
        // 通知所有回调
        this.handlers.forEach((h) => h(data));
      } catch {
        // 非 JSON 消息，忽略
      }
    };

    this.ws.onclose = (event) => {
      console.log(`[RealtimeWS] 连接关闭 (code=${event.code})`);
      this.ws = null;
      if (!this.isManualClose) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      console.warn('[RealtimeWS] 连接错误');
    };
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectAttempts++;
    // 指数退避，最大 30 秒
    const delay = Math.min(
      RECONNECT_INTERVAL * this.reconnectAttempts,
      MAX_RECONNECT_DELAY,
    );
    console.log(`[RealtimeWS] ${delay}ms 后重连 (第${this.reconnectAttempts}次)`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._doConnect();
    }, delay);
  }
}

// 全局单例
export const realtimeWs = new RealtimeWebSocket();

export type { RealtimeMessage };
