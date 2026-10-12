/**
 * 驾驶舱下钻弹窗数据层诚实化测试（P1-03，IA-01/IA-02）
 *
 * 覆盖 use-drill.ts 四条口径：
 * - IA-01 分页拉全：ranking 端点 le=100，原 limit:200 必 422 —— 现
 *   limit=100+offset 循环拉取，>100 条可查全（C26）；
 * - IA-01 显式失败态：请求失败（422/500）置 list.error，不再 catch 清空
 *   伪装成"暂无数据"；
 * - IA-02 数值排序：指标清单按数值列降序（原对 "85.00%" 百分号字符串
 *   Number → NaN，排序全失效），缺值排末尾不按 0 比较；
 * - IA-01/E4 截断提示：预警清单 limit 50 静默截断 → 显式
 *   "仅当前 N 条（共 M 条）"footerNote。
 */
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getAlertEventsApi, getRankingApi } = vi.hoisted(() => ({
  getAlertEventsApi: vi.fn(),
  getRankingApi: vi.fn(),
}));

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('#/api/metric', () => ({ getRankingApi: (...a: any[]) => getRankingApi(...a) }));
vi.mock('#/api/alert', () => ({
  getAlertEventsApi: (...a: any[]) => getAlertEventsApi(...a),
}));
vi.mock('#/api/diagnosis', () => ({ getDiagnosisRunsApi: vi.fn() }));
vi.mock('#/api/handling', () => ({
  getHandlingOrdersApi: vi.fn(),
  getHandlingSuggestionsApi: vi.fn(),
}));
vi.mock('#/api/tuning', () => ({ getTuningTasksApi: vi.fn() }));

import { useCockpitDrill } from '../views/cockpit/wb-comps/use-drill';

/** 构造 RankingItem（loadLoops/loadMetric 消费的字段） */
function rankItem(i: number, accuracyRate?: null | number) {
  return {
    accuracyRate: accuracyRate ?? null,
    includeInEvaluation: true,
    loopId: `loop-${String(i).padStart(3, '0')}`,
    loopName: `回路${i}`,
    score: 60 + (i % 40),
    tagName: `TAG-${i}`,
    unitName: '装置A',
  };
}

describe('use-drill 下钻清单诚实化（P1-03 IA-01/IA-02）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getRankingApi.mockReset();
    getAlertEventsApi.mockReset();
  });

  it('IA-01：>100 条分页拉全（limit=100 + offset 循环，不再发 limit=200）', async () => {
    getRankingApi.mockImplementation(async (params: any) => {
      // 第二页不足 100 条 → 终止翻页
      if (params.offset === 0) {
        return Array.from({ length: 100 }, (_, i) => rankItem(i));
      }
      return Array.from({ length: 37 }, (_, i) => rankItem(100 + i));
    });

    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'loops' });

    expect(getRankingApi).toHaveBeenCalledTimes(2);
    const first = getRankingApi.mock.calls[0]![0] as any;
    const second = getRankingApi.mock.calls[1]![0] as any;
    // 单页不超端点 le=100 上限（原 limit:200 必 422）
    expect(first.limit).toBe(100);
    expect(first.offset).toBe(0);
    expect(second.limit).toBe(100);
    expect(second.offset).toBe(100);
    // 全量合并：137 条（原实现只能拿到 0 条——422 后 catch 清空）
    expect(list.rows).toHaveLength(137);
    expect(list.error).toBe('');
    expect(list.footerNote).toBe('');
  });

  it('IA-01：请求失败显式失败态（error 非空），不伪装成空数据', async () => {
    getRankingApi.mockRejectedValue(
      new Error('Request failed with status code 422'),
    );

    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'loops' });

    expect(list.rows).toHaveLength(0);
    expect(list.loading).toBe(false);
    // 422/500 是失败：error 非空且含失败语义，与"暂无数据"空态可区分
    expect(list.error).toContain('回路清单加载失败');
    expect(list.error).toContain('422');
  });

  it('IA-02：指标清单按数值列降序，缺值排末尾（百分号字符串 Number→NaN 修复）', async () => {
    getRankingApi.mockResolvedValue([
      rankItem(1, 0.42),
      rankItem(2, null),
      rankItem(3, 0.99),
      rankItem(4, 0.85),
      rankItem(5, 0.07),
    ]);

    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'metric', metric: 'accuracy_rate' });

    // 旧实现对 metricText（"85.00%" 字符串）Number() 恒 NaN，降序比较全失效
    const values = list.rows.map((r) => r.metricValue as null | number);
    expect(values).toEqual([0.99, 0.85, 0.42, 0.07, null]);
    // 展示列仍是百分号文案
    expect(list.rows[0]!.metricText).toBe('99.00%');
    expect(list.rows[4]!.metricText).toBe('—');
    expect(list.error).toBe('');
  });

  it('IA-01/E4：预警清单截断提示"仅当前 N 条（共 M 条）"，未截断不提示', async () => {
    getAlertEventsApi.mockResolvedValue({
      items: [
        {
          acknowledgedAt: null,
          eventId: 'e1',
          loopId: 'loop-1',
          loopName: '回路1',
          ruleCode: 'R1',
          ruleName: '规则1',
          severity: 'HIGH',
          triggeredAt: '2026-10-10T08:00:00Z',
        },
        {
          acknowledgedAt: null,
          eventId: 'e2',
          loopId: 'loop-2',
          loopName: '回路2',
          ruleCode: 'R2',
          ruleName: '规则2',
          severity: 'WARN',
          triggeredAt: '2026-10-10T08:05:00Z',
        },
      ],
      total: 120,
    });

    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'alerts' });

    expect(list.rows).toHaveLength(2);
    expect(list.footerNote).toContain('仅当前 2 条');
    expect(list.footerNote).toContain('共 120 条');
    expect(list.error).toBe('');

    // 未截断（total ≤ 当前条数）：无提示
    getAlertEventsApi.mockResolvedValue({
      items: [
        {
          acknowledgedAt: null,
          eventId: 'e1',
          loopId: 'loop-1',
          loopName: '回路1',
          ruleCode: 'R1',
          ruleName: '规则1',
          severity: 'HIGH',
          triggeredAt: '2026-10-10T08:00:00Z',
        },
      ],
      total: 1,
    });
    await drill({ kind: 'alerts' });
    expect(list.footerNote).toBe('');
  });

  it('IA-01：失败态重试复用最近一次清单意图', async () => {
    getRankingApi.mockRejectedValueOnce(new Error('500'));
    getRankingApi.mockResolvedValueOnce([rankItem(1)]);

    const { drill, list, retryLastDrill } = useCockpitDrill();
    await drill({ kind: 'metric', metric: 'steady_rate' });
    expect(list.error).not.toBe('');

    await retryLastDrill();
    expect(list.error).toBe('');
    expect(list.rows).toHaveLength(1);
    // 重试仍是同一次意图（steady_rate，而非回退到其他清单）
    expect(getRankingApi.mock.calls.at(-1)![0]).toMatchObject({
      sortBy: 'score',
    });
  });
});
