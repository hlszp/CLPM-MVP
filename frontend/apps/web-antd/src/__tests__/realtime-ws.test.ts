/**
 * utils/realtime-ws 单元测试（数据链路整改 R15 前端兼容 / R18 连接竞态 / R19 状态通知）
 *
 * 使用真实 RealtimeWebSocket 类 + fake WebSocket/timer：
 * - R15：batch 批量帧逐条分发；ping/未知 type 控制帧忽略；单对象帧照常分发
 * - R18：断线→页面 connect→旧 timer 触发全程 ≤1 个有效 socket；token 更换替换
 *   旧 socket 且旧事件经"当前连接身份"守卫忽略；多次挂载/卸载无增长
 * - R19：每次完整状态迁移后通知且与 getter 一致（offline→reconnecting→online）；
 *   注册即回调当前状态；同一时刻至多一个重连 timer
 */
import type { ConnectionStatus } from '#/utils/realtime-ws';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// mock @vben/stores（_doConnect 重连前读取 accessToken）
vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({ accessToken: 'test-token' }),
}));

type Listener = (event: any) => void;

class FakeWebSocket {
  static CLOSED = 3;
  static CLOSING = 2;
  static CONNECTING = 0;
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;

  closeCalled = 0;
  readyState = FakeWebSocket.CONNECTING;
  url: string;
  get live() {
    return (
      this.readyState === FakeWebSocket.OPEN ||
      this.readyState === FakeWebSocket.CONNECTING
    );
  }

  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  addEventListener(type: string, cb: Listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(cb);
  }

  close() {
    this.closeCalled++;
    if (this.readyState !== FakeWebSocket.CLOSED) {
      this.readyState = FakeWebSocket.CLOSED;
    }
  }

  removeEventListener(type: string, cb: Listener) {
    this.listeners.get(type)?.delete(cb);
  }

  simulateCloseEvent(code = 1006) {
    this.readyState = FakeWebSocket.CLOSED;
    this.emit('close', { code });
  }

  simulateMessage(obj: unknown) {
    this.emit('message', { data: JSON.stringify(obj) });
  }

  simulateOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.emit('open', {});
  }

  // ---- 测试驱动辅助 ----
  private emit(type: string, event: any) {
    for (const cb of this.listeners.get(type) ?? []) cb(event);
  }
}

type RealtimeWsModule = typeof import('#/utils/realtime-ws');
let realtimeWs: RealtimeWsModule['realtimeWs'];

