import type { EngineeringTemplateApi } from '#/api/engineering-template';
import type { LoopApi } from '#/api/loop';

/**
 * P6 Slice M（03/04-a）P2-04 工程模板维护 UI 测试
 *
 * 覆盖任务书验证口径：
 * - 页面渲染：模板列表行 / 详情抽屉动作区；
 * - 流程状态机（dry-run → 确认 → 发布 → 结果）：reducePublishFlow 全链路 +
 *   非法迁移拒绝（未预览不得发布），三向导（模板版本/批量应用/覆盖发布）
 *   组件级逐步推进断言；
 * - 失败项呈现：批量 dry-run 结果 FAIL 条目的 error 文本与四态徽标、
 *   汇总计数（成功/无变化/失败/不存在）；
 * - 权限降级：IC（config:view 只读）对 新建/发布/批量/覆盖发布 按钮
 *   灰而不藏（disabled + 占位），ADMIN 全量可用；
 * - 纯函数：profile 键级差异 / 千回路分批（≤200）/ 汇总 / 截断提示 /
 *   profile JSON 校验。
 * - P6-04 E2E 缺陷修复（DEF-1/DEF-4，2026-10-16 报告 §5）：批量应用
 *   选择面改 /loops 分页+筛选（覆盖未关联回路——0 关联不再死锁），
 *   跨页勾选保留，指引文案与实际能力对齐。
 */
import { flushPromises, mount } from '@vue/test-utils';

import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ---------------------------------------------------------------------------
// API mock（hoisted）
// ---------------------------------------------------------------------------

const api = vi.hoisted(() => ({
  batchApplyLoopsApi: vi.fn(),
  createEngineeringTemplateApi: vi.fn(),
  getConfigPublicationsApi: vi.fn(),
  getConfigRevisionApi: vi.fn(),
  getEngineeringTemplateApi: vi.fn(),
  getEngineeringTemplateLoopsApi: vi.fn(),
  getEngineeringTemplateVersionsApi: vi.fn(),
  getEngineeringTemplatesApi: vi.fn(),
  previewConfigOverrideApi: vi.fn(),
  publishConfigOverrideApi: vi.fn(),
  publishEngineeringTemplateVersionApi: vi.fn(),
  rollbackConfigPublicationApi: vi.fn(),
  rollbackEngineeringTemplateVersionApi: vi.fn(),
}));

vi.mock('#/api/engineering-template', async () => {
  const actual = await vi.importActual<any>('#/api/engineering-template');
  const wrapped: Record<string, any> = { ...actual };
  for (const key of Object.keys(api)) {
    wrapped[key] = (...a: any[]) => (api as any)[key](...a);
  }
  return wrapped;
});

vi.mock('#/api/metric', async () => {
  const actual = await vi.importActual<any>('#/api/metric');
  return {
    ...actual,
    getAlgorithmParamsApi: (...a: any[]) => getAlgorithmParamsApiMock(...a),
  };
});

const getAlgorithmParamsApiMock = vi.hoisted(() => vi.fn());

// P6-04 DEF-1：批量应用选择面改用 /loops 列表（分页+筛选）与工厂节点树
const loopApi = vi.hoisted(() => ({
  getLoopListApi: vi.fn(),
}));
vi.mock('#/api/loop', async () => {
  const actual = await vi.importActual<any>('#/api/loop');
  return {
    ...actual,
    getLoopListApi: (...a: any[]) => loopApi.getLoopListApi(...a),
  };
});

const plantNodeApi = vi.hoisted(() => ({
  getPlantNodeTreeApi: vi.fn(),
}));
vi.mock('#/api/plant-node', async () => {
  const actual = await vi.importActual<any>('#/api/plant-node');
  return {
    ...actual,
    getPlantNodeTreeApi: (...a: any[]) => plantNodeApi.getPlantNodeTreeApi(...a),
  };
});

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div class="page-stub"><slot /></div>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmPageToolbar: {
    template:
      '<div class="toolbar-stub"><slot /><slot name="actions" /></div>',
  },
  ClpmStandardActions: { template: '<div class="actions-stub" />' },
}));

vi.mock('#/composables/use-page-toolbar', () => ({
  showPageHelp: vi.fn(),
  usePageToolbar: () => ({ toolbarItems: [] }),
}));

// ---------------------------------------------------------------------------
// ant-design-vue 轻量 stub（保留断言所需事件与 disabled 语义）
// ---------------------------------------------------------------------------

vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual<any>('ant-design-vue');
  return {
    ...actual,
    Alert: {
      props: ['message', 'description', 'type'],
      template:
        '<div class="alert-stub" :data-type="type">{{ message }} {{ description }}</div>',
    },
    Button: {
      props: ['type', 'size', 'loading', 'disabled', 'title', 'danger'],
      template:
        '<button class="btn-stub" :disabled="disabled" :title="title" @click="$emit(\'click\')"><slot /></button>',
    },
    Drawer: {
      props: ['open', 'title', 'width'],
      template: '<div v-if="open" class="drawer-stub"><slot /></div>',
    },
    Input: {
      props: ['value', 'placeholder', 'maxlength'],
      emits: ['update:value', 'change'],
      template:
        '<input class="input-stub" :value="value" @input="$emit(\'update:value\', $event.target.value); $emit(\'change\', $event.target.value)" />',
    },
    InputNumber: {
      props: ['value', 'min', 'max', 'precision'],
      template:
        '<input class="input-number-stub" type="number" :value="value" @input="$emit(\'update:value\', Number($event.target.value))" />',
    },
    Modal: {
      props: ['open', 'title'],
      template:
        '<div v-if="open" class="modal-stub"><slot /><slot name="footer" /></div>',
    },
    Radio: {
      props: ['value'],
      template: '<label class="radio-stub"><slot /></label>',
    },
    RadioGroup: {
      props: ['value'],
      template: '<div class="radio-group-stub"><slot /></div>',
    },
    Select: {
      props: ['value', 'options', 'placeholder'],
      emits: ['update:value', 'change'],
      template:
        '<select class="select-stub" :value="value" @change="$emit(\'update:value\', $event.target.value); $emit(\'change\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>',
    },
    Spin: { template: '<div class="spin-stub"><slot /></div>' },
    Switch: {
      props: ['checked', 'size'],
      template: '<span class="switch-stub" />',
    },
    Table: {
      props: ['columns', 'dataSource', 'loading', 'rowSelection', 'rowKey', 'pagination'],
      methods: {
        selectAllRows(this: any) {
          const key = typeof this.rowKey === 'string' ? this.rowKey : 'loopId';
          this.rowSelection?.onChange?.(
            (this.dataSource ?? []).map((r: any) => r[key]),
          );
        },
        gotoPage2(this: any) {
          this.pagination?.onChange?.(2);
        },
      },
      template:
        '<div class="table-stub">' +
        '<button v-if="rowSelection" class="select-all-stub" type="button" @click="selectAllRows">全选</button>' +
        '<button v-if="pagination && pagination.onChange" class="pager-next-stub" type="button" @click="gotoPage2">下一页</button>' +
        '<div v-for="(record, i) in dataSource" :key="i" class="row-stub">' +
        '<span v-for="col in columns" :key="col.key" class="cell-stub">' +
        '<slot name="bodyCell" :column="col" :record="record" :index="i" />' +
        '</span>' +
        '</div>' +
        '</div>',
    },
    TabPane: {
      props: ['tab'],
      template: '<div class="tabpane-stub"><slot /></div>',
    },
    Tabs: {
      props: ['activeKey'],
      template: '<div class="tabs-stub"><slot /></div>',
    },
    Tag: {
      props: ['color'],
      template: '<span class="tag-stub"><slot /></span>',
    },
    Textarea: {
      props: ['value', 'rows', 'maxlength', 'placeholder'],
      template:
        '<textarea class="textarea-stub" :value="value" @input="$emit(\'update:value\', $event.target.value)" />',
    },
    Tooltip: {
      props: ['title'],
      template: '<span class="tooltip-stub"><slot /></span>',
    },
    message: {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    },
  };
});

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import BatchApplyModal from '#/views/config/engineering-templates/batch-apply-modal.vue';
// oxlint-disable-next-line import/first
import IndexPage from '#/views/config/engineering-templates/index.vue';
// oxlint-disable-next-line import/first
import OverridePublishModal from '#/views/config/engineering-templates/override-publish-modal.vue';
// oxlint-disable-next-line import/first
import PublishVersionModal from '#/views/config/engineering-templates/publish-version-modal.vue';
// oxlint-disable-next-line import/first
import {
  BATCH_LOOP_IDS_LIMIT,
  canConfirmPublish,
  canPreview,
  chunkLoopIds,
  countProfileChanges,
  diffProfileEntries,
  parseProfileJson,
  reducePublishFlow,
  summarizeBatchResults,
  truncationNote,
} from '#/views/config/engineering-templates/template-utils';

// ---------------------------------------------------------------------------
// 测试数据
// ---------------------------------------------------------------------------

const TEMPLATE: EngineeringTemplateApi.TemplateItem = {
  id: 'tpl-1',
  templateKey: 'FAST_RESP',
  name: '快速响应流量模板',
  category: 'RESPONSE',
  currentVersion: 2,
  description: '快速响应类回路',
  createdBy: 'admin',
  createdAt: null,
  updatedAt: null,
  versionCount: 2,
  loopCount: 2,
};

