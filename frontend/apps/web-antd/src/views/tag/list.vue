<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';
import type { UploadProps } from 'ant-design-vue';

/**
 * 测点清单页
 *
 * - Table 展示测点列表（位号/名称/测点类型/量程/实时值/单位/质量戳/参数类型/所属单元/原始ID/操作）
 * - 测点类型彩色 Tag 展示
 * - 质量戳：GOOD 绿 / BAD 红 / UNCERTAIN 黄
 * - 支持按装置/单元、测点类型、参数类型、位号、关联状态筛选
 * - 单条编辑 Modal（名称/测点类型/量程/单位/参数类型/原始ID）
 * - 删除二次确认，已关联测点不允许删除
 * - 详情 Drawer 展示完整信息
 * - 导入/导出：Excel 批量导入导出
 * - RBAC: ADMIN/IC_ENGINEER 可写，PE_ENGINEER 只读
 */
import type { TagApi } from '#/api/tag';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Table,
  Tag,
  Upload,
} from 'ant-design-vue';

import { deleteTagApi, getTagListApi, updateTagApi } from '#/api/tag';
import { requestClient } from '#/api/request';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import QualityTag from '#/components/loop/quality-tag.vue';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'TagList' });

// List state
const loading = ref(false);
const tagList = ref<TagApi.TagItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  measureType: undefined as TagApi.MeasureType | undefined,
  tagType: undefined as TagApi.TagType | undefined,
  isLinked: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 100,
});

// Plant nodes for filter (flattened)
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

/** 工厂节点层级选项（显示完整路径：工厂A / 装置B / 单元C） */
const plantNodeOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const node of plantNodes.value) {
    nodeMap.set(node.id, node);
  }
  return plantNodes.value.map((node) => {
    const path: string[] = [];
    let current: PlantNodeApi.PlantNode | undefined = node;
    while (current) {
      path.unshift(current.name);
      current = current.parentId
        ? nodeMap.get(current.parentId)
        : undefined;
    }
    return {
      label: path.join(' / '),
      value: node.id,
    };
  });
});

/** 测点类型映射（label + color） */
const MEASURE_TYPE_MAP: Record<string, { label: string; color: string }> = {
  TEMPERATURE: { label: '温度', color: 'red' },
  PRESSURE: { label: '压力', color: 'blue' },
  LEVEL: { label: '液位', color: 'green' },
  FLOW: { label: '流量', color: 'cyan' },
  ANALYSIS: { label: '分析', color: 'purple' },
  SPEED: { label: '速度', color: 'orange' },
  OTHER: { label: '其他', color: 'default' },
};

const measureTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(MEASURE_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

/** 参数类型映射（label + color） */
const TAG_TYPE_MAP: Record<string, { label: string; color: string }> = {
  PV: { label: 'PV', color: 'blue' },
  SP: { label: 'SP', color: 'green' },
  OP: { label: 'OP', color: 'orange' },
  MODE: { label: 'MODE', color: 'purple' },
  PID_P: { label: 'PID_P', color: 'cyan' },
  PID_I: { label: 'PID_I', color: 'cyan' },
  PID_D: { label: 'PID_D', color: 'cyan' },
  OTHER: { label: 'OTHER', color: 'default' },
};

const tagTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(TAG_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

const linkedOptions = [
  { label: '全部', value: undefined },
  { label: '已关联', value: 'true' },
  { label: '未关联', value: 'false' },
];

const columns: TableColumnsType = [
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '名称',
    dataIndex: 'tagDescription',
    key: 'tagDescription',
    ellipsis: true,
    width: 160,
  },
  {
    title: '测点类型',
    dataIndex: 'measureType',
    key: 'measureType',
    width: 100,
  },
  { title: '量程下限', dataIndex: 'rangeMin', key: 'rangeMin', width: 100 },
  { title: '量程上限', dataIndex: 'rangeMax', key: 'rangeMax', width: 100 },
  { title: '实时值', dataIndex: 'currentValue', key: 'currentValue', width: 100 },
  { title: '单位', dataIndex: 'unit', key: 'unit', width: 80 },
  { title: '质量戳', dataIndex: 'quality', key: 'quality', width: 110 },
  { title: '参数类型', dataIndex: 'tagType', key: 'tagType', width: 100 },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 160 },
  { title: '原始ID', dataIndex: 'tdengineTagId', key: 'tdengineTagId', width: 160 },
  { title: '操作', key: 'action', width: 200, fixed: 'right' },
];

