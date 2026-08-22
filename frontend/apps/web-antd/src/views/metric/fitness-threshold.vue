<script lang="tsx" setup>
/**
 * 回路适用性阈值配置 Tab（IA 优化 P2：L0~L4 预诊断）
 *
 * 视图结构：按 L1 / L2 / L3 分为 3 个分组卡片（分组展示 + 对齐 P2 分层语义），
 * 每组若干行：标签 Tag + 中文名 + 说明 Tooltip + NumberInput（带单位）+ 默认值回显。
 *
 * 交互：
 * - 刷新：调用 refresh() 重新拉取 GET /configs/fitness-thresholds
 * - 保存：仅 ADMIN；提交变化项差值，或勾选“全部重置为默认”一次性回落 algorithm 默认值
 * - 二次确认 Modal（remark 备注栏）
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import {
  Card,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getFitnessThresholdsApi,
  saveFitnessThresholdsApi,
} from '#/api/metric';
import { ClpmToolbarButton } from '#/components/clpm';

defineOptions({ name: 'MetricFitnessThreshold' });

defineExpose({ refresh });

const loading = ref(false);
const saving = ref(false);
const schema = ref<MetricApi.FitnessThresholdSchema>({ items: [] });

const LEVEL_META: Record<
  MetricApi.FitnessThresholdItem['level'],
  { color: string; hint: string; label: string; }
> = {
  L1: {
    label: 'L1 仅可监视',
    color: 'default',
    hint: '手动主导 / 自控率极低 → 仅监视，不进入 KPI / 诊断 / 整定',
  },
  L2: {
    label: 'L2 条件异常',
    color: 'warning',
    hint: 'OP 饱和 / SP-PV 持续偏离 → 可评估可诊断，诊断发起需显示条件异常横幅',
  },
  L3: {
    label: 'L3 待激励',
    color: 'processing',
    hint: '无有效激励 / 响应极弱 → 整定入口禁用（需要 L4 可优化）',
  },
};

const LEVEL_ORDER: MetricApi.FitnessThresholdItem['level'][] = [
  'L1',
  'L2',
  'L3',
];

/** 编辑态：{key: currentEditValue} */
const editState = reactive<Record<string, number>>({});
/** 重置默认 flag */
const resetAll = ref(false);
/** 备注（写入审计日志） */
const remark = ref<string>('');
/** 保存 Modal 显示态 */
const showSaveModal = ref(false);

/**
 * 按 level 分组后的渲染用数据
 */
const groups = computed<
  Array<{
    items: MetricApi.FitnessThresholdItem[];
    level: MetricApi.FitnessThresholdItem['level'];
  }>
>(() => {
  const map = new Map<string, MetricApi.FitnessThresholdItem[]>();
  for (const item of schema.value.items ?? []) {
    const arr = map.get(item.level) ?? [];
    arr.push(item);
    map.set(item.level, arr);
  }
  return LEVEL_ORDER.map((lv) => ({
    level: lv,
    items: map.get(lv) ?? [],
  }));
});