const TEMPLATE_B: EngineeringTemplateApi.TemplateItem = {
  ...TEMPLATE,
  id: 'tpl-2',
  templateKey: 'SLOW_LEVEL',
  name: '慢速液位模板',
  currentVersion: null,
  versionCount: 0,
  loopCount: 0,
};

const VERSIONS: EngineeringTemplateApi.TemplateVersionItem[] = [
  {
    templateId: 'tpl-1',
    version: 2,
    profile: { response: { lag: 8 }, control: { gain: 1.2 } },
    reason: 'v2 标定更新',
    publishedBy: 'admin',
    publishedAt: '2026-10-01T00:00:00Z',
    revisionRef: 'engineering_template:tpl-1@v2',
  },
  {
    templateId: 'tpl-1',
    version: 1,
    profile: { response: { lag: 10 } },
    reason: 'v1 初始发布',
    publishedBy: 'admin',
    publishedAt: '2026-09-01T00:00:00Z',
    revisionRef: 'engineering_template:tpl-1@v1',
  },
];

const LOOPS: EngineeringTemplateApi.TemplateLoopItem[] = [
  { loopId: 'loop-1', tagName: 'FIC-101', isActive: true },
  { loopId: 'loop-2', tagName: 'FIC-102', isActive: true },
];

/** DEF-1：选择面数据源改为 /loops 列表项（全量回路，含未关联） */
const TAG_MAPPING_ALL_FALSE: LoopApi.TagMappingStatus = {
  pv: false,
  sp: false,
  op: false,
  mode: false,
  pid_p: false,
  pid_i: false,
  pid_d: false,
};

function makeLoopListItem(
  loopId: string,
  tagName: string,
): LoopApi.LoopListItem {
  return {
    loopId,
    tagName,
    description: '',
    unitId: 'unit-1',
    unitName: '一单元',
    controlMode: 'Auto',
    isActive: true,
    status: 'READY',
    loopType: 'FLOW',
    controlType: 'STABLE',
    importanceLevel: 2,
    includeInEvaluation: true,
    tagMappingStatus: { ...TAG_MAPPING_ALL_FALSE },
  };
}

const LOOP_LIST_PAGE_1: LoopApi.LoopListItem[] = [
  makeLoopListItem('loop-1', 'FIC-101'),
  makeLoopListItem('loop-2', 'FIC-102'),
];

const DETAIL: EngineeringTemplateApi.TemplateDetail = {
  ...TEMPLATE,
  currentVersionDetail: VERSIONS[0]!,
  loops: LOOPS,
};

function setAccount(roles: string[], codes: string[]) {
  const userStore = useUserStore();
  const accessStore = useAccessStore();
  userStore.setUserInfo({ realName: 'T', roles, userId: 'u-1', username: 't' } as any);
  accessStore.setAccessCodes(codes);
}

function mockBaseApis() {
  api.getEngineeringTemplatesApi.mockResolvedValue({
    items: [TEMPLATE, TEMPLATE_B],
    total: 2,
  });
  api.getEngineeringTemplateApi.mockResolvedValue(DETAIL);
  api.getEngineeringTemplateVersionsApi.mockResolvedValue({
    items: VERSIONS,
    total: 2,
  });
  api.getEngineeringTemplateLoopsApi.mockResolvedValue({
    items: LOOPS,
    total: 2,
  });
  // DEF-1：批量应用选择面 = /loops 分页（全量回路，含未关联）
  loopApi.getLoopListApi.mockResolvedValue({
    items: LOOP_LIST_PAGE_1,
    total: 2,
    page: 1,
    pageSize: 50,
  });
  plantNodeApi.getPlantNodeTreeApi.mockResolvedValue([]);
  api.getConfigRevisionApi.mockResolvedValue({
    revision: 7,
    lastPublication: null,
  });
  api.getConfigPublicationsApi.mockResolvedValue({ items: [], total: 0 });
  getAlgorithmParamsApiMock.mockResolvedValue({
    metrics: [
      {
        metricCode: 'oscillation_rate',
        metricName: '振荡率',
        items: [],
        paramMeta: {
          similarity_threshold: { label: '相似度阈值', type: 'float' },
          min_points: { label: '最小点数', type: 'int' },
        },
      },
    ],
  });
}

// ---------------------------------------------------------------------------
// 纯函数：状态机 / 差异 / 分批 / 汇总 / 校验
// ---------------------------------------------------------------------------

