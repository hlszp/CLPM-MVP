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

// mock @vben/stores（_doConnect 重连前读取 accessToken；可变以覆盖 token 陈旧场景）
const storeMock = vi.hoisted(() => ({
  store: {
    accessToken: 'test-token',
    refreshToken: null as null | string,
    setAccessToken(t: string) {
      this.accessToken = t;
    },
    setRefreshToken(t: string) {
      this.refreshToken = t;
    },
  },
}));
vi.mock('@vben/stores', () => ({
  useAccessStore: () => storeMock.store,
}));

// mock 刷新 API（token-freshness 真实逻辑 + 假 refresh 响应）
const refreshMock = vi.hoisted(() => ({ calls: 0 }));
vi.mock('#/api/core', () => ({
  refreshTokenApi: async () => {
    refreshMock.calls += 1;
    return { accessToken: 'fresh-token', refreshToken: 'fresh-refresh' };
  },
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
  sent: string[] = [];
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

  send(data: string) {
    this.sent.push(data);
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
  storeMock.store.accessToken = 'test-token';
  storeMock.store.refreshToken = null;
  refreshMock.calls = 0;
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


describe('token 陈旧主动刷新（2026-09-06 修复）', () => {
  function makeJwt(expSecFromNow: number): string {
    const b64 = (obj: unknown) =>
      btoa(JSON.stringify(obj)).replaceAll('+', '-').replaceAll('/', '_');
    return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ exp: Math.floor(Date.now() / 1000) + expSecFromNow })}.sig`;
  }

  it('store 中 token 已过期：建连前主动刷新并使用新 token', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    storeMock.store.refreshToken = 'r-old';
    realtimeWs.connect('whatever');
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(lastSocket().url).toContain('token=fresh-token');
    expect(refreshMock.calls).toBe(1);
  });

  it('刷新期间手动断开：不再建连', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    storeMock.store.refreshToken = 'r-old';
    realtimeWs.connect('whatever');
    realtimeWs.disconnect();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeWebSocket.instances.length).toBe(0);
  });
});

describe('订阅过滤协议（2026-09-06 接通）', () => {
  it('未声明兴趣的连接不发送 subscribe（旧版全量转发语义）', () => {
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    expect(s.sent).toEqual([]);
  });

  it('声明兴趣后 open 自动发送订阅帧，重连后自动重发', async () => {
    realtimeWs.subscribe(['T1', 'T2'], ['LOOP_A']);
    realtimeWs.connect('test-token');
    let s = lastSocket();
    s.simulateOpen();
    let frame = JSON.parse(s.sent.at(-1)!);
    expect(frame).toEqual({ loops: ['LOOP_A'], tags: ['T1', 'T2'], type: 'subscribe' });

    // 断线重连：新连接 open 后重发（服务端订阅状态随连接重置）
    s.simulateCloseEvent(1006);
    await vi.advanceTimersByTimeAsync(3000);
    s = lastSocket();
    s.simulateOpen();
    frame = JSON.parse(s.sent.at(-1)!);
    expect(frame.type).toBe('subscribe');
    expect(frame.loops).toEqual(['LOOP_A']);
  });

  it('clearInterest 发送空订阅（非实时页零流量）且替换语义生效', () => {
    realtimeWs.subscribe(['T1'], []);
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    realtimeWs.clearInterest();
    const frame = JSON.parse(s.sent.at(-1)!);
    expect(frame).toEqual({ loops: [], tags: [], type: 'subscribe' });
  });
});

describe('P6 O02-RT：owner 兴趣 registry（并集/释放隔离/重连重发）', () => {
  const ownerA = Symbol('page-a');
  const ownerB = Symbol('page-b');

  function lastFrame(s: FakeWebSocket) {
    return JSON.parse(s.sent.at(-1)!);
  }

  it('双 owner 并集：open 发送 tags/loops 并集帧', () => {
    realtimeWs.registerInterest(ownerA, { loops: ['L1'], tags: ['T1'] });
    realtimeWs.registerInterest(ownerB, { tags: ['T2'] });
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    expect(lastFrame(s)).toEqual({
      loops: ['L1'],
      tags: ['T1', 'T2'],
      type: 'subscribe',
    });
  });

  it('释放 owner B 不影响 owner A；全部释放后显式空并集（零流量）', () => {
    realtimeWs.registerInterest(ownerA, { loops: ['L1'], tags: ['T1'] });
    realtimeWs.registerInterest(ownerB, { tags: ['T2'] });
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    realtimeWs.unregisterInterest(ownerB);
    expect(lastFrame(s)).toEqual({
      loops: ['L1'],
      tags: ['T1'],
      type: 'subscribe',
    });
    realtimeWs.unregisterInterest(ownerA);
    expect(lastFrame(s)).toEqual({ loops: [], tags: [], type: 'subscribe' });
  });

  it('同 owner 重复注册幂等：内容相同零发送，内容变化整体替换', () => {
    realtimeWs.registerInterest(ownerA, { tags: ['T1'] });
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    const sentBefore = s.sent.length;
    realtimeWs.registerInterest(ownerA, { tags: ['T1'] });
    expect(s.sent.length).toBe(sentBefore);
    realtimeWs.registerInterest(ownerA, { tags: ['T1', 'T3'] });
    expect(s.sent.length).toBe(sentBefore + 1);
    expect(lastFrame(s).tags).toEqual(['T1', 'T3']);
  });

  it('释放不存在的 owner 为幂等空操作（不发帧不报错）', () => {
    realtimeWs.registerInterest(ownerA, { tags: ['T1'] });
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    const sentBefore = s.sent.length;
    realtimeWs.unregisterInterest(Symbol('never-registered'));
    expect(s.sent.length).toBe(sentBefore);
  });

  it('断线重连后自动重发当前并集', async () => {
    realtimeWs.registerInterest(ownerA, { loops: ['L1'], tags: ['T1'] });
    realtimeWs.registerInterest(ownerB, { tags: ['T2'] });
    realtimeWs.connect('test-token');
    let s = lastSocket();
    s.simulateOpen();
    s.simulateCloseEvent(1006);
    await vi.advanceTimersByTimeAsync(3000);
    s = lastSocket();
    s.simulateOpen();
    expect(lastFrame(s)).toEqual({
      loops: ['L1'],
      tags: ['T1', 'T2'],
      type: 'subscribe',
    });
  });

  it('反复注册/释放无残留：全释放后新注册只含新 owner 内容', () => {
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    for (let i = 0; i < 5; i++) {
      realtimeWs.registerInterest(ownerA, { tags: [`A${i}`] });
      realtimeWs.registerInterest(ownerB, { tags: [`B${i}`] });
      realtimeWs.unregisterInterest(ownerA);
      realtimeWs.unregisterInterest(ownerB);
    }
    realtimeWs.registerInterest(Symbol('page-c'), { tags: ['TC'] });
    expect(lastFrame(s)).toEqual({ loops: [], tags: ['TC'], type: 'subscribe' });
  });

  it('unregisterAllInterests 清空全部 owner 并发空并集；从未声明时空操作', () => {
    // 从未声明：连接 open 后零发送，unregisterAll 不多发帧
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    expect(s.sent).toEqual([]);
    realtimeWs.unregisterAllInterests();
    expect(s.sent).toEqual([]);
    // 有 owner：清空并发送空并集
    realtimeWs.registerInterest(ownerA, { tags: ['T1'] });
    realtimeWs.registerInterest(ownerB, { loops: ['L2'] });
    realtimeWs.unregisterAllInterests();
    expect(lastFrame(s)).toEqual({ loops: [], tags: [], type: 'subscribe' });
    // 清空后新注册正常并集
    realtimeWs.registerInterest(ownerA, { tags: ['T9'] });
    expect(lastFrame(s).tags).toEqual(['T9']);
  });

  it('兼容层 subscribe 与 owner 并集共存：释放 owner 不影响兼容切片', () => {
    realtimeWs.subscribe(['TL'], []);
    realtimeWs.registerInterest(ownerA, { tags: ['T1'] });
    realtimeWs.connect('test-token');
    const s = lastSocket();
    s.simulateOpen();
    expect(lastFrame(s).tags).toEqual(['T1', 'TL']);
    realtimeWs.unregisterInterest(ownerA);
    expect(lastFrame(s).tags).toEqual(['TL']);
  });
});
