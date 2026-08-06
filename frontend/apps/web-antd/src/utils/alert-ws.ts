/**
 * WebSocket 预警实时推送客户端
 *
 * 连接后端 /api/v1/ws/alerts 端点，接收预警事件实时通知。
 * 支持自动重连、心跳检测、消息回调。
 *
 * 消息格式（dispatcher._notify 发布）:
 * { type: "alert", ruleCode, ruleName, loopId, severity, triggeredValue, triggeredAt, snapshot }
 */

export type AlertSeverity = 'INFO' | 'WARN' | 'ERROR' | 'CRITICAL';

export type AlertWsMessage = {
  type: 'alert';
  ruleCode: string;
  ruleName: string;
  loopId: string;
  severity: AlertSeverity;
  triggeredValue: number | null;
  triggeredAt: string;
  snapshot: Record<string, any>;
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
  private token = '';
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
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

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
      if (import.meta.env.DEV) {
        console.warn('[AlertWS] 已连接');
      }
    });

    this.ws.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'ping') return;
        if (data.type === 'alert') {
          this.handlers.forEach((h) => h(data as AlertWsMessage));
        }
      } catch {
        // 非 JSON 消息，忽略
      }
    });

    this.ws.addEventListener('close', (event) => {
      if (import.meta.env.DEV) {
        console.warn(`[AlertWS] 连接关闭 (code=${event.code})`);
      }
      this.ws = null;
      if (!this.isManualClose) {
        this._scheduleReconnect();
      }
    });

    this.ws.addEventListener('error', () => {
      if (import.meta.env.DEV) {
        console.warn('[AlertWS] 连接错误');
      }
    });
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectAttempts++;
    const delay = Math.min(
      RECONNECT_INTERVAL * this.reconnectAttempts,
      MAX_RECONNECT_DELAY,
    );
    if (import.meta.env.DEV) {
      console.warn(`[AlertWS] ${delay}ms 后重连 (第${this.reconnectAttempts}次)`);
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._doConnect();
    }, delay);
  }
}

export const alertWs = new AlertWebSocket();
