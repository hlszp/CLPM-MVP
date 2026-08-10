/**
 * useMonitorContext 单元测试（MW-P1-01 共享监控上下文）
 *
 * 覆盖：
 * - URL query 解析：空值、非法值、回退默认值
 * - update：增量合并 + router.replace
 * - reset：清空只保留 seed
 * - navigateWithMonitorContext：携带已知上下文跳转
 *
 * 测试策略：mock vue-router 的 useRoute/useRouter，用响应式 route 模拟 URL 变化。
 */
import { reactive } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useMonitorContext } from '#/composables/use-monitor-context';

// 模拟 route 和 router
const mockRoute = reactive<{ query: Record<string, string> }>({
  query: {},
});

const replaceCalls: { query: Record<string, string> }[] = [];
const pushCalls: { path?: string; query: Record<string, string> }[] = [];

vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({
    push: (to: any) => {
      pushCalls.push({ path: to.path, query: to.query || {} });
    },
    replace: (to: any) => {
      replaceCalls.push({ query: to.query || {} });
      // 模拟 URL 变化：清空旧 query 再赋值新 query
      for (const k of Object.keys(mockRoute.query)) {
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete mockRoute.query[k];
      }
      Object.assign(mockRoute.query, to.query || {});
    },
  }),
}));

function resetMock() {
  for (const k of Object.keys(mockRoute.query)) {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete mockRoute.query[k];
  }
  replaceCalls.length = 0;
  pushCalls.length = 0;
}

