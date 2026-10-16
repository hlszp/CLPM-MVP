import type { WorkbenchContext } from '../views/loop/workbench360/workbench-context';

/**
 * 实例会话测试（P6-02a O02-CORE）
 *
 * 覆盖 01a §4.3 + 09 §3.2 契约：
 * - 双实例 prefill 隔离：A 实例预填不写入 B（会话私有 reactive + inject 读路径 +
 *   会话栈顶写路径三层各自验证）；
 * - 对象/证据变化递增请求代次、预填随旧身份显式失效；
 * - 迟到响应判定：令牌身份 + 代次双重核对，任一不符丢弃；
 * - 兼容模块（use-diag-prefill/use-tuning-prefill）读经 inject 解析本实例、
 *   写经栈顶解析当前可见实例。
 */
import { mount } from '@vue/test-utils';
import { defineComponent, h, nextTick, ref } from 'vue';

import { describe, expect, it, vi } from 'vitest';

// mock 最近继续 API（本文件不覆盖 resume 行为；避免真实 request 链加载）
vi.mock('#/api/personal-work', () => ({
  extractRevisionConflict: () => null,
  getWorkResumeApi: vi.fn(),
  newRequestId: () => 'req-test',
  putWorkResumeApi: vi.fn(),
}));

import {
  clearDiagPrefill,
  setDiagPrefill,
  useDiagPrefill,
} from '../views/loop/workbench360/composables/use-diag-prefill';
import {
  clearTuningPrefill,
  setTuningPrefill,
} from '../views/loop/workbench360/composables/use-tuning-prefill';
import {
  useWb360Session,
  type Wb360Session,
} from '../views/loop/workbench360/composables/use-wb360-session';

/** 挂一个宿主组件创建会话（setup 同步路径：provide/生命周期接线生效） */
function mountSession(context: WorkbenchContext) {
  const ctxRef = ref(context);
  let session!: Wb360Session;
  const wrapper = mount(
    defineComponent({
      setup() {
        session = useWb360Session(ctxRef);
        return () => h('div');
      },
    }),
  );
  return { ctxRef, session, wrapper };
}

describe('useWb360Session 请求代次与迟到判定', () => {
  it('对象变化递增代次；旧令牌身份+代次均不符被丢弃', async () => {
    const { ctxRef, session } = mountSession({ loopId: 'loop-A' });
    expect(session.generation.value).toBe(0);

    const tokenA = session.beginRequest();
    expect(session.isCurrent(tokenA)).toBe(true);

    ctxRef.value = { loopId: 'loop-B' };
    await nextTick();
    expect(session.generation.value).toBe(1);
    expect(session.isCurrent(tokenA)).toBe(false);

    const tokenB = session.beginRequest();
    expect(session.isCurrent(tokenB)).toBe(true);
  });

  it('同回路换证据也换代次（完整上下文身份，不只比 loopId）', async () => {
    const { ctxRef, session } = mountSession({ loopId: 'loop-A' });
    const t0 = session.beginRequest();
    ctxRef.value = {
      loopId: 'loop-A',
      evidenceRefs: [{ kind: 'diagnosis_run', id: 'R1' }],
    };
    await nextTick();
    expect(session.generation.value).toBe(1);
    expect(session.isCurrent(t0)).toBe(false);
  });

  it('对象/证据切换：预填随旧身份显式失效（不跨对象携带）', async () => {
    const { ctxRef, session } = mountSession({ loopId: 'loop-A' });
    session.setDiagPrefill('2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z');
    session.setTuningPrefill({ tsStart: 's', tsEnd: 'e', categoryLabel: '振荡' });
    expect(session.diagPrefill.from).toBe('assess');
    expect(session.tuningPrefill.from).toBe('diag');

    ctxRef.value = { loopId: 'loop-B' };
    await nextTick();
    expect(session.diagPrefill.from).toBeNull();
    expect(session.diagPrefill.tsStart).toBeNull();
    expect(session.tuningPrefill.from).toBeNull();
    expect(session.tuningPrefill.categoryLabel).toBeNull();
  });
});

