import type { FilterPreset } from '#/composables/use-clpm-preferences';
import type { MonitorContext } from '#/composables/use-monitor-context';

/**
 * useSavedView 单元测试（MW-P4-03 统一筛选与保存视图）
 *
 * 覆盖：
 * - buildSavedViewFilters：保存视图字段完整性和排除项
 * - buildApplyPatch：权限安全过滤和深链接上下文清除
 * - canUseTableViewByRoles：角色权限判定
 *
 * 测试策略：纯函数测试，不依赖 Vue 响应式系统。
 */
import { describe, expect, it } from 'vitest';

import {
  buildApplyPatch,
  buildSavedViewFilters,
  canUseTableViewByRoles,
  SAVED_VIEW_FIELDS,
} from '#/composables/use-saved-view';

/** 构造完整 MonitorContext 用于测试 */
function makeCtx(overrides: Partial<MonitorContext> = {}): MonitorContext {
  return {
    view: 'workspace',
    loopId: null,
    plantNodeId: null,
    loopType: null,
    keyword: '',
    attentionOnly: false,
    timeWindow: '24h',
    eventId: null,
    section: null,
    from: null,
    fitnessLevels: [],
    ...overrides,
  };
}

/** 构造 FilterPreset 用于测试 */
function makePreset(
  filters: Record<string, any>,
  id = 'preset-1',
  name = '测试预设',
): FilterPreset {
  return {
    id,
    name,
    page: 'monitor-workbench',
    filters,
    createdAt: '2026-08-10T00:00:00Z',
  };
}

