import { mount } from '@vue/test-utils';
import { flushPromises } from '@vue/test-utils';
import { nextTick } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  loops: vi.fn(),
  tree: vi.fn().mockResolvedValue([]),
  trigger: vi.fn().mockResolvedValue({ accepted: 1 }),
  warning: vi.fn(),
}));
vi.mock('#/api/loop', () => ({ getLoopListApi: mocks.loops }));
vi.mock('#/api/plant-node', () => ({ getPlantNodeTreeApi: mocks.tree }));
vi.mock('#/api/diagnosis', () => ({ triggerDiagnosisApi: mocks.trigger }));
vi.mock('ant-design-vue', async (original) => ({
  ...(await original<typeof import('ant-design-vue')>()),
  message: { warning: mocks.warning, success: vi.fn(), error: vi.fn() },
}));

// oxlint-disable-next-line import/first -- mocks precede component import
import BatchDiagnoseModal from '#/views/diagnosis/components/batch-diagnose-modal.vue';

const loops = Array.from({ length: 1209 }, (_, i) => ({
  loopId: `loop-${i}`,
  tagName: i === 0 ? '04PIC03001_PIDA' : `LOOP_${i}`,
}));

async function createModal() {
  const wrapper = mount(BatchDiagnoseModal, {
    props: { open: false },
    global: {
      stubs: {
        AModal: {
          name: 'TestModal',
          props: ['okButtonProps'],
          template: '<div><slot /></div>',
        },
        Tree: true,
        Spin: { template: '<div><slot /></div>' },
        Checkbox: false,
        Input: true,
        Alert: true,
      },
    },
  });
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
}

describe('批量诊断选择与上限', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loops.mockImplementation(({ page, pageSize }) =>
      Promise.resolve({
        items: loops.slice((page - 1) * pageSize, page * pageSize),
        total: loops.length,
      }),
    );
  });

  it('1209 个回路默认不选，搜索后仅提交明确勾选的一个', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    expect(vm.checkedIds).toEqual([]);
    vm.loopKeyword = '04PIC03001_PIDA';
    await nextTick();
    vm.toggleCheckAll();
    await vm.submit();
    expect(mocks.trigger).toHaveBeenCalledWith(
      expect.objectContaining({ loopIds: ['loop-0'] }),
    );
    wrapper.unmount();
  });

  it('超过 50 个时禁止提交，恰好 50 个可以提交', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    vm.checkedIds = loops.slice(0, 51).map((l) => l.loopId);
    await vm.submit();
    expect(mocks.trigger).not.toHaveBeenCalled();
    expect(mocks.warning).toHaveBeenCalled();
    await nextTick();
    expect(
      wrapper.findComponent({ name: 'TestModal' }).props('okButtonProps')
        .disabled,
    ).toBe(true);
    vm.checkedIds = loops.slice(0, 50).map((l) => l.loopId);
    await vm.submit();
    expect(mocks.trigger.mock.calls[0]?.[0].loopIds).toHaveLength(50);
    wrapper.unmount();
  });

  it('全选不能静默截断或选中超过 50 个', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    vm.toggleCheckAll();
    expect(vm.checkedIds).toEqual([]);
    expect(mocks.warning).toHaveBeenCalled();
    wrapper.unmount();
  });

  it('搜索明确提示隐藏的已选回路，并支持清空全部', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    vm.checkedIds = ['loop-0', 'loop-1'];
    vm.loopKeyword = '04PIC03001_PIDA';
    await nextTick();
    expect(wrapper.text()).toContain('另有 1 个已选回路不在搜索结果中');
    await wrapper
      .findAll('button')
      .find((b) => b.text() === '清空已选')!
      .trigger('click');
    expect(vm.checkedIds).toEqual([]);
    wrapper.unmount();
  });

  it('重开弹窗清空上一批选择和搜索', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    vm.checkedIds = ['loop-0'];
    vm.loopKeyword = '04PIC03001_PIDA';
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    expect(vm.checkedIds).toEqual([]);
    expect(vm.loopKeyword).toBe('');
    wrapper.unmount();
  });

  it('真实复选框跨搜索勾选保留隐藏选择，提交两项', async () => {
    const wrapper = await createModal();
    const vm = wrapper.vm as any;
    vm.loopKeyword = '04PIC03001_PIDA';
    await nextTick();
    await wrapper
      .find('.batch-diag-loops__list input[type="checkbox"]')
      .setValue(true);
    vm.loopKeyword = 'LOOP_1208';
    await nextTick();
    await wrapper
      .find('.batch-diag-loops__list input[type="checkbox"]')
      .setValue(true);
    expect(vm.checkedIds).toEqual(['loop-0', 'loop-1208']);
    expect(wrapper.text()).toContain('另有 1 个已选回路不在搜索结果中');
    await vm.submit();
    expect(mocks.trigger.mock.calls[0]?.[0].loopIds).toEqual([
      'loop-0',
      'loop-1208',
    ]);
    wrapper.unmount();
  });
});
