/**
 * 批次诊断记录抽屉（diagnosis/components/batch-runs-drawer.vue）单元测试
 *
 * 2026-10-05 诊断任务页"查看结果"改抽屉：覆盖
 * - 打开时按 taskId 拉取该批次诊断记录（GET /diagnosis/runs?taskId=）
 * - 主分类空值语义：NULL+终态=未见异常（NO_SYMPTOM 落库 NULL）/ FAILED=—
 * - 次分类多值拼接与空态
 */
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getRunsMock } = vi.hoisted(() => ({
  getRunsMock: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

vi.mock('#/api/diagnosis', () => ({
  getDiagnosisRunsApi: getRunsMock,
}));

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import BatchRunsDrawer from '#/views/diagnosis/components/batch-runs-drawer.vue';

const mountOptions = {
  global: {
    directives: { permission: {} },
    stubs: {
      ADrawer: true,
      ATable: true,
      ATag: true,
    },
  },
  props: {
    open: true,
    taskId: 'task-abc',
    taskTitle: '回路诊断-261005-3',
  },
};

describe('批次诊断记录抽屉 batch-runs-drawer.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getRunsMock.mockResolvedValue({ items: [], total: 0 });
  });

  it('uT-BRD-001: 打开即按 taskId 拉取批次诊断记录', async () => {
    mount(BatchRunsDrawer, mountOptions);
    await nextTick();
    await nextTick();
    expect(getRunsMock).toHaveBeenCalled();
    const params = getRunsMock.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(params.taskId).toBe('task-abc');
    expect(params.page).toBe(1);
  });

  it('uT-BRD-002: 未打开不拉取', async () => {
    mount(BatchRunsDrawer, { ...mountOptions, props: { ...mountOptions.props, open: false } });
    await nextTick();
    expect(getRunsMock).not.toHaveBeenCalled();
  });

  it('uT-BRD-003: 主分类空值语义（NULL=未见异常，FAILED=无产出）', async () => {
    const wrapper = mount(BatchRunsDrawer, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    expect(
      vm.primaryText({
        primaryCategory: 'TUNING',
        primaryCategoryLabel: '参数问题（PID 整定）',
        status: 'SUCCESS',
      }),
    ).toBe('参数问题（PID 整定）');
    // NULL + SUCCESS → 未见异常（分类引擎 NO_SYMPTOM 落库 NULL）
    expect(vm.primaryText({ primaryCategory: null, status: 'SUCCESS' })).toBe(
      '未见异常',
    );
    // NULL + FAILED → 失败留痕无结论
    expect(vm.primaryText({ primaryCategory: null, status: 'FAILED' })).toBe('—');
  });

  it('uT-BRD-004: 次分类多值拼接与空态', async () => {
    const wrapper = mount(BatchRunsDrawer, mountOptions);
    await nextTick();
    const vm = wrapper.vm as any;
    expect(vm.secondaryText({ secondaryCategories: [] })).toBe('');
    expect(
      vm.secondaryText({
        secondaryCategories: [
          { category: 'UTILIZATION', categoryLabel: '投用率低' },
          { category: 'DESIGN', categoryLabel: undefined },
        ],
      }),
    ).toBe('投用率低、组态/设计问题');
  });
});