describe('双实例 prefill 隔离', () => {
  it('A 实例预填不写入 B；inject 读路径各归各实例', async () => {
    const a = mountSession({ loopId: 'loop-A' });
    const b = mountSession({ loopId: 'loop-B' });

    // 核心写路径（SingleLoopWorkbench.onDiagnoseWindow 同款）：直写本实例会话
    a.session.setDiagPrefill('2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z');
    expect(a.session.diagPrefill.tsStart).toBe('2026-10-01T00:00:00Z');
    expect(b.session.diagPrefill.from).toBeNull();
    expect(b.session.diagPrefill.tsStart).toBeNull();

    // 剖面读路径（DiagSection 同款）：inject 解析本实例会话
    let aView!: ReturnType<typeof useDiagPrefill>;
    let bView!: ReturnType<typeof useDiagPrefill>;
    const Reader = defineComponent({
      emits: ['ready'],
      setup(_, { emit }) {
        const v = useDiagPrefill();
        emit('ready', v);
        return () => h('div');
      },
    });
    mount(
      defineComponent({
        setup() {
          useWb360Session(ref({ loopId: 'loop-A' }));
          // 子组件经 inject 读（不在本 setup 直接读，模拟剖面层级）
          return () => h(Reader, { onReady: (v: never) => (aView = v) });
        },
      }),
    );
    mount(
      defineComponent({
        setup() {
          const s = useWb360Session(ref({ loopId: 'loop-B' }));
          s.setDiagPrefill('2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z');
          return () => h(Reader, { onReady: (v: never) => (bView = v) });
        },
      }),
    );
    await nextTick();
    expect(aView.from).toBeNull(); // A 树内新会话：无预填
    expect(bView.tsStart).toBe('2026-11-01T00:00:00Z');

    a.wrapper.unmount();
    b.wrapper.unmount();
  });

  it('兼容模块写路径（栈顶=当前可见实例）：clear 只影响可见实例', async () => {
    const a = mountSession({ loopId: 'loop-A' });
    const b = mountSession({ loopId: 'loop-B' });

    // 栈顶 = 后注册的 B（抽屉/可见实例）；模块函数写 B 不写 A
    setDiagPrefill('2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z');
    expect(b.session.diagPrefill.from).toBe('assess');
    expect(a.session.diagPrefill.from).toBeNull();

    a.session.setDiagPrefill('2026-09-01T00:00:00Z', '2026-09-02T00:00:00Z');
    clearDiagPrefill(); // 栈顶 B 被清；A 不受影响
    expect(b.session.diagPrefill.from).toBeNull();
    expect(a.session.diagPrefill.from).toBe('assess');

    // B 卸载（抽屉关闭）→ 栈顶回落 A；此后模块写路径归 A
    b.wrapper.unmount();
    await nextTick();
    setTuningPrefill({ tsStart: 's', tsEnd: 'e' });
    expect(a.session.tuningPrefill.from).toBe('diag');
    clearTuningPrefill();
    expect(a.session.tuningPrefill.from).toBeNull();

    a.wrapper.unmount();
  });

  it('卸载释放后不再成为写目标（栈回落）', async () => {
    const a = mountSession({ loopId: 'loop-A' });
    const b = mountSession({ loopId: 'loop-B' });
    b.wrapper.unmount();
    await nextTick();
    setDiagPrefill('2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z');
    expect(a.session.diagPrefill.from).toBe('assess');
    expect(b.session.diagPrefill.from).toBeNull();
    a.wrapper.unmount();
  });

  it('useTuningPrefill 读路径同样按实例隔离', async () => {
    const a = mountSession({ loopId: 'loop-A' });
    mountSession({ loopId: 'loop-B' });
    a.session.setTuningPrefill({
      tsStart: 's',
      tsEnd: 'e',
      categoryLabel: '振荡',
      primaryConfidence: 0.9,
    });
    expect(a.session.tuningPrefill.categoryLabel).toBe('振荡');
    // 另一实例（栈顶 B）无预填
    setTuningPrefill({ tsStart: 'x', tsEnd: 'y' });
    expect(a.session.tuningPrefill.tsStart).toBe('s');
    a.wrapper.unmount();
  });
});
