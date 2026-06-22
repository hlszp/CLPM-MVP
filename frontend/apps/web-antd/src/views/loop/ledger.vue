<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-009 回路台账页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.7 ~ §2.2.11
 * - Table 展示回路列表（位号/描述/装置/状态/评分/操作）
 * - 状态徽章：Ready 绿 / Partial 红 / INCONCLUSIVE 灰
 * - 支持按装置/状态/关键字筛选
 * - 新增/编辑回路 Modal 表单（含 scoreWeights 6 个 KPI 权重，总和须 100%）
 * - 删除二次确认
 * - RBAC: ADMIN/IC_ENGINEER 可写
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
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
} from 'ant-design-vue';

import {
  createLoopApi,
  deleteLoopApi,
  getLoopListApi,
  updateLoopApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import StatusBadge from '#/components/loop/status-badge.vue';

defineOptions({ name: 'LoopLedger' });

// List state
const loading = ref(false);
const loopList = ref<LoopApi.LoopListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  status: undefined as LoopApi.LoopStatus | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
});

// Plant nodes for filter (flattened)
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const statusOptions = [
  { label: '全部', value: undefined },
  { label: '就绪', value: 'Ready' },
  { label: '部分关联', value: 'Partial' },
  { label: '未确定', value: 'INCONCLUSIVE' },
];

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 160 },
  {
    title: '控制方式',
    dataIndex: 'controlMode',
    key: 'controlMode',
    width: 100,
  },
  { title: '状态', dataIndex: 'status', key: 'status', width: 110 },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80 },
  { title: 'Tag 关联', key: 'tagMapping', width: 200 },
  {
    title: '最后评分',
    dataIndex: 'lastScoreAt',
    key: 'lastScoreAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 180, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalMode = ref<'add' | 'edit'>('add');
const modalLoading = ref(false);
const editingLoop = ref<LoopApi.LoopListItem | null>(null);
const formRef = ref();
const formState = reactive({
  tagName: '',
  description: '',
  unitId: undefined as string | undefined,
  isActive: true,
  remark: '',
  scoreWeights: {
    good_value_rate: 20,
    auto_mode_rate: 20,
    steady_rate: 20,
    accuracy_rate: 15,
    oscillation_rate: 15,
    saturation_rate: 10,
  } as LoopApi.ScoreWeights,
});

const weightItems: { key: keyof LoopApi.ScoreWeights; label: string }[] = [
  { key: 'good_value_rate', label: '优良值率' },
  { key: 'auto_mode_rate', label: '自动模式率' },
  { key: 'steady_rate', label: '稳定率' },
  { key: 'accuracy_rate', label: '准确度' },
  { key: 'oscillation_rate', label: '振荡率' },
  { key: 'saturation_rate', label: '饱和率' },
];

const weightTotal = computed(() => {
  return Object.values(formState.scoreWeights).reduce(
    (sum, v) => sum + (Number(v) || 0),
    0,
  );
});

const weightValid = computed(() => weightTotal.value === 100);

/** 扁平化工厂节点树 */
function flattenNodes(
  nodes: PlantNodeApi.PlantNode[],
  result: PlantNodeApi.PlantNode[] = [],
): PlantNodeApi.PlantNode[] {
  for (const node of nodes) {
    result.push(node);
    if (node.children) {
      flattenNodes(node.children, result);
    }
  }
  return result;
}

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载回路列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopListApi({
      plantNodeId: query.plantNodeId,
      status: query.status,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    loopList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 打开新增 Modal */
function handleAdd() {
  modalMode.value = 'add';
  editingLoop.value = null;
  formState.tagName = '';
  formState.description = '';
  formState.unitId = undefined;
  formState.isActive = true;
  formState.remark = '';
  formState.scoreWeights = {
    accuracy_rate: 15,
    auto_mode_rate: 20,
    good_value_rate: 20,
    oscillation_rate: 15,
    saturation_rate: 10,
    steady_rate: 20,
  };
  modalVisible.value = true;
}

/** 打开编辑 Modal */
function handleEdit(record: LoopApi.LoopListItem) {
  modalMode.value = 'edit';
  editingLoop.value = record;
  formState.tagName = record.tagName;
  formState.description = record.description;
  formState.unitId = record.unitId;
  formState.isActive = record.isActive;
  formState.remark = '';
  // 编辑时需要完整 scoreWeights，通过详情接口获取
  loadLoopDetailForEdit(record.loopId);
  modalVisible.value = true;
}

/** 加载回路详情以填充编辑表单 */
async function loadLoopDetailForEdit(loopId: string) {
  try {
    const { getLoopDetailApi } = await import('#/api/loop');
    const detail = await getLoopDetailApi(loopId);
    formState.scoreWeights = { ...detail.basicInfo.scoreWeights };
    formState.remark = detail.basicInfo.remark || '';
    formState.description = detail.basicInfo.description;
  } catch {
    // 错误已由拦截器处理
  }
}

/** 提交表单 */
async function handleSubmit() {
  await formRef.value?.validate();
  if (!weightValid.value) {
    message.warning(`权重总和须为 100%，当前为 ${weightTotal.value}%`);
    return;
  }
  modalLoading.value = true;
  try {
    if (modalMode.value === 'add') {
      if (!formState.unitId) {
        message.warning('请选择所属单元');
        return;
      }
      await createLoopApi({
        tagName: formState.tagName,
        description: formState.description,
        unitId: formState.unitId,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路创建成功');
    } else if (editingLoop.value) {
      await updateLoopApi(editingLoop.value.loopId, {
        description: formState.description,
        scoreWeights: formState.scoreWeights,
        isActive: formState.isActive,
        remark: formState.remark,
      });
      message.success('回路更新成功');
    }
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

/** 删除回路 */
async function handleDelete(record: LoopApi.LoopListItem) {
  try {
    await deleteLoopApi(record.loopId);
    message.success('回路删除成功');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** Tag 关联完整性展示 */
const tagMappingRoles: {
  key: keyof LoopApi.TagMappingStatus;
  label: string;
}[] = [
  { key: 'pv', label: 'PV' },
  { key: 'sp', label: 'SP' },
  { key: 'op', label: 'OP' },
  { key: 'mode', label: 'MODE' },
  { key: 'pid_p', label: 'P' },
  { key: 'pid_i', label: 'I' },
  { key: 'pid_d', label: 'D' },
];

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page title="回路台账">
    <Card>
      <!-- 筛选区 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.plantNodeId"
          placeholder="按装置/单元筛选"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.status"
          placeholder="按状态筛选"
          style="width: 160px"
          allow-clear
          :options="statusOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="query.keyword"
          placeholder="搜索位号/描述"
          allow-clear
          style="width: 240px"
          @press-enter="handleSearch"
        />
        <Button type="primary" @click="handleSearch">查询</Button>
        <Button
          v-permission="['ADMIN', 'IC_ENGINEER']"
          type="primary"
          @click="handleAdd"
        >
          新增回路
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="loopList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: LoopApi.LoopListItem) => record.loopId"
        :scroll="{ x: 1200 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <StatusBadge :status="record.status" :is-active="record.isActive" />
          </template>
          <template v-else-if="column.key === 'score'">
            <span v-if="record.score != null" class="font-medium">
              {{ record.score.toFixed(1) }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'tagMapping'">
            <div class="flex flex-wrap gap-1">
              <Tag
                v-for="role in tagMappingRoles"
                :key="role.key"
                :color="record.tagMappingStatus[role.key] ? 'green' : 'default'"
                class="m-0"
              >
                {{ role.label }}
              </Tag>
            </div>
          </template>
          <template v-else-if="column.key === 'lastScoreAt'">
            {{ formatTime(record.lastScoreAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                v-permission="['ADMIN', 'IC_ENGINEER']"
                type="link"
                size="small"
                @click="handleEdit(record as LoopApi.LoopListItem)"
              >
                编辑
              </Button>
              <Popconfirm
                v-permission="['ADMIN']"
                title="确认删除该回路？删除后历史数据保留，但回路将不可用。"
                @confirm="handleDelete(record as LoopApi.LoopListItem)"
              >
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  danger
                >
                  删除
                </Button>
              </Popconfirm>
            </div>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="modalMode === 'add' ? '新增回路' : '编辑回路'"
      :confirm-loading="modalLoading"
      width="640px"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <div class="grid grid-cols-2 gap-4">
          <FormItem
            name="tagName"
            label="回路位号"
            :rules="[{ required: true, message: '请输入回路位号' }]"
          >
            <Input
              v-model:value="formState.tagName"
              placeholder="例如：101-FC-1023"
              :disabled="modalMode === 'edit'"
            />
          </FormItem>
          <FormItem
            name="unitId"
            label="所属单元"
            :rules="[{ required: true, message: '请选择所属单元' }]"
          >
            <Select
              v-model:value="formState.unitId"
              placeholder="请选择所属单元"
              :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
              :disabled="modalMode === 'edit'"
              show-search
              :filter-option="
                (input: string, option: any) => option.label.includes(input)
              "
            />
          </FormItem>
        </div>
        <FormItem name="description" label="回路描述">
          <Input
            v-model:value="formState.description"
            placeholder="请输入回路描述"
          />
        </FormItem>

        <!-- 评分权重 -->
        <div class="mb-2 font-medium">
          评分权重
          <span
            class="ml-2 text-xs"
            :class="weightValid ? 'text-green-500' : 'text-red-500'"
          >
            总和：{{ weightTotal }}%
          </span>
        </div>
        <div class="grid grid-cols-3 gap-3 rounded border p-3">
          <FormItem
            v-for="item in weightItems"
            :key="item.key"
            :label="item.label"
            name="scoreWeights"
          >
            <InputNumber
              v-model:value="formState.scoreWeights[item.key]"
              :min="0"
              :max="100"
              class="w-full"
              addon-after="%"
            />
          </FormItem>
        </div>

        <FormItem name="isActive" label="启用状态">
          <Switch v-model:checked="formState.isActive" />
        </FormItem>
        <FormItem name="remark" label="备注">
          <Input.TextArea
            v-model:value="formState.remark"
            placeholder="备注信息"
            :rows="2"
          />
        </FormItem>
      </Form>
    </Modal>
  </Page>
</template>