describe('useMonitorContext', () => {
  beforeEach(() => {
    resetMock();
  });

  it('空 query：所有字段回退默认值', () => {
    const ctx = useMonitorContext();

    expect(ctx.view.value).toBe('workspace');
    expect(ctx.loopId.value).toBeNull();
    expect(ctx.plantNodeId.value).toBeNull();
    expect(ctx.loopType.value).toBeNull();
    expect(ctx.keyword.value).toBe('');
    expect(ctx.attentionOnly.value).toBe(false);
    expect(ctx.timeWindow.value).toBe('24h');
    expect(ctx.eventId.value).toBeNull();
    expect(ctx.trackerId.value).toBeNull();
    expect(ctx.section.value).toBeNull();
  });

  it('合法 query：逐字段解析正确', () => {
    Object.assign(mockRoute.query, {
      attentionOnly: '1',
      eventId: 'evt-001',
      keyword: 'LIC',
      loopId: 'loop-abc',
      loopType: 'FC',
      plantNodeId: 'unit-01',
      section: 'diagnosis',
      timeWindow: '72h',
      trackerId: 'trk-01',
      view: 'table',
    });

    const ctx = useMonitorContext();

    expect(ctx.view.value).toBe('table');
    expect(ctx.loopId.value).toBe('loop-abc');
    expect(ctx.plantNodeId.value).toBe('unit-01');
    expect(ctx.loopType.value).toBe('FC');
    expect(ctx.keyword.value).toBe('LIC');
    expect(ctx.attentionOnly.value).toBe(true);
    expect(ctx.timeWindow.value).toBe('72h');
    expect(ctx.eventId.value).toBe('evt-001');
    expect(ctx.trackerId.value).toBe('trk-01');
    expect(ctx.section.value).toBe('diagnosis');
  });

  it('非法值回退：view→workspace, timeWindow→24h, section→null', () => {
    Object.assign(mockRoute.query, {
      section: 'invalid',
      timeWindow: '99h',
      view: 'invalid',
    });

    const ctx = useMonitorContext();

    expect(ctx.view.value).toBe('workspace');
    expect(ctx.timeWindow.value).toBe('24h');
    expect(ctx.section.value).toBeNull();
  });

  it('timeWindow 保留五档：8h/12h/24h/48h/72h', () => {
    const windows = ['8h', '12h', '24h', '48h', '72h'] as const;

    for (const tw of windows) {
      resetMock();
      mockRoute.query.timeWindow = tw;
      const ctx = useMonitorContext();
      expect(ctx.timeWindow.value).toBe(tw);
    }
  });

  it('keyword 允许空字符串（不回退 null）', () => {
    mockRoute.query.keyword = '';
    const ctx = useMonitorContext();
    expect(ctx.keyword.value).toBe('');
  });

  it('attentionOnly 只接受 1/true', () => {
    mockRoute.query.attentionOnly = '1';
    expect(useMonitorContext().attentionOnly.value).toBe(true);

    resetMock();
    mockRoute.query.attentionOnly = 'true';
    expect(useMonitorContext().attentionOnly.value).toBe(true);

    resetMock();
    mockRoute.query.attentionOnly = '0';
    expect(useMonitorContext().attentionOnly.value).toBe(false);

    resetMock();
    mockRoute.query.attentionOnly = 'false';
    expect(useMonitorContext().attentionOnly.value).toBe(false);
  });

  it('update：增量合并，保留未传字段，使用 router.replace', () => {
    // 初始 query 已有 loopId 和 view
    Object.assign(mockRoute.query, {
      loopId: 'loop-abc',
      view: 'workspace',
    });

    const ctx = useMonitorContext();
    ctx.update({ timeWindow: '48h' });

    // 应调用 router.replace 一次
    expect(replaceCalls).toHaveLength(1);
    // 合并后应保留 loopId/view 并新增 timeWindow
    expect(replaceCalls[0]!.query).toMatchObject({
      loopId: 'loop-abc',
      timeWindow: '48h',
      view: 'workspace',
    });
  });

  it('update：传 null 清除该字段', () => {
    Object.assign(mockRoute.query, {
      loopId: 'loop-abc',
      view: 'workspace',
    });

    const ctx = useMonitorContext();
    ctx.update({ loopId: null });

    expect(replaceCalls[0]!.query).not.toHaveProperty('loopId');
    expect(replaceCalls[0]!.query).toHaveProperty('view', 'workspace');
  });

  it('reset：清空所有字段，只保留 seed', () => {
    Object.assign(mockRoute.query, {
      keyword: 'LIC',
      loopId: 'loop-abc',
      plantNodeId: 'unit-01',
      view: 'table',
    });

    const ctx = useMonitorContext();
    ctx.reset({ view: 'workspace' });

    expect(replaceCalls[0]!.query).toEqual({ view: 'workspace' });
  });

  it('navigateWithMonitorContext：携带 loopId/timeWindow/eventId 跳转', () => {
    Object.assign(mockRoute.query, {
      eventId: 'evt-001',
      loopId: 'loop-abc',
      timeWindow: '48h',
    });

    const ctx = useMonitorContext();
    ctx.navigateWithMonitorContext('/diagnosis/detail', {
      section: 'overview',
    });

    expect(pushCalls).toHaveLength(1);
    expect(pushCalls[0]!.path).toBe('/diagnosis/detail');
    expect(pushCalls[0]!.query).toMatchObject({
      eventId: 'evt-001',
      loopId: 'loop-abc',
      section: 'overview',
      timeWindow: '48h',
    });
  });

  it('context 完整对象包含所有 10 个字段', () => {
    const ctx = useMonitorContext();
    const c = ctx.context.value;

    expect(c).toHaveProperty('view');
    expect(c).toHaveProperty('loopId');
    expect(c).toHaveProperty('plantNodeId');
    expect(c).toHaveProperty('loopType');
    expect(c).toHaveProperty('keyword');
    expect(c).toHaveProperty('attentionOnly');
    expect(c).toHaveProperty('timeWindow');
    expect(c).toHaveProperty('eventId');
    expect(c).toHaveProperty('trackerId');
    expect(c).toHaveProperty('section');
  });

  it('section 合法值：overview/assessment/diagnosis/tuning/verification', () => {
    const valid = [
      'overview',
      'assessment',
      'diagnosis',
      'tuning',
      'verification',
    ] as const;

    for (const s of valid) {
      resetMock();
      mockRoute.query.section = s;
      const ctx = useMonitorContext();
      expect(ctx.section.value).toBe(s);
    }
  });
});
