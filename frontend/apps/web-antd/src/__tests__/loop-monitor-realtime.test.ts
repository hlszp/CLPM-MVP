/**
 * 回路监控列表 WS 实时消息处理单元测试（R06/R17 数据链路整改）
 *
 * 覆盖 loop/monitor.vue 普通 script 块导出的 applyRealtimeMessage：
 * - R06：无效数值（-1.#QNAN0/nan/空串/valueValid=false）不整条丢弃——
 *   数值置 null、质量按消息独立更新，不得保留旧值 + 旧 GOOD 冒充有效读数
 * - R06：合法科学计数法照常解析（"1.5E3" → 1500）；质量 2=GOOD（OPC UA）
 * - R17：MODE 按「回路 modeMapping（REST 下发）→ 默认映射 → Unknown」解析，
 *   删除旧"0=Manual/正数=Auto"硬编码（MODE=2 曾被误判为 Auto）
 * - PID_P/PID_I/PID_D 不在监控列表展示（返回 null，不更新 readAt）
 */
import { describe, expect, it } from 'vitest';

import {
  applyRealtimeMessage,
  type MonitorRealtimeItem,
  type MonitorRealtimeMessage,
} from '../views/loop/monitor.vue';

/** 构造监控列表项（含旧的有效读数，用于验证"不冒充"语义） */
function makeItem(
  overrides: Partial<MonitorRealtimeItem> = {},
): MonitorRealtimeItem {
  return {
    tagName: 'LIC101_PIDA',
    controlMode: 'Auto',
    currentValues: {
      sp: 50,
      pv: 42,
      op: 45,
      mode: 1,
      modeLabel: 'Auto',
      pvQuality: 'GOOD',
      readAt: '2026-09-06T02:00:00.000Z',
    },
    ...overrides,
  };
}

/** 构造 WS 实时消息（默认 PV 角色、质量 1=GOOD） */
function msg(partial: Partial<MonitorRealtimeMessage> = {}) {
  return {
    collectTime: '2026-09-06T02:00:05.000Z',
    quality: 1,
    tagCode: 'LIC101_PIDA.PV',
    value: '7',
    ...partial,
  };
}

describe('loop/monitor.vue applyRealtimeMessage（R06 数值契约）', () => {
  it('PV 无效字面量 "-1.#QNAN0"：数值置 null、质量按消息独立更新（不再整条丢弃）', () => {
    const item = makeItem();
    const applied = applyRealtimeMessage(msg({ value: '-1.#QNAN0', quality: 0 }), [
      item,
    ]);
    expect(applied).toBe('PV');
    expect(item.currentValues.pv).toBeNull();
    expect(item.currentValues.pvQuality).toBe('BAD');
    expect(item.currentValues.readAt).toBe('2026-09-06T02:00:05.000Z');
  });

  it('PV "nan" / 空串：不保留旧值 + 旧 GOOD 冒充，读数显式不可用 + BAD', () => {
    const item1 = makeItem();
    applyRealtimeMessage(msg({ value: 'nan', quality: 0 }), [item1]);
    expect(item1.currentValues.pv).toBeNull();
    expect(item1.currentValues.pvQuality).toBe('BAD');

    const item2 = makeItem();
    applyRealtimeMessage(msg({ value: '', quality: 0 }), [item2]);
    expect(item2.currentValues.pv).toBeNull();
    expect(item2.currentValues.pvQuality).toBe('BAD');
  });

  it('发布侧增量字段 valueValid=false 视为无效（质量仍按消息更新）', () => {
    const item = makeItem();
    applyRealtimeMessage(
      msg({ value: '42', valueValid: false, quality: 0 }),
      [item],
    );
    expect(item.currentValues.pv).toBeNull();
    expect(item.currentValues.pvQuality).toBe('BAD');
  });

  it('合法科学计数法照常解析（"1.5E3" → 1500）；质量 2=GOOD', () => {
    const item = makeItem();
    const applied = applyRealtimeMessage(msg({ value: '1.5E3', quality: 2 }), [
      item,
    ]);
    expect(applied).toBe('PV');
    expect(item.currentValues.pv).toBe(1500);
    expect(item.currentValues.pvQuality).toBe('GOOD');
  });

  it('MODE 无效字面量：mode 置 null + 显式 Unknown（不再整条丢弃）', () => {
    const item = makeItem();
    const applied = applyRealtimeMessage(
      msg({ tagCode: 'LIC101_PIDA.MODE', value: '-1.#QNAN0' }),
      [item],
    );
    expect(applied).toBe('MODE');
    expect(item.currentValues.mode).toBeNull();
    expect(item.currentValues.modeLabel).toBe('Unknown');
    expect(item.controlMode).toBe('Unknown');
  });
});

