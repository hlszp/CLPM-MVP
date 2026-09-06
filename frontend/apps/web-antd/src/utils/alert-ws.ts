/**
 * WebSocket 预警推送客户端（整改 E4）
 *
 * 连接后端 /api/v1/ws/alerts 端点，接收规则引擎预警通知，
 * 用于顶栏通知铃铛的实时预警条目推送。
 * 模式与 utils/realtime-ws.ts 对齐：自动重连、心跳、消息回调。
 */

import { useAccessStore } from '@vben/stores';

import { ensureFreshToken, isTokenStale } from '#/utils/token-freshness';

/** 后端推送消息格式（见 ws_alert.py  docstring） */
export type AlertWsMessage = {
  eventId?: string;
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
    // token 新鲜度双路径（2026-09-06 修复：闲置 30min 过期后 403 重连死循环
    // ——原"实时读取 store token"只解决固化旧 token，解决不了 store 里的
    // token 本身过期且无人触发刷新）。非陈旧走原同步路径，行为不变。
    const storeToken = useAccessStore().accessToken || this.token;
    if (!isTokenStale(storeToken, 15)) {
      if (storeToken) this.token = storeToken;
      if (!this.token) {
        if (useAccessStore().refreshToken) {
          this._scheduleReconnect();
        }
        return;
      }
      this._openSocket();
      return;
    }
    void this._refreshThenConnect();
  }

  private _openSocket() {
    try {
      this.ws = new WebSocket(`${this.baseUrl}?token=${this.token}`);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this.ws.addEventListener('open', () => {
      this.reconnectAttempts = 0;
    });

    this.ws.addEventListener('message', (event) => {
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
    });

    this.ws.addEventListener('close', () => {
      if (!this.isManualClose) this._scheduleReconnect();
    });

    this.ws.addEventListener('error', () => {
      this.ws?.close();
    });
  }

  private async _refreshThenConnect() {
    const fresh = await ensureFreshToken(15);
    if (this.isManualClose) return;
    if (fresh) this.token = fresh;
    if (!this.token) {
      if (useAccessStore().refreshToken) {
        this._scheduleReconnect();
      }
      return;
    }
    this._openSocket();
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
      void this._doConnect();
    }, delay);
  }
}

export const alertWs = new AlertWebSocket();