describe('useSavedView', () => {
  // ===== canUseTableViewByRoles =====

  describe('canUseTableViewByRoles', () => {
    it('ADMIN 可用 table 模式', () => {
      expect(canUseTableViewByRoles(['ADMIN'])).toBe(true);
    });

    it('IC_ENGINEER 可用 table 模式', () => {
      expect(canUseTableViewByRoles(['IC_ENGINEER'])).toBe(true);
    });

    it('PE_ENGINEER 可用 table 模式', () => {
      expect(canUseTableViewByRoles(['PE_ENGINEER'])).toBe(true);
    });

    it('EXPERT 不可用 table 模式', () => {
      expect(canUseTableViewByRoles(['EXPERT'])).toBe(false);
    });

    it('SPONSOR 不可用 table 模式', () => {
      expect(canUseTableViewByRoles(['SPONSOR'])).toBe(false);
    });

    it('多角色中含 ADMIN 则可用', () => {
      expect(canUseTableViewByRoles(['EXPERT', 'ADMIN'])).toBe(true);
    });

    it('空角色列表不可用', () => {
      expect(canUseTableViewByRoles([])).toBe(false);
    });
  });

  // ===== buildSavedViewFilters =====

  describe('buildSavedViewFilters', () => {
    it('包含 view 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ view: 'table' }));
      expect(filters.view).toBe('table');
    });

    it('包含 timeWindow 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ timeWindow: '72h' }));
      expect(filters.timeWindow).toBe('72h');
    });

    it('包含 plantNodeId 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ plantNodeId: 'node-1' }));
      expect(filters.plantNodeId).toBe('node-1');
    });

    it('包含 loopType 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ loopType: 'FLOW' }));
      expect(filters.loopType).toBe('FLOW');
    });

    it('包含 keyword 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ keyword: 'FT-101' }));
      expect(filters.keyword).toBe('FT-101');
    });

    it('包含 attentionOnly 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ attentionOnly: true }));
      expect(filters.attentionOnly).toBe(true);
    });

    it('不包含 eventId 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ eventId: 'evt-123' }));
      expect(filters).not.toHaveProperty('eventId');
    });

    it('不包含 section 字段', () => {
      const filters = buildSavedViewFilters(makeCtx({ section: 'assessment' }));
      expect(filters).not.toHaveProperty('section');
    });

    it('不包含 loopId 字段（回路选择不属于筛选视图）', () => {
      const filters = buildSavedViewFilters(makeCtx({ loopId: 'loop-789' }));
      expect(filters).not.toHaveProperty('loopId');
    });

    it('SAVED_VIEW_FIELDS 恰好 6 项', () => {
      expect(SAVED_VIEW_FIELDS).toHaveLength(6);
      expect([...SAVED_VIEW_FIELDS]).toEqual([
        'view',
        'timeWindow',
        'plantNodeId',
        'loopType',
        'keyword',
        'attentionOnly',
      ]);
    });
  });

  // ===== buildApplyPatch =====

  describe('buildApplyPatch', () => {
    it('ADMIN 应用 view=table 正常通过', () => {
      const preset = makePreset({ view: 'table' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.view).toBe('table');
    });

    it('EXPERT 应用 view=table 回退为 workspace', () => {
      const preset = makePreset({ view: 'table' });
      const patch = buildApplyPatch(preset, ['EXPERT']);
      expect(patch.view).toBe('workspace');
    });

    it('SPONSOR 应用 view=table 回退为 workspace', () => {
      const preset = makePreset({ view: 'table' });
      const patch = buildApplyPatch(preset, ['SPONSOR']);
      expect(patch.view).toBe('workspace');
    });

    it('EXPERT 应用 view=workspace 正常通过', () => {
      const preset = makePreset({ view: 'workspace' });
      const patch = buildApplyPatch(preset, ['EXPERT']);
      expect(patch.view).toBe('workspace');
    });

    it('应用 timeWindow', () => {
      const preset = makePreset({ timeWindow: '48h' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.timeWindow).toBe('48h');
    });

    it('应用 plantNodeId（有值）', () => {
      const preset = makePreset({ plantNodeId: 'node-1' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.plantNodeId).toBe('node-1');
    });

    it('应用 plantNodeId（空值清除为 null）', () => {
      const preset = makePreset({ plantNodeId: '' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.plantNodeId).toBeNull();
    });

    it('应用 loopType（有值）', () => {
      const preset = makePreset({ loopType: 'FLOW' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.loopType).toBe('FLOW');
    });

    it('应用 loopType（空值清除为 null）', () => {
      const preset = makePreset({ loopType: '' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.loopType).toBeNull();
    });

    it('应用 keyword', () => {
      const preset = makePreset({ keyword: 'FT-101' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.keyword).toBe('FT-101');
    });

    it('应用 attentionOnly=true', () => {
      const preset = makePreset({ attentionOnly: true });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.attentionOnly).toBe(true);
    });

    it('应用 attentionOnly=false', () => {
      const preset = makePreset({ attentionOnly: false });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.attentionOnly).toBe(false);
    });

    it('始终清除 eventId 为 null', () => {
      const preset = makePreset({ view: 'workspace' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.eventId).toBeNull();
    });

    it('始终清除 section 为 null', () => {
      const preset = makePreset({ view: 'workspace' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.section).toBeNull();
    });

    it('预设不含 view 字段时不设置 patch.view', () => {
      const preset = makePreset({ keyword: 'FT-101' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.view).toBeUndefined();
    });

    it('预设不含 attentionOnly 时不设置 patch.attentionOnly', () => {
      const preset = makePreset({ view: 'workspace' });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch.attentionOnly).toBeUndefined();
    });

    it('完整预设应用所有字段', () => {
      const preset = makePreset({
        view: 'table',
        timeWindow: '72h',
        plantNodeId: 'node-1',
        loopType: 'FLOW',
        keyword: 'FT-101',
        attentionOnly: true,
      });
      const patch = buildApplyPatch(preset, ['ADMIN']);
      expect(patch).toEqual({
        view: 'table',
        timeWindow: '72h',
        plantNodeId: 'node-1',
        loopType: 'FLOW',
        keyword: 'FT-101',
        attentionOnly: true,
        eventId: null,
        section: null,
      });
    });

    it('EXPERT 完整预设应用时 view 回退但其余字段正常', () => {
      const preset = makePreset({
        view: 'table',
        timeWindow: '48h',
        plantNodeId: 'node-2',
        loopType: 'TEMPERATURE',
        keyword: 'TT-201',
        attentionOnly: false,
      });
      const patch = buildApplyPatch(preset, ['EXPERT']);
      expect(patch.view).toBe('workspace');
      expect(patch.timeWindow).toBe('48h');
      expect(patch.plantNodeId).toBe('node-2');
      expect(patch.loopType).toBe('TEMPERATURE');
      expect(patch.keyword).toBe('TT-201');
      expect(patch.attentionOnly).toBe(false);
      expect(patch.eventId).toBeNull();
      expect(patch.section).toBeNull();
    });
  });
});
