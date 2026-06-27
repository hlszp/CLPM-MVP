<script lang="ts" setup>
/**
 * 回路类型权重配置页（FE-10）
 *
 * 对齐 PRD §4.3 + IDS v3.2 §2.3
 * - 表格：4 种类型（STABLE/SLOW/FAST/LOGIC）
 * - 列：类型名称、weightA、weightF、weightS、描述
 * - 行内编辑
 * - 保存调用 PUT /api/v1/config/loop-type-weights/{loop_type}
 * - 仅 ADMIN 可编辑
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { ControlType, MetricApi } from '#/api/metric';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Input,
  InputNumber,
  message,
  Modal,
  Table,
  Tag,
} from 'ant-design-vue';

import ConfigTabs from '#/components/metric/config-tabs.vue';
import { getLoopTypeWeightsApi, updateLoopTypeWeightApi } from '#/api/metric';

defineOptions({ name: 'MetricTypeWeight' });

const loading = ref(false);
const saving = ref<Record<string, boolean>>({});
const list = ref<MetricApi.LoopTypeWeightItem[]>([]);

const CONTROL_TYPE_MAP: Record<
  ControlType,
  { color: string; desc: string; label: string }
> = {
  STABLE: { label: '稳定型', color: 'blue', desc: '温度、液位等慢过程回路' },
  SLOW: { label: '慢速型', color: 'cyan', desc: '缓慢响应的回路' },
  FAST: { label: '快速型', color: 'orange', desc: '流量、压力等快过程回路' },
  LOGIC: { label: '逻辑型', color: 'purple', desc: '开关/逻辑控制回路' },
};

/** 编辑态：以 loopType 为 key 存储编辑中的值 */
const editState = reactive<
  Record<
    string,
    { description: string; weightA: number; weightF: number; weightS: number }
  >
>({});

/** 获取编辑态（保证非 undefined，用于模板 v-model） */
function editStateOf(loopType: string): {
  description: string;
  weightA: number;
  weightF: number;
  weightS: number;
} {
  if (!editState[loopType]) {
    editState[loopType] = {
      weightA: 0,
      weightF: 0,
      weightS: 0,
      description: '',
    };
  }
  return (
    editState[loopType] ?? {
      weightA: 0,
      weightF: 0,
      weightS: 0,
      description: '',
    }
  );
}

const columns: TableColumnsType = [
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 120 },
  {
    title: 'weightA（自动模式率）',
    dataIndex: 'weightA',
    key: 'weightA',
    width: 180,
  },
  {
    title: 'weightF（快速率）',
    dataIndex: 'weightF',
    key: 'weightF',
    width: 160,
  },
  {
    title: 'weightS（稳定率）',
    dataIndex: 'weightS',
    key: 'weightS',
    width: 160,
  },
  { title: '描述', dataIndex: 'description', key: 'description' },
  { title: '操作', key: 'action', width: 120, fixed: 'right', align: 'center' },
];

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopTypeWeightsApi();
    list.value = data.items ?? [];
    // 同步编辑态
    for (const item of list.value) {
      editState[item.loopType] = {
        weightA: item.weightA,
        weightF: item.weightF,
        weightS: item.weightS,
        description: item.description ?? '',
      };
    }
    // 补全 4 种类型（后端可能未返回全部）
    const types: ControlType[] = ['STABLE', 'SLOW', 'FAST', 'LOGIC'];
    for (const t of types) {
      if (!list.value.some((it) => it.loopType === t)) {
        const placeholder: MetricApi.LoopTypeWeightItem = {
          loopType: t,
          loopTypeName: CONTROL_TYPE_MAP[t].label,
          weightA: 0,
          weightF: 0,
          weightS: 0,
          description: CONTROL_TYPE_MAP[t].desc,
        };
        list.value.push(placeholder);
        editState[t] = {
          weightA: 0,
          weightF: 0,
          weightS: 0,
          description: CONTROL_TYPE_MAP[t].desc,
        };
      }
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 保存单行 */
function handleSave(item: MetricApi.LoopTypeWeightItem) {
  const state = editState[item.loopType];
  if (!state) return;
  Modal.confirm({
    title: '确认变更类型权重',
    content: `即将更新「${CONTROL_TYPE_MAP[item.loopType].label}」的权重配置，保存后立即生效。是否继续？`,
    okText: '确认保存',
    cancelText: '取消',
    onOk: () => doSave(item.loopType),
  });
}

async function doSave(loopType: ControlType) {
  const state = editState[loopType];
  if (!state) return;
  saving.value[loopType] = true;
  try {
    await updateLoopTypeWeightApi(loopType, {
      weightA: state.weightA,
      weightF: state.weightF,
      weightS: state.weightS,
      description: state.description,
    });
    message.success('类型权重更新成功');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value[loopType] = false;
  }
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page title="回路类型权重配置">
    <ConfigTabs />
    <Card>
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          配置 4 种回路类型（STABLE/SLOW/FAST/LOGIC）的评分权重：
          weightA（自动模式率）、weightF（快速率）、weightS（稳定率）。
          不同类型在综合评分中各 KPI 的权重不同。
        </p>
        <Button :loading="loading" @click="loadList">刷新</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="list"
        :loading="loading"
        :pagination="false"
        :row-key="(record: MetricApi.LoopTypeWeightItem) => record.loopType"
        :scroll="{ x: 900 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'loopType'">
            <Tag
              :color="
                CONTROL_TYPE_MAP[record.loopType as ControlType]?.color ??
                'default'
              "
            >
              {{
                CONTROL_TYPE_MAP[record.loopType as ControlType]?.label ??
                record.loopType
              }}
            </Tag>
            <div class="mt-1 text-xs text-gray-400">{{ record.loopType }}</div>
          </template>
          <template v-else-if="column.key === 'weightA'">
            <InputNumber
              v-model:value="editStateOf(record.loopType).weightA"
              :min="0"
              :max="100"
              size="small"
              addon-after="%"
              style="width: 120px"
            />
          </template>
          <template v-else-if="column.key === 'weightF'">
            <InputNumber
              v-model:value="editStateOf(record.loopType).weightF"
              :min="0"
              :max="100"
              size="small"
              addon-after="%"
              style="width: 120px"
            />
          </template>
          <template v-else-if="column.key === 'weightS'">
            <InputNumber
              v-model:value="editStateOf(record.loopType).weightS"
              :min="0"
              :max="100"
              size="small"
              addon-after="%"
              style="width: 120px"
            />
          </template>
          <template v-else-if="column.key === 'description'">
            <Input
              v-model:value="editStateOf(record.loopType).description"
              placeholder="描述"
              size="small"
            />
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              :loading="saving[record.loopType]"
              @click="handleSave(record as MetricApi.LoopTypeWeightItem)"
            >
              保存
            </Button>
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
