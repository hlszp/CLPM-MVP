/**
 * WebSocket 预警推送客户端（整改 E4）
 *
 * 连接后端 /api/v1/ws/alerts 端点，接收规则引擎预警通知，
 * 用于顶栏通知铃铛的实时预警条目推送。
 * 模式与 utils/realtime-ws.ts 对齐：自动重连、心跳、消息回调。
 */

/** 后端推送消息格式（见 ws_alert.py  docstring） */
export type AlertWsMessage = {
  loopId?: string;
  ruleCode?: string;
  ruleName?: string;
  severity?: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';
  snapshot?: Record<string, unknown>;
  triggeredAt?: string;
  triggeredValue?: number;
  type: 'alert' | 'ping' | string;
};

type MessageHandler = (msg: AlertWsMessage) => void;

const RECONNECT_INTERVAL = 3000;
const MAX_RECONNECT_DELAY = 30_000;

class AlertWebSocket {
  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private baseUrl: string;
  private handlers = new Set<MessageHandler>();
  private isManualClose = false;
  private reconnectAttempts = 0;
  private reconnectTimer: null | ReturnType<typeof setTimeout> = null;
  private token: string = '';

  private ws: null | WebSocket = null;

  constructor() {
    const apiUrl = import.meta.env.VITE_GLOB_API_URL || '';
    const wsBaseUrl = apiUrl
      .replace(/^http:/, 'ws:')
      .replace(/^https:/, 'wss:')
      .replace(/\/api\/v1$/, '');
    this.baseUrl = `${wsBaseUrl}/api/v1/ws/alerts`;
  }

  connect(token: string) {
    this.token = token;
    this.isManualClose = false;
    this._doConnect();
  }

  disconnect() {
    this.isManualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  onMessage(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private _doConnect() {
    if (!this.token) return;
    try {
      this.ws = new WebSocket(`${this.baseUrl}?token=${this.token}`);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as AlertWsMessage;
        // 心跳：回复 pong，不派发
        if (msg.type === 'ping') {
          this.ws?.send(JSON.stringify({ type: 'pong' }));
          return;
        }
        this.handlers.forEach((h) => h(msg));
      } catch {
        // 非法消息忽略
      }
    };

    this.ws.onclose = () => {
      if (!this.isManualClose) this._scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer || this.isManualClose) return;
    const delay = Math.min(
      RECONNECT_INTERVAL * 2 ** this.reconnectAttempts,
      MAX_RECONNECT_DELAY,
    );
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._doConnect();
    }, delay);
  }
}

export const alertWs = new AlertWebSocket();