beforeEach(async () => {
  FakeWebSocket.instances = [];
  vi.useFakeTimers();
  vi.resetModules();
  vi.stubGlobal('WebSocket', FakeWebSocket);
  ({ realtimeWs } = await import('#/utils/realtime-ws'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function liveSockets(): number {
  return FakeWebSocket.instances.filter((s) => s.live).length;
}

function lastSocket(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1]!;
}

describe('R15 前端兼容：消息分发', () => {
  it('batch 批量帧逐条分发给回调', () => {
    const received: any[] = [];
    realtimeWs.onMessage((m) => received.push(m));
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    const m1 = { collectTime: 'a', quality: 1, tagCode: 'T1.PV', value: '1' };
    const m2 = { collectTime: 'b', quality: 0, tagCode: 'T2.PV', value: '2' };
    s.simulateMessage({ type: 'batch', items: [m1, m2] });
    expect(received).toEqual([m1, m2]);
  });

  it('ping/未知 type 控制帧忽略，单对象帧照常分发', () => {
    const received: any[] = [];
    realtimeWs.onMessage((m) => received.push(m));
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    s.simulateMessage({ type: 'ping' });
    s.simulateMessage({ type: 'subscribed', tags: ['T1'] });
    expect(received).toEqual([]);
    const m = { collectTime: 'a', quality: 1, tagCode: 'T1.PV', value: '1' };
    s.simulateMessage(m);
    expect(received).toEqual([m]);
  });
});

describe('R18 连接竞态', () => {
  it('断线→重连 timer 触发→页面 connect：全程 ≤1 个有效 socket', () => {
    realtimeWs.connect('test-token');
    const s1 = lastSocket();
    s1.simulateOpen();
    expect(realtimeWs.status).toBe('online');

    // 断线 → 状态 offline → 重连 timer 挂起（状态 reconnecting）
    s1.simulateCloseEvent();
    expect(realtimeWs.status).toBe('reconnecting');
    expect(vi.getTimerCount()).toBe(1);

    // 等待重连 timer 触发 → 新 socket（CONNECTING）
    vi.advanceTimersByTime(3000);
    const s2 = lastSocket();
    expect(FakeWebSocket.instances.length).toBe(2);

    // 新 socket 建立期间页面又 connect（同 token）：幂等复用，不建第三条
    realtimeWs.connect('test-token');
    s2.simulateOpen();
    expect(FakeWebSocket.instances.length).toBe(2);
    expect(realtimeWs.status).toBe('online');
    expect(liveSockets()).toBe(1);
  });

  it('页面 connect 清除挂起的重连 timer（不再交错建连）', () => {
    realtimeWs.connect('test-token');
    const s1 = lastSocket();
    s1.simulateOpen();
    s1.simulateCloseEvent();
    expect(vi.getTimerCount()).toBe(1);

    // 重连等待期间页面显式 connect：timer 必须被清除，只建一条新连接
    realtimeWs.connect('test-token');
    expect(vi.getTimerCount()).toBe(0);
    const s2 = lastSocket();
    // 旧 timer 即使（竞态下）触发也没有额外 socket：推进全部时间验证
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances.length).toBe(2);
    s2.simulateOpen();
    expect(realtimeWs.status).toBe('online');
    expect(liveSockets()).toBe(1);
  });

  it('token 变更换路径走同一状态机：旧 socket 被关闭且其事件被忽略', () => {
    realtimeWs.connect('t1');
    const s1 = lastSocket();
    realtimeWs.connect('t2');
    // 旧 socket 已被替换关闭
    expect(s1.closeCalled).toBe(1);
    expect(FakeWebSocket.instances.length).toBe(2);
    const s2 = lastSocket();

    // 旧 socket 迟到的 open 事件不得影响当前状态（身份守卫）
    s1.simulateOpen();
    expect(realtimeWs.status).toBe('offline');
    // 新 socket open → online
    s2.simulateOpen();
    expect(realtimeWs.status).toBe('online');
  });

  it('多次挂载/卸载（connect/disconnect 循环）无连接泄漏', () => {
    for (let i = 0; i < 3; i++) {
      realtimeWs.connect(`t${i}`);
      const s = lastSocket();
      s.simulateOpen();
      expect(liveSockets()).toBe(1);
      realtimeWs.disconnect();
      expect(liveSockets()).toBe(0);
      expect(vi.getTimerCount()).toBe(0);
      // 卸载后推进时间不得产生新连接
      vi.advanceTimersByTime(60_000);
      expect(liveSockets()).toBe(0);
    }
    expect(FakeWebSocket.instances.length).toBe(3);
  });
});

describe('R19 状态通知', () => {
  it('每次通知时 getter 值一致：offline→online→offline→reconnecting→online', () => {
    const seen: ConnectionStatus[] = [];
    realtimeWs.onConnectionChange(() => seen.push(realtimeWs.status));
    // 注册即回调当前状态（初始 offline）
    expect(seen).toEqual(['offline']);

    realtimeWs.connect('test-token');
    const s1 = lastSocket();
    s1.simulateOpen();
    expect(seen).toEqual(['offline', 'online']);

    // 异常断开：先通知 offline（状态已置），设置重连 timer 后再通知 reconnecting
    s1.simulateCloseEvent();
    expect(seen).toEqual(['offline', 'online', 'offline', 'reconnecting']);

    // 重连成功 → online
    vi.advanceTimersByTime(3000);
    const s2 = lastSocket();
    s2.simulateOpen();
    expect(seen[seen.length - 1]).toBe('online');
  });

  it('显式 disconnect 通知 offline 且不再自动重连', () => {
    const seen: ConnectionStatus[] = [];
    realtimeWs.onConnectionChange(() => seen.push(realtimeWs.status));
    realtimeWs.connect('test-token');
    lastSocket().simulateOpen();
    realtimeWs.disconnect();
    expect(realtimeWs.status).toBe('offline');
    expect(seen[seen.length - 1]).toBe('offline');
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(120_000);
    expect(FakeWebSocket.instances.length).toBe(1);
  });

  it('同一时刻至多一个重连 timer', () => {
    realtimeWs.connect('test-token');
    const s1 = lastSocket();
    s1.simulateOpen();
    s1.simulateCloseEvent();
    expect(vi.getTimerCount()).toBe(1);
    // 重连成功后再次断开不会叠加第二个 timer
    vi.advanceTimersByTime(3000);
    const s2 = lastSocket();
    s2.simulateOpen();
    s2.simulateCloseEvent();
    expect(vi.getTimerCount()).toBe(1);
  });
});
