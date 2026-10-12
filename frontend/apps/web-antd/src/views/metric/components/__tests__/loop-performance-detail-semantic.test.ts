import type { LoopPerformanceRow } from '#/views/metric/components/loop-performance-shared';

/**
 * G3-R01：回路性能详情内容——评估语义真实消费渲染（14 号文 §2）
 *
 * 测试数据形状对齐真实 HTTP 响应（/performance/loops/snapshots 行）：
 * metricsSemantic 从该行自己的 result_record payload 原样透出
 * （键=指标 DB 列名 + __composite__）。
 *
 * 覆盖：
 * - 合格静稳例：正式 F null + 参考分 100 徽标 + 动态证据不足注记；
 *   composite PARTIAL → 等级 '—'（不定正式等级）
 * - 正式 0 优先互斥：正式值在场（含 0）时残留 referenceScore 不重复计参考
 * - legacy 行（无语义）：单值原样渲染 + 评估语义"历史不可追溯"
 * - 新 profile 断链：semanticLinkStatus=BROKEN 显示断链不回退
 */
import { mount } from '@vue/test-utils';

import { describe, expect, it, vi } from 'vitest';

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import LoopPerformanceDetailContent from '#/views/metric/components/loop-performance-detail-content.vue';

vi.mock('#/api/metric', () => ({
  getLoopSnapshotsApi: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

function baseRow(): LoopPerformanceRow {
  return {
    loopId: '00000000-0000-0000-0000-000000000301',
    loopTagName: 'FIC-101',
    tsStart: '2026-10-12T08:00:00Z',
    tsEnd: '2026-10-12T09:00:00Z',
    score: null,
    status: 'SUCCESS',
    fastRate: null,
    accuracyRate: 90,
    steadyRate: 95,
    effectiveAutoRate: 90,
    autoModeRate: 92,
    goodValueRate: 99,
    oscillationRate: 5,
    confidenceLevel: 'A',
    algorithmVersion: 'KPI_CALC_v3.0-rc1',
    semanticLinkStatus: 'AVAILABLE',
    resultRecordId: '00000000-0000-0000-0000-000000000901',
    metricsSemantic: {
      fast_rate: {
        value: null,
        confidenceLevel: 'A',
        assessmentState: 'UNASSESSABLE',
        details: {
          referenceScore: 100,
          referenceMethod: 'STATIC_WINDOW_POLICY',
          evidenceStatus: 'INSUFFICIENT_DYNAMIC_EVIDENCE',
          reasonCode: 'NO_DYNAMIC_EVIDENCE',
        },
      },
      __composite__: {
        value: null,
        confidenceLevel: null,
        assessmentState: 'PARTIAL',
        details: {
          incompleteComponents: [
            { metric: 'fast_rate', referenceOnly: true },
          ],
        },
      },
    },
  } as unknown as LoopPerformanceRow;
}

function textOf(wrapper: ReturnType<typeof mount>) {
  return wrapper.text();
}

describe('G3-R01 loop-performance-detail-content 语义消费', () => {
  it('合格静稳例：正式 F null + 参考分 100 两行渲染，等级不定', () => {
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: baseRow(), withHistory: false },
    });
    const text = textOf(wrapper);
    // 正式 F 行（null → '—'）与参考分行并存
    expect(text).toContain('正式 F：');
    expect(text).toContain('参考分 100');
    expect(text).toContain('动态证据不足');
    // composite PARTIAL → 不定正式等级：评估等级格显示 '—'（紧邻无等级文案）
    expect(text).toContain('评估等级—');
    // 参考分不进等级：注记行明示
    expect(text).toContain('不参与定级');
    // 评估语义可追溯
    expect(text).toContain('完整语义可追溯');
  });

  it('正式 0 优先互斥：正式值在场时残留 referenceScore 不重复计参考', () => {
    const row = baseRow();
    row.fastRate = 0;
    row.metricsSemantic!.fast_rate = {
      value: 0,
      confidenceLevel: 'A',
      assessmentState: 'COMPLETE',
      details: { referenceScore: 100 },
    };
    row.metricsSemantic!.__composite__ = {
      value: 85,
      confidenceLevel: 'A',
      assessmentState: 'COMPLETE',
      details: {},
    };
    row.score = 85;
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: row, withHistory: false },
    });
    const text = textOf(wrapper);
    expect(text).not.toContain('参考分 100');
    expect(text).not.toContain('正式 F：');
  });

  it('legacy 行（无语义）：单值渲染 + 显式历史不可追溯', () => {
    const row = baseRow();
    row.metricsSemantic = undefined;
    row.semanticLinkStatus = 'NOT_TRACEABLE';
    row.fastRate = 88;
    row.score = 82;
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: row, withHistory: false },
    });
    const text = textOf(wrapper);
    expect(text).toContain('88');
    expect(text).not.toContain('正式 F：');
    expect(text).toContain('历史不可追溯');
  });

  it('新 profile 断链：semanticLinkStatus=BROKEN 显示断链不静默回退', () => {
    const row = baseRow();
    row.metricsSemantic = undefined;
    row.semanticLinkStatus = 'BROKEN';
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: row, withHistory: false },
    });
    const text = textOf(wrapper);
    expect(text).toContain('语义断链');
    expect(text).not.toContain('历史不可追溯');
  });

  // R1（2026-10-12 审核修复批）：R 折扣归因——综合分低值不得渲染成
  // "确定为 0 分"；契约 details 携带未知占比时显式标注已证实下界。
  it('R=0 且未知占比高：综合分旁标注已证实下界与数据不足折扣', () => {
    const row = baseRow();
    row.score = 0;
    row.effectiveAutoRate = 0;
    row.metricsSemantic!.effective_auto_rate = {
      value: 0,
      confidenceLevel: 'A',
      assessmentState: 'COMPLETE',
      details: {
        numeratorSeconds: 0,
        denominatorSeconds: 3600,
        unknownSeconds: 3564,
        knownSeconds: 36,
        coverageRatio: 0.01,
        possibleRange: [0, 99],
        denominatorBasis: 'FULL_REQUEST_WINDOW',
      },
    };
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: row, withHistory: false },
    });
    const text = textOf(wrapper);
    expect(text).toContain('已证实下界');
    expect(text).toContain('未知占比 99%');
    expect(text).toContain('可能区间 0%~99%');
    expect(text).toContain('综合分含数据不足折扣');
  });

  it('未知占比可忽略（30% 以下）：不渲染 R 折扣注记（不加噪）', () => {
    const row = baseRow();
    row.score = 88;
    row.metricsSemantic!.effective_auto_rate = {
      value: 95,
      confidenceLevel: 'A',
      assessmentState: 'COMPLETE',
      details: {
        numeratorSeconds: 3400,
        denominatorSeconds: 3600,
        unknownSeconds: 180,
        knownSeconds: 3420,
        possibleRange: [94.4, 99.4],
      },
    };
    const wrapper = mount(LoopPerformanceDetailContent, {
      props: { record: row, withHistory: false },
    });
    expect(wrapper.text()).not.toContain('数据不足折扣');
  });
});
