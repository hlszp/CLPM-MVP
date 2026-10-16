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
 *
 * P6-02a O02-RT（2026-10-14）owner 兴趣 registry：
 * - 兴趣按 owner（symbol|string）登记为快照 {tags, loops}，对 WS 发送全部
 *   存活 owner 的并集；释放 owner B 不影响 owner A；同 owner 重复注册为
 *   整体替换（并集内容不变 → 零订阅帧，幂等）；
 * - 连接 open/断线重连后自动重发当前并集（服务端订阅状态随连接生命周期）；
 * - 兼容层：旧 subscribe()/clearInterest() 映射到保留 owner 切片（LEGACY_
 *   INTEREST_OWNER），等价旧全局替换语义，供未迁移调用方与路由离开时的
 *   "立即停推"安全网沿用；新代码一律 registerInterest 显式 owner；
 * - 本层不感知 Vue 生命周期：页面级 watch、KeepAlive 停用释放/恢复重注册、
 *   布局退出停全部 owner，由 composables/use-loop-realtime.ts 的冻结
 *   Interface 与 layouts/basic.vue 负责。
 */

import { useAccessStore } from '@vben/stores';

import { ensureFreshToken, isTokenStale } from '#/utils/token-freshness';

type RealtimeMessage = {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
};

type MessageHandler = (msg: RealtimeMessage) => void;

/** WS 连接状态三态（Phase 10 UX 包：监控页在线状态栏） */
export type ConnectionStatus = 'offline' | 'online' | 'reconnecting';

/**
 * 兴趣 owner 身份（P6-02a O02-RT）：symbol 或字符串，由调用方生命周期托管
 * （页面/组件实例级唯一）；同一 owner 的多次注册为整体替换语义。
 */
export type InterestOwner = string | symbol;

/**
 * 单个 owner 的实时兴趣快照（声明式：每次注册整体替换该 owner 上次内容）。
 * loops = 回路名（服务端经权威 tag 映射解析）；tags = 精确位号。
 */
export interface RealtimeInterest {
  loops?: string[];
  tags?: string[];
}

/**
 * 兼容层保留 owner（O02-RT）：旧 subscribe()/clearInterest() 与无实例身份的
 * 兼容 bind 共用此切片——后者替换前者（等价旧全局替换语义），路由离开时由
 * layouts/basic.vue 调 clearInterest 清空。新代码必须持有自有 owner，禁用。
 */
export const LEGACY_INTEREST_OWNER: InterestOwner = Symbol(
  'clpm-realtime-legacy-interest',
);

const RECONNECT_INTERVAL = 3000; // 重连间隔 3 秒
const MAX_RECONNECT_DELAY = 30_000; // 最大重连延迟 30 秒

/** 归一化兴趣名单：去空、去重、排序（保证并集帧内容稳定，内容不变不重发） */
function normalizeNames(names?: string[]): string[] {
  return [...new Set((names ?? []).filter(Boolean))].toSorted();
}

