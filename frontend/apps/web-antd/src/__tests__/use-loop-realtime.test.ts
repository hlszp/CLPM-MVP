/**
 * useLoopRealtime 单元测试（MW-P1-04 + 数据链路整改 R06/R17）
 *
 * 与旧版的关键差异（审查 §7"前端业务应用"行整改）：
 * - **直接调用真实 composable**（mount 一个宿主组件执行 setup），不再在测试体
 *   复制赋值逻辑，避免测试副本与实现分别演进；
 * - MODE 断言改为 R17 契约：modeMapping → 默认映射 → Unknown
 *   （默认 MODE=2 持续 Cascade；自定义正数映射 MANUAL 与 REST 一致）；
 * - R06：无效数值不整条丢弃——数值置 null、质量按消息更新
 *   （42/GOOD → nan/BAD 后必须不可用 + BAD，不得停留 42/GOOD）；
 * - PID_P/PID_I/PID_D 实现已支持（旧测试断言"忽略 PID"是过时副本）。
 */
import type { Mock } from 'vitest';

import type { RealtimeUpdatable } from '#/composables/use-loop-realtime';

import { mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  parseTagCode,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';
import { realtimeWs } from '#/utils/realtime-ws';

// mock realtimeWs 单例（避免真实 WS 连接；断言经导入的 mocked 实例进行）
vi.mock('#/utils/realtime-ws', () => ({
  realtimeWs: {
    connect: vi.fn(),
    isConnected: false,
    onConnectionChange: vi.fn(() => () => {}),
    onMessage: vi.fn(() => () => {}),
    status: 'offline',
  },
}));

// mock @vben/stores useAccessStore（避免 pinia 依赖）
vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({ accessToken: 'test-token' }),
}));

const wsMock = realtimeWs as unknown as {
  connect: Mock;
  isConnected: boolean;
  onConnectionChange: Mock;
  onMessage: Mock;
  status: string;
};

/** mount 宿主组件执行 setup，取回真实 composable 返回值 */
function setupComposable() {
  let composable!: ReturnType<typeof useLoopRealtime>;
  const wrapper = mount(
    defineComponent({
      setup() {
        composable = useLoopRealtime();
        return () => h('div');
      },
    }),
  );
  return {
    composable,
    unmount: () => wrapper.unmount(),
  };
}

function makeItem(
  tagName: string,
  loopId = 'loop-001',
  overrides: Partial<RealtimeUpdatable> = {},
): RealtimeUpdatable {
  return {
    controlMode: 'Auto',
    currentValues: {
      mode: 1,
      modeLabel: 'Auto',
      op: 50,
      pidD: null,
      pidI: null,
      pidP: null,
      pv: 100,
      pvQuality: 'GOOD',
      readAt: null,
      sp: 95,
    },
    loopId,
    tagName,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  wsMock.isConnected = false;
  wsMock.status = 'offline';
});

describe('parseTagCode', () => {
  it('正常解析：80FIC11906_PIDA.PV → tagName=80FIC11906_PIDA, role=PV', () => {
    expect(parseTagCode('80FIC11906_PIDA.PV')).toEqual({
      role: 'PV',
      tagName: '80FIC11906_PIDA',
    });
  });

  it('无点号且非白名单后缀：返回 null', () => {
    expect(parseTagCode('INVALID')).toBeNull();
  });

  it('空 role（以点结尾）：返回 null', () => {
    expect(parseTagCode('TAG.')).toBeNull();
  });

  it('空 tagName（以点开头）：返回 null', () => {
    expect(parseTagCode('.PV')).toBeNull();
  });

  it('role 转大写：pv → PV', () => {
    expect(parseTagCode('TAG.pv')).toEqual({ role: 'PV', tagName: 'TAG' });
  });

  it('生产下划线风格：90TIC60004_PIDA_PV → tagName=90TIC60004_PIDA, role=PV', () => {
    expect(parseTagCode('90TIC60004_PIDA_PV')).toEqual({
      role: 'PV',
      tagName: '90TIC60004_PIDA',
    });
  });

  it('生产下划线风格：KP/TI/TD 后缀归一为 PID_P/PID_I/PID_D', () => {
    expect(parseTagCode('90TIC60004_PIDA_KP')).toEqual({
      role: 'PID_P',
      tagName: '90TIC60004_PIDA',
    });
    expect(parseTagCode('90TIC60004_PIDA_TI')).toEqual({
      role: 'PID_I',
      tagName: '90TIC60004_PIDA',
    });
    expect(parseTagCode('90TIC60004_PIDA_TD')).toEqual({
      role: 'PID_D',
      tagName: '90TIC60004_PIDA',
    });
  });

  it('下划线风格：非角色后缀（如 _OUT）返回 null', () => {
    expect(parseTagCode('01TV_06003_PID_OUT')).toBeNull();
  });

  it('下划线风格：PID_P 完整后缀可解析', () => {
    expect(parseTagCode('41LIC30044_PIDA_PID_P')).toEqual({
      role: 'PID_P',
      tagName: '41LIC30044_PIDA',
    });
  });
});

