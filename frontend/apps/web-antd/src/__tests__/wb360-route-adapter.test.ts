/**
 * 路由 Adapter 优先级矩阵测试（P6-02a O02-CORE）
 *
 * 覆盖 09 §2 路由建议与 01a §4.2 契约：
 * - 优先级：有效显式 mode → embed=1 → 无 mode 非空 loopId → 默认 all；
 * - 非法显式 mode/section 直接提示（errors），不静默降级；
 * - section=diagnosis/diag 映射对外名；window/record/work/scope 辅助参数
 *   非法时 notices 显式列出被忽略项，不静默丢弃；
 * - 全模式选中写 `mode=all&loopId=…`（wb360QueryFromContext）；
 * - 上下文键受管往返：queryFromContext → resolve 还原同一上下文。
 */
import { describe, expect, it } from 'vitest';

import {
  resolveWb360Embed,
  resolveWb360Route,
  wb360ContextKey,
  wb360QueryFromContext,
  type WorkbenchContext,
} from '../views/loop/workbench360/workbench-context';

describe('resolveWb360Route 模式优先级矩阵', () => {
  it('有效显式 mode=single + loopId → single', () => {
    const r = resolveWb360Route({ mode: 'single', loopId: 'L1' });
    expect(r.mode).toBe('single');
    expect(r.errors).toEqual([]);
    expect(r.context?.loopId).toBe('L1');
  });

  it('有效显式 mode=all + loopId → all 且预选该对象（刷新不误切 single）', () => {
    const r = resolveWb360Route({ mode: 'all', loopId: 'L1' });
    expect(r.mode).toBe('all');
    expect(r.initialLoopId).toBe('L1');
    expect(r.errors).toEqual([]);
  });

  it('有效显式 mode=all 优先于 embed=1', () => {
    const r = resolveWb360Route({ mode: 'all', embed: '1', loopId: 'L1' });
    expect(r.mode).toBe('all');
    expect(r.compact).toBe(false);
  });

  it('非法显式 mode → 直接提示，不被低优先级参数掩盖', () => {
    const r = resolveWb360Route({ mode: 'bogus', loopId: 'L1' });
    expect(r.errors.length).toBe(1);
    expect(r.errors[0]).toContain('bogus');
    expect(r.context).toBeNull();
  });

  it('embed=1 + loopId → single 精简模式', () => {
    const r = resolveWb360Route({ embed: '1', loopId: 'L1', from: '/loop/monitor' });
    expect(r.mode).toBe('single');
    expect(r.compact).toBe(true);
    expect(r.returnRef).toBe('/loop/monitor');
    expect(r.context?.loopId).toBe('L1');
  });

  it('无 mode 非空 loopId → 旧深链规范为 single', () => {
    const r = resolveWb360Route({ loopId: 'L1' });
    expect(r.mode).toBe('single');
    expect(r.compact).toBe(false);
  });

  it('无参数 → 默认 all', () => {
    const r = resolveWb360Route({});
    expect(r.mode).toBe('all');
    expect(r.initialLoopId).toBeNull();
    expect(r.context).toBeNull();
    expect(r.errors).toEqual([]);
  });

  it('all 模式未指定对象：?section=/window 携带项保留，供后续选中消费', () => {
    const r = resolveWb360Route({
      section: 'diag',
      tsStart: '2026-10-01T00:00:00Z',
      tsEnd: '2026-10-02T00:00:00Z',
    });
    expect(r.mode).toBe('all');
    expect(r.context).toBeNull();
    expect(r.carried.section).toBe('diagnosis');
    expect(r.carried.window?.tsStart).toBe('2026-10-01T00:00:00.000Z');
  });

  it('hash loopId 优先于 query loopId（tabbar 恢复态防带偏）', () => {
    const r = resolveWb360Route({ mode: 'all', loopId: 'Q1' }, 'H1');
    expect(r.initialLoopId).toBe('H1');
  });

  it('显式 mode=single 缺 loopId → 缺必需对象错误，不静默换模式', () => {
    const r = resolveWb360Route({ mode: 'single' });
    expect(r.errors.length).toBe(1);
    expect(r.errors[0]).toContain('loopId');
    expect(r.context).toBeNull();
  });
});