// Modal state
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingTag = ref<TagApi.TagItem | null>(null);
const formState = reactive({
  tagDescription: '',
  measureType: 'OTHER' as TagApi.MeasureType | undefined,
  rangeMin: undefined as number | undefined,
  rangeMax: undefined as number | undefined,
  unit: '',
  tagType: 'OTHER' as TagApi.TagType | undefined,
  tdengineTagId: '',
});

// Detail Drawer state
const detailVisible = ref(false);
const detailLoading = ref(false);
const detailData = ref<TagApi.TagItem | null>(null);

// ===== 导入导出 state =====
const importing = ref(false);
const exporting = ref(false);

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载测点列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getTagListApi({
      plantNodeId: query.plantNodeId,
      measureType: query.measureType,
      tagType: query.tagType,
      isLinked:
        query.isLinked === undefined ? undefined : query.isLinked === 'true',
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    tagList.value = data.items;
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
  query.pageSize = pagination.pageSize || 100;
  loadList();
}

/** 打开编辑 Modal */
function handleEdit(record: TagApi.TagItem) {
  editingTag.value = record;
  formState.tagDescription = record.tagDescription ?? '';
  formState.measureType = record.measureType ?? 'OTHER';
  formState.rangeMin = record.rangeMin ?? undefined;
  formState.rangeMax = record.rangeMax ?? undefined;
  formState.unit = record.unit ?? '';
  formState.tagType = record.tagType ?? 'OTHER';
  formState.tdengineTagId = record.tdengineTagId ?? '';
  modalVisible.value = true;
}

/** 提交编辑表单 */
async function handleSubmit() {
  if (!editingTag.value) return;
  modalLoading.value = true;
  try {
    await updateTagApi(editingTag.value.id, {
      tagDescription: formState.tagDescription,
      measureType: formState.measureType,
      rangeMin: formState.rangeMin ?? null,
      rangeMax: formState.rangeMax ?? null,
      unit: formState.unit,
      tagType: formState.tagType,
      tdengineTagId: formState.tdengineTagId,
    });
    message.success('测点更新成功');
    modalVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

/** 删除测点 */
async function handleDelete(record: TagApi.TagItem) {
  if (record.isLinked) {
    message.warning('该测点已关联回路，不允许删除');
    return;
  }
  try {
    await deleteTagApi(record.id);
    message.success('测点删除成功');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 打开详情 Drawer */
async function handleViewDetail(record: TagApi.TagItem) {
  detailVisible.value = true;
  detailLoading.value = true;
  detailData.value = record;
  try {
    const { getTagDetailApi } = await import('#/api/tag');
    detailData.value = await getTagDetailApi(record.id);
  } catch {
    // 错误已由拦截器处理，保留列表数据展示
  } finally {
    detailLoading.value = false;
  }
}

function formatTime(t?: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

// ===== 导入导出方法 =====

/** 导出测点清单 Excel */
async function handleExport() {
  exporting.value = true;
  try {
    const blob = await requestClient.download<Blob>('/tags/export', {
      params: {
        plantNodeId: query.plantNodeId,
        measureType: query.measureType,
        tagType: query.tagType,
        isLinked:
          query.isLinked === undefined ? undefined : query.isLinked === 'true',
        keyword: query.keyword || undefined,
      },
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `测点清单_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success('导出成功');
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

/** 导入测点清单 Excel（Upload beforeUpload 钩子） */
function handleImportBeforeUpload(file: File): boolean {
  importing.value = true;
  const formData = new FormData();
  formData.append('file', file);
  requestClient
    .post('/tags/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then(() => {
      message.success('导入成功');
      loadList();
    })
    .catch(() => {
      // 错误已由拦截器处理
    })
    .finally(() => {
      importing.value = false;
    });
  // 返回 false 阻止 Upload 组件默认上传行为
  return false;
}

const uploadAccept = '.xlsx,.xls';

const uploadProps: UploadProps = {
  accept: uploadAccept,
  showUploadList: false,
  beforeUpload: handleImportBeforeUpload as UploadProps['beforeUpload'],
};

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page title="测点清单">
    <Card>
      <!-- 筛选区 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.plantNodeId"
          placeholder="按装置/单元筛选"
          style="width: 260px"
          allow-clear
          show-search
          :options="plantNodeOptions"
          :filter-option="
            (input: string, option: any) => option.label.includes(input)
          "
          @change="handleSearch"
        />
        <Select
          v-model:value="query.measureType"
          placeholder="按测点类型筛选"
          style="width: 160px"
          allow-clear
          :options="measureTypeOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.tagType"
          placeholder="按参数类型筛选"
          style="width: 160px"
          allow-clear
          :options="tagTypeOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.isLinked"
          placeholder="按关联状态筛选"
          style="width: 140px"
          allow-clear
          :options="linkedOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="query.keyword"
          placeholder="搜索位号/名称"
          allow-clear
          style="width: 240px"
          @press-enter="handleSearch"
        />
        <Button type="primary" @click="handleSearch">查询</Button>
        <!-- 导入导出 -->
        <div class="ml-auto flex items-center gap-2">
          <Upload v-bind="uploadProps">
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              :loading="importing"
            >
              导入
            </Button>
          </Upload>
          <Button :loading="exporting" @click="handleExport">导出</Button>
        </div>
      </div>

      <Table
        :columns="columns"
        :data-source="tagList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: TagApi.TagItem) => record.id"
        :scroll="{ x: 1600 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagDescription'">
            <span v-if="record.tagDescription">{{ record.tagDescription }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'measureType'">
            <Tag
              v-if="record.measureType"
              :color="
                MEASURE_TYPE_MAP[record.measureType]?.color ?? 'default'
              "
              class="m-0"
            >
              {{ MEASURE_TYPE_MAP[record.measureType]?.label ?? '其他' }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'rangeMin'">
            <span v-if="record.rangeMin != null">{{ record.rangeMin }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'rangeMax'">
            <span v-if="record.rangeMax != null">{{ record.rangeMax }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'currentValue'">
            <span v-if="record.currentValue != null" class="font-medium">
              {{ record.currentValue }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'unit'">
            <span v-if="record.unit">{{ record.unit }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'quality'">
            <QualityTag :quality="record.quality" />
          </template>
          <template v-else-if="column.key === 'tagType'">
            <Tag
              :color="TAG_TYPE_MAP[record.tagType]?.color ?? 'default'"
              class="m-0"
            >
              {{ TAG_TYPE_MAP[record.tagType]?.label ?? record.tagType }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'unitName'">
            <span v-if="record.unitName">{{ record.unitName }}</span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'tdengineTagId'">
            <span v-if="record.tdengineTagId" class="text-xs text-gray-500">
              {{ record.tdengineTagId }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="handleViewDetail(record as TagApi.TagItem)"
              >
                详情
              </Button>
              <Button
                v-permission="['ADMIN', 'IC_ENGINEER']"
                type="link"
                size="small"
                @click="handleEdit(record as TagApi.TagItem)"
              >
                编辑
              </Button>
              <Popconfirm
                v-permission="['ADMIN']"
                :title="
                  (record as TagApi.TagItem).isLinked
                    ? '该测点已关联回路，不允许删除'
                    : '确认删除该测点？删除后不可恢复。'
                "
                :disabled="(record as TagApi.TagItem).isLinked"
                @confirm="handleDelete(record as TagApi.TagItem)"
              >
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  danger
                  :disabled="(record as TagApi.TagItem).isLinked"
                >
                  删除
                </Button>
              </Popconfirm>
            </div>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      title="编辑测点"
      :confirm-loading="modalLoading"
      width="640px"
      @ok="handleSubmit"
    >
      <Form :model="formState" layout="vertical" class="pt-4">
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="tagDescription" label="名称">
            <Input
              v-model:value="formState.tagDescription"
              placeholder="请输入测点名称"
            />
          </FormItem>
          <FormItem name="measureType" label="测点类型">
            <Select
              v-model:value="formState.measureType"
              placeholder="请选择测点类型"
              :options="
                Object.entries(MEASURE_TYPE_MAP).map(([value, { label }]) => ({
                  label,
                  value,
                }))
              "
            />
          </FormItem>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="rangeMin" label="量程下限">
            <InputNumber
              v-model:value="formState.rangeMin"
              class="w-full"
              placeholder="请输入量程下限"
            />
          </FormItem>
          <FormItem name="rangeMax" label="量程上限">
            <InputNumber
              v-model:value="formState.rangeMax"
              class="w-full"
              placeholder="请输入量程上限"
            />
          </FormItem>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <FormItem name="unit" label="单位">
            <Input
              v-model:value="formState.unit"
              placeholder="例如：°C、MPa、%"
            />
          </FormItem>
          <FormItem name="tagType" label="参数类型">
            <Select
              v-model:value="formState.tagType"
              placeholder="请选择参数类型"
              :options="
                Object.entries(TAG_TYPE_MAP).map(([value, { label }]) => ({
                  label,
                  value,
                }))
              "
            />
          </FormItem>
        </div>
        <FormItem name="tdengineTagId" label="原始ID">
          <Input
            v-model:value="formState.tdengineTagId"
            placeholder="请输入 TDengine 原始 ID"
          />
        </FormItem>
      </Form>
    </Modal>

    <!-- 详情 Drawer -->
    <Drawer
      v-model:open="detailVisible"
      title="测点详情"
      placement="right"
      width="560px"
      :loading="detailLoading"
    >
      <Descriptions
        v-if="detailData"
        :column="1"
        bordered
        size="small"
        class="mb-4"
      >
        <DescriptionsItem label="位号">
          {{ detailData.tagName }}
        </DescriptionsItem>
        <DescriptionsItem label="名称">
          {{ detailData.tagDescription || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="测点类型">
          <Tag
            v-if="detailData.measureType"
            :color="
              MEASURE_TYPE_MAP[detailData.measureType]?.color ?? 'default'
            "
            class="m-0"
          >
            {{ MEASURE_TYPE_MAP[detailData.measureType]?.label ?? '其他' }}
          </Tag>
          <span v-else class="text-gray-400">—</span>
        </DescriptionsItem>
        <DescriptionsItem label="量程下限">
          {{ detailData.rangeMin ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="量程上限">
          {{ detailData.rangeMax ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="实时值">
          {{ detailData.currentValue ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="单位">
          {{ detailData.unit || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="质量戳">
          <QualityTag :quality="detailData.quality" />
        </DescriptionsItem>
        <DescriptionsItem label="参数类型">
          <Tag
            :color="TAG_TYPE_MAP[detailData.tagType]?.color ?? 'default'"
            class="m-0"
          >
            {{ TAG_TYPE_MAP[detailData.tagType]?.label ?? detailData.tagType }}
          </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="所属单元">
          {{ detailData.unitName || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="原始ID">
          {{ detailData.tdengineTagId || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="关联状态">
          <Tag :color="detailData.isLinked ? 'green' : 'default'" class="m-0">
            {{ detailData.isLinked ? '已关联' : '未关联' }}
          </Tag>
        </DescriptionsItem>
        <DescriptionsItem label="关联回路">
          <span v-if="detailData.loopTagName">{{ detailData.loopTagName }}</span>
          <span v-else class="text-gray-400">—</span>
        </DescriptionsItem>
        <DescriptionsItem label="回路描述">
          {{ detailData.loopDescription || '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="最后同步时间">
          {{ formatTime(detailData.lastSyncAt) }}
        </DescriptionsItem>
      </Descriptions>
    </Drawer>
  </Page>
</template>
