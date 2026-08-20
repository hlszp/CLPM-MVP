<script lang="ts" setup>
/**
 * 指标定义管理（指标配置-指标定义 Tab）
 *
 * - 后端 /configs/metric-definitions 驱动（内置 13 项 + 自定义，版本化存储）
 * - 列表 CRUD：指标代码 / 指标名称 / 类别 / 算法 / 说明 / 操作（编辑、删除、查看）
 * - 内置指标（GB/T 44693.2-2024 3+1+8 体系 + 综合评分）：代码/类别/公式锁定，
 *   仅可编辑名称/说明/单位；删除按钮置灰并提示原因
 * - 自定义指标（category=CUSTOM）：可增删改（仅登记，不参与 KPI 计算引擎）
 * - 每次保存自动生成新版本并立即生效；「版本」入口查看生效—失效时间并支持回滚
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { onMounted, reactive, ref } from 'vue';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Switch,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  createMetricDefinitionApi,
  deleteMetricDefinitionApi,
  getMetricDefinitionHistoryApi,
  getMetricDefinitionsApi,
  rollbackMetricDefinitionApi,
  updateMetricDefinitionApi,
} from '#/api/metric';
import {
  ClpmDangerConfirmModal,
  ClpmHelpIcon,
  ClpmToolbarButton,
  ClpmVersionHistoryModal,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'MetricConfigDefinition' });

const { themeColors } = useClpmTheme();
const { isAdmin } = useClpmRoles();

const loading = ref(false);
const list = ref<MetricApi.MetricDefinitionItem[]>([]);
const currentVersion = ref(1);

// ===== 类别配置 =====

const CATEGORY_CONFIG: Record<
  MetricApi.MetricDefinitionCategory,
  { label: string; order: number }
> = {
  COMPOSITE: { label: '综合评分', order: 0 },
  CORE: { label: '核心质量', order: 1 },
  COMMISSIONING: { label: '投用', order: 2 },
  AUXILIARY_DIAGNOSTIC: { label: '辅助诊断', order: 3 },
  CUSTOM: { label: '自定义', order: 4 },
};

function categoryLabel(cat: MetricApi.MetricDefinitionCategory): string {
  return CATEGORY_CONFIG[cat]?.label ?? cat;
}

// ===== 帮助（汇总原页面说明块） =====

const HELP_CONTENT = [
  '指标体系（对齐 GB/T 44693.2-2024，3+1+8 + 综合评分）：',
  '· 综合评分：P = (A·a + F·f + S·s) / (a + f + s) × R。A/F/S 为核心质量指标（准确率/快速率/稳定率），a/f/s 为对应权重（权重总和 100），R 为有效自控率（折扣因子，非加权项）。权重配置请前往「权重配置」Tab，定级阈值请前往「定级阈值」Tab。',
  '· 核心质量（CORE · 3 项）：准确率 A / 快速率 F / 稳定率 S — 参与综合评分加权。',
  '· 投用（COMMISSIONING · 1 项）：有效自控率 R — 综合评分折扣因子。',
  '· 辅助诊断（AUXILIARY_DIAGNOSTIC · 8 项）：好值率/自控率/振荡率/饱和率/稳态时间/理想稳态时间/粘滞指数/输出行程指数。',
  '· 内置指标为 KPI 计算引擎依赖项：代码/类别/算法锁定，仅可编辑名称/说明；不可删除。',
  '· 自定义指标仅作为登记项管理，不参与 KPI 计算引擎；可增删改。',
  '· 每次保存自动生成新版本并立即生效；「版本」入口可查看各版本生效—失效时间并回滚。',
].join('\n');

// ===== 表格列（列序对齐需求：指标代码/指标名称/类别/算法/说明/操作） =====

const columns: TableColumnsType = [
  {
    title: '指标代码',
    dataIndex: 'metricCode',
    key: 'metricCode',
    width: 200,
  },
  {
    title: '指标名称',
    dataIndex: 'metricName',
    key: 'metricName',
    width: 130,
  },
  {
    title: '类别',
    key: 'category',
    width: 110,
  },
  {
    title: '算法',
    dataIndex: 'formula',
    key: 'formula',
    ellipsis: true,
  },
  {
    title: '说明',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  {
    title: '操作',
    key: 'action',
    width: 150,
    fixed: 'right',
  },
];

// ===== 数据加载 =====

async function loadList() {
  loading.value = true;
  try {
    const data = await getMetricDefinitionsApi();
    list.value = [...(data.items ?? [])].toSorted(
      (a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0),
    );
    currentVersion.value = data.version ?? 1;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  loadList();
});

// ===== 查看（详情弹窗） =====

const detailOpen = ref(false);
const detailTarget = ref<MetricApi.MetricDefinitionItem | null>(null);

function handleView(record: MetricApi.MetricDefinitionItem) {
  detailTarget.value = record;
  detailOpen.value = true;
}

// ===== 编辑（内置：名称/说明/单位 + 启停；自定义：另可编辑公式） =====

const editOpen = ref(false);
const editSaving = ref(false);
const editTarget = ref<MetricApi.MetricDefinitionItem | null>(null);
const editForm = reactive({
  metricName: '',
  formula: '',
  description: '',
  unit: '',
  isEnabled: true,
});

function handleEdit(record: MetricApi.MetricDefinitionItem) {
  editTarget.value = record;
  editForm.metricName = record.metricName;
  editForm.formula = record.formula ?? '';
  editForm.description = record.description ?? '';
  editForm.unit = record.unit ?? '';
  editForm.isEnabled = record.isEnabled;
  editOpen.value = true;
}

async function submitEdit() {
  if (!editTarget.value) return;
  if (!editForm.metricName.trim()) {
    message.warning('指标名称不能为空');
    return;
  }
  editSaving.value = true;
  try {
    const payload: MetricApi.MetricDefinitionUpdateRequest = {
      metricName: editForm.metricName.trim(),
      description: editForm.description.trim(),
      unit: editForm.unit.trim(),
      isEnabled: editForm.isEnabled,
    };
    // 公式仅自定义指标可编辑（后端同样锁定内置指标公式）
    if (!editTarget.value.isBuiltin) {
      payload.formula = editForm.formula.trim();
    }
    await updateMetricDefinitionApi(editTarget.value.metricCode, payload);
    message.success('指标定义已更新（已生成新版本）');
    editOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    editSaving.value = false;
  }
}

// ===== 新增（仅自定义指标） =====

const createOpen = ref(false);
const createSaving = ref(false);
const createForm = reactive({
  metricCode: '',
  metricName: '',
  formula: '',
  description: '',
  unit: '',
});

function handleCreate() {
  createForm.metricCode = '';
  createForm.metricName = '';
  createForm.formula = '';
  createForm.description = '';
  createForm.unit = '';
  createOpen.value = true;
}

async function submitCreate() {
  const code = createForm.metricCode.trim();
  if (!/^[a-z][a-z0-9_]*$/.test(code)) {
    message.warning('指标代码须为小写字母开头的 snake_case（如 my_metric）');
    return;
  }
  if (!createForm.metricName.trim()) {
    message.warning('指标名称不能为空');
    return;
  }
  createSaving.value = true;
  try {
    await createMetricDefinitionApi({
      metricCode: code,
      metricName: createForm.metricName.trim(),
      formula: createForm.formula.trim() || undefined,
      description: createForm.description.trim() || undefined,
      unit: createForm.unit.trim() || undefined,
    });
    message.success('自定义指标已新增（已生成新版本）');
    createOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    createSaving.value = false;
  }
}

// ===== 删除（内置锁定；自定义二次确认） =====

const deleteConfirmOpen = ref(false);
const deleteSaving = ref(false);
const deleteTarget = ref<MetricApi.MetricDefinitionItem | null>(null);

function handleDelete(record: MetricApi.MetricDefinitionItem) {
  if (record.isBuiltin) return;
  deleteTarget.value = record;
  deleteConfirmOpen.value = true;
}

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleteSaving.value = true;
  try {
    await deleteMetricDefinitionApi(deleteTarget.value.metricCode);
    message.success('自定义指标已删除（已生成新版本）');
    deleteConfirmOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    deleteSaving.value = false;
  }
}

// ===== 版本历史 =====

const versionOpen = ref(false);
const versionLoading = ref(false);
const versionItems = ref<MetricApi.VersionHistoryItem[]>([]);
const rollingBack = ref(false);

async function openVersionHistory() {
  versionOpen.value = true;
  versionLoading.value = true;
  try {
    const data = await getMetricDefinitionHistoryApi();
    versionItems.value = data.items ?? [];
  } catch {
    versionItems.value = [];
  } finally {
    versionLoading.value = false;
  }
}

async function handleRollback(version: number) {
  rollingBack.value = true;
  try {
    await rollbackMetricDefinitionApi(version);
    message.success(`已回滚到版本 v${version}（生成新版本）`);
    await Promise.all([loadList(), openVersionHistory()]);
  } catch {
    // 错误已由拦截器处理
  } finally {
    rollingBack.value = false;
  }
}

// ===== P3-01：子组件暴露 refresh() =====

function refresh() {
  return loadList();
}

defineExpose({ refresh });
</script>

<template>
  <div class="metric-config-definition">
    <!-- 头部：简短说明 + 帮助符号 + 动作 -->
    <div class="mb-3 flex items-center justify-between">
      <div class="flex items-center text-sm" :style="{ color: themeColors.NEUTRAL }">
        <span>
          指标定义列表（内置 {{ list.filter((i) => i.isBuiltin).length }} 项 +
          自定义 {{ list.filter((i) => !i.isBuiltin).length }} 项）
        </span>
        <ClpmHelpIcon title="指标定义 帮助" :content="HELP_CONTENT" />
      </div>
      <div class="flex items-center gap-2">
        <Tag class="mr-1">
          当前版本 v{{ currentVersion }}
        </Tag>
        <Button size="small" @click="openVersionHistory"> 版本 </Button>
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <Button
          v-permission="['ADMIN']"
          type="primary"
          @click="handleCreate"
        >
          新增指标
        </Button>
      </div>
    </div>

    <Table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="false"
      :row-key="
        (record: MetricApi.MetricDefinitionItem) => record.metricCode
      "
      :scroll="{ x: 960 }"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'metricCode'">
          <span class="font-mono text-xs">
            {{ record.metricCode }}
          </span>
          <Tag v-if="!record.isEnabled" class="ml-1"> 停用 </Tag>
        </template>
        <template v-else-if="column.key === 'metricName'">
          <span>{{ record.metricName }}</span>
          <span v-if="record.unit" class="ml-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
            ({{ record.unit }})
          </span>
        </template>
        <template v-else-if="column.key === 'category'">
          <Tag>
            {{ categoryLabel(record.category) }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'formula'">
          <span
            class="font-mono text-xs"
            :style="{ color: themeColors.NEUTRAL }"
          >
            {{ record.formula || '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'description'">
          <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
            {{ record.description || '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <div class="flex items-center gap-1">
            <Button
              type="link"
              size="small"
              @click="handleView(record as MetricApi.MetricDefinitionItem)"
            >
              查看
            </Button>
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              @click="handleEdit(record as MetricApi.MetricDefinitionItem)"
            >
              编辑
            </Button>
            <Tooltip
              :title="
                record.isBuiltin
                  ? '内置指标为 KPI 计算引擎依赖项，不可删除（可停用）'
                  : ''
              "
            >
              <Button
                v-permission="['ADMIN']"
                type="link"
                size="small"
                danger
                :disabled="record.isBuiltin"
                @click="handleDelete(record as MetricApi.MetricDefinitionItem)"
              >
                删除
              </Button>
            </Tooltip>
          </div>
        </template>
      </template>
    </Table>

    <!-- 查看详情 -->
    <Modal
      v-model:open="detailOpen"
      title="指标定义详情"
      :footer="null"
      width="640px"
    >
      <Descriptions
        v-if="detailTarget"
        :column="1"
        bordered
        size="small"
        class="pt-2"
      >
        <DescriptionsItem label="指标代码">
          <span class="font-mono text-xs">{{ detailTarget.metricCode }}</span>
        </DescriptionsItem>
        <DescriptionsItem label="指标名称">
          {{ detailTarget.metricName }}
          <span v-if="detailTarget.unit" class="text-xs">
            （{{ detailTarget.unit }}）
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="类别">
          <Tag>{{ categoryLabel(detailTarget.category) }}</Tag>
          <Tag v-if="detailTarget.isBuiltin"> 内置 </Tag>
          <Tag v-else> 自定义 </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="算法">
          <span class="font-mono text-xs">
            {{ detailTarget.formula || '—' }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="说明">
          <span class="text-xs">{{ detailTarget.description || '—' }}</span>
        </DescriptionsItem>
        <DescriptionsItem label="状态">
          {{ detailTarget.isEnabled ? '启用' : '停用' }}
        </DescriptionsItem>
        <DescriptionsItem v-if="detailTarget.updatedAt" label="最近更新">
          <span class="text-xs">
            {{ formatTime(detailTarget.updatedAt) }}
            <template v-if="detailTarget.updatedBy">
              （{{ detailTarget.updatedBy }}）
            </template>
          </span>
        </DescriptionsItem>
      </Descriptions>
    </Modal>

    <!-- 编辑 -->
    <Modal
      v-model:open="editOpen"
      :title="`编辑指标${editTarget?.isBuiltin ? '（内置）' : '（自定义）'}`"
      :confirm-loading="editSaving"
      width="560px"
      @ok="submitEdit"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="指标代码">
          <Input :value="editTarget?.metricCode" disabled />
        </FormItem>
        <FormItem label="指标名称" required>
          <Input
            v-model:value="editForm.metricName"
            placeholder="请输入指标名称"
            :maxlength="50"
          />
        </FormItem>
        <FormItem
          :label="`算法公式${editTarget?.isBuiltin ? '（内置指标锁定）' : ''}`"
        >
          <Input
            v-model:value="editForm.formula"
            :disabled="editTarget?.isBuiltin"
            placeholder="算法公式（自定义指标可编辑）"
          />
        </FormItem>
        <FormItem label="说明">
          <Input.TextArea
            v-model:value="editForm.description"
            :rows="3"
            placeholder="指标说明"
            :maxlength="1000"
          />
        </FormItem>
        <FormItem label="单位">
          <Input
            v-model:value="editForm.unit"
            placeholder="如：s、%、—"
            :maxlength="20"
          />
        </FormItem>
        <FormItem label="启用">
          <Switch v-model:checked="editForm.isEnabled" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 新增自定义指标 -->
    <Modal
      v-model:open="createOpen"
      title="新增自定义指标"
      :confirm-loading="createSaving"
      width="560px"
      @ok="submitCreate"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="指标代码" required>
          <Input
            v-model:value="createForm.metricCode"
            placeholder="小写字母开头的 snake_case，如 my_metric"
            :maxlength="64"
          />
        </FormItem>
        <FormItem label="指标名称" required>
          <Input
            v-model:value="createForm.metricName"
            placeholder="请输入指标名称"
            :maxlength="50"
          />
        </FormItem>
        <FormItem label="算法公式">
          <Input
            v-model:value="createForm.formula"
            placeholder="算法公式（可选）"
          />
        </FormItem>
        <FormItem label="说明">
          <Input.TextArea
            v-model:value="createForm.description"
            :rows="3"
            placeholder="指标说明（可选）"
            :maxlength="1000"
          />
        </FormItem>
        <FormItem label="单位">
          <Input
            v-model:value="createForm.unit"
            placeholder="如：s、%（可选）"
            :maxlength="20"
          />
        </FormItem>
        <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
          自定义指标仅作为登记项管理，不参与 KPI 计算引擎。
        </div>
      </Form>
    </Modal>

    <!-- 删除确认（内置不可删，仅自定义） -->
    <ClpmDangerConfirmModal
      v-model:open="deleteConfirmOpen"
      title="删除自定义指标"
      action="删除"
      :target="deleteTarget?.metricCode"
      impact-scope="删除后该自定义指标从列表移除，并生成新版本。"
      :loading="deleteSaving"
      @confirm="confirmDelete"
    />

    <!-- 版本历史 -->
    <ClpmVersionHistoryModal
      v-model:open="versionOpen"
      title="指标定义 版本历史"
      :items="versionItems"
      :loading="versionLoading"
      :rolling-back="rollingBack"
      :rollbackable="isAdmin"
      @rollback="handleRollback"
    />
  </div>
</template>
