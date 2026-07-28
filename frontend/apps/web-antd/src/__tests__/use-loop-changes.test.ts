/**
 * use-loop-changes 单元测试
 *
 * 覆盖变更确认弹窗的三类 diff 构建：
 * - buildUpdateDiff：回路基础信息编辑（描述/类型/控制类型/级别/参评/单元/OP 限位/分组）
 * - buildTagMappingDiff：Tag 关联变更（已关联/未关联）
 * - buildBatchDiff：批量配置（from 恒为「保持原值」）
 */
import type { LoopApi } from '#/api/loop';

import { describe, expect, it } from 'vitest';

import {
  buildBatchDiff,
  buildTagMappingDiff,
  buildUpdateDiff,
  formatOpLimit,
} from '#/views/loop/manage/use-loop-changes';

function makeLoop(
  overrides: Partial<LoopApi.LoopListItem> = {},
): LoopApi.LoopListItem {
  return {
    loopId: 'loop-1',
    tagName: '101-FC-1023',
    description: '原描述',
    unitId: 'unit-1',
    unitName: '单元一',
    controlMode: 'Auto',
    isActive: true,
    status: 'READY',
    loopType: 'FLOW',
    controlType: 'SLOW',
    importanceLevel: 2,
    includeInEvaluation: true,
    tagMappingStatus: {
      pv: true,
      sp: true,
      op: true,
      mode: true,
      pid_p: false,
      pid_i: false,
      pid_d: false,
    },
    ...overrides,
  };
}

const unitLabel = (id: string | undefined) =>
  ({ 'unit-1': '工厂 / 单元一', 'unit-2': '工厂 / 单元二' })[id ?? ''] ??
  id ??
  '—';

describe('formatOpLimit', () => {
  it('使用默认或空值时返回「默认」', () => {
    expect(formatOpLimit(true, 10)).toBe('默认');
    expect(formatOpLimit(false, null)).toBe('默认');
    expect(formatOpLimit(false, undefined)).toBe('默认');
  });

  it('自定义值返回字符串', () => {
    expect(formatOpLimit(false, 5)).toBe('5');
  });
});

describe('buildUpdateDiff', () => {
  it('无变更时返回空数组', () => {
    const orig = makeLoop();
    const summary = buildUpdateDiff(
      orig,
      {
        description: '原描述',
        unitId: 'unit-1',
        loopType: 'FLOW',
        controlType: 'SLOW',
        importanceLevel: 2,
        includeInEvaluation: true,
      },
      { useDefaultOpLimits: true, unitLabel },
    );
    expect(summary).toEqual([]);
  });

  it('描述/类型/控制类型/级别/参评/单元变更生成对应条目', () => {
    const orig = makeLoop();
    const summary = buildUpdateDiff(
      orig,
      {
        description: '新描述',
        unitId: 'unit-2',
        loopType: 'TEMPERATURE',
        controlType: 'STABLE',
        importanceLevel: 1,
        includeInEvaluation: false,
      },
      { useDefaultOpLimits: true, unitLabel },
    );
    const byField = new Map(summary.map((s) => [s.field, s]));
    expect(byField.get('回路描述')).toEqual({
      field: '回路描述',
      from: '原描述',
      to: '新描述',
    });
    expect(byField.get('回路类型')).toEqual({
      field: '回路类型',
      from: '流量',
      to: '温度',
    });
    expect(byField.get('控制类型')).toEqual({
      field: '控制类型',
      from: '慢速型',
      to: '稳定型',
    });
    expect(byField.get('回路级别')).toEqual({
      field: '回路级别',
      from: '2 级',
      to: '1 级',
    });
    expect(byField.get('参评状态')).toEqual({
      field: '参评状态',
      from: '参评',
      to: '不参评',
    });
    expect(byField.get('所属单元')).toEqual({
      field: '所属单元',
      from: '工厂 / 单元一',
      to: '工厂 / 单元二',
    });
  });

  it('OP 限位：原默认 → 自定义时生成条目；勾选使用默认时按「默认」对比', () => {
    const orig = makeLoop({
      opOutputLowerLimit: null,
      opOutputUpperLimit: null,
    });
    const form = {
      includeInEvaluation: true,
      description: '原描述',
      unitId: 'unit-1',
      loopType: 'FLOW',
      controlType: 'SLOW' as const,
      importanceLevel: 2 as const,
      opOutputLowerLimit: 5,
      opOutputUpperLimit: 90,
    };
    const custom = buildUpdateDiff(orig, form, {
      useDefaultOpLimits: false,
      unitLabel,
    });
    expect(custom.find((s) => s.field === 'OP 输出限位')).toEqual({
      field: 'OP 输出限位',
      from: '默认 ~ 默认',
      to: '5 ~ 90',
    });
    const useDefault = buildUpdateDiff(orig, form, {
      useDefaultOpLimits: true,
      unitLabel,
    });
    expect(useDefault.find((s) => s.field === 'OP 输出限位')).toBeUndefined();
  });

  it('复杂回路分组变更对比原始快照，from/to 截断分组 ID 前 8 位', () => {
    const orig = makeLoop();
    const gid = '12345678-abcd-0000';
    const summary = buildUpdateDiff(
      orig,
      {
        includeInEvaluation: true,
        description: '原描述',
        unitId: 'unit-1',
        loopType: 'FLOW',
        controlType: 'SLOW',
        importanceLevel: 2,
        complexLoopGroupId: gid,
        complexRole: 'MAIN',
        _origComplexLoopGroupId: undefined,
        _origComplexRole: undefined,
      },
      { useDefaultOpLimits: true, unitLabel },
    );
    expect(summary.find((s) => s.field === '回路分组')).toEqual({
      field: '回路分组',
      from: '未分组',
      to: `主回路 12345678…`,
    });
  });
});

describe('buildTagMappingDiff', () => {
  const tagData: LoopApi.LoopTagsResult = {
    loopId: 'loop-1',
    tagName: '101-FC-1023',
    status: 'PARTIAL',
    tags: [
      {
        role: 'PV',
        tagId: 'tag-pv',
        tagName: 'PV1',
        description: null,
        required: true,
        associated: true,
        currentValue: null,
        quality: null,
        lastSyncAt: null,
      },
      {
        role: 'SP',
        tagId: null,
        tagName: null,
        description: null,
        required: true,
        associated: false,
        currentValue: null,
        quality: null,
        lastSyncAt: null,
      },
    ],
  };

  it('槽位新增/解除关联生成条目，未变化槽位不出现', () => {
    const summary = buildTagMappingDiff(tagData, {
      pv: undefined, // 原已关联 → 解除
      sp: 'tag-sp', // 原未关联 → 新增
      op: undefined,
      mode: undefined,
      pid_p: undefined,
      pid_i: undefined,
      pid_d: undefined,
    });
    expect(summary).toEqual([
      { field: 'PV', from: '已关联', to: '未关联' },
      { field: 'SP', from: '未关联', to: '已关联' },
    ]);
  });
});

describe('buildBatchDiff', () => {
  it('仅为已设置字段生成条目，from 恒为「保持原值」', () => {
    expect(buildBatchDiff({})).toEqual([]);
    const summary = buildBatchDiff({
      isMonitored: true,
      importanceLevel: 3,
      includeInEvaluation: false,
    });
    expect(summary).toEqual([
      { field: '监控状态', from: '保持原值', to: '启用监控' },
      { field: '回路级别', from: '保持原值', to: '3 级' },
      { field: '参评状态', from: '保持原值', to: '不参评' },
    ]);
  });
});
