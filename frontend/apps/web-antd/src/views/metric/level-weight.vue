<script lang="ts" setup>
/**
 * 回路级别权重配置页（FE-11）
 *
 * 对齐 PRD §4.3 + IDS v3.2 §2.3
 * - 表格：3 个级别（1/2/3）
 * - 列：级别名称、weight、描述
 * - 行内编辑
 * - 保存调用 PUT /api/v1/config/loop-level-weights/{level}
 * - 仅 ADMIN 可编辑
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

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
import { getLoopLevelWeightsApi, updateLoopLevelWeightApi } from '#/api/metric';

defineOptions({ name: 'MetricLevelWeight' });

const loading = ref(false);
const saving = ref<Record<number, boolean>>({});
const list = ref<MetricApi.LoopLevelWeightItem[]>([]);

const LEVEL_MAP: Record<
  number,
  { color: string; desc: string; label: string }
> = {
  1: { label: '1 级', color: 'red', desc: '关键回路（影响生产安全/质量）' },
  2: { label: '2 级', color: 'orange', desc: '重要回路（影响装置稳定）' },
  3: { label: '3 级', color: 'blue', desc: '一般回路（辅助/常规）' },
};

const editState = reactive<
  Record<number, { description: string; weight: number }>
>({});

/** 获取编辑态（保证非 undefined，用于模板 v-model） */
function editStateOf(level: number): { description: string; weight: number } {
  if (!editState[level]) {
    editState[level] = { weight: 0, description: '' };
  }
  return editState[level] ?? { weight: 0, description: '' };
}

const columns: TableColumnsType = [
  { title: '级别', dataIndex: 'level', key: 'level', width: 120 },
  { title: 'weight（权重）', dataIndex: 'weight', key: 'weight', width: 180 },
  { title: '描述', dataIndex: 'description', key: 'description' },
  { title: '操作', key: 'action', width: 120, fixed: 'right', align: 'center' },
];

/** 加载列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopLevelWeightsApi();
    list.value = data.items ?? [];
    for (const item of list.value) {
      editState[item.level] = {
        weight: item.weight,
        description: item.description ?? '',
      };
    }
    // 补全 3 个级别
    const levels: MetricApi.LoopLevel[] = [1, 2, 3];
    for (const lv of levels) {
      if (!list.value.some((it) => it.level === lv)) {
        list.value.push({
          level: lv,
          levelName: LEVEL_MAP[lv]?.label ?? `${lv} 级`,
          weight: 0,
          description: LEVEL_MAP[lv]?.desc ?? '',
        });
        editState[lv] = {
          weight: 0,
          description: LEVEL_MAP[lv]?.desc ?? '',
        };
      }
    }
    // 按 level 排序
    list.value.sort((a, b) => a.level - b.level);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 保存单行 */
function handleSave(item: MetricApi.LoopLevelWeightItem) {
  const state = editState[item.level];
  if (!state) return;
  Modal.confirm({
    title: '确认变更级别权重',
    content: `即将更新「${LEVEL_MAP[item.level]?.label}」的权重配置，保存后立即生效。是否继续？`,
    okText: '确认保存',
    cancelText: '取消',
    onOk: () => doSave(item.level),
  });
}

async function doSave(level: MetricApi.LoopLevel) {
  const state = editState[level];
  if (!state) return;
  saving.value[level] = true;
  try {
    await updateLoopLevelWeightApi(level, {
      weight: state.weight,
      description: state.description,
    });
    message.success('级别权重更新成功');
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value[level] = false;
  }
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <Page title="回路级别权重配置">
    <ConfigTabs />
    <Card>
      <div class="mb-4 flex items-center justify-between">
        <p class="text-sm text-gray-500">
          配置 3 个回路级别（1/2/3）的评分权重。级别越高，对综合评分的影响越大。
          用于在装置/工厂聚合评分时按级别加权。
        </p>
        <Button :loading="loading" @click="loadList">刷新</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="list"
        :loading="loading"
        :pagination="false"
        :row-key="(record: MetricApi.LoopLevelWeightItem) => record.level"
        :scroll="{ x: 700 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'level'">
            <Tag :color="LEVEL_MAP[record.level as number]?.color ?? 'default'">
              {{ LEVEL_MAP[record.level as number]?.label ?? record.level }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'weight'">
            <InputNumber
              v-model:value="editStateOf(record.level).weight"
              :min="0"
              :max="100"
              size="small"
              addon-after="%"
              style="width: 140px"
            />
          </template>
          <template v-else-if="column.key === 'description'">
            <Input
              v-model:value="editStateOf(record.level).description"
              placeholder="描述"
              size="small"
            />
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              v-permission="['ADMIN']"
              type="link"
              size="small"
              :loading="saving[record.level]"
              @click="handleSave(record as MetricApi.LoopLevelWeightItem)"
            >
              保存
            </Button>
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
