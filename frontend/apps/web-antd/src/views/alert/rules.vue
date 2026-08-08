<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Textarea,
} from 'ant-design-vue';

import {
  createAlertRuleApi,
  deleteAlertRuleApi,
  getAlertRulesApi,
  getGlobalSwitchApi,
  setGlobalSwitchApi,
  toggleAlertRuleApi,
  updateAlertRuleApi,
} from '#/api/alert';
import {
  ClpmAlertDslEditor,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { ALERT_RULE_TYPE_LABEL } from '#/constants/clpm-ui';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'AlertRules' });

// 规则类型中文标签（对齐 clpm-ui.ts 统一映射）
const ruleTypeLabel = ALERT_RULE_TYPE_LABEL;

// 列表
const loading = ref(false);
const ruleList = ref<AlertApi.RuleItem[]>([]);
const total = ref(0);
const query = reactive({
  ruleType: undefined as AlertApi.RuleType | undefined,
  isEnabled: undefined as string | undefined,
  page: 1,
  pageSize: 20,
});

// 全局开关
const globalEnabled = ref(true);
const switchLoading = ref(false);

// 筛选区折叠态
const filterVisible = ref(true);

// 最近刷新时间
const lastRefresh = ref('');

// 编辑弹窗
const editVisible = ref(false);
const editMode = ref<'create' | 'edit'>('create');
const editForm = reactive({
  ruleId: '',
  ruleCode: '',
  ruleName: '',
  ruleType: 'THRESHOLD' as AlertApi.RuleType,
  description: '',
  priority: 100,
  isEnabled: true,
  /** #8: DSL 对象（由可视化编辑器维护，替代原 dslText JSON 字符串） */
  dsl: {} as Record<string, any>,
});
const editLoading = ref(false);

const ruleTypeColor: Record<AlertApi.RuleType, string> = {
  // 整改 A-02 类别中性化：规则类型为中性分类，antd default 灰阶
  THRESHOLD: 'default',
  DRIFT: 'default',
  COMPOSITE: 'default',
  CONFIDENCE: 'default',
};

// DSL 模板
const dslTemplates: Record<AlertApi.RuleType, Record<string, any>> = {
  THRESHOLD: {
    ruleType: 'THRESHOLD',
    scope: { loopSelector: { type: 'ALL' } },
    condition: { metric: 'PV', operator: '>', value: 90 },
    durationSeconds: 0,
    cooldownSeconds: 1800,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
    dedupKey: '${loop_id}+${rule_id}',
  },
  CONFIDENCE: {
    ruleType: 'CONFIDENCE',
    scope: { loopSelector: { type: 'ALL' } },
    condition: { maxLevel: 'D' },
    durationSeconds: 300,
    cooldownSeconds: 3600,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
    dedupKey: '${loop_id}+${rule_id}',
  },
  DRIFT: {
    ruleType: 'DRIFT',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      metric: 'PV',
      statistic: 'MEAN',
      windowSeconds: 1800,
      baseline: { type: 'STATIC', value: 50 },
      deviationThreshold: 10,
      deviationType: 'ABSOLUTE',
    },
    durationSeconds: 600,
    cooldownSeconds: 3600,
    severity: 'WARN',
    actions: [{ type: 'CREATE_EVENT' }],
    priority: 100,
  },
  COMPOSITE: {
    ruleType: 'COMPOSITE',
    scope: { loopSelector: { type: 'ALL' } },
    condition: {
      logic: 'AND',
      operands: [
        { type: 'THRESHOLD', metric: 'PV', operator: '>', value: 90 },
        { type: 'CONFIDENCE', maxLevel: 'C' },
      ],
    },
    durationSeconds: 300,
    cooldownSeconds: 3600,
    severity: 'ERROR',
    actions: [{ type: 'CREATE_EVENT' }, { type: 'CREATE_TRACKER' }],
    priority: 50,
  },
};