/** 等值比对（并集变化检测用；入参均已排序） */
function sameNames(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

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
  /** 并集缓存（loops）：全部存活 owner 快照的回路名并集（O02-RT） */
  private interestLoops: string[] = [];
  /**
   * owner 兴趣 registry（P6-02a O02-RT）：每个 owner 一份归一化快照；
   * interestTags/interestLoops 缓存其并集，registry 任何变更经 _applyUnion
   * 重算并按"内容是否变化"决定是否发送 subscribe 帧。
   */
  private interestOwners = new Map<
    InterestOwner,
    { loops: string[]; tags: string[] }
  >();
  /**
   * 并集缓存（tags，服务端订阅过滤，2026-09-06 慢链路防冲垮）。
   * null = 从未声明 → 保持旧版全量转发（兼容未接线的消费方）；
   * 一旦声明（含空并集）→ 连接建立后发送 subscribe 帧，仅接收所订阅位号，
   * 空并集 = 显式零流量。
   */
  private interestTags: null | string[] = null;
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

  /** 清空兼容层切片（路由离开实时页时调用：非实时页面零流量；O02-RT 起
   * 页面 owner 由各页面停用/卸载时自行释放，本方法只清保留切片作安全网） */
  clearInterest() {
    this.registerInterest(LEGACY_INTEREST_OWNER, { loops: [], tags: [] });
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

  /**
   * 登记 owner 兴趣快照（P6-02a O02-RT）：整体替换该 owner 上次内容，
   * 对 WS 发送全部存活 owner 的并集；幂等——并集内容不变时零订阅帧。
   * owner 必须由调用方生命周期托管：页面停用/卸载时配套 unregisterInterest。
   * 连接未建立时先记住，open/重连后自动重发当前并集。
   */
  registerInterest(owner: InterestOwner, interest: RealtimeInterest = {}) {
    this.interestOwners.set(owner, {
      loops: normalizeNames(interest.loops),
      tags: normalizeNames(interest.tags),
    });
    if (this._applyUnion()) {
      this._sendSubscribe();
    }
  }

  /**
   * 声明页面兴趣集合（兼容层，保留旧替换语义：映射到保留 owner 切片，
   * 与其他 owner 切片求并集）。新代码改用 registerInterest(owner, ...)。
   * 连接未建立时先记住，open 时自动发送；重连后自动重发（服务端订阅状态
   * 随连接生命周期）。
   */
  subscribe(tags: string[] = [], loops: string[] = []) {
    this.registerInterest(LEGACY_INTEREST_OWNER, { loops, tags });
  }

  /**
   * 释放全部 owner 兴趣（布局退出/登出前调用：并集置空并立即通知服务端
   * 停推，之后再断开连接）。从未声明过兴趣时为空操作（保持旧版全量转发
   * 语义，不多发 subscribe 帧）。
   */
  unregisterAllInterests() {
    this.interestOwners.clear();
    if (this.interestTags === null) return;
    if (this._applyUnion()) {
      this._sendSubscribe();
    }
  }

  /**
   * 释放单个 owner 兴趣（幂等：owner 不存在为空操作；不影响其他 owner）。
   */
  unregisterInterest(owner: InterestOwner) {
    if (!this.interestOwners.delete(owner)) return;
    if (this._applyUnion()) {
      this._sendSubscribe();
    }
  }

  /**
   * 重算并集缓存（O02-RT）：全部存活 owner 快照的 tags/loops 并集（排序去重，
   * 帧内容稳定）。interestTags 的 null → 非 null 迁移即"从未声明 → 首次声明"。
   * @returns 并集内容变化或首次声明时为 true（调用方据此发送 subscribe 帧）
   */
  private _applyUnion(): boolean {
    const tags = new Set<string>();
    const loops = new Set<string>();
    for (const entry of this.interestOwners.values()) {
      for (const t of entry.tags) tags.add(t);
      for (const l of entry.loops) loops.add(l);
    }
    const nextTags = [...tags].toSorted();
    const nextLoops = [...loops].toSorted();
    const changed =
      this.interestTags === null ||
      !sameNames(nextTags, this.interestTags) ||
      !sameNames(nextLoops, this.interestLoops);
    this.interestTags = nextTags;
    this.interestLoops = nextLoops;
    return changed;
  }

  private _clearReconnectTimer() {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  // -------------------------------------------------------------------------
  // 内部状态机（R18：所有建连路径收敛到 _startConnection）
  // -------------------------------------------------------------------------

  private _doConnect() {
    // R18：timer 触发 / 手动重连等路径同样幂等——已存在活跃连接时不重建
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN ||
        this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    // token 新鲜度双路径（2026-09-06 修复：闲置 30min 过期后 403 重连死循环）：
    // 非"可判定陈旧"（含非 JWT 格式）走原同步路径，行为不变；
    // 可解析出 exp 且临期/过期 → 异步刷新后再建连。
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

  private _notifyConnectionChange() {
    this.connectionHandlers.forEach((h) => h());
  }

  private _openSocket() {

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
      // 页面已声明兴趣集合 → 连接建立即（重）发送订阅（服务端状态随连接重置）
      this._sendSubscribe();
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
    // await 期间可能已有新连接（connect/reconnect 并发）或已手动断开
    if (
      this.isManualClose ||
      (this.ws &&
        (this.ws.readyState === WebSocket.OPEN ||
          this.ws.readyState === WebSocket.CONNECTING))
    ) {
      return;
    }
    this._openSocket();
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
      void this._doConnect();
    }, delay);
  }

  private _sendSubscribe() {
    if (
      this.interestTags === null ||
      this.ws?.readyState !== WebSocket.OPEN
    ) {
      return;
    }
    try {
      this.ws.send(
        JSON.stringify({
          loops: this.interestLoops,
          tags: this.interestTags,
          type: 'subscribe',
        }),
      );
    } catch {
      // 发送失败（连接正在关闭）：open/重连后会重发
    }
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
