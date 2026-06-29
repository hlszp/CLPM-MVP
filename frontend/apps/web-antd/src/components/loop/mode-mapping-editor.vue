<script lang="ts" setup>
/**
 * 投用定义配置组件（FE-02）
 *
 * 用于回路编辑抽屉的「投用定义」Tab：
 * - 表格：MODE 值、控制模式（AUTO/CAS/REMOTE/APC/MANUAL）、是否自动、是否有效、备注
 * - 支持增删行
 * - 保存时调用 PUT /api/v1/loops/{id}/mode-mapping
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';

import { ref, watch } from 'vue';

import {
  Button,
  Input,
  message,
  Popconfirm,
  Select,
  Switch,
  Table,
} from 'ant-design-vue';

import { getLoopModeMappingApi, updateLoopModeMappingApi } from '#/api/loop';

defineOptions({ name: 'ModeMappingEditor' });

const props = defineProps<{
  /** 回路 ID */
  loopId: string;
  /** 是否只读（无权限时） */
  readonly?: boolean;
}>();

const emit = defineEmits<{
  (e: 'saved'): void;
}>();

const loading = ref(false);
const saving = ref(false);
const items = ref<LoopApi.ModeMappingItem[]>([]);

/** 控制模式选项 */
const controlModeOptions: {
  label: string;
  value: LoopApi.ModeMappingControlMode;
}[] = [
  { label: 'AUTO（自动）', value: 'AUTO' },
  { label: 'CAS（串级）', value: 'CAS' },
  { label: 'REMOTE（远程）', value: 'REMOTE' },
  { label: 'APC（先进控制）', value: 'APC' },
  { label: 'MANUAL（手动）', value: 'MANUAL' },
];

const controlModeLabel: Record<LoopApi.ModeMappingControlMode, string> = {
  APC: 'APC',
  AUTO: 'AUTO',
  CAS: 'CAS',
  MANUAL: 'MANUAL',
  REMOTE: 'REMOTE',
};

const columns: TableColumnsType = [
  { title: 'MODE 值', dataIndex: 'modeValue', key: 'modeValue', width: 140 },
  {
    title: '控制模式',
    dataIndex: 'controlMode',
    key: 'controlMode',
    width: 160,
  },
  {
    title: '是否自动',
    dataIndex: 'isAuto',
    key: 'isAuto',
    width: 100,
    align: 'center',
  },
  {
    title: '是否有效',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 100,
    align: 'center',
  },
  { title: '备注', dataIndex: 'remark', key: 'remark' },
  { title: '操作', key: 'action', width: 80, fixed: 'right', align: 'center' },
];

/** 加载投用定义 */
async function load() {
  if (!props.loopId) return;
  loading.value = true;
  try {
    const data = await getLoopModeMappingApi(props.loopId);
    items.value = (data.items ?? []).map((it) => ({ ...it }));
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 新增一行 */
function handleAdd() {
  items.value.push({
    modeValue: '',
    controlMode: 'MANUAL',
    isAuto: false,
    isEnabled: true,
    remark: '',
  });
}

/** 删除一行 */
function handleDelete(index: number) {
  items.value.splice(index, 1);
}

/** 保存 */
async function handleSave() {
  // 校验：MODE 值不能为空且不能重复
  const seen = new Set<string>();
  for (const it of items.value) {
    const v = (it.modeValue ?? '').trim();
    if (!v) {
      message.warning('存在 MODE 值为空的行，请补全或删除');
      return;
    }
    if (seen.has(v)) {
      message.warning(`MODE 值「${v}」重复，请检查`);
      return;
    }
    seen.add(v);
  }

  saving.value = true;
  try {
    await updateLoopModeMappingApi(props.loopId, {
      items: items.value.map((it) => ({
        modeValue: it.modeValue.trim(),
        controlMode: it.controlMode,
        isAuto: !!it.isAuto,
        isEnabled: !!it.isEnabled,
        remark: it.remark || undefined,
      })),
    });
    message.success('投用定义保存成功');
    emit('saved');
    await load();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

watch(
  () => props.loopId,
  (val) => {
    if (val) load();
    else items.value = [];
  },
  { immediate: true },
);
</script>

<template>
  <div class="mode-mapping-editor">
    <div class="mb-3 flex items-center justify-between">
      <div class="text-sm text-gray-500">
        配置 DCS MODE 原始值到控制模式的映射，用于实时自控率统计。
        <span class="ml-1 text-xs">
          （AUTO/CAS/REMOTE/APC 通常视为自动；MANUAL 视为手动）
        </span>
      </div>
      <div v-if="!readonly" class="flex gap-2">
        <Button size="small" @click="handleAdd">新增行</Button>
        <Button
          type="primary"
          size="small"
          :loading="saving"
          :disabled="loading"
          @click="handleSave"
        >
          保存
        </Button>
      </div>
    </div>

    <Table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="false"
      :row-key="(_record, index) => String(index)"
      size="small"
      :scroll="{ x: 720 }"
    >
      <template #bodyCell="{ column, record, index }">
        <template v-if="column.key === 'modeValue'">
          <Input
            v-model:value="record.modeValue"
            placeholder="如 1、2、AUTO"
            :disabled="readonly"
            size="small"
            style="width: 120px"
          />
        </template>
        <template v-else-if="column.key === 'controlMode'">
          <Select
            v-model:value="record.controlMode"
            :options="controlModeOptions"
            :disabled="readonly"
            size="small"
            style="width: 140px"
            @change="
              (val: any) => {
                // AUTO/CAS/REMOTE/APC 默认视为自动
                record.isAuto = val !== 'MANUAL';
              }
            "
          />
        </template>
        <template v-else-if="column.key === 'isAuto'">
          <Switch
            v-model:checked="record.isAuto"
            :disabled="readonly"
            size="small"
          />
        </template>
        <template v-else-if="column.key === 'isEnabled'">
          <Switch
            v-model:checked="record.isEnabled"
            :disabled="readonly"
            size="small"
          />
        </template>
        <template v-else-if="column.key === 'remark'">
          <Input
            v-model:value="record.remark"
            placeholder="备注"
            :disabled="readonly"
            size="small"
          />
        </template>
        <template v-else-if="column.key === 'action'">
          <Popconfirm
            v-if="!readonly"
            title="确认删除该行？"
            @confirm="handleDelete(index)"
          >
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
          <span v-else class="text-gray-400">{{
            controlModeLabel[
              record.controlMode as LoopApi.ModeMappingControlMode
            ]
          }}</span>
        </template>
      </template>
    </Table>

    <div
      v-if="!loading && items.length === 0"
      class="py-4 text-center text-gray-400"
    >
      暂无投用定义，点击「新增行」开始配置
    </div>
  </div>
</template>