const columns: TableColumnsType = [
  { title: '规则代码', dataIndex: 'ruleCode', key: 'ruleCode', width: 160 },
  { title: '规则名称', dataIndex: 'ruleName', key: 'ruleName', width: 180 },
  {
    title: '类型',
    dataIndex: 'ruleType',
    key: 'ruleType',
    width: 110,
    customRender: ({ value }) =>
      h(
        Tag,
        { color: ruleTypeColor[value as AlertApi.RuleType] },
        () => ruleTypeLabel[value as AlertApi.RuleType] ?? value,
      ),
  },
  { title: '优先级', dataIndex: 'priority', key: 'priority', width: 80 },
  {
    title: '版本',
    dataIndex: 'version',
    key: 'version',
    width: 70,
  },
  {
    title: '状态',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 80,
    customRender: ({ value }) =>
      h(Tag, { color: value ? 'green' : 'default' }, () =>
        value ? '启用' : '停用',
      ),
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 160,
    customRender: ({ value }) => (value ? formatTime(value) : '-'),
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// ===== 列设置（排除「操作」列） =====
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns
    .filter((c: any) => c.key !== 'action')
    .map((c: any, i: number) => ({
      key: String(c.key),
      label: String(c.title ?? ''),
      visible: true,
      order: i,
    }));
}
const columnConfigs = ref<ColumnConfig[]>(buildDefaultColumnConfigs());
const visibleColumns = computed<TableColumnsType>(() =>
  columns.filter((c: any) => {
    if (c.key === 'action') return true;
    const cfg = columnConfigs.value.find((cc) => cc.key === c.key);
    return cfg ? cfg.visible : true;
  }),
);
function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
}
function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
}

async function loadRules() {
  loading.value = true;
  try {
    const res = await getAlertRulesApi({
      ruleType: query.ruleType,
      isEnabled:
        query.isEnabled === undefined ? undefined : query.isEnabled === 'true',
      limit: query.pageSize,
      offset: (query.page - 1) * query.pageSize,
    });
    ruleList.value = res.items;
    total.value = res.total;
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch {
    message.error('加载规则失败');
  } finally {
    loading.value = false;
  }
}

async function loadSwitch() {
  try {
    const res = await getGlobalSwitchApi();
    globalEnabled.value = res.enabled;
  } catch {
    // 静默
  }
}

async function handleSwitchChange(checked: any) {
  const val = Boolean(checked);
  switchLoading.value = true;
  try {
    await setGlobalSwitchApi(val);
    globalEnabled.value = val;
    message.success(val ? '预警已开启' : '预警已暂停');
  } catch {
    globalEnabled.value = !val;
    message.error('更新开关失败');
  } finally {
    switchLoading.value = false;
  }
}

function openCreateModal() {
  editMode.value = 'create';
  editForm.ruleId = '';
  editForm.ruleCode = '';
  editForm.ruleName = '';
  editForm.ruleType = 'THRESHOLD';
  editForm.description = '';
  editForm.priority = 100;
  editForm.isEnabled = true;
  // #8: 深拷贝模板 DSL 对象，避免引用污染
  editForm.dsl = structuredClone(dslTemplates.THRESHOLD);
  editVisible.value = true;
}

function openEditModal(record: AlertApi.RuleItem) {
  editMode.value = 'edit';
  editForm.ruleId = record.ruleId;
  editForm.ruleCode = record.ruleCode;
  editForm.ruleName = record.ruleName;
  editForm.ruleType = record.ruleType;
  editForm.description = record.description || '';
  editForm.priority = record.priority;
  editForm.isEnabled = record.isEnabled;
  editForm.dsl = record.dsl ? structuredClone(record.dsl) : {};
  editVisible.value = true;
}

function handleRuleTypeChange(value: any) {
  const type = value as AlertApi.RuleType;
  // 仅在创建模式下切换模板
  if (editMode.value === 'create') {
    editForm.dsl = structuredClone(dslTemplates[type]);
  }
}

async function handleSave() {
  if (!editForm.ruleCode.trim() || !editForm.ruleName.trim()) {
    message.warning('规则代码和名称不可为空');
    return;
  }
  // #8: DSL 由可视化编辑器生成，无需手动 JSON.parse
  const dsl = editForm.dsl;
  if (!dsl || !dsl.ruleType) {
    message.warning('规则 DSL 未配置完整');
    return;
  }
  editLoading.value = true;
  try {
    if (editMode.value === 'create') {
      await createAlertRuleApi({
        ruleCode: editForm.ruleCode,
        ruleName: editForm.ruleName,
        ruleType: editForm.ruleType,
        dsl,
        description: editForm.description || undefined,
        priority: editForm.priority,
        isEnabled: editForm.isEnabled,
      });
      message.success('规则已创建');
    } else {
      await updateAlertRuleApi(editForm.ruleId, {
        ruleName: editForm.ruleName,
        dsl,
        description: editForm.description || undefined,
        priority: editForm.priority,
        isEnabled: editForm.isEnabled,
      });
      message.success('规则已更新');
    }
    editVisible.value = false;
    await loadRules();
  } catch (error: any) {
    const msg = error?.response?.data?.message || '保存失败';
    message.error(msg);
  } finally {
    editLoading.value = false;
  }
}

async function handleToggle(record: AlertApi.RuleItem) {
  try {
    await toggleAlertRuleApi(record.ruleId, !record.isEnabled);
    message.success(record.isEnabled ? '已停用' : '已启用');
    await loadRules();
  } catch {
    message.error('操作失败');
  }
}

async function handleDelete(record: AlertApi.RuleItem) {
  try {
    await deleteAlertRuleApi(record.ruleId);
    message.success('规则已删除');
    await loadRules();
  } catch {
    message.error('删除失败');
  }
}

function handleSearch() {
  query.page = 1;
  loadRules();
}

function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadRules();
}

