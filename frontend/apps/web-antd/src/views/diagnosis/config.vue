<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Card,
  Input,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Table,
  TabPane,
  Tabs,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';

import {
  createDiagnosisConfigApi,
  deleteDiagnosisConfigApi,
  type DiagnosisApi,
  type DiagnosisConfigApi,
  getDiagnosisConfigsApi,
  getDiagnosisOperatorsApi,
  updateDiagnosisConfigsApi,
} from '#/api/diagnosis';
import {
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'DiagnosisConfig' });

const { isAdmin } = useClpmRoles();

const activeTab = ref('operators');

// ---------------------------------------------------------------------------
// 常量：症状码（diag_code）与算子家族的中文映射
// ---------------------------------------------------------------------------

const DIAG_CODE_TEXT: Record<string, string> = {
  EXTERNAL_DISTURBANCE: '外扰频繁',
  MANUAL_REVIEW: '人工复核',
  OSCILLATION: '振荡',
  OUTPUT_SATURATION: '输出饱和',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  QUALITY_ABNORMAL: 'PV 质量异常',
  VALVE_STICTION: '阀门粘滞',
};

const FAMILY_TEXT: Record<string, { color: string; label: string }> = {
  disturbance: { color: 'purple', label: '外扰' },
  link: { color: 'red', label: '链路' },
  oscillation: { color: 'orange', label: '振荡' },
  saturation: { color: 'gold', label: '饱和' },
  sensor: { color: 'cyan', label: '仪表' },
  stiction: { color: 'geekblue', label: '粘滞' },
  tuning: { color: 'blue', label: '整定' },
};

function diagCodeText(code: null | string): string {
  return code ? (DIAG_CODE_TEXT[code] ?? code) : '-';
}

function familyMeta(family: string) {
  return (
    FAMILY_TEXT[family] ?? { color: 'default', label: family || '-' }
  );
}

/** 诊断代码枚举（后端 DiagnosisLabel Literal；新增仅可从中选择） */
const DIAG_CODE_OPTIONS = Object.keys(DIAG_CODE_TEXT);

/** 键值对 schema → 紧凑展示串（k=v；中文说明）；对象值 JSON 序列化防 [object Object] */
function schemaEntriesText(
  schema: null | Record<string, unknown> | undefined,
): string {
  if (!schema) return '-';
  const entries = Object.entries(schema);
  if (entries.length === 0) return '-';
  return entries
    .map(([k, v]) => {
      const val =
        v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v);
      return `${k}=${val}`;
    })
    .join('；');
}

// ---------------------------------------------------------------------------
// Tab 1：诊断指标（算子注册表字典，只读）
// ---------------------------------------------------------------------------

const operatorsLoading = ref(false);
const operators = ref<DiagnosisApi.OperatorInfo[]>([]);

async function loadOperators() {
  operatorsLoading.value = true;
  try {
    operators.value = await getDiagnosisOperatorsApi();
  } finally {
    operatorsLoading.value = false;
  }
}

const operatorColumns: TableColumnsType = [
  { title: '指标（算子）', dataIndex: 'displayName', key: 'displayName', width: 160 },
  {
    title: '家族',
    dataIndex: 'family',
    key: 'family',
    width: 80,
  },
  {
    title: '诊断码',
    dataIndex: 'diagCode',
    key: 'diagCode',
    width: 150,
    customRender: ({ text }) => diagCodeText(text),
  },
  { title: '说明', dataIndex: 'description', key: 'description', ellipsis: true },
  {
    title: '输出指标',
    dataIndex: 'outputsSchema',
    key: 'outputsSchema',
    width: 320,
    ellipsis: true,
    customRender: ({ text }) =>
      Object.entries(text ?? {})
        .map(([k, v]) => `${k}(${v})`)
        .join('；') || '-',
  },
  {
    title: '默认阈值参数',
    dataIndex: 'thresholdSchema',
    key: 'thresholdSchema',
    width: 280,
    ellipsis: true,
    customRender: ({ text }) => schemaEntriesText(text),
  },
  {
    title: '所需信号',
    dataIndex: 'requiredSignals',
    key: 'requiredSignals',
    width: 130,
    customRender: ({ text }) => (text ?? []).join(' / ') || '-',
  },
];

