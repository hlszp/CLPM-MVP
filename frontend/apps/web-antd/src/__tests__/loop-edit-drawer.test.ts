/**
 * 回路编辑抽屉（views/loop/manage/LoopEditDrawer.vue）单元测试 — ENG-01 回归
 *
 * P1-04/ENG-01（验收后补充）：Tag 关联保存失败必须阻断新建向导推进。
 * 修复前 doSaveTagMapping 的 catch 只 console.error 不 re-throw，
 * wizardNext 步骤 2 的 `try { await doSaveTagMapping(); } catch { return; }`
 * 守卫永不触发——保存失败向导照常推进到步骤 3。
 *
 * 覆盖：
 * 1. updateLoopTagMappingApi reject（axios 形态错误，全局拦截器路径）时
 *    wizardNext 不推进（wizardCurrent 停留 1）、不 emit('saved')
 * 2. 保存成功才推进（wizardCurrent → 2 + emit('saved')），守卫不过度阻断
 * 3. 非 HTTP 层异常（无 response，本地兜底提示路径）同样阻断推进
 * 4. doSaveTagMapping 失败 rejection 向调用方传播——父组件 manage.vue
 *    confirmSave 的 catch 依赖该契约（失败保持确认弹窗打开）
 *
 * 手法：组件 defineExpose 未含 wizardCurrent/wizardNext，经 VTU 对
 * script-setup 的 setupState 代理读写（@vue/test-utils createVMProxy）。
 */
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  createLoopApiMock,
  getLoopDetailApiMock,
  getLoopTagsApiMock,
  updateLoopTagMappingApiMock,
} = vi.hoisted(() => ({
  createLoopApiMock: vi.fn(),
  getLoopDetailApiMock: vi.fn().mockResolvedValue({ basicInfo: {} }),
  getLoopTagsApiMock: vi.fn().mockResolvedValue({ loopId: 'loop-1', status: 'PARTIAL', tags: [] }),
  updateLoopTagMappingApiMock: vi.fn(),
}));

vi.mock('#/api/loop', async (importOriginal) => ({
  ...(await importOriginal<typeof import('#/api/loop')>()),
  createLoopApi: createLoopApiMock,
  getLoopDetailApi: getLoopDetailApiMock,
  getLoopTagsApi: getLoopTagsApiMock,
  updateLoopTagMappingApi: updateLoopTagMappingApiMock,
}));

vi.mock('#/api/dcs', () => ({
  getModelsApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('#/api/dict', () => ({
  DICT_TYPE_LOOP_TYPE: 'LOOP_TYPE',
  getDictItemsApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('#/api/tag', () => ({
  getTagListApi: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  matchTagsForLoopApi: vi.fn().mockResolvedValue([]),
}));

vi.mock('@vben/icons', () => ({
  IconifyIcon: { name: 'IconifyIcon', template: '<span>icon</span>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmInfoTip: { name: 'ClpmInfoTip', template: '<span>tip</span>' },
}));

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import type { LoopApi } from '#/api/loop';

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import LoopEditDrawer from '#/views/loop/manage/LoopEditDrawer.vue';

/** 最小 LoopListItem（向导步骤 2 只消费 loopId/tagName） */
const LOOP: LoopApi.LoopListItem = {
  loopId: 'loop-1',
  tagName: 'FIC-101',
  description: '',
  unitId: 'unit-1',
  unitName: '单元一',
  controlMode: 'Auto',
  isActive: true,
  status: 'PARTIAL',
  tagMappingStatus: {
    pv: true,
    sp: true,
    op: true,
    mode: true,
    pid_p: false,
    pid_i: false,
    pid_d: false,
  },
};

/** 模拟步骤 1 已保存完成、进入步骤 2 的向导状态 */
async function setupWizardStep2() {
  const wrapper = mount(LoopEditDrawer, {
    props: { plantNodeOptions: [] },
    global: {
      directives: { permission: {} },
      stubs: {
        ADrawer: true,
        AForm: true,
        AFormItem: true,
        AInput: true,
        AInputNumber: true,
        ASelect: true,
        ASpin: true,
        ASteps: true,
        AStep: true,
        ASwitch: true,
        ATabPane: true,
        ATabs: true,
        ATag: true,
        ATooltip: true,
        ARadioGroup: true,
        ACheckbox: true,
        AButton: true,
        ModeMappingEditor: true,
        LoopStatusBadge: true,
      },
    },
  });
  const vm = wrapper.vm as any;
  await vm.open(null, 'create');
  await nextTick();
  // 步骤 1 的产物：基础信息保存成功后 editingLoop 已存在（此处直设，绕过表单校验）
  vm.editingLoop = LOOP;
  // 必填槽位填满（pv/sp/op/mode，缺则 wizardNext 走"必填未关联"提前 return）
  vm.slotState.pv = 'tag-pv';
  vm.slotState.sp = 'tag-sp';
  vm.slotState.op = 'tag-op';
  vm.slotState.mode = 'tag-mode';
  vm.wizardCurrent = 1;
  return { vm, wrapper };
}

describe('回路编辑抽屉 LoopEditDrawer ENG-01 保存失败阻断向导推进', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uT-LED-001: 保存失败（API reject）不推进到步骤 2，不 emit saved', async () => {
    // axios 形态错误（含 response）：用户可见提示走全局拦截器路径
    updateLoopTagMappingApiMock.mockRejectedValueOnce({
      response: { status: 500, data: { message: '服务异常' } },
      config: {},
    });
    const { vm, wrapper } = await setupWizardStep2();

    await vm.wizardNext();
    await nextTick();

    expect(updateLoopTagMappingApiMock).toHaveBeenCalledWith('loop-1', {
      pv: 'tag-pv',
      sp: 'tag-sp',
      op: 'tag-op',
      mode: 'tag-mode',
      pid_p: null,
      pid_i: null,
      pid_d: null,
    });
    // 核心断言：向导停留在步骤 2（索引 1），未推进到索引 2
    expect(vm.wizardCurrent).toBe(1);
    // 保存失败不得通知父组件"已保存"（否则父层误刷新/误关闭）
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('uT-LED-002: 保存成功推进到步骤 3 并 emit saved（守卫不过度阻断）', async () => {
    updateLoopTagMappingApiMock.mockResolvedValueOnce({
      loopId: 'loop-1',
      status: 'READY',
      tags: [],
    });
    const { vm, wrapper } = await setupWizardStep2();

    await vm.wizardNext();
    await nextTick();

    expect(vm.wizardCurrent).toBe(2);
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('uT-LED-003: 非 HTTP 层异常（无 response，本地兜底提示路径）同样阻断推进', async () => {
    updateLoopTagMappingApiMock.mockRejectedValueOnce(
      new Error('render failed'),
    );
    const { vm, wrapper } = await setupWizardStep2();

    await vm.wizardNext();
    await nextTick();

    expect(vm.wizardCurrent).toBe(1);
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('uT-LED-004: doSaveTagMapping 失败 rejection 向调用方传播（父组件 confirmSave 契约）', async () => {
    // 父组件 manage.vue confirmSave 以 try/catch 承接该 rejection——
    // 失败保持确认弹窗打开；若吞错则弹窗照常关闭（ENG-01 修复前行为）
    updateLoopTagMappingApiMock.mockRejectedValueOnce(new Error('boom'));
    const { vm } = await setupWizardStep2();

    await expect(vm.doSaveTagMapping()).rejects.toThrow('boom');
  });
});