describe('发布流程状态机（dry-run → 确认 → 发布 → 结果）', () => {
  it('全链路：edit→previewing→previewed→publishing→done→RESET 归位', () => {
    let step = reducePublishFlow('edit', 'START_PREVIEW');
    expect(step).toBe('previewing');
    step = reducePublishFlow(step, 'PREVIEW_OK');
    expect(step).toBe('previewed');
    step = reducePublishFlow(step, 'CONFIRM_PUBLISH');
    expect(step).toBe('publishing');
    step = reducePublishFlow(step, 'PUBLISH_OK');
    expect(step).toBe('done');
    step = reducePublishFlow(step, 'RESET');
    expect(step).toBe('edit');
  });

  it('未预览不得发布：edit 态 CONFIRM_PUBLISH 非法迁移保持原态', () => {
    expect(reducePublishFlow('edit', 'CONFIRM_PUBLISH')).toBe('edit');
    expect(reducePublishFlow('previewing', 'CONFIRM_PUBLISH')).toBe(
      'previewing',
    );
    expect(canConfirmPublish('edit')).toBe(false);
    expect(canPreview('publishing')).toBe(false);
    expect(canConfirmPublish('previewed')).toBe(true);
  });

  it('失败路径：预览失败回 edit；发布失败入 failed 可重试或重新预览', () => {
    expect(reducePublishFlow('previewing', 'PREVIEW_ERROR')).toBe('edit');
    const failed = reducePublishFlow('publishing', 'PUBLISH_ERROR');
    expect(failed).toBe('failed');
    expect(reducePublishFlow(failed, 'CONFIRM_PUBLISH')).toBe('publishing');
    expect(reducePublishFlow(failed, 'START_PREVIEW')).toBe('previewing');
    expect(canConfirmPublish(failed)).toBe(true);
  });
});

