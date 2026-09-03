/**
 * 数据接入页（loop/aas.vue）单元测试
 *
 * 覆盖 2026-07-28 统一组件体系迁移：
 * - 挂载后加载数据源配置；加载失败进入 ClpmDataCanvas error 态，retry 后恢复
 * - Tab 切换到 DCS 系统时加载品牌/型号列表
 * - 写操作按钮 v-permission="['ADMIN']"（ADMIN 可见 / 非 ADMIN Comment 占位）
 * - 确认流全部由 ClpmDangerConfirmModal 承载（5 个实例，无 Popconfirm）
 */
import { flushPromises, mount } from '@vue/test-utils';

import { useAccessStore, useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { permissionDirective } from '#/directives/permission';

import Aas from '../views/loop/aas.vue';

const getDatasourceConfigApiMock = vi.fn();
const refreshSubscriptionApiMock = vi.fn();
const getVendorsApiMock = vi.fn();
const getModelsApiMock = vi.fn();
const getAasConfigApiMock = vi.fn();
const triggerAasSyncApiMock = vi.fn();

vi.mock('#/api/aas', () => ({
  getAasConfigApi: (...args: unknown[]) => getAasConfigApiMock(...args),
  triggerAasSyncApi: (...args: unknown[]) => triggerAasSyncApiMock(...args),
}));

vi.mock('#/api/datasource', () => ({
  getDatasourceConfigApi: (...args: unknown[]) =>
    getDatasourceConfigApiMock(...args),
  refreshSubscriptionApi: (...args: unknown[]) =>
    refreshSubscriptionApiMock(...args),
  testHistoryApiApi: vi.fn(),
  testSignalrApi: vi.fn(),
  updateDatasourceConfigApi: vi.fn(),
}));

vi.mock('#/api/dcs', () => ({
  createModelApi: vi.fn(),
  createVendorApi: vi.fn(),
  deleteModelApi: vi.fn(),
  deleteVendorApi: vi.fn(),
  exportModelsApi: vi.fn(),
  exportVendorsApi: vi.fn(),
  getModeDefinitionsApi: vi.fn().mockResolvedValue([]),
  getModelsApi: (...args: unknown[]) => getModelsApiMock(...args),
  getModeMatrixApi: vi.fn(),
  getPidStructuresApi: vi.fn().mockResolvedValue([]),
  getVendorsApi: (...args: unknown[]) => getVendorsApiMock(...args),
  importModelsApi: vi.fn(),
  importVendorsApi: vi.fn(),
  updateModeDefinitionApi: vi.fn(),
  updateModelApi: vi.fn(),
  updateVendorApi: vi.fn(),
  upsertModeMappingApi: vi.fn(),
}));

vi.mock('ant-design-vue', () => ({
  Alert: { template: '<div><slot /></div>' },
  Button: { name: 'Button', template: '<button><slot /></button>' },
  Card: { template: '<div><slot name="extra" /><slot /></div>' },
  Checkbox: { template: '<label><slot /></label>' },
  Form: { template: '<form><slot /></form>' },
  FormItem: { template: '<div><slot /></div>' },
  Input: {
    Password: { template: '<input />' },
    TextArea: { template: '<textarea />' },
    template: '<input />',
  },
  InputNumber: { template: '<input />' },
  message: {
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(() => vi.fn()),
    success: vi.fn(),
    warning: vi.fn(),
  },
  Modal: { template: '<div><slot /></div>' },
  Radio: {
    Group: { name: 'RadioGroup', template: '<div><slot /></div>' },
    template: '<label><slot /></label>',
  },
  Select: {
    props: ['disabled', 'options', 'placeholder', 'value'],
    template: '<select />',
  },
  Switch: { template: '<button type="button" />' },
  Table: { template: '<div><slot /></div>' },
  TabPane: { template: '<div><slot /></div>' },
  Tabs: { name: 'Tabs', template: '<div><slot /></div>' },
  Tag: { template: '<span><slot /></span>' },
  Upload: { template: '<div><slot /></div>' },
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div><slot /></div>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmDangerConfirmModal: {
    name: 'ClpmDangerConfirmModal',
    props: ['open', 'title'],
    template: '<div />',
  },
  ClpmDataCanvas: {
    name: 'ClpmDataCanvas',
    props: ['error', 'errorText', 'loading', 'loadingVariant', 'title'],
    emits: ['retry'],
    template: '<div><slot name="extra" /><slot /></div>',
  },
  ClpmPageToolbar: {
    name: 'ClpmPageToolbar',
    props: ['compact', 'subtitle', 'title'],
    template: '<div />',
  },
}));

vi.mock('../views/loop/components/pid-structure-drawer.vue', () => ({
  default: { template: '<div />' },
}));

const configFixture = {
  historyApiTimeout: 30,
  historyApiToken: 'abcd****wxyz',
  historyApiUrl: 'http://192.168.100.2:81/api/services/v1/HistoryData/Get',
  networkMode: 'lan',
  signalrEnabled: false,
  signalrHubUrl: 'ws://192.168.100.2:81/signalr/realValueForClpmHub',
  signalrReconnectInterval: 5,
  signalrSubscriberRunning: false,
  tailscaleAvailable: true,
};

const aasConfigFixture = {
  enabled: false,
  endpoint: 'opc.tcp://192.168.100.2:4840',
  lastSyncAt: '2026-09-02T15:00:00+08:00',
  lastSyncStatus: 'SUCCESS',
  mockMode: false,
  securityMode: 'None',
  syncIntervalSeconds: 300,
};

function setRoles(roles: string[]) {
  const accessStore = useAccessStore();
  accessStore.setAccessCodes([]);
  const userStore = useUserStore();
  userStore.setUserInfo({
    avatar: '',
    realName: 'tester',
    roles,
    userId: 'tester',
    username: 'tester',
  });
}

function mountAas() {
  return mount(Aas, {
    global: {
      directives: { permission: permissionDirective },
    },
  });
}

/** 页面内 4 个 ClpmDataCanvas：数据源 / DCS 品牌 / DCS 型号 / MODE 矩阵 */
function findCanvases(wrapper: ReturnType<typeof mountAas>) {
  return wrapper.findAllComponents({ name: 'ClpmDataCanvas' });
}

describe('loopAas（数据接入页）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    getDatasourceConfigApiMock.mockResolvedValue(configFixture);
    getVendorsApiMock.mockResolvedValue([]);
    getModelsApiMock.mockResolvedValue([]);
    getAasConfigApiMock.mockResolvedValue(aasConfigFixture);
    triggerAasSyncApiMock.mockResolvedValue({
      checkUrl: '/api/v1/tasks/task-1',
      status: 'PROCESSING',
      taskId: 'task-1',
    });
    setRoles(['ADMIN']);
  });

  it('挂载后加载数据源配置，数据源画布无错误态', async () => {
    const wrapper = mountAas();
    await flushPromises();

    expect(getDatasourceConfigApiMock).toHaveBeenCalledTimes(1);
    const canvases = findCanvases(wrapper);
    expect(canvases.length).toBe(4);
    expect(canvases[0]!.props('error')).toBe(false);
  });

  it('数据源配置加载失败进入 error 态，retry 后重新加载并恢复', async () => {
    getDatasourceConfigApiMock.mockRejectedValueOnce(new Error('网络异常'));
    const wrapper = mountAas();
    await flushPromises();

    const canvas = findCanvases(wrapper)[0]!;
    expect(canvas.props('error')).toBe(true);

    canvas.vm.$emit('retry');
    await flushPromises();

    expect(getDatasourceConfigApiMock).toHaveBeenCalledTimes(2);
    expect(canvas.props('error')).toBe(false);
  });

  it('切换到 DCS 系统 Tab 时加载品牌与型号列表', async () => {
    const wrapper = mountAas();
    await flushPromises();
    expect(getVendorsApiMock).not.toHaveBeenCalled();

    wrapper.findComponent({ name: 'Tabs' }).vm.$emit('update:activeKey', 'dcs');
    await flushPromises();

    expect(getVendorsApiMock).toHaveBeenCalledTimes(1);
    expect(getModelsApiMock).toHaveBeenCalledTimes(1);
  });

  it('aDMIN 角色可见写操作按钮（保存配置 / 新增品牌）', async () => {
    const wrapper = mountAas();
    await flushPromises();

    const buttonTexts = wrapper.findAll('button').map((b) => b.text());
    expect(buttonTexts).toContain('保存配置');
    expect(buttonTexts).toContain('新增品牌');
  });

  it('非 ADMIN 角色写操作按钮被 Comment 占位替换', async () => {
    setRoles(['SPONSOR']);
    const wrapper = mountAas();
    await flushPromises();

    const buttonTexts = wrapper.findAll('button').map((b) => b.text());
    expect(buttonTexts).not.toContain('保存配置');
    expect(buttonTexts).not.toContain('新增品牌');
    expect(buttonTexts).not.toContain('立即同步');
    expect(wrapper.html()).toContain('<!-- v-permission: ADMIN -->');
  });

  it('确认流统一由 ClpmDangerConfirmModal 承载（3 个实例）', async () => {
    const wrapper = mountAas();
    await flushPromises();

    const modals = wrapper.findAllComponents({
      name: 'ClpmDangerConfirmModal',
    });
    expect(modals.length).toBe(3);
    const titles = modals.map((m) => m.props('title'));
    expect(titles).toEqual(
      expect.arrayContaining([
        '切换网络模式',
        '删除 DCS 品牌',
        '删除 DCS 型号',
      ]),
    );
  });

  it('启用实时订阅时可点击刷新实时订阅并展示结果摘要', async () => {
    getDatasourceConfigApiMock.mockResolvedValue({
      ...configFixture,
      signalrEnabled: true,
      signalrSubscriberRunning: true,
    });
    refreshSubscriptionApiMock.mockResolvedValue({
      added: ['LIC-103.PV'],
      error: null,
      finishedAt: '2026-09-02T00:00:01+00:00',
      invocationId: 'manual_refresh_7',
      leaderPid: 12_345,
      removed: [],
      requestId: 'req-1',
      requestedAt: '2026-09-02T00:00:00+00:00',
      source: 'manual-api',
      total: 3,
    });
    const wrapper = mountAas();
    await flushPromises();

    const button = wrapper
      .findAll('button')
      .find((b) => b.text() === '刷新实时订阅');
    expect(button).toBeTruthy();

    await button!.trigger('click');
    await flushPromises();

    expect(refreshSubscriptionApiMock).toHaveBeenCalledTimes(1);
    // 结果摘要：总数/新增/移除/耗时/执行进程（Tag 展示）
    expect(wrapper.text()).toContain('共 3 个测点');
    expect(wrapper.text()).toContain('+1 / -0');
    expect(wrapper.text()).toContain('PID 12345');
  });

  it('ADMIN 可点击立即同步并轮询至 SUCCESS', async () => {
    vi.useFakeTimers();
    try {
      // 挂载时上次状态 SUCCESS（空闲）；触发后两次轮询：PROCESSING → SUCCESS
      getAasConfigApiMock
        .mockResolvedValueOnce(aasConfigFixture)
        .mockResolvedValueOnce({
          ...aasConfigFixture,
          lastSyncStatus: 'PROCESSING',
        })
        .mockResolvedValueOnce(aasConfigFixture);
      const wrapper = mountAas();
      await flushPromises();

      const button = wrapper
        .findAll('button')
        .find((b) => b.text() === '立即同步');
      expect(button).toBeTruthy();
      expect(wrapper.text()).toContain('同步成功');

      await button!.trigger('click');
      await flushPromises();
      expect(triggerAasSyncApiMock).toHaveBeenCalledTimes(1);

      // 第一次轮询：PROCESSING → 状态 Tag 变"同步中"并继续排程
      await vi.advanceTimersByTimeAsync(3000);
      expect(wrapper.text()).toContain('同步中');

      // 第二次轮询：SUCCESS → 完成提示
      await vi.advanceTimersByTimeAsync(3000);
      expect(message.success).toHaveBeenCalledWith(
        'AAS 同步完成，测点元数据已更新',
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