// ---------------------------------------------------------------------------
// Tab 2：诊断配置（CRUD）
// ---------------------------------------------------------------------------

const configsLoading = ref(false);
const configs = ref<DiagnosisConfigApi.ConfigItem[]>([]);

/** 已存在的诊断代码（新增时禁用，防重复提交 409） */
const existingDiagKeys = computed(
  () => new Set(configs.value.map((c) => c.diagKey ?? '')),
);

async function loadConfigs() {
  configsLoading.value = true;
  try {
    const resp = await getDiagnosisConfigsApi();
    configs.value = resp.items ?? [];
  } finally {
    configsLoading.value = false;
  }
}

const configColumns: TableColumnsType = [
  {
    title: '诊断代码',
    dataIndex: 'diagKey',
    key: 'diagKey',
    width: 170,
    customRender: ({ text }) => diagCodeText(text),
  },
  { title: '名称', dataIndex: 'diagName', key: 'diagName', width: 130 },
  { title: '算法类型', dataIndex: 'algorithmType', key: 'algorithmType', width: 130 },
  { title: '计算方法', dataIndex: 'calcMethod', key: 'calcMethod', width: 160 },
  {
    title: '启用',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 90,
  },
  { title: '更新人', dataIndex: 'updatedBy', key: 'updatedBy', width: 90 },
  { title: '更新时间', dataIndex: 'updatedAt', key: 'updatedAt', width: 150 },
  { title: '操作', key: 'actions', width: 130, fixed: 'right' },
];

// ----- 编辑 / 新增（共用 Modal 表单 + JSON 文本域） -----

type EditMode = 'create' | 'update';

const modalVisible = ref(false);
const modalMode = ref<EditMode>('update');
const saving = ref(false);

const form = reactive({
  diagId: '',
  diagKey: '',
  diagName: '',
  algorithmType: '',
  calcMethod: '',
  isEnabled: true,
  paramsText: '{}',
  thresholdText: '{}',
});

/** JSON 文本域解析错误（Poka-Yoke：错误前置提示，禁止提交） */
const paramsError = ref('');
const thresholdError = ref('');

function parseJsonText(text: string, errRef: { value: string }): null | Record<string, any> {
  const trimmed = text.trim();
  if (!trimmed) {
    errRef.value = '';
    return null;
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      errRef.value = '必须是 JSON 对象（如 {"key": value}）';
      return null;
    }
    errRef.value = '';
    return parsed;
  } catch {
    errRef.value = 'JSON 格式错误，请检查括号/引号/逗号';
    return null;
  }
}

const modalTitle = computed(() =>
  modalMode.value === 'create' ? '新增诊断配置' : '编辑诊断配置',
);

function openEditModal(record: DiagnosisConfigApi.ConfigItem) {
  modalMode.value = 'update';
  form.diagId = record.diagId;
  form.diagKey = record.diagKey ?? '';
  form.diagName = record.diagName ?? '';
  form.algorithmType = record.algorithmType ?? '';
  form.calcMethod = record.calcMethod ?? '';
  form.isEnabled = record.isEnabled;
  form.paramsText = JSON.stringify(record.params ?? {}, null, 2);
  form.thresholdText = JSON.stringify(record.threshold ?? {}, null, 2);
  paramsError.value = '';
  thresholdError.value = '';
  modalVisible.value = true;
}

function openCreateModal() {
  modalMode.value = 'create';
  form.diagId = '';
  form.diagKey = '';
  form.diagName = '';
  form.algorithmType = '';
  form.calcMethod = '';
  form.isEnabled = true;
  form.paramsText = '{}';
  form.thresholdText = '{}';
  paramsError.value = '';
  thresholdError.value = '';
  modalVisible.value = true;
}

