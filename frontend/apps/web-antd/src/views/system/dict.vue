<script lang="ts" setup>
/**
 * 字典管理页（通用可配置枚举）
 *
 * - 字典类型下拉（后端注册：MEASURE_TYPE 测点类型）
 * - 字典项列表：编码/显示名/排序/启用（行内 Switch）/引用标记/更新 + CRUD
 * - 引用保护：被业务数据引用的项不可删除/禁用（后端校验，前端置灰提示）
 * - 业务生效：测点配置页下拉/展示、测点导入校验均以字典为准
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { DictApi } from '#/api/dict';

import { computed, onMounted, reactive, ref } from 'vue';

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
  Switch,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  createDictItemApi,
  deleteDictItemApi,
  getDictItemsPagedApi,
  getDictTypesApi,
  updateDictItemApi,
} from '#/api/dict';
import {
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemDict' });

// ===== 字典类型 =====

const dictTypes = ref<DictApi.DictType[]>([]);
const dictType = ref('MEASURE_TYPE');

const dictTypeTitle = computed(
  () => dictTypes.value.find((t) => t.dictType === dictType.value)?.title ?? '',
);

// ===== 列表 =====

const loading = ref(false);
const list = ref<DictApi.DictItem[]>([]);
const total = ref(0);
const query = reactive({ page: 1, pageSize: 20 });

const columns: TableColumnsType = [
  { title: '编码', dataIndex: 'itemCode', key: 'itemCode', width: 180 },
  { title: '显示名', dataIndex: 'itemLabel', key: 'itemLabel', width: 140 },
  {
    title: '排序',
    dataIndex: 'sortOrder',
    key: 'sortOrder',
    width: 70,
    align: 'center',
  },
  { title: '启用', key: 'isEnabled', width: 90, align: 'center' },
  { title: '引用', key: 'isReferenced', width: 90, align: 'center' },
  { title: '更新', key: 'updated', width: 170 },
  { title: '操作', key: 'action', width: 130, fixed: 'right' },
];

async function loadTypes() {
  try {
    dictTypes.value = await getDictTypesApi();
  } catch {
    // 错误已由拦截器处理
  }
}

async function loadList() {
  loading.value = true;
  try {
    const res = await getDictItemsPagedApi(dictType.value, {
      page: query.page,
      pageSize: query.pageSize,
    });
    list.value = res.items ?? [];
    total.value = res.total ?? 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleTypeChange() {
  query.page = 1;
  loadList();
}

function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadList();
}

// ===== 新建 / 编辑 =====

const modalVisible = ref(false);
const modalMode = ref<'create' | 'edit'>('create');
const saving = ref(false);
const editingItem = ref<DictApi.DictItem | null>(null);
const form = reactive({
  itemCode: '',
  itemLabel: '',
  sortOrder: 0,
  isEnabled: true,
});

function openCreateModal() {
  modalMode.value = 'create';
  editingItem.value = null;
  form.itemCode = '';
  form.itemLabel = '';
  form.sortOrder = (list.value.length + 1) * 10;
  form.isEnabled = true;
  modalVisible.value = true;
}

function openEditModal(record: DictApi.DictItem) {
  modalMode.value = 'edit';
  editingItem.value = record;
  form.itemCode = record.itemCode;
  form.itemLabel = record.itemLabel;
  form.sortOrder = record.sortOrder;
  form.isEnabled = record.isEnabled;
  modalVisible.value = true;
}

async function handleSave() {
  if (!form.itemCode.trim() || !form.itemLabel.trim()) {
    message.warning('编码与显示名不可为空');
    return;
  }
  saving.value = true;
  try {
    if (modalMode.value === 'create') {
      await createDictItemApi({
        dictType: dictType.value,
        itemCode: form.itemCode.trim(),
        itemLabel: form.itemLabel.trim(),
        sortOrder: form.sortOrder,
        isEnabled: form.isEnabled,
      });
      message.success('字典项已创建');
    } else if (editingItem.value) {
      await updateDictItemApi(editingItem.value.id, {
        itemLabel: form.itemLabel.trim(),
        sortOrder: form.sortOrder,
        isEnabled: form.isEnabled,
      });
      message.success('字典项已更新');
    }
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

// ===== 启用行内切换 =====

const toggling = ref<Set<string>>(new Set());

async function handleEnabledToggle(record: DictApi.DictItem, checked: boolean) {
  if (toggling.value.has(record.id)) return;
  toggling.value.add(record.id);
  try {
    await updateDictItemApi(record.id, { isEnabled: checked });
    record.isEnabled = checked;
    message.success(`「${record.itemLabel}」已${checked ? '启用' : '禁用'}`);
  } catch {
    // 失败回滚显示
    record.isEnabled = !checked;
  } finally {
    toggling.value.delete(record.id);
  }
}

// ===== 删除 =====

async function handleDelete(record: DictApi.DictItem) {
  try {
    await deleteDictItemApi(record.id);
    message.success('字典项已删除');
    await loadList();
  } catch {
    // 错误已由拦截器处理（含引用保护提示）
  }
}

// ===== 工具栏 =====

async function handleRefresh() {
  await Promise.all([loadTypes(), loadList()]);
}

function handleHelp() {
  showPageHelp({
    title: '字典管理 帮助',
    content: [
      '字典管理页：维护系统可配置枚举（当前支持：测点类型、参数类型）。',
      '· 测点类型：用于测点配置页的筛选/展示与 Excel 导入校验（温度/压力/液位/流量等，可自定义如：浓度）。',
      '· 参数类型：测点在回路中的角色（测量值 PV/设定值 SP/操作值 OP/模式 MODE/PID 参数等，可自定义）。',
      '· 两类字典均即时生效于测点新建/编辑下拉与导入校验（约 30s 缓存收敛）；导入 Excel 支持填编码或中文显示名（如：测量值 ↔ PV）。',
      '· 编码（code）：落库值，建议英文大写；显示名（label）：中文。',
      '· 引用保护：已被测点数据引用的类型不可删除/禁用（列表「引用」列标记）。',
      '· 排序：小值在前，影响下拉与筛选项顺序。',
    ].join('\n'),
  });
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  void loadTypes();
  void loadList();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="字典管理"
      subtitle="系统可配置枚举（测点类型、参数类型等），业务页面下拉与导入校验自动生效"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 字典类型切换 + 新建 -->
    <div class="mb-3 mt-4 flex flex-wrap items-center gap-2">
      <Select
        v-model:value="dictType"
        style="width: 200px"
        :options="
          dictTypes.map((t) => ({ value: t.dictType, label: t.title }))
        "
        @change="handleTypeChange"
      />
      <span class="text-sm text-gray-400">
        当前字典：{{ dictTypeTitle }}（{{ dictType }}）
      </span>
      <span class="ml-auto"></span>
      <Button
        v-permission="['ADMIN']"
        type="primary"
        @click="openCreateModal"
      >
        新增字典项
      </Button>
    </div>

    <Table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="{
        current: query.page,
        pageSize: query.pageSize,
        total,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 项`,
      }"
      :scroll="{ x: 900 }"
      row-key="id"
      size="middle"
      @change="handlePageChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'itemCode'">
          <span class="font-mono">{{ record.itemCode }}</span>
        </template>
        <template v-else-if="column.key === 'isEnabled'">
          <Switch
            v-permission="['ADMIN']"
            size="small"
            :checked="record.isEnabled"
            :loading="toggling.has(record.id)"
            :disabled="record.isReferenced && record.isEnabled"
            @change="
              (checked: unknown) =>
                handleEnabledToggle(record as DictApi.DictItem, checked === true)
            "
          />
        </template>
        <template v-else-if="column.key === 'isReferenced'">
          <Tooltip
            :title="
              record.isReferenced
                ? '已被业务数据引用：不可删除/禁用（存量数据仍可显示）'
                : '未被引用：可删除'
            "
          >
            <Tag :color="record.isReferenced ? 'blue' : 'default'">
              {{ record.isReferenced ? '引用中' : '—' }}
            </Tag>
          </Tooltip>
        </template>
        <template v-else-if="column.key === 'updated'">
          <span class="text-xs text-gray-500">
            <template v-if="record.updatedAt">
              {{ formatTime(record.updatedAt) }}
              <template v-if="record.updatedBy">（{{ record.updatedBy }}）</template>
            </template>
            <template v-else>—</template>
          </span>
        </template>
        <template v-else-if="column.key === 'action'">
          <div class="flex items-center gap-1">
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              @click="openEditModal(record as DictApi.DictItem)"
            >
              编辑
            </Button>
            <Popconfirm
              :title="
                record.isReferenced
                  ? `「${record.itemLabel}」已被业务数据引用，无法删除`
                  : `确认删除字典项「${record.itemLabel}」？`
              "
              :disabled="record.isReferenced"
              @confirm="handleDelete(record as DictApi.DictItem)"
            >
              <Button
                v-permission="['ADMIN']"
                type="link"
                size="small"
                danger
                :disabled="record.isReferenced"
              >
                删除
              </Button>
            </Popconfirm>
          </div>
        </template>
      </template>
    </Table>

    <!-- 新建/编辑字典项 -->
    <Modal
      v-model:open="modalVisible"
      :title="modalMode === 'create' ? '新增字典项' : '编辑字典项'"
      :confirm-loading="saving"
      width="480px"
      @ok="handleSave"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="编码（落库值）" required>
          <Input
            v-model:value="form.itemCode"
            :disabled="modalMode === 'edit'"
            :maxlength="50"
            placeholder="如 CONCENTRATION（建议英文大写）"
          />
        </FormItem>
        <FormItem label="显示名" required>
          <Input
            v-model:value="form.itemLabel"
            :maxlength="100"
            placeholder="如 浓度"
          />
        </FormItem>
        <FormItem label="排序值">
          <InputNumber
            v-model:value="form.sortOrder"
            :min="0"
            :max="999999"
            style="width: 160px"
          />
          <span class="ml-2 text-xs text-gray-400">小值在前</span>
        </FormItem>
        <FormItem label="启用">
          <Switch v-model:checked="form.isEnabled" />
          <span class="ml-2 text-xs text-gray-400">
            禁用后不出现在业务下拉中（存量数据仍可显示）
          </span>
        </FormItem>
        <div v-if="modalMode === 'edit'" class="text-xs text-gray-400">
          编辑支持修改显示名/排序/启停；编码不可改（落库值变更会导致存量数据类型失联）。
        </div>
      </Form>
    </Modal>
  </Page>
</template>