describe('纯函数：profile 差异 / 分批 / 汇总 / 截断提示', () => {
  it('diffProfileEntries：added/removed/changed/unchanged 四态键并集', () => {
    const entries = diffProfileEntries(
      { a: 1, b: 2, c: 3 },
      { b: 2, c: 4, d: 5 },
    );
    const byKey = Object.fromEntries(entries.map((e) => [e.key, e.kind]));
    expect(byKey).toEqual({
      a: 'removed',
      b: 'unchanged',
      c: 'changed',
      d: 'added',
    });
    expect(countProfileChanges(entries)).toBe(3);
  });

  it('diffProfileEntries：基线 null（首版）全部 added', () => {
    const entries = diffProfileEntries(null, { a: 1 });
    expect(entries).toEqual([
      { key: 'a', kind: 'added', before: undefined, after: 1 },
    ]);
  });

  it('chunkLoopIds：千回路路径分批（每批 ≤200；size 上限收敛）', () => {
    const ids = Array.from({ length: 450 }, (_, i) => `loop-${i}`);
    const chunks = chunkLoopIds(ids);
    expect(chunks.map((c) => c.length)).toEqual([200, 200, 50]);
    expect(chunkLoopIds(ids, 1000).map((c) => c.length)).toEqual([
      200, 200, 50,
    ]);
    expect(BATCH_LOOP_IDS_LIMIT).toBe(200);
    expect(chunkLoopIds([])).toEqual([]);
  });

  it('summarizeBatchResults：四态计数 + affected=OK 口径', () => {
    const summary = summarizeBatchResults([
      { status: 'OK' },
      { status: 'OK' },
      { status: 'UNCHANGED' },
      { status: 'FAIL' },
      { status: 'NOT_FOUND' },
    ]);
    expect(summary).toEqual({
      total: 5,
      ok: 2,
      unchanged: 1,
      failed: 1,
      notFound: 1,
      affected: 2,
    });
  });

  it('truncationNote：total>loaded 显式提示（禁止静默截断）', () => {
    expect(truncationNote(50, 120)).toBe('仅显示前 50 条（共 120 条）');
    expect(truncationNote(120, 120)).toBeNull();
  });

  it('parseProfileJson：对象放行；数组/标量/空/语法错拒绝', () => {
    expect(parseProfileJson('{"a":1}').ok).toBe(true);
    expect(parseProfileJson('[1]').ok).toBe(false);
    expect(parseProfileJson('5').ok).toBe(false);
    expect(parseProfileJson('').ok).toBe(false);
    expect(parseProfileJson('{bad').ok).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 页面渲染 + 权限降级
// ---------------------------------------------------------------------------

describe('工程模板维护页（index.vue）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of Object.values(api)) fn.mockReset();
    getAlgorithmParamsApiMock.mockReset();
    loopApi.getLoopListApi.mockReset();
    plantNodeApi.getPlantNodeTreeApi.mockReset();
    mockBaseApis();
  });

  it('渲染模板列表；ADMIN 新建/维护动作可用，抽屉三向导按钮 enabled', async () => {
    setAccount(['ADMIN'], ['*']);
    const wrapper = mount(IndexPage);
    await flushPromises();

    expect(api.getEngineeringTemplatesApi).toHaveBeenCalledTimes(1);
    const text = wrapper.text();
    expect(text).toContain('FAST_RESP');
    expect(text).toContain('SLOW_LEVEL');

    const createBtn = wrapper.find('[data-testid="btn-create-template"]');
    expect(createBtn.exists()).toBe(true);
    expect(createBtn.attributes('disabled')).toBeUndefined();

    // 打开维护抽屉
    await wrapper.find('[data-testid="btn-open-detail"]').trigger('click');
    await flushPromises();
    for (const testid of [
      'btn-publish-version',
      'btn-batch-apply',
      'btn-override-publish',
    ]) {
      const btn = wrapper.find(`[data-testid="${testid}"]`);
      expect(btn.exists()).toBe(true);
      expect(btn.attributes('disabled')).toBeUndefined();
    }
  });

  it('权限降级：IC（config:view 只读）动作按钮灰而不藏（disabled 占位）', async () => {
    setAccount(['IC_ENGINEER'], ['config:view']);
    const wrapper = mount(IndexPage);
    await flushPromises();

    // 列表/读取可用
    expect(wrapper.text()).toContain('FAST_RESP');
    // 新建灰显但保留占位
    const createBtn = wrapper.find('[data-testid="btn-create-template"]');
    expect(createBtn.exists()).toBe(true);
    expect(createBtn.attributes('disabled')).toBeDefined();

    await wrapper.find('[data-testid="btn-open-detail"]').trigger('click');
    await flushPromises();
    for (const testid of [
      'btn-publish-version',
      'btn-batch-apply',
      'btn-override-publish',
    ]) {
      const btn = wrapper.find(`[data-testid="${testid}"]`);
      expect(btn.exists()).toBe(true);
      expect(btn.attributes('disabled')).toBeDefined();
    }
  });
});

// ---------------------------------------------------------------------------
// Flow A：模板版本发布向导（预览 → 确认 → 发布 → 结果 / 409 失败）
// ---------------------------------------------------------------------------

describe('模板版本发布向导（publish-version-modal）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of Object.values(api)) fn.mockReset();
    loopApi.getLoopListApi.mockReset();
    plantNodeApi.getPlantNodeTreeApi.mockReset();
    mockBaseApis();
  });

  function mountModal() {
    return mount(PublishVersionModal, {
      props: { open: true, template: TEMPLATE },
    });
  }

  it('dry-run→确认→发布→结果：差异/影响回路渲染，发布携带 expectedRevision，结果展示 revisionRef', async () => {
    api.publishEngineeringTemplateVersionApi.mockResolvedValue({
      revision: 8,
      rollbackRevision: 7,
      templateId: 'tpl-1',
      templateKey: 'FAST_RESP',
      version: 3,
      revisionRef: 'engineering_template:tpl-1@v3',
      affectedLoops: 2,
    });
    const wrapper = mountModal();
    await flushPromises();

    // edit：上下文加载（预填当前 profile），填原因
    expect(wrapper.find('[data-testid="flow-edit"]').isVisible()).toBe(true);
    await wrapper.find('[data-testid="reason-input"]').setValue('v3 标定更新');
    await wrapper.find('[data-testid="btn-preview"]').trigger('click');
    await flushPromises();

    // previewed：影响回路 + 差异 + revision 可见
    expect(wrapper.find('[data-testid="flow-preview"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="preview-affected"]').text()).toContain(
      '2',
    );
    expect(wrapper.text()).toContain('当前配置 revision');

    // 确认发布 → done
    await wrapper.find('[data-testid="btn-confirm-publish"]').trigger('click');
    await flushPromises();
    expect(api.publishEngineeringTemplateVersionApi).toHaveBeenCalledWith(
      'tpl-1',
      {
        profile: { response: { lag: 8 }, control: { gain: 1.2 } },
        expectedRevision: 7,
        reason: 'v3 标定更新',
      },
    );
    expect(wrapper.find('[data-testid="flow-done"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="done-revision-ref"]').text()).toContain(
      'engineering_template:tpl-1@v3',
    );
    expect(wrapper.emitted('published')).toHaveLength(1);
  });

  it('发布 409 冲突 → failed 显式提示 + 可重新预览', async () => {
    api.publishEngineeringTemplateVersionApi.mockRejectedValue({
      response: { status: 409, data: { message: '配置版本冲突' } },
    });
    const wrapper = mountModal();
    await flushPromises();
    await wrapper.find('[data-testid="reason-input"]').setValue('v3');
    await wrapper.find('[data-testid="btn-preview"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="btn-confirm-publish"]').trigger('click');
    await flushPromises();

    const failed = wrapper.find('[data-testid="flow-failed"]');
    expect(failed.exists()).toBe(true);
    expect(failed.text()).toContain('409');
    expect(wrapper.find('[data-testid="btn-repreview"]').exists()).toBe(true);
    expect(wrapper.emitted('published')).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Flow B：批量应用向导（dry-run 失败项呈现 → 确认执行 → 逐条结果）
// ---------------------------------------------------------------------------

describe('批量应用向导（batch-apply-modal）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of Object.values(api)) fn.mockReset();
    loopApi.getLoopListApi.mockReset();
    plantNodeApi.getPlantNodeTreeApi.mockReset();
    mockBaseApis();
  });

  function mountModal() {
    return mount(BatchApplyModal, {
      props: { open: true, template: TEMPLATE },
    });
  }

  const DRY_RUN_RESULTS: EngineeringTemplateApi.LoopBatchResultItem[] = [
    {
      loopId: 'loop-1',
      tagName: 'FIC-101',
      status: 'OK',
      changes: [
        { field: 'engineering_template_id', before: null, after: 'tpl-1' },
      ],
      error: null,
    },
    {
      loopId: 'loop-2',
      tagName: 'FIC-102',
      status: 'FAIL',
      changes: [],
      error: 'OP 输出下限 90 与该回路既有上限 80 颠倒',
    },
  ];

  it('dry-run 零写入预览：调用契约断言 + 失败项逐条呈现（error 文本/四态徽标/汇总）', async () => {
    api.batchApplyLoopsApi.mockResolvedValue({
      affected: 1,
      action: 'update',
      loopIds: ['loop-1'],
      dryRun: true,
      results: DRY_RUN_RESULTS,
    });
    const wrapper = mountModal();
    await flushPromises();

    // 全选两条回路 → dry-run
    await wrapper.find('.select-all-stub').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="btn-dry-run"]').trigger('click');
    await flushPromises();

    expect(api.batchApplyLoopsApi).toHaveBeenCalledWith({
      loopIds: ['loop-1', 'loop-2'],
      updates: { engineeringTemplateId: 'tpl-1' },
      dryRun: true,
    });
    // 失败项呈现：FAIL 徽标 + error 文本；汇总计数对得上
    expect(wrapper.find('[data-testid="status-FAIL"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('OP 输出下限 90 与该回路既有上限 80 颠倒');
    expect(wrapper.find('[data-testid="batch-summary"]').text()).toContain(
      '失败 1',
    );
    expect(wrapper.text()).toContain('dry-run 预览（零写入）');
  });

  it('确认执行：dryRun:false 调用 → done 汇总渲染 + applied 事件', async () => {
    api.batchApplyLoopsApi
      .mockResolvedValueOnce({
        affected: 1,
        action: 'update',
        loopIds: ['loop-1'],
        dryRun: true,
        results: DRY_RUN_RESULTS,
      })
      .mockResolvedValueOnce({
        affected: 1,
        action: 'update',
        loopIds: ['loop-1'],
        dryRun: false,
        results: [
          { ...DRY_RUN_RESULTS[0]! },
          { ...DRY_RUN_RESULTS[1]! },
        ],
      });
    const wrapper = mountModal();
    await flushPromises();
    await wrapper.find('.select-all-stub').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="btn-dry-run"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="btn-confirm-execute"]').trigger('click');
    await flushPromises();

    expect(api.batchApplyLoopsApi).toHaveBeenLastCalledWith({
      loopIds: ['loop-1', 'loop-2'],
      updates: { engineeringTemplateId: 'tpl-1' },
      dryRun: false,
    });
    expect(wrapper.find('[data-testid="batch-summary"]').text()).toContain(
      '成功 1',
    );
    expect(wrapper.emitted('applied')).toHaveLength(1);
  });

  it('未预览不得执行：edit 态无确认执行按钮（状态机硬约束）', async () => {
    const wrapper = mountModal();
    await flushPromises();
    expect(wrapper.find('[data-testid="btn-confirm-execute"]').exists()).toBe(
      false,
    );
  });

  // -------------------------------------------------------------------------
  // P6-04 E2E 缺陷修复：DEF-1（首次关联死锁）+ DEF-4（指引文案）
  // -------------------------------------------------------------------------

  it('DEF-1 首次关联不死锁：0 关联模板打开弹窗，选择面来自 /loops 全量列表（未关联回路可选可预览）', async () => {
    // 0 关联模板（E2E 起始态）——旧实现选择面=已关联回路前 200 → 空表死锁
    const zeroAssociation = { ...TEMPLATE, loopCount: 0 };
    api.batchApplyLoopsApi.mockResolvedValue({
      affected: 2,
      action: 'update',
      loopIds: ['loop-1', 'loop-2'],
      dryRun: true,
      results: DRY_RUN_RESULTS,
    });
    const wrapper = mount(BatchApplyModal, {
      props: { open: true, template: zeroAssociation },
    });
    await flushPromises();

    // 选择面数据源 = /loops（page/pageSize/keyword/plantNodeId 契约）
    expect(loopApi.getLoopListApi).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      keyword: undefined,
      plantNodeId: undefined,
    });
    // 未关联回路在选择面可见（不再空表）
    expect(wrapper.text()).toContain('FIC-101');
    expect(wrapper.text()).toContain('FIC-102');

    // 未勾选时 dry-run 禁用；全选后可预览（路径通）
    const dryRunBtn = wrapper.find('[data-testid="btn-dry-run"]');
    expect(dryRunBtn.attributes('disabled')).toBeDefined();
    await wrapper.find('.select-all-stub').trigger('click');
    await flushPromises();
    expect(dryRunBtn.attributes('disabled')).toBeUndefined();
    await dryRunBtn.trigger('click');
    await flushPromises();

    expect(api.batchApplyLoopsApi).toHaveBeenCalledWith({
      loopIds: ['loop-1', 'loop-2'],
      updates: { engineeringTemplateId: 'tpl-1' },
      dryRun: true,
    });
  });

  it('DEF-1 >200 可达：筛选回第 1 页重查 + 翻页第 2 页重查 + 跨页勾选保留并进入 dry-run', async () => {
    const page2Item = makeLoopListItem('loop-3', 'FIC-201');
    // 装置筛选需要非空节点树（下拉可选项）
    plantNodeApi.getPlantNodeTreeApi.mockResolvedValueOnce([
      { id: 'node-9', name: '装置A', type: 'PLANT', parentId: null },
    ]);
    loopApi.getLoopListApi
      .mockResolvedValueOnce({
        items: LOOP_LIST_PAGE_1,
        total: 3,
        page: 1,
        pageSize: 50,
      })
      .mockResolvedValueOnce({
        // 装置筛选触发（回第 1 页）
        items: LOOP_LIST_PAGE_1,
        total: 2,
        page: 1,
        pageSize: 50,
      })
      .mockResolvedValueOnce({
        // 翻页第 2 页
        items: [page2Item],
        total: 3,
        page: 2,
        pageSize: 50,
      });
    api.batchApplyLoopsApi.mockResolvedValue({
      affected: 3,
      action: 'update',
      loopIds: ['loop-1', 'loop-2', 'loop-3'],
      dryRun: true,
      results: [],
    });
    const wrapper = mountModal();
    await flushPromises();

    // 第 1 页全选
    await wrapper.find('.select-all-stub').trigger('click');
    await flushPromises();

    // 装置筛选变更 → page=1 重查携带 plantNodeId
    const nodeSelect = wrapper.find('[data-testid="loop-filter-node"]');
    await nodeSelect.setValue('node-9');
    await flushPromises();
    expect(loopApi.getLoopListApi).toHaveBeenLastCalledWith({
      page: 1,
      pageSize: 50,
      keyword: undefined,
      plantNodeId: 'node-9',
    });

    // 翻页第 2 页（重查携带 page=2）+ 跨页勾选保留
    await wrapper.find('.pager-next-stub').trigger('click');
    await flushPromises();
    expect(loopApi.getLoopListApi).toHaveBeenLastCalledWith({
      page: 2,
      pageSize: 50,
      keyword: undefined,
      plantNodeId: 'node-9',
    });
    expect(wrapper.text()).toContain('FIC-201');
    await wrapper.find('.select-all-stub').trigger('click');
    await flushPromises();
    await wrapper.find('[data-testid="btn-dry-run"]').trigger('click');
    await flushPromises();

    expect(api.batchApplyLoopsApi).toHaveBeenCalledWith({
      loopIds: ['loop-1', 'loop-2', 'loop-3'],
      updates: { engineeringTemplateId: 'tpl-1' },
      dryRun: true,
    });
  });

  it('DEF-4 指引文案与能力对齐：选择面含未关联回路 + 自动分批说明；断头指引已移除', async () => {
    const wrapper = mountModal();
    await flushPromises();
    const note = wrapper.find('[data-testid="loops-note"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain('含未关联本模板');
    expect(note.text()).toContain('单批 ≤200');
    // 旧断头指引（回路配置页批量表单不支持模板字段）不得再出现
    expect(note.text()).not.toContain('回路配置页');
  });
});