async function handleSave() {
  if (modalMode.value === 'create' && !form.diagKey) {
    Modal.warning({ content: '请选择诊断代码（8 类标签之一）', title: '缺少诊断代码' });
    return;
  }
  const params = parseJsonText(form.paramsText, paramsError);
  if (paramsError.value) return;
  const threshold = parseJsonText(form.thresholdText, thresholdError);
  if (thresholdError.value) return;

  saving.value = true;
  try {
    if (modalMode.value === 'create') {
      await createDiagnosisConfigApi({
        algorithmType: form.algorithmType,
        calcMethod: form.calcMethod || null,
        diagKey: form.diagKey,
        diagName: form.diagName,
        isEnabled: form.isEnabled,
        params,
        threshold,
      });
      Modal.success({ content: `已新增诊断配置 ${form.diagKey}`, title: '新增成功' });
    } else {
      await updateDiagnosisConfigsApi([
        {
          algorithmType: form.algorithmType,
          calcMethod: form.calcMethod || null,
          diagId: form.diagId,
          isEnabled: form.isEnabled,
          params,
          threshold,
        },
      ]);
      Modal.success({ content: `已保存诊断配置 ${form.diagKey}`, title: '保存成功' });
    }
    modalVisible.value = false;
    await loadConfigs();
  } finally {
    saving.value = false;
  }
}

async function handleDelete(record: DiagnosisConfigApi.ConfigItem) {
  await deleteDiagnosisConfigApi(record.diagId);
  Modal.success({
    content: `已删除诊断配置 ${record.diagKey}（阈值覆盖等关联配置不受影响）`,
    title: '删除成功',
  });
  await loadConfigs();
}

/** 表格行内直接切换启用状态（管理员） */
async function handleToggleEnabled(
  record: DiagnosisConfigApi.ConfigItem,
  enabled: boolean,
) {
  await updateDiagnosisConfigsApi([
    { diagId: record.diagId, isEnabled: enabled },
  ]);
  record.isEnabled = enabled;
  Modal.success({
    content: `${record.diagKey} 已${enabled ? '启用' : '停用'}`,
    title: enabled ? '已启用' : '已停用',
  });
}

// ---------------------------------------------------------------------------
// 工具栏（刷新当前 Tab / 帮助）
// ---------------------------------------------------------------------------

const loading = ref(false);

async function handleRefresh() {
  loading.value = true;
  try {
    await (activeTab.value === 'operators' ? loadOperators() : loadConfigs());
  } finally {
    loading.value = false;
  }
}

function handleHelp() {
  showPageHelp({
    title: '诊断配置 帮助',
    content:
      '诊断配置页：「诊断指标」展示 11 个诊断元算子的指标字典（输出指标与默认阈值参数，代码级定义只读）；「诊断配置」管理 8 类诊断标签的全局默认配置（算法类型/计算方法/算法参数/阈值/启停），支持新增、编辑、删除与行内启停，仅 ADMIN 可操作。修改全局默认后，回路级阈值覆盖（诊断阈值模板）不受影响；生效优先级为 全局默认 < 回路类型模板 < 装置 < 回路。',
  });
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  void loadOperators();
  void loadConfigs();
});