/** 导出当前规则列表为 CSV */
function exportRulesCsv() {
  if (ruleList.value.length === 0) {
    message.warning('当前无可导出的规则');
    return;
  }
  const header = [
    '规则代码',
    '规则名称',
    '类型',
    '优先级',
    '版本',
    '状态',
    '更新时间',
  ];
  const rows = ruleList.value.map((r) => [
    r.ruleCode,
    r.ruleName,
    ruleTypeLabel[r.ruleType] ?? r.ruleType,
    String(r.priority ?? ''),
    String(r.version ?? ''),
    r.isEnabled ? '启用' : '停用',
    r.updatedAt ? formatTime(r.updatedAt) : '',
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `alert-rules-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${ruleList.value.length} 条规则`);
}

function handleHelp() {
  showPageHelp({
    title: '预警规则 帮助',
    content:
      '规则类型：阈值(THRESHOLD)、漂移(DRIFT)、组合(COMPOSITE)、可信度(CONFIDENCE)。DSL 以 JSON 描述触发条件、时效窗口、动作与抑制策略；字段键名保留英文以对齐后端校验。全局开关暂停后所有规则停止求值，但保留已产生的事件。',
  });
}

// ===== 统一工具栏（标准 5 工具：刷新/筛选/导出/列设置/帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadRules, loading: loading.value },
  filter: { onClick: () => toggleFilter(), active: filterVisible.value },
  export: {
    onClick: exportRulesCsv,
    permission: ['ADMIN', 'IC_ENGINEER'],
    disabledReason: '仅工程师/管理员可导出',
  },
  setting: {},
  help: { onClick: handleHelp },
}));

function toggleFilter() {
  filterVisible.value = !filterVisible.value;
}

onMounted(() => {
  loadRules();
  loadSwitch();
});
</script>