describe('resolveWb360Route section / 辅助参数', () => {
  it('section=diagnosis 映射对外名；section=diag 兼容', () => {
    expect(
      resolveWb360Route({ loopId: 'L1', section: 'diagnosis' }).context
        ?.section,
    ).toBe('diagnosis');
    expect(
      resolveWb360Route({ loopId: 'L1', section: 'diag' }).context?.section,
    ).toBe('diagnosis');
    expect(
      resolveWb360Route({ loopId: 'L1', section: 'tuning' }).context?.section,
    ).toBe('tuning');
  });

  it('非法 section → 直接提示不静默换剖面', () => {
    const r = resolveWb360Route({ loopId: 'L1', section: 'bogus' });
    expect(r.errors.length).toBe(1);
    expect(r.errors[0]).toContain('bogus');
  });

  it('合法 window（tsStart/tsEnd/tz）真实消费进上下文', () => {
    const r = resolveWb360Route({
      loopId: 'L1',
      tsStart: '2026-10-01T00:00:00Z',
      tsEnd: '2026-10-02T00:00:00Z',
      tz: 'Asia/Shanghai',
    });
    expect(r.context?.window).toEqual({
      tsStart: '2026-10-01T00:00:00.000Z',
      tsEnd: '2026-10-02T00:00:00.000Z',
      timezone: 'Asia/Shanghai',
    });
    expect(r.notices).toEqual([]);
  });

  it('window 缺一端/倒置 → notices 显式提示被忽略，不进上下文', () => {
    const r1 = resolveWb360Route({ loopId: 'L1', tsStart: '2026-10-01T00:00:00Z' });
    expect(r1.context?.window).toBeUndefined();
    expect(r1.notices.length).toBe(1);
    const r2 = resolveWb360Route({
      loopId: 'L1',
      tsStart: '2026-10-02T00:00:00Z',
      tsEnd: '2026-10-01T00:00:00Z',
    });
    expect(r2.context?.window).toBeUndefined();
    expect(r2.notices.length).toBe(1);
  });

  it('record 三元组 → evidenceRefs；缺项 → notice', () => {
    const r = resolveWb360Route({
      loopId: 'L1',
      recordKind: 'diagnosis_run',
      recordId: 'R1',
      recordVersion: 'v3',
    });
    expect(r.context?.evidenceRefs).toEqual([
      { kind: 'diagnosis_run', id: 'R1', version: 'v3' },
    ]);
    const bad = resolveWb360Route({ loopId: 'L1', recordKind: 'diagnosis_run' });
    expect(bad.context?.evidenceRefs).toBeUndefined();
    expect(bad.notices.length).toBe(1);
  });

  it('work 引用白名单校验；非法 kind → notice', () => {
    const r = resolveWb360Route({
      loopId: 'L1',
      workKind: 'handling_order',
      workId: 'W1',
    });
    expect(r.context?.workRef).toEqual({ kind: 'handling_order', id: 'W1' });
    const bad = resolveWb360Route({ loopId: 'L1', workKind: 'bogus', workId: 'W1' });
    expect(bad.context?.workRef).toBeUndefined();
    expect(bad.notices.length).toBe(1);
  });

  it('nodeId/profile/configRevision 进 scope/profileRef/configRevision', () => {
    const r = resolveWb360Route({
      loopId: 'L1',
      nodeId: 'N1',
      profile: 'P1',
      configRevision: 'C7',
    });
    expect(r.context?.scope).toEqual({ nodeId: 'N1' });
    expect(r.context?.profileRef).toBe('P1');
    expect(r.context?.configRevision).toBe('C7');
  });
});

describe('wb360QueryFromContext 回写协议', () => {
  it('全模式选中恒写 mode=all&loopId（防刷新误切 single）', () => {
    expect(wb360QueryFromContext('all', { loopId: 'L1' })).toEqual({
      mode: 'all',
      loopId: 'L1',
    });
  });

  it('section 不回写（一次性消费）', () => {
    const q = wb360QueryFromContext('single', {
      loopId: 'L1',
      section: 'diagnosis',
    });
    expect(q.section).toBeUndefined();
  });

  it('受管键往返：全上下文 → query → resolve 还原（模式 single）', () => {
    const ctx: WorkbenchContext = {
      loopId: 'L1',
      window: {
        tsStart: '2026-10-01T00:00:00.000Z',
        tsEnd: '2026-10-02T00:00:00.000Z',
        timezone: 'Asia/Shanghai',
      },
      evidenceRefs: [{ kind: 'diagnosis_run', id: 'R1', version: 'v3' }],
      workRef: { kind: 'investigation', id: 'I1' },
      scope: { nodeId: 'N1' },
      profileRef: 'P1',
      configRevision: 'C7',
    };
    const q = wb360QueryFromContext('single', ctx);
    const r = resolveWb360Route(q);
    expect(r.errors).toEqual([]);
    expect(r.notices).toEqual([]);
    expect(r.mode).toBe('single');
    // section 为一次性键不参与往返
    expect(wb360ContextKey(r.context!)).toBe(
      wb360ContextKey({ ...ctx }),
    );
  });
});

describe('resolveWb360Embed 旧 embed props 协议（02b 迁移前兼容）', () => {
  it('显式对象 + 剖面别名 → single 上下文', () => {
    const r = resolveWb360Embed('L1', 'diag');
    expect(r.mode).toBe('single');
    expect(r.errors).toEqual([]);
    expect(r.context).toEqual({ loopId: 'L1', section: 'diagnosis' });
  });

  it('无剖面 → 默认（核心默认 assess）', () => {
    const r = resolveWb360Embed('L1');
    expect(r.context?.section).toBeUndefined();
  });

  it('缺对象 / 非法剖面 → 直接提示', () => {
    expect(resolveWb360Embed('').errors[0]).toContain('loopId');
    expect(resolveWb360Embed('L1', 'bogus').errors[0]).toContain('bogus');
    expect(resolveWb360Embed('L1', 'bogus').context).toBeNull();
  });
});

describe('wb360ContextKey 上下文身份', () => {
  it('loopId 相同但证据/窗口不同 → 不同会话键', () => {
    const a = wb360ContextKey({ loopId: 'L1' });
    const b = wb360ContextKey({
      loopId: 'L1',
      evidenceRefs: [{ kind: 'k', id: 'R1' }],
    });
    const c = wb360ContextKey({
      loopId: 'L1',
      window: { tsStart: 's', tsEnd: 'e', timezone: 'UTC' },
    });
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
    expect(b).not.toBe(c);
  });

  it('证据顺序不影响身份（归一化排序）', () => {
    const a = wb360ContextKey({
      loopId: 'L1',
      evidenceRefs: [
        { kind: 'k1', id: 'R1' },
        { kind: 'k2', id: 'R2' },
      ],
    });
    const b = wb360ContextKey({
      loopId: 'L1',
      evidenceRefs: [
        { kind: 'k2', id: 'R2' },
        { kind: 'k1', id: 'R1' },
      ],
    });
    expect(a).toBe(b);
  });
});
