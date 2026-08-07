<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { AlertApi } from '#/api/alert';

import { h, onMounted, reactive, ref } from 'vue';

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
import { formatTime } from '#/utils/format';
import { ALERT_METRIC_LABEL, ALERT_RULE_TYPE_LABEL } from '#/constants/clpm-ui';

defineOptions({ name: 'AlertRules' });

// 规则类型中文标签（对齐 clpm-ui.ts 统一映射）
const ruleTypeLabel = ALERT_RULE_TYPE_LABEL;
// DSL 指标名中文提示文本（编辑器下方帮助说明）
const metricHint = Object.entries(ALERT_METRIC_LABEL)
  .map(([k, v]) => `${k}=${v}`)
  .join('；');

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
  dslText: '',
});
const editLoading = ref(false);

const ruleTypeColor: Record<AlertApi.RuleType, string> = {
  THRESHOLD: 'blue',
  DRIFT: 'cyan',
  COMPOSITE: 'purple',
  CONFIDENCE: 'orange',
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
  editForm.dslText = JSON.stringify(dslTemplates.THRESHOLD, null, 2);
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
  editForm.dslText = JSON.stringify(record.dsl, null, 2);
  editVisible.value = true;
}

function handleRuleTypeChange(value: any) {
  const type = value as AlertApi.RuleType;
  // 仅在创建模式下切换模板
  if (editMode.value === 'create') {
    editForm.dslText = JSON.stringify(dslTemplates[type], null, 2);
  }
}

async function handleSave() {
  if (!editForm.ruleCode.trim() || !editForm.ruleName.trim()) {
    message.warning('规则代码和名称不可为空');
    return;
  }
  let dsl: Record<string, any>;
  try {
    dsl = JSON.parse(editForm.dslText);
  } catch {
    message.error('DSL JSON 格式错误');
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
  } catch (err: any) {
    const msg = err?.response?.data?.message || '保存失败';
    message.error(msg);
  } finally {
    editLoading.value = false;
  }
}

async function handleToggle(record: AlertApi.RuleItem) {
  try {
    await toggleAlertRuleApi(record.ruleId, !record.isEnabled);
    message.success(!record.isEnabled ? '已启用' : '已停用');
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

onMounted(() => {
  loadRules();
  loadSwitch();
});
</script>

<template>
  <Page title="预警规则">
    <template #extra>
      <Space>
        <span class="text-sm">全局开关：</span>
        <Switch
          :checked="globalEnabled"
          :loading="switchLoading"
          checked-children="开"
          un-checked-children="关"
          @change="handleSwitchChange"
        />
      </Space>
    </template>

    <!-- 筛选 + 操作栏 -->
    <Form layout="inline" class="mb-4">
      <FormItem label="类型">
        <Select
          v-model:value="query.ruleType"
          allow-clear
          placeholder="全部"
          style="width: 140px"
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
      <FormItem label="状态">
        <Select
          v-model:value="query.isEnabled"
          allow-clear
          placeholder="全部"
          style="width: 100px"
          :options="[
            { value: 'true', label: '启用' },
            { value: 'false', label: '停用' },
          ]"
        />
      </FormItem>
      <FormItem>
        <Button type="primary" @click="handleSearch">查询</Button>
      </FormItem>
      <FormItem>
        <Button type="primary" @click="openCreateModal">新建规则</Button>
      </FormItem>
    </Form>

    <!-- 规则表格 -->
    <Table
      :columns="columns"
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
      :ok-text="`保存`"
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
        <FormItem label="规则 DSL（JSON）" required>
          <Textarea
            v-model:value="editForm.dslText"
            :rows="14"
            class="font-mono text-xs"
            placeholder="规则 DSL JSON"
          />
          <div class="mt-1 text-xs text-gray-400 leading-5">
            <div>指标名对照：{{ metricHint }}</div>
            <div>
              字段保留英文键名（ruleType/scope/condition/durationSeconds/cooldownSeconds/severity/actions
              等），以对齐后端 DSL 校验；中文仅用于页面展示。
            </div>
          </div>
        </FormItem>
      </Form>
    </Modal>
  </Page>
</template>