// ---------------------------------------------------------------------------
// Flow C：覆盖发布向导（真实 dry-run 校验门禁 + wouldConflict）
// ---------------------------------------------------------------------------

describe('覆盖发布向导（override-publish-modal）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of Object.values(api)) fn.mockReset();
    getAlgorithmParamsApiMock.mockReset();
    loopApi.getLoopListApi.mockReset();
    plantNodeApi.getPlantNodeTreeApi.mockReset();
    mockBaseApis();
  });

  function mountModal() {
    return mount(OverridePublishModal, {
      props: { open: true, template: TEMPLATE },
    });
  }

  /** 填好首个参数行（select 序：0=指标 1=控制类型 2=参数键）与原因 */
  async function fillEditForm(wrapper: any, reason = '收紧阈值') {
    await wrapper.find('[data-testid="override-reason"]').setValue(reason);
    const selects = wrapper.findAll('.select-stub');
    await selects[2]?.setValue('similarity_threshold');
    await wrapper.find('.input-number-stub').setValue('0.8');
  }

  function previewResult(
    overrides: Partial<EngineeringTemplateApi.OverridePreviewResult> = {},
  ): EngineeringTemplateApi.OverridePreviewResult {
    return {
      layer: 'TEMPLATE',
      scopeId: 'FAST_RESP',
      metricCode: 'oscillation_rate',
      currentRevision: 7,
      wouldConflict: false,
      validation: { ok: true, errors: [] },
      before: null,
      after: { similarity_threshold: 0.8 },
      affectedLoops: 2,
      note: null,
      affectedLoopSample: LOOPS,
      mergePreview: [
        {
          controlType: 'STABLE',
          base: { similarity_threshold: 0.5 },
          result: { similarity_threshold: 0.8 },
        },
      ],
      ...overrides,
    };
  }

  it('校验未通过（跨层颠倒）→ 错误逐条渲染 + 确认发布禁用（发布闸门）', async () => {
    api.previewConfigOverrideApi.mockResolvedValue(
      previewResult({
        validation: {
          ok: false,
          errors: ['组合约束：trip_normal 不得高于下层 trip_inactive'],
        },
      }),
    );
    const wrapper = mountModal();
    await flushPromises();

    await fillEditForm(wrapper);
    await wrapper.find('[data-testid="btn-override-preview"]').trigger('click');
    await flushPromises();

    expect(api.previewConfigOverrideApi).toHaveBeenCalled();
    const errors = wrapper.find('[data-testid="validation-errors"]');
    expect(errors.exists()).toBe(true);
    expect(errors.text()).toContain('组合约束');
    const publishBtn = wrapper.find('[data-testid="btn-override-publish"]');
    expect(publishBtn.exists()).toBe(true);
    expect(publishBtn.attributes('disabled')).toBeDefined();
  });

  it('wouldConflict=true → revision 漂移警告显式渲染且发布禁用', async () => {
    api.previewConfigOverrideApi.mockResolvedValue(
      previewResult({ wouldConflict: true }),
    );
    const wrapper = mountModal();
    await flushPromises();
    await fillEditForm(wrapper, 'r');
    await wrapper.find('[data-testid="btn-override-preview"]').trigger('click');
    await flushPromises();

    expect(wrapper.find('[data-testid="would-conflict"]').exists()).toBe(true);
    const publishBtn = wrapper.find('[data-testid="btn-override-publish"]');
    expect(publishBtn.attributes('disabled')).toBeDefined();
  });

  it('dry-run→确认→真实发布：preview/publish 调用契约 + 结果回退引用渲染', async () => {
    api.previewConfigOverrideApi.mockResolvedValue(previewResult());
    api.publishConfigOverrideApi.mockResolvedValue({
      revision: 9,
      rollbackRevision: 7,
      affectedLoops: 2,
      note: null,
      before: null,
      after: { similarity_threshold: 0.8 },
      layer: 'TEMPLATE',
      scopeId: 'FAST_RESP',
      metricCode: 'oscillation_rate',
    });
    const wrapper = mountModal();
    await flushPromises();
    await fillEditForm(wrapper);
    await wrapper.find('[data-testid="btn-override-preview"]').trigger('click');
    await flushPromises();

    expect(api.previewConfigOverrideApi).toHaveBeenCalledWith({
      layer: 'TEMPLATE',
      scopeId: 'FAST_RESP',
      metricCode: 'oscillation_rate',
      params: { similarity_threshold: 0.8 },
      expectedRevision: 7,
    });

    await wrapper.find('[data-testid="btn-override-publish"]').trigger('click');
    await flushPromises();
    expect(api.publishConfigOverrideApi).toHaveBeenCalledWith({
      layer: 'TEMPLATE',
      scopeId: 'FAST_RESP',
      metricCode: 'oscillation_rate',
      params: { similarity_threshold: 0.8 },
      expectedRevision: 7,
      reason: '收紧阈值',
    });
    expect(wrapper.find('[data-testid="override-done"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('rollbackRevision');
    expect(wrapper.emitted('published')).toHaveLength(1);
  });
});
