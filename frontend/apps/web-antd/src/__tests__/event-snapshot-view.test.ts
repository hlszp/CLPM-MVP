/**
 * EventSnapshotView 单元测试（预警事件详情快照人类可读渲染）
 *
 * 覆盖：
 * - kind=condition：单阈值判定摘要 / 分级阈值命中档高亮 / 可信度 A~E 色带 / 未完成判定原因
 * - kind=dsl：监测·判定·响应分组 + 动作中文 + 未识别字段折叠兜底
 * - 空快照：占位 '-'
 */
import { mount } from '@vue/test-utils';

import { describe, expect, it } from 'vitest';

import EventSnapshotView from '#/views/alert/components/event-snapshot-view.vue';

function mountCondition(snapshot: Record<string, any>) {
  return mount(EventSnapshotView, {
    props: { kind: 'condition' as const, snapshot },
  });
}

function mountDsl(snapshot: Record<string, any>) {
  return mount(EventSnapshotView, {
    props: { kind: 'dsl' as const, snapshot },
  });
}

describe('EventSnapshotView 触发条件快照（kind=condition）', () => {
  it('单阈值：判定语句 + 实际值 + 数据时间/指标来源上下文行', () => {
    const w = mountCondition({
      metricSource: 'KPI',
      metric: 'score',
      operator: '<',
      threshold: 60,
      actualValue: 55.2,
      dataTime: '2026-10-03T10:00:00Z',
    });
    const text = w.text();
    expect(text).toContain('综合评分 低于 60');
    expect(text).toContain('55.2');
    expect(text).toContain('数据时间');
    expect(text).toContain('指标来源 KPI 评估结果');
  });

  it('分级阈值：命中档高亮（✓ + 命中「重要」档 Tag）', () => {
    const w = mountCondition({
      metricSource: 'KPI',
      metric: 'score',
      operator: '<',
      levels: [
        { severity: 'WARN', value: 70 },
        { severity: 'ERROR', value: 60 },
        { severity: 'CRITICAL', value: 50 },
      ],
      matchedLevel: 'ERROR',
      actualValue: 55.2,
      dataTime: '2026-10-03T10:00:00Z',
    });
    const text = w.text();
    expect(text).toContain('综合评分 低于 分级阈值');
    expect(text).toContain('命中「重要」档');
    // 命中档（ERROR=重要）带 ✓，未命中档不带
    const chips = w.findAll('.min-w-14');
    expect(chips).toHaveLength(3);
    expect(chips[1]!.text()).toContain('✓');
    expect(chips[0]!.text()).not.toContain('✓');
    expect(chips[1]!.text()).toContain('低于 60');
  });

  it('可信度联动：A~E 五级色带 + 实际等级高亮', () => {
    const w = mountCondition({ maxLevel: 'C', actualLevel: 'D' });
    const text = w.text();
    expect(text).toContain('可信度劣于 C');
    expect(text).toContain('实际等级 D');
    const chips = w.findAll('.min-w-14');
    expect(chips).toHaveLength(5);
    expect(chips[3]!.text()).toContain('✓');
  });

  it('未完成判定：显示中文原因，无实际值', () => {
    const w = mountCondition({ reason: 'no_data' });
    expect(w.text()).toContain('未完成判定：回路无数据');
  });

  it('未知字段兜底透出（不静默丢弃）', () => {
    const w = mountCondition({
      metric: 'score',
      operator: '<',
      threshold: 60,
      actualValue: 55,
      extraField: 'xyz',
    });
    expect(w.text()).toContain('extraField');
    expect(w.text()).toContain('xyz');
  });

  it('空快照：占位 -', () => {
    const w = mountCondition({});
    expect(w.text()).toBe('-');
  });
});

describe('EventSnapshotView 规则 DSL 快照（kind=dsl）', () => {
  const baseDsl = {
    ruleType: 'METRIC_THRESHOLD',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      metricSource: 'KPI',
      metric: 'score',
      operator: '<',
      checkIntervalMinutes: 60,
      levels: [
        { severity: 'WARN', value: 70 },
        { severity: 'ERROR', value: 60 },
      ],
    },
    actions: [{ type: 'CREATE_EVENT' }, { type: 'NOTIFY' }],
  };

  it('监测/判定/响应分组齐全，动作转中文', () => {
    const w = mountDsl(baseDsl);
    const text = w.text();
    expect(text).toContain('监测');
    expect(text).toContain('规则类型');
    expect(text).toContain('指标阈值');
    expect(text).toContain('监测指标');
    expect(text).toContain('综合评分');
    expect(text).toContain('监测周期');
    expect(text).toContain('每 60 分钟');
    expect(text).toContain('作用范围');
    expect(text).toContain('全部回路');
    expect(text).toContain('判定');
    expect(text).toContain('响应');
    expect(text).toContain('生成预警事件');
    expect(text).toContain('推送通知');
    // DSL 侧分级阈值色带无命中标记
    const chips = w.findAll('.min-w-14');
    expect(chips).toHaveLength(2);
    expect(chips[0]!.text()).not.toContain('✓');
  });

  it('条件内未识别字段折叠在「原始定义」中保留（展开可见）', async () => {
    const w = mountDsl({
      ...baseDsl,
      condition: { ...baseDsl.condition, futureField: 42 },
    });
    expect(w.text()).toContain('原始定义');
    // Collapse 默认收起不渲染内容；展开后兜底字段可见
    await w.find('.ant-collapse-header').trigger('click');
    expect(w.text()).toContain('futureField');
    expect(w.text()).toContain('42');
  });

  it('空 DSL：占位 -', () => {
    const w = mountDsl(undefined as any);
    expect(w.text()).toBe('-');
  });
});