<template>
  <Page>
    <!-- 统一工具栏 -->
    <ClpmPageToolbar
      title="预警规则"
      subtitle="阈值 / 漂移 / 组合 / 可信度 四类规则配置"
      :loading="loading"
      :last-refresh="lastRefresh"
    >
      <template #actions>
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选 + 全局开关 + 新建 -->
    <div
      class="clpm-filter-bar"
      :class="{ 'clpm-filter-bar--collapsed': !filterVisible }"
    >
      <FormItem label="类型" class="!mb-0">
        <Select
          v-model:value="query.ruleType"
          allow-clear
          placeholder="全部"
          style="width: 160px"
          :options="
            (
              [
                'THRESHOLD',
                'DRIFT',
                'COMPOSITE',
                'CONFIDENCE',
              ] as AlertApi.RuleType[]
            ).map((v) => ({ value: v, label: `${ruleTypeLabel[v]}（${v}）` }))
          "
        />
      </FormItem>
      <FormItem label="状态" class="!mb-0">
        <Select
          v-model:value="query.isEnabled"
          allow-clear
          placeholder="全部"
          style="width: 110px"
          :options="[
            { value: 'true', label: '启用' },
            { value: 'false', label: '停用' },
          ]"
        />
      </FormItem>
      <Button type="primary" @click="handleSearch">查询</Button>
      <div class="!ml-auto flex items-center gap-3">
        <span class="text-sm text-gray-500">全局开关</span>
        <Switch
          :checked="globalEnabled"
          :loading="switchLoading"
          checked-children="开"
          un-checked-children="关"
          @change="handleSwitchChange"
        />
        <Button type="primary" @click="openCreateModal">新建规则</Button>
      </div>
    </div>

    <!-- 规则表格 -->
    <Table
      :columns="visibleColumns"
      :data-source="ruleList"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :scroll="{ x: 1100 }"
      row-key="ruleId"
      size="small"
      @change="handlePageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'action'">
          <Space :size="4">
            <Button
              type="link"
              size="small"
              @click="openEditModal(record as AlertApi.RuleItem)"
            >
              编辑
            </Button>
            <Popconfirm
              :title="
                record.isEnabled ? '确认停用此规则？' : '确认启用此规则？'
              "
              @confirm="handleToggle(record as AlertApi.RuleItem)"
            >
              <Button type="link" size="small">
                {{ record.isEnabled ? '停用' : '启用' }}
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确认删除此规则？删除后不可恢复"
              ok-type="danger"
              @confirm="handleDelete(record as AlertApi.RuleItem)"
            >
              <Button type="link" size="small" danger>删除</Button>
            </Popconfirm>
          </Space>
        </template>
      </template>
    </Table>

    <!-- 编辑弹窗 -->
    <Modal
      v-model:open="editVisible"
      :title="editMode === 'create' ? '新建规则' : '编辑规则'"
      width="720px"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="editLoading"
      @ok="handleSave"
    >
      <Form layout="vertical">
        <Space style="display: flex" :size="16">
          <FormItem label="规则代码" style="flex: 1" required>
            <Input
              v-model:value="editForm.ruleCode"
              :disabled="editMode === 'edit'"
              placeholder="如 PV_HIGH_ALARM"
              :maxlength="50"
            />
          </FormItem>
          <FormItem label="规则名称" style="flex: 1" required>
            <Input
              v-model:value="editForm.ruleName"
              placeholder="规则中文名称"
              :maxlength="100"
            />
          </FormItem>
        </Space>
        <Space style="display: flex" :size="16">
          <FormItem label="规则类型" style="width: 200px">
            <Select
              v-model:value="editForm.ruleType"
              :disabled="editMode === 'edit'"
              :options="
                (
                  [
                    'THRESHOLD',
                    'DRIFT',
                    'COMPOSITE',
                    'CONFIDENCE',
                  ] as AlertApi.RuleType[]
                ).map((v) => ({
                  value: v,
                  label: `${ruleTypeLabel[v]}（${v}）`,
                }))
              "
              @change="handleRuleTypeChange"
            />
          </FormItem>
          <FormItem label="优先级" style="width: 140px">
            <InputNumber
              v-model:value="editForm.priority"
              :min="1"
              :max="9999"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="启用" style="width: 100px">
            <Switch v-model:checked="editForm.isEnabled" />
          </FormItem>
        </Space>
        <FormItem label="描述">
          <Textarea
            v-model:value="editForm.description"
            :rows="2"
            :maxlength="500"
            placeholder="规则描述（可选）"
          />
        </FormItem>
        <FormItem label="规则配置" required>
          <ClpmAlertDslEditor
            v-model="editForm.dsl"
            :rule-type="editForm.ruleType"
          />
        </FormItem>
      </Form>
    </Modal>
  </Page>
</template>
