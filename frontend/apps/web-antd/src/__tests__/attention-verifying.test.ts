/**
 * VERIFYING 状态口径统一测试（P1-03，IA-04）
 *
 * - 关注前端字典：ATTENTION_STATUS_LABEL/COLOR/ORDER 覆盖 VERIFYING
 *   （后端 monitor._VALID_STATUSES 已含 VERIFYING，前端此前四态漏配，
 *   VERIFYING 关注项会渲染裸枚举）；
 * - 工作台在途集合：use-journey-summary 在途工单四态
 *   PENDING/EXECUTING/VERIFYING/REOPENED 并行查询 total 相加（与归档口径
 *   api/governance.ts 注释统一——原三态漏 VERIFYING，验证中工单不计在途）。
 */
import type { Mock } from 'vitest';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getHandlingOrdersApi, getTuningTasksApi } = vi.hoisted(() => ({
  getHandlingOrdersApi: vi.fn(),
  getTuningTasksApi: vi.fn(),
}));

vi.mock('#/api/handling', () => ({
  getHandlingOrdersApi: (...a: unknown[]) => getHandlingOrdersApi(...a),
}));
vi.mock('#/api/tuning', () => ({
  getTuningTasksApi: (...a: unknown[]) => getTuningTasksApi(...a),
}));

// diagWithZone（use-diag-data）引入诊断 API 与 store——本用例不消费其行为，打桩
vi.mock('../views/loop/workbench360/composables/use-diag-data', () => ({
  diagWithZone: (ts: null | string) => ts,
  useDiagData: vi.fn(),
}));

import { useJourneySummary } from '../views/loop/workbench360/composables/use-journey-summary';
import {
  ATTENTION_STATUS_COLOR,
  ATTENTION_STATUS_LABEL,
  ATTENTION_STATUS_ORDER,
} from '../views/monitor/attention.vue';

function orderRes(total: number) {
  return { items: [], page: 1, pageSize: 1, total };
}

describe('关注状态字典 VERIFYING 补齐（P1-03 IA-04）', () => {
  it('LABEL/COLOR 覆盖全部后端状态（含 VERIFYING，不渲染裸枚举）', () => {
    const backendStatuses = [
      'ACKNOWLEDGED',
      'IN_PROGRESS',
      'OPEN',
      'SUPPRESSED',
      'VERIFYING',
    ];
    for (const s of backendStatuses) {
      expect(ATTENTION_STATUS_LABEL[s as 'OPEN']).toBeTruthy();
      expect(ATTENTION_STATUS_COLOR[s as 'OPEN']).toBeTruthy();
    }
    expect(ATTENTION_STATUS_LABEL.VERIFYING).toBe('验证中');
  });

  it('筛选顺序含 VERIFYING（状态过滤下拉可选验证中）', () => {
    expect(ATTENTION_STATUS_ORDER).toContain('VERIFYING');
    expect(ATTENTION_STATUS_ORDER).toHaveLength(5);
  });
});

describe('use-journey-summary 在途集合含 VERIFYING（P1-03 IA-04）', () => {
  beforeEach(() => {
    getHandlingOrdersApi.mockReset();
    getTuningTasksApi.mockReset();
  });

  it('四态并行查询（PENDING/EXECUTING/VERIFYING/REOPENED）total 相加', async () => {
    getTuningTasksApi.mockResolvedValue({ items: [], total: 0 });
    getHandlingOrdersApi.mockImplementation(async (params: any) => {
      // 每态一次计数查询（pageSize:1）+ 一次最近工单查询（pageSize:10）
      if (params.pageSize === 1) {
        return orderRes(params.status === 'VERIFYING' ? 3 : 1);
      }
      return { items: [], total: 4 };
    });

    const summary = useJourneySummary(() => 'loop-1', {
      enabled: () => ({ handling: true, tuning: true }),
    });
    // watch immediate 已触发；等微任务队列清空
    await new Promise((resolve) => setTimeout(resolve, 0));

    const countCalls = (getHandlingOrdersApi as Mock).mock.calls
      .map((c) => c[0] as Record<string, unknown>)
      .filter((p) => p.pageSize === 1);
    const statuses = countCalls.map((p) => p.status);
    expect(statuses.toSorted()).toEqual(
      ['EXECUTING', 'PENDING', 'REOPENED', 'VERIFYING'].toSorted(),
    );
    // 在途 = 1+1+3+1 = 6（原三态口径只有 3，验证中 3 单漏计）
    expect(summary.handlingSummary.value?.inFlightCount).toBe(6);
  });

  it('handling 模块禁用时零请求', async () => {
    const summary = useJourneySummary(() => 'loop-1', {
      enabled: () => ({ handling: false, tuning: false }),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(getHandlingOrdersApi).not.toHaveBeenCalled();
    expect(getTuningTasksApi).not.toHaveBeenCalled();
    expect(summary.handlingSummary.value).toBeNull();
  });
});