async function loadList() {
  loading.value = true;
  try {
    const data = await getFitnessThresholdsApi();
    schema.value = data;
    for (const item of data.items ?? []) {
      editState[item.key] = item.value;
    }
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  await loadList();
}

/** 某键的当前值是否已修改（用于高亮变化条目） */
function isModified(item: MetricApi.FitnessThresholdItem): boolean {
  return editState[item.key] !== item.value;
}

/** 生成 diff items：与服务器原始 value 对比（浮点 1e-6 容差） */
function buildDiffItems(): MetricApi.FitnessThresholdSaveItem[] {
  const out: MetricApi.FitnessThresholdSaveItem[] = [];
  for (const item of schema.value.items ?? []) {
    const cur = editState[item.key];
    if (cur == null) continue;
    if (Math.abs(cur - item.value) > 1e-9) {
      out.push({ key: item.key, value: cur });
    }
  }
  return out;
}

function handleOpenSave() {
  if (resetAll.value) {
    showSaveModal.value = true;
    return;
  }
  const diff = buildDiffItems();
  if (diff.length === 0) {
    message.info('暂无修改项，无需保存');
    return;
  }
  showSaveModal.value = true;
}

async function handleConfirmSave() {
  saving.value = true;
  try {
    const payload: MetricApi.FitnessThresholdSaveRequest = resetAll.value
      ? { resetAll: true, remark: remark.value || undefined }
      : {
          items: buildDiffItems(),
          remark: remark.value || undefined,
        };
    const view = await saveFitnessThresholdsApi(payload);
    schema.value = view;
    for (const item of view.items ?? []) {
      editState[item.key] = item.value;
    }
    resetAll.value = false;
    remark.value = '';
    showSaveModal.value = false;
    message.success(
      resetAll.value ? '已重置为默认值' : `已保存 ${payload.items?.length ?? 0} 项阈值`,
    );
  } finally {
    saving.value = false;
  }
}

function handleResetEditState() {
  for (const item of schema.value.items ?? []) {
    editState[item.key] = item.value;
  }
  resetAll.value = false;
  message.info('已撤销未保存的修改');
}

function formatUpdatedInfo() {
  const s = schema.value;
  if (!s?.updatedAt) return '尚未保存，当前使用默认阈值';
  const at = s.updatedAt.replace('T', ' ').replace('Z', ' UTC');
  return `最近更新：${at}${s.updatedBy ? `  by ${s.updatedBy}` : ''}`;
}

const columns: TableColumnsType<MetricApi.FitnessThresholdItem> = [
  {
    title: '标签',
    dataIndex: 'tag',
    key: 'tag',
    width: 180,
    customRender: ({ record }) => {
      return (
        <Tag color="geekblue" style={{ fontFamily: 'monospace' }}>
          {record.tag}
        </Tag>
      );
    },
  },
  {
    title: '阈值名称',
    dataIndex: 'label',
    key: 'label',
    width: 200,
    customRender: ({ record }) => (
      <Space>
        <span>{record.label}</span>
        {record.description ? (
          <Tooltip placement="right" title={record.description}>
            <IconifyIcon height={14} icon="ant-design:question-circle-outlined" width={14} />
          </Tooltip>
        ) : null}
      </Space>
    ),
  },
  {
    title: '当前值',
    dataIndex: 'value',
    key: 'value',
    width: 280,
    customRender: ({ record }) => {
      const cur = editState[record.key] ?? record.value;
      const modified = isModified(record);
      return (
        <Space>
          <InputNumber
            disabled={resetAll.value}
            max={record.maxValue ?? undefined}
            min={record.minValue ?? undefined}
            onChange={(v) => {
              if (v != null) editState[record.key] = Number(v);
            }}
            precision={record.maxValue != null && record.maxValue <= 1 ? 4 : 2}
            step={record.maxValue != null && record.maxValue <= 1 ? 0.01 : 0.1}
            style={{ width: 180 }}
            value={cur}
          />
          <span style={{ color: 'var(--color-neutral-500)', width: 56 }}>
            {record.unit ?? ''}
          </span>
          {modified ? (
            <Tag color="blue" style={{ marginLeft: 8 }}>
              已修改
            </Tag>
          ) : null}
        </Space>
      );
    },
  },
  {
    title: '默认值',
    key: 'default',
    width: 130,
    customRender: ({ record }) => {
      const dv = record.defaultValue;
      return dv == null
        ? '-'
        : `${dv}${record.unit && record.unit !== '无量纲' ? record.unit : ''}`;
    },
  },
  {
    title: '范围',
    key: 'range',
    width: 170,
    customRender: ({ record }) => {
      if (record.minValue == null && record.maxValue == null) return '-';
      return `[${record.minValue ?? '-∞'}, ${record.maxValue ?? '+∞'}] ${record.unit ?? ''}`;
    },
  },
];

onMounted(() => {
  loadList().catch(() => {
    /* handled by global request layer */
  });
});
</script>

<template>
  <div class="fitness-threshold-container">
    <div
      style="
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
      "
    >
      <div style="display: flex; align-items: center; gap: 12px">
        <Tooltip
          title="适用性分层规则：L0=数据不足复用数据门禁；L1=仅可监视（手动主导/自控率低）；L2=条件异常（OP饱和/SP-PV偏离）；L3=待激励（无激励/弱响应）；L4=可优化。阈值调整后下轮 KPI 计算立即生效，无需重启。"
          placement="right"
        >
          <IconifyIcon icon="ant-design:question-circle-outlined" :width="16" :height="16" />
        </Tooltip>
        <span style="color: var(--color-neutral-500); font-size: 13px">
          {{ formatUpdatedInfo() }}
        </span>
      </div>
      <Space>
        <ClpmToolbarButton label="刷新" icon="lucide:refresh-cw" @click="refresh" />
        <ClpmToolbarButton
          label="撤销修改"
          icon="lucide:undo-2"
          @click="handleResetEditState"
        />
        <ClpmToolbarButton
          label="保存..."
          icon="lucide:save"
          type="primary"
          @click="handleOpenSave"
        />
      </Space>
    </div>

    <Space direction="vertical" size="middle" style="width: 100%">
      <Card
        v-for="g in groups"
        :key="g.level"
        :title="LEVEL_META[g.level].label"
        size="small"
        :bordered="true"
      >
        <template #extra>
          <Tag :color="LEVEL_META[g.level].color" :bordered="false">
            {{ LEVEL_META[g.level].hint }}
          </Tag>
        </template>
        <Table
          :columns="columns"
          :data-source="g.items"
          :loading="loading"
          :pagination="false"
          size="small"
          :row-key="(r: any) => r.key"
        />
      </Card>
    </Space>

    <Modal
      v-model:open="showSaveModal"
      :title="resetAll ? '重置适用性阈值为默认值' : '保存适用性阈值'"
      :confirm-loading="saving"
      @ok="handleConfirmSave"
      ok-text="确认"
      cancel-text="取消"
    >
      <Space direction="vertical" style="width: 100%">
        <div v-if="resetAll">
          将删除 <code>sys_config</code> 表中全部
          <code>fitness.*</code> 覆盖项，回退为 algorithm 默认阈值
          （见下条）。
        </div>
        <div v-else>
          本次提交变化项：
          <Tag color="blue" style="marginLeft: 6px">
            {{ buildDiffItems().length }} 项
          </Tag>
          <div
            style="
              margin-top: 8px;
              padding: 8px;
              background: var(--color-neutral-100);
              borderRadius: 6px;
              maxHeight: 180px;
              overflow: auto;
            "
          >
            <div
              v-for="d in buildDiffItems()"
              :key="d.key"
              style="fontSize: 12px; marginBottom: 4px"
            >
              <span style="fontFamily: monospace; color: var(--color-blue-700)">
                {{ d.key }}
              </span>
              <span style="color: var(--color-neutral-500)">
                &nbsp;→&nbsp;{{ d.value }}
              </span>
            </div>
            <div
              v-if="buildDiffItems().length === 0"
              style="fontSize: 12px; color: var(--color-neutral-500)"
            >
              无修改项（勾选「重置为默认值」或调整阈值后再保存）
            </div>
          </div>
        </div>
        <Form layout="vertical" style="marginTop: 4px">
          <Form.Item label="变更备注（写入审计日志，选填）">
            <Input
              v-model:value="remark"
              placeholder="例如：批量调紧 OP 饱和阈值至 25%（100 回路 72h 验证发现误报偏多）"
              :maxlength="500"
              show-count
              allow-clear
            />
          </Form.Item>
          <Form.Item label="重置选项">
            <Space>
              <input
                id="fitness-reset-all"
                v-model="resetAll"
                type="checkbox"
              />
              <label for="fitness-reset-all">全部重置为默认值</label>
            </Space>
          </Form.Item>
        </Form>
      </Space>
    </Modal>
  </div>
</template>