defineExpose({
  refresh: handleRefresh,
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="诊断配置"
      subtitle="诊断指标字典 / 诊断配置管理"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <div class="mt-4">
      <Tabs v-model:active-key="activeTab">
        <!-- Tab 1：诊断指标（算子字典，只读） -->
        <TabPane key="operators">
          <template #tab>
            <Tooltip title="11 个诊断元算子的指标与默认参数定义（代码级，只读）" placement="top">
              <span>诊断指标</span>
            </Tooltip>
          </template>
          <Card size="small">
            <Table
              :columns="operatorColumns"
              :data-source="operators"
              :loading="operatorsLoading"
              :pagination="false"
              row-key="name"
              size="small"
              :scroll="{ x: 1300 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'family'">
                  <Tag :color="familyMeta((record as any).family).color">
                    {{ familyMeta((record as any).family).label }}
                  </Tag>
                </template>
              </template>
            </Table>
          </Card>
        </TabPane>

        <!-- Tab 2：诊断配置（CRUD） -->
        <TabPane key="configs">
          <template #tab>
            <Tooltip title="8 类诊断标签的全局默认配置（算法/参数/阈值/启停）管理" placement="top">
              <span>诊断配置</span>
            </Tooltip>
          </template>
          <Card size="small">
            <div v-if="isAdmin" class="mb-3 flex justify-end">
              <ClpmToolbarButton
                :icon-only="false"
                label="新增配置"
                type="primary"
                size="small"
                @click="openCreateModal()"
              />
            </div>
            <Table
              :columns="configColumns"
              :data-source="configs"
              :loading="configsLoading"
              :pagination="false"
              row-key="diagId"
              size="small"
              :scroll="{ x: 1200 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'isEnabled'">
                  <Switch
                    v-if="isAdmin"
                    :checked="(record as any).isEnabled"
                    checked-children="启"
                    size="small"
                    un-checked-children="停"
                    @change="(v: any) => handleToggleEnabled(record as any, !!v)"
                  />
                  <Tag v-else :color="(record as any).isEnabled ? 'green' : 'default'">
                    {{ (record as any).isEnabled ? '启用' : '停用' }}
                  </Tag>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <template v-if="isAdmin">
                    <ClpmToolbarButton
                      :icon-only="false"
                      label="编辑"
                      type="link"
                      size="small"
                      @click="openEditModal(record as any)"
                    />
                    <Popconfirm
                      :title="`确认删除诊断配置 ${diagCodeText((record as any).diagKey)}？全局默认将失效，回路阈值覆盖不受影响。`"
                      @confirm="handleDelete(record as any)"
                    >
                      <ClpmToolbarButton
                        :icon-only="false"
                        danger
                        label="删除"
                        type="link"
                        size="small"
                      />
                    </Popconfirm>
                  </template>
                  <span v-else class="text-xs text-muted-foreground">只读</span>
                </template>
              </template>
            </Table>
          </Card>
        </TabPane>
      </Tabs>
    </div>

    <!-- 编辑 / 新增 Modal -->
    <Modal
      v-model:open="modalVisible"
      :confirm-loading="saving"
      :ok-button-props="{ disabled: !!paramsError || !!thresholdError }"
      :ok-text="isAdmin ? '保存' : '关闭'"
      cancel-text="取消"
      :title="modalTitle"
      width="640px"
      @ok="isAdmin ? handleSave() : (modalVisible = false)"
    >
      <div class="flex flex-col gap-3 py-2">
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">诊断代码</span>
          <Select
            v-if="modalMode === 'create'"
            v-model:value="form.diagKey"
            class="flex-1"
            placeholder="选择诊断代码（8 类标签之一）"
          >
            <Select.Option
              v-for="code in DIAG_CODE_OPTIONS"
              :key="code"
              :disabled="existingDiagKeys.has(code)"
              :value="code"
            >
              {{ code }}（{{ diagCodeText(code) }}）{{ existingDiagKeys.has(code) ? '· 已存在' : '' }}
            </Select.Option>
          </Select>
          <Input v-else v-model:value="form.diagKey" class="flex-1" disabled />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">名称</span>
          <Input v-model:value="form.diagName" placeholder="诊断中文名" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">算法类型</span>
          <Input v-model:value="form.algorithmType" placeholder="如 IAE_FFT / SCATTER_FIT" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">计算方法</span>
          <Input v-model:value="form.calcMethod" placeholder="如 IAE_ZERO_CROSSING" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">启用</span>
          <Switch v-model:checked="form.isEnabled" checked-children="启" un-checked-children="停" />
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-sm">算法参数（JSON 对象）</span>
          <Textarea
            v-model:value="form.paramsText"
            :rows="5"
            :class="{ 'border-red-500': !!paramsError }"
            placeholder="{&quot;param&quot;: value}"
          />
          <span v-if="paramsError" class="text-xs text-red-500">{{ paramsError }}</span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-sm">阈值（JSON 对象，全局默认层）</span>
          <Textarea
            v-model:value="form.thresholdText"
            :rows="8"
            :class="{ 'border-red-500': !!thresholdError }"
            placeholder="{&quot;threshold_key&quot;: value}"
          />
          <span v-if="thresholdError" class="text-xs text-red-500">{{ thresholdError }}</span>
          <span class="text-xs text-muted-foreground">
            修改全局默认不影响已配置的回路类型模板/装置/回路级覆盖（生效优先级：全局默认 &lt; 模板 &lt; 装置 &lt; 回路）
          </span>
        </div>
      </div>
    </Modal>
  </Page>
</template>