describe('loop/monitor.vue applyRealtimeMessage（R17 MODE 解析链）', () => {
  it('MODE=2 → Cascade（旧"正数=Auto"硬编码曾误判为 Auto）', () => {
    const item = makeItem();
    const applied = applyRealtimeMessage(
      msg({ tagCode: 'LIC101_PIDA.MODE', value: '2' }),
      [item],
    );
    expect(applied).toBe('MODE');
    expect(item.currentValues.mode).toBe(2);
    expect(item.currentValues.modeLabel).toBe('Cascade');
    expect(item.controlMode).toBe('Cascade');
  });

  it('MODE=3/4 按后端默认映射归并为 Auto', () => {
    const item = makeItem();
    applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.MODE', value: '3' }), [
      item,
    ]);
    expect(item.currentValues.mode).toBe(3);
    expect(item.currentValues.modeLabel).toBe('Auto');

    applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.MODE', value: '4' }), [
      item,
    ]);
    expect(item.currentValues.mode).toBe(4);
    expect(item.currentValues.modeLabel).toBe('Auto');
  });

  it('回路 modeMapping（REST 列表下发）优先于默认映射', () => {
    const item = makeItem({
      modeMapping: { '1': 'Cascade', '7': 'Auto' },
    });
    // 自定义覆盖的值（1→Cascade）
    applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.MODE', value: '1' }), [
      item,
    ]);
    expect(item.currentValues.modeLabel).toBe('Cascade');
    // 自定义新增的值（7→Auto）生效
    applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.MODE', value: '7' }), [
      item,
    ]);
    expect(item.currentValues.modeLabel).toBe('Auto');
  });

  it('未知 MODE 值（9）显式 Unknown，不保留旧标签冒充', () => {
    const item = makeItem();
    applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.MODE', value: '9' }), [
      item,
    ]);
    expect(item.currentValues.mode).toBe(9);
    expect(item.currentValues.modeLabel).toBe('Unknown');
  });
});

describe('loop/monitor.vue applyRealtimeMessage（角色匹配）', () => {
  it('SP/OP 正常更新；生产下划线命名（_OP）同样匹配并推进 readAt', () => {
    const item = makeItem();
    expect(
      applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA.SP', value: '55.5' }), [
        item,
      ]),
    ).toBe('SP');
    expect(item.currentValues.sp).toBe(55.5);

    expect(
      applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA_OP', value: '60' }), [
        item,
      ]),
    ).toBe('OP');
    expect(item.currentValues.op).toBe(60);
    expect(item.currentValues.readAt).toBe('2026-09-06T02:00:05.000Z');
  });

  it('PID 角色不在监控列表展示：返回 null 且不更新 readAt', () => {
    const item = makeItem();
    expect(
      applyRealtimeMessage(msg({ tagCode: 'LIC101_PIDA_KP', value: '0.5' }), [
        item,
      ]),
    ).toBeNull();
    expect(item.currentValues.readAt).toBe('2026-09-06T02:00:00.000Z');
  });

  it('tagCode 无法解析或回路不在列表：返回 null', () => {
    const item = makeItem();
    expect(applyRealtimeMessage(msg({ tagCode: 'garbage' }), [item])).toBeNull();
    expect(
      applyRealtimeMessage(msg({ tagCode: 'OTHER_PV', value: '1' }), [item]),
    ).toBeNull();
  });
});