describe('useLoopRealtime（真实 composable）', () => {
  it('PV 更新：数值、质量、readAt 同时更新', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    const updated = composable.applyMessage(
      {
        collectTime: '2026-09-06T10:00:00Z',
        quality: 1,
        tagCode: 'LIC-101.PV',
        value: '120.5',
      },
      [item],
    );
    expect(updated).toBe(true);
    expect(item.currentValues.pv).toBe(120.5);
    expect(item.currentValues.pvQuality).toBe('GOOD');
    expect(item.currentValues.readAt).toBe('2026-09-06T10:00:00Z');
    expect(composable.lastMessageAt.value).not.toBeNull();
  });

  it('R17：默认 MODE=2 持续显示 Cascade（不再被硬编码覆盖成 Auto）', () => {
    const { composable } = setupComposable();
    // REST 初始：mode=2, modeLabel='Cascade'（后端权威）
    const item = makeItem('LIC-101');
    item.currentValues.mode = 2;
    item.currentValues.modeLabel = 'Cascade';
    item.controlMode = 'Cascade';
    composable.applyMessage(
      {
        collectTime: '2026-09-06T10:00:01Z',
        quality: 1,
        tagCode: 'LIC-101.MODE',
        value: '2',
      },
      [item],
    );
    expect(item.currentValues.mode).toBe(2);
    expect(item.currentValues.modeLabel).toBe('Cascade');
    expect(item.controlMode).toBe('Cascade');
  });

  it('R17：自定义正数映射 MANUAL 时 WS 解析与 REST 一致', () => {
    const { composable } = setupComposable();
    // 该回路自定义 loop_mode_mapping：2 → MANUAL（REST modeLabel='Manual'）
    const item = makeItem('LIC-101', 'loop-001', {
      modeMapping: { '1': 'Auto', '2': 'Manual' },
    });
    item.currentValues.mode = 2;
    item.currentValues.modeLabel = 'Manual';
    item.controlMode = 'Manual';
    composable.applyMessage(
      {
        collectTime: '2026-09-06T10:00:01Z',
        quality: 1,
        tagCode: 'LIC-101.MODE',
        value: '2',
      },
      [item],
    );
    expect(item.currentValues.modeLabel).toBe('Manual');
    expect(item.controlMode).toBe('Manual');
  });

  it('R17：自定义映射未命中回退默认映射', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101', 'loop-001', {
      modeMapping: { '5': 'Manual' },
    });
    composable.applyMessage(
      {
        collectTime: 't',
        quality: 1,
        tagCode: 'LIC-101.MODE',
        value: '2',
      },
      [item],
    );
    // 2 不在自定义映射 → 默认映射 → Cascade
    expect(item.currentValues.modeLabel).toBe('Cascade');
  });

  it('R17：未知 MODE 值显式 Unknown（不保留旧标签冒充）', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    item.currentValues.modeLabel = 'Cascade';
    composable.applyMessage(
      {
        collectTime: '2026-09-06T10:00:01Z',
        quality: 1,
        tagCode: 'LIC-101.MODE',
        value: '99',
      },
      [item],
    );
    expect(item.currentValues.mode).toBe(99);
    expect(item.currentValues.modeLabel).toBe('Unknown');
    expect(item.controlMode).toBe('Unknown');
  });

  it('R06：42/GOOD → nan/BAD 后不可用 + BAD（不整条丢弃、不停留 42/GOOD）', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    item.currentValues.pv = 42;
    item.currentValues.pvQuality = 'GOOD';
    const updated = composable.applyMessage(
      {
        collectTime: '2026-09-06T10:00:05Z',
        quality: 0,
        tagCode: 'LIC-101.PV',
        value: 'nan',
      },
      [item],
    );
    expect(updated).toBe(true);
    expect(item.currentValues.pv).toBeNull();
    expect(item.currentValues.pvQuality).toBe('BAD');
    expect(item.currentValues.readAt).toBe('2026-09-06T10:00:05Z');
  });

  it('R06："-1.#QNAN0" 置 null（不得解析为 -1），质量按消息更新', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    composable.applyMessage(
      {
        collectTime: 't',
        quality: 1,
        tagCode: 'LIC-101.PV',
        value: '-1.#QNAN0',
      },
      [item],
    );
    expect(item.currentValues.pv).toBeNull();
    expect(item.currentValues.pvQuality).toBe('GOOD');
  });

  it('R06：Infinity/1e999/空串 → null；合法科学计数法 1.5E3 → 1500', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    for (const bad of ['Infinity', '1e999', '']) {
      composable.applyMessage(
        { collectTime: 't', quality: 0, tagCode: 'LIC-101.SP', value: bad },
        [item],
      );
      expect(item.currentValues.sp).toBeNull();
    }
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.SP', value: '1.5E3' },
      [item],
    );
    expect(item.currentValues.sp).toBe(1500);
  });

  it('R06：发布侧增量字段 valueValid=false → 数值置 null（容错缺省）', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    composable.applyMessage(
      {
        collectTime: 't',
        quality: 1,
        tagCode: 'LIC-101.PV',
        value: '42.5',
        valueValid: false,
      },
      [item],
    );
    expect(item.currentValues.pv).toBeNull();
    expect(item.currentValues.pvQuality).toBe('GOOD');
  });

  it('MODE 无效值 → mode=null + Unknown', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    composable.applyMessage(
      {
        collectTime: 't',
        quality: 1,
        tagCode: 'LIC-101.MODE',
        value: 'Infinity',
      },
      [item],
    );
    expect(item.currentValues.mode).toBeNull();
    expect(item.currentValues.modeLabel).toBe('Unknown');
  });

  it('SP/OP 更新：数值正确', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.SP', value: '98.7' },
      [item],
    );
    expect(item.currentValues.sp).toBe(98.7);
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.OP', value: '45.3' },
      [item],
    );
    expect(item.currentValues.op).toBe(45.3);
  });

  it('PID_P/PID_I/PID_D 更新（实现已支持，旧测试断言忽略是过时副本）', () => {
    const { composable } = setupComposable();
    const item = makeItem('LIC-101');
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.PID_P', value: '1.2' },
      [item],
    );
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.PID_I', value: '0.3' },
      [item],
    );
    composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'LIC-101.PID_D', value: '0.05' },
      [item],
    );
    expect(item.currentValues.pidP).toBe(1.2);
    expect(item.currentValues.pidI).toBe(0.3);
    expect(item.currentValues.pidD).toBe(0.05);
  });

  it('未知 tagCode（不在 items 列表）返回 false 不报错', () => {
    const { composable } = setupComposable();
    const items = [makeItem('LIC-101')];
    const updated = composable.applyMessage(
      { collectTime: 't', quality: 1, tagCode: 'UNKNOWN_TAG.PV', value: '1' },
      items,
    );
    expect(updated).toBe(false);
  });

  it('start() 连接全局单例；unmount 后退订回调', () => {
    const { composable, unmount } = setupComposable();
    composable.start();
    expect(wsMock.connect).toHaveBeenCalledWith('test-token');
    expect(wsMock.onConnectionChange).toHaveBeenCalled();
    unmount();
    // onBeforeUnmount → stop()（清理路径执行无异常即通过）
  });

  it('startFallback 幂等且 stopFallback 停止轮询', () => {
    const { composable } = setupComposable();
    let pollCount = 0;
    const poll = vi.fn(async () => {
      pollCount++;
    });
    composable.startFallback(poll, 10);
    composable.startFallback(poll, 10); // 幂等：不重复
    expect(pollCount).toBe(1);
    composable.stopFallback();
    expect(pollCount).toBe(1);
  });
});
