<script lang="ts" setup>
/**
 * A6 算法参数配置
 *
 * P0-B 可配置基础设施：3 指标 × 4 控制类型的算法参数覆盖管理。
 * - 列表区：按指标分组（振荡率 / 快速率 / 准确率），每个指标一张表，
 *   行=4 控制类型，列=该指标的算法参数键，并标记是否已被覆盖。
 * - 编辑区：Drawer 内按控制类型分组，可编辑数值参数、恢复默认、保存。
 * - 后端端点 /configs/algorithm-params，部分覆盖合并，未覆盖回落算法默认。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { ControlType, MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Drawer,
  InputNumber,
  message,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getAlgorithmParamsApi,
  saveMetricAlgorithmParamsApi,
} from '#/api/metric';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'SystemAlgorithmParams' });

const { themeColors } = useClpmTheme();

// ---------------------------------------------------------------------------
// 元数据
// ---------------------------------------------------------------------------

/** 控制类型元数据（STABLE/SLOW/FAST/LOGIC） */
const CONTROL_TYPE_META: Record<ControlType, { color: string; label: string }> =
  {
    STABLE: { label: '稳定型', color: '#10b981' },
    SLOW: { label: '慢速型', color: '#3b82f6' },
    FAST: { label: '快速型', color: '#f59e0b' },
    LOGIC: { label: '逻辑型', color: '#722ed1' },
  };

/** 控制类型展示顺序（对齐后端 4 控制类型） */
const CONTROL_TYPES: ControlType[] = ['STABLE', 'SLOW', 'FAST', 'LOGIC'];

interface ParamMeta {
  key: string;
  title: string;
  min: number;
  max: number;
  step: number;
  precision: number;
  /** 渲染类型：number（默认数值输入）| switch（布尔开关，存 0/1） */
  type?: 'number' | 'switch';
}

/** 指标元数据（中文名 + 参数列定义与校验边界） */
const METRIC_META: Record<string, { params: ParamMeta[] }> = {
  oscillation_rate: {
    params: [
      {
        key: 'similarity_threshold',
        title: '相似度阈值',
        min: 0,
        max: 1,
        step: 0.01,
        precision: 3,
      },
      {
        key: 'min_ratio',
        title: '最小比值',
        min: 0,
        max: 100,
        step: 0.01,
        precision: 3,
      },
      {
        key: 'max_ratio',
        title: '最大比值',
        min: 0,
        max: 1000,
        step: 0.1,
        precision: 2,
      },
    ],
  },
  fast_rate: {
    params: [
      {
        key: 'ideal_settling_ratio',
        title: '理想稳定比值',
        min: 0,
        max: 100,
        step: 0.1,
        precision: 2,
      },
      {
        key: 'settling_tolerance',
        title: '稳定容差',
        min: 0,
        max: 1,
        step: 0.01,
        precision: 3,
      },
      {
        key: 'anti_disturbance_enabled',
        title: '抗扰性分析',
        min: 0,
        max: 1,
        step: 1,
        precision: 0,
        type: 'switch',
      },
      {
        key: 'disturbance_band_sigma',
        title: '扰动带(σ)',
        min: 0.5,
        max: 5,
        step: 0.1,
        precision: 2,
      },
      {
        key: 'recovery_persistence',
        title: '恢复持续点数',
        min: 1,
        max: 20,
        step: 1,
        precision: 0,
      },
      {
        key: 'min_disturbance_duration',
        title: '最小扰动时长(s)',
        min: 0,
        max: 60,
        step: 0.5,
        precision: 1,
      },
      {
        key: 'sp_step_sigma',
        title: 'SP阶跃阈值(σ)',
        min: 1,
        max: 10,
        step: 0.5,
        precision: 1,
      },
    ],
  },
  accuracy_rate: {
    params: [
      {
        key: 'e_max_percentile',
        title: '最大误差百分位',
        min: 0,
        max: 1,
        step: 0.01,
        precision: 3,
      },
    ],
  },
};

function paramMetaOf(metricCode: string): ParamMeta[] {
  return METRIC_META[metricCode]?.params ?? [];
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const loading = ref(false);
const saving = ref(false);
const updatedAt = ref<null | string>(null);
const updatedBy = ref<null | string>(null);

/** 全部指标参数合并视图 */
const metrics = ref<MetricApi.AlgorithmParamsMetricGroup[]>([]);

/** 编辑态：metricCode -> controlType -> paramKey -> value */
const editParams = reactive<
  Record<string, Record<string, Record<string, number>>>
>({});
/** 已加载快照（变更摘要 diff 基准）：metricCode -> controlType -> paramKey -> value */
const loadedParams = reactive<
  Record<string, Record<string, Record<string, number>>>
>({});

/** 当前编辑的指标 */
const editingMetric = ref<MetricApi.AlgorithmParamsMetricGroup | null>(null);
const drawerOpen = ref(false);

// ---------------------------------------------------------------------------
// 表格列（按指标动态构建）
// ---------------------------------------------------------------------------

function buildColumns(metricCode: string): TableColumnsType {
  const paramCols = paramMetaOf(metricCode).map((p) => ({
    title: p.title,
    dataIndex: ['params', p.key],
    key: p.key,
    width: 150,
    align: 'right' as const,
  }));
  return [
    {
      title: '控制类型',
      dataIndex: 'controlType',
      key: 'controlType',
      width: 160,
      fixed: 'left',
    },
    ...paramCols,
    {
      title: '覆盖状态',
      key: 'overridden',
      width: 110,
      align: 'center' as const,
    },
  ];
}

/** 保证 items 按 CONTROL_TYPES 顺序展示，缺失控制类型用 defaults 填充 */
function metricRows(
  group: MetricApi.AlgorithmParamsMetricGroup,
): MetricApi.AlgorithmParamsControlItem[] {
  const byType = new Map((group.items ?? []).map((it) => [it.controlType, it]));
  return CONTROL_TYPES.map((ct) => {
    const found = byType.get(ct);
    if (found) return found;
    // 缺失控制类型：构造一个默认行（params 取 defaults，无覆盖）
    const defaults: Record<string, any> = {};
    for (const p of paramMetaOf(group.metricCode)) {
      defaults[p.key] = 0;
    }
    return {
      controlType: ct,
      params: { ...defaults },
      defaults: { ...defaults },
      overridden: false,
    };
  });
}

/** 列举某控制类型中相对 defaults 被覆盖的参数键 */
function overriddenKeys(
  item: MetricApi.AlgorithmParamsControlItem,
  metricCode: string,
): string[] {
  const keys: string[] = [];
  for (const p of paramMetaOf(metricCode)) {
    const cur = item.params?.[p.key];
    const def = item.defaults?.[p.key];
    if (cur !== undefined && cur !== def) keys.push(p.key);
  }
  return keys;
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

async function loadData() {
  loading.value = true;
  try {
    const data = await getAlgorithmParamsApi();
    metrics.value = data.metrics ?? [];
    updatedAt.value = data.updatedAt ?? null;
    updatedBy.value = data.updatedBy ?? null;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 编辑 Drawer
// ---------------------------------------------------------------------------

/** 当前编辑指标的 metricCode（便捷访问） */
const editingMetricCode = computed(() => editingMetric.value?.metricCode ?? '');

/** 确保编辑态嵌套对象存在并返回 */
function ensureEditRow(
  metricCode: string,
  controlType: string,
): Record<string, number> {
  let metric = editParams[metricCode];
  if (!metric) {
    metric = {};
    editParams[metricCode] = metric;
  }
  let row = metric[controlType];
  if (!row) {
    row = {};
    metric[controlType] = row;
  }
  return row;
}

/** 取某指标某控制类型某参数的默认值（用于 placeholder 与恢复） */
function defaultValue(
  group: MetricApi.AlgorithmParamsMetricGroup,
  controlType: string,
  paramKey: string,
): number | undefined {
  const item = (group.items ?? []).find((it) => it.controlType === controlType);
  return item?.defaults?.[paramKey];
}

/** 将配置值归一化为 number（兼容后端布尔默认值 false/true → 0/1）。 */
function toNum(val: unknown): number {
  if (typeof val === 'number') return val;
  if (typeof val === 'boolean') return val ? 1 : 0;
  return 0;
}

function handleOpenEdit(group: MetricApi.AlgorithmParamsMetricGroup) {
  editingMetric.value = group;
  const mc = group.metricCode;
  // 用当前生效值初始化编辑态（缺失回落 defaults）
  for (const ct of CONTROL_TYPES) {
    const item = (group.items ?? []).find((it) => it.controlType === ct);
    const row = ensureEditRow(mc, ct);
    for (const p of paramMetaOf(mc)) {
      const v = item?.params?.[p.key];
      const def = item?.defaults?.[p.key];
      row[p.key] = v !== null && v !== undefined ? toNum(v) : toNum(def);
    }
    let loaded = loadedParams[mc];
    if (!loaded) {
      loaded = {};
      loadedParams[mc] = loaded;
    }
    loaded[ct] = { ...row };
  }
  drawerOpen.value = true;
}

/** 恢复某控制类型为默认值 */
function handleResetControl(controlType: ControlType) {
  const mc = editingMetricCode.value;
  if (!mc || !editingMetric.value) return;
  const row = ensureEditRow(mc, controlType);
  for (const p of paramMetaOf(mc)) {
    const def = defaultValue(editingMetric.value, controlType, p.key);
    row[p.key] = toNum(def);
  }
}

/**
 * Switch 参数变更处理器。
 * Ant Design Vue 的 Switch @change 签名为 (checked: CheckedType, e: Event)，
 * CheckedType = boolean | string | number，故此处用联合类型承接并归一化为 0/1。
 */
function handleParamSwitch(
  controlType: string,
  paramKey: string,
  checked: boolean | number | string,
): void {
  const mc = editingMetricCode.value;
  if (!mc) return;
  ensureEditRow(mc, controlType)[paramKey] = checked === true ? 1 : 0;
}

// ---------------------------------------------------------------------------
// 变更摘要与校验
// ---------------------------------------------------------------------------

interface ChangeItem {
  label: string;
  from: string;
  to: string;
}

const changes = computed<ChangeItem[]>(() => {
  const mc = editingMetricCode.value;
  if (!mc) return [];
  const out: ChangeItem[] = [];
  for (const ct of CONTROL_TYPES) {
    const cur = editParams[mc]?.[ct] ?? {};
    const loaded = loadedParams[mc]?.[ct] ?? {};
    for (const p of paramMetaOf(mc)) {
      const c = cur[p.key];
      const l = loaded[p.key];
      if (c === undefined || l === undefined || c === l) continue;
      const fmt = (v: number) =>
        p.type === 'switch' ? (v === 1 ? '开启' : '关闭') : String(v);
      out.push({
        label: `${CONTROL_TYPE_META[ct].label}(${ct}) · ${p.title}`,
        from: fmt(l),
        to: fmt(c),
      });
    }
  }
  return out;
});

const hasChanges = computed(() => changes.value.length > 0);

const violations = computed<string[]>(() => {
  const mc = editingMetricCode.value;
  if (!mc) return [];
  const list: string[] = [];
  for (const ct of CONTROL_TYPES) {
    const row = editParams[mc]?.[ct] ?? {};
    for (const p of paramMetaOf(mc)) {
      const v = row[p.key];
      if (v === undefined || v === null) continue;
      if (v < p.min || v > p.max) {
        list.push(
          `${CONTROL_TYPE_META[ct].label}(${ct}) · ${p.title} 须在 [${p.min}, ${p.max}] 内`,
        );
      }
    }
  }
  return list;
});

const isValid = computed(() => violations.value.length === 0);

// ---------------------------------------------------------------------------
// 保存
// ---------------------------------------------------------------------------

async function handleSave() {
  const mc = editingMetricCode.value;
  if (!mc) return;
  if (!hasChanges.value) {
    message.info('没有需要保存的变更');
    return;
  }
  if (!isValid.value) {
    message.warning(`参数校验未通过：${violations.value[0]}`);
    return;
  }
  saving.value = true;
  try {
    // 提交全部 4 控制类型的当前编辑值（后端做部分覆盖合并）
    const items: MetricApi.AlgorithmParamsSaveItem[] = CONTROL_TYPES.map(
      (ct) => ({
        controlType: ct,
        params: { ...editParams[mc]?.[ct] },
      }),
    );
    await saveMetricAlgorithmParamsApi(mc, { items });
    message.success('算法参数保存成功');
    drawerOpen.value = false;
    editingMetric.value = null;
    await loadData();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

function formatTime(t?: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

onMounted(() => {
  loadData();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="算法参数配置"
      subtitle="管理 3 个核心指标的算法参数覆盖（按 4 类控制类型）。未覆盖参数回落算法默认值；保存后立即生效。"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadData"
        />
      </template>
    </ClpmPageToolbar>

    <p class="mt-3 text-sm" :style="{ color: themeColors.NEUTRAL }">
      <span v-if="updatedAt">
        最近更新：{{ updatedBy ?? '-' }} @ {{ formatTime(updatedAt) }}
      </span>
      <span v-else>暂无更新记录</span>
    </p>

    <div class="mt-4 space-y-4">
      <ClpmDataCanvas
        v-for="group in metrics"
        :key="group.metricCode"
        :loading="loading"
        :title="`${group.metricName}（${group.metricCode}）`"
      >
        <template #extra>
          <Button
            v-permission="['ADMIN']"
            type="primary"
            size="small"
            @click="handleOpenEdit(group)"
          >
            编辑
          </Button>
        </template>
        <Table
          :columns="buildColumns(group.metricCode)"
          :data-source="metricRows(group)"
          :pagination="false"
          :row-key="
            (record: MetricApi.AlgorithmParamsControlItem) => record.controlType
          "
          size="middle"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'controlType'">
              <Tag
                :color="
                  CONTROL_TYPE_META[
                    (record as MetricApi.AlgorithmParamsControlItem).controlType
                  ]?.color
                "
              >
                {{
                  CONTROL_TYPE_META[
                    (record as MetricApi.AlgorithmParamsControlItem).controlType
                  ]?.label
                }}
                ({{
                  (record as MetricApi.AlgorithmParamsControlItem).controlType
                }})
              </Tag>
            </template>
            <template
              v-else-if="
                paramMetaOf(group.metricCode).some((p) => p.key === column.key)
              "
            >
              <span
                v-if="
                  paramMetaOf(group.metricCode).find(
                    (p) => p.key === column.key,
                  )?.type === 'switch'
                "
                class="text-sm"
              >
                {{
                  (record as MetricApi.AlgorithmParamsControlItem).params?.[
                    String(column.key)
                  ] === 1
                    ? '开启'
                    : '关闭'
                }}
              </span>
              <span v-else class="font-mono">
                {{
                  (record as MetricApi.AlgorithmParamsControlItem).params?.[
                    String(column.key)
                  ] ?? '—'
                }}
              </span>
            </template>
            <template v-else-if="column.key === 'overridden'">
              <Tag
                v-if="
                  overriddenKeys(
                    record as MetricApi.AlgorithmParamsControlItem,
                    group.metricCode,
                  ).length > 0
                "
                color="orange"
              >
                已覆盖
                {{
                  overriddenKeys(
                    record as MetricApi.AlgorithmParamsControlItem,
                    group.metricCode,
                  ).length
                }}
                项
              </Tag>
              <Tag v-else color="default">默认</Tag>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>

    <!-- 编辑 Drawer -->
    <Drawer
      v-model:open="drawerOpen"
      :title="
        editingMetric
          ? `编辑算法参数 - ${editingMetric.metricName}（${editingMetric.metricCode}）`
          : '编辑算法参数'
      "
      width="640"
      :mask-closable="false"
      :destroy-on-close="true"
    >
      <div v-if="editingMetric" class="space-y-6 py-2">
        <div
          v-for="ct in CONTROL_TYPES"
          :key="ct"
          class="rounded border p-3"
          :style="{ borderColor: 'hsl(var(--border))' }"
        >
          <div class="mb-3 flex items-center justify-between">
            <div class="flex items-center gap-2">
              <Tag :color="CONTROL_TYPE_META[ct]?.color">
                {{ CONTROL_TYPE_META[ct]?.label }} ({{ ct }})
              </Tag>
            </div>
            <Button size="small" @click="handleResetControl(ct)">
              恢复默认
            </Button>
          </div>
          <div class="grid grid-cols-1 gap-3">
            <div
              v-for="p in paramMetaOf(editingMetric.metricCode)"
              :key="p.key"
              class="flex items-center justify-between gap-3"
            >
              <div class="flex flex-col">
                <span class="text-sm">{{ p.title }}</span>
                <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                  默认：
                  {{
                    p.type === 'switch'
                      ? defaultValue(editingMetric, ct, p.key)
                        ? '开启'
                        : '关闭'
                      : (defaultValue(editingMetric, ct, p.key) ?? '—')
                  }}
                </span>
              </div>
              <Switch
                v-if="p.type === 'switch'"
                :checked="
                  ensureEditRow(editingMetric.metricCode, ct)[p.key] === 1
                "
                @change="(checked) => handleParamSwitch(ct, p.key, checked)"
              />
              <InputNumber
                v-else
                v-model:value="
                  ensureEditRow(editingMetric.metricCode, ct)[p.key]
                "
                :min="p.min"
                :max="p.max"
                :step="p.step"
                :precision="p.precision"
                style="width: 160px"
              />
            </div>
          </div>
        </div>

        <!-- 变更摘要 -->
        <div
          v-if="changes.length > 0"
          class="rounded p-3"
          :style="{
            border: '1px solid hsl(var(--border))',
            background: 'hsl(var(--muted) / 42%)',
          }"
        >
          <div
            class="mb-2 text-xs font-medium"
            :style="{ color: themeColors.NEUTRAL }"
          >
            变更摘要（{{ changes.length }} 项）
          </div>
          <div
            v-for="(c, i) in changes"
            :key="`ch-${i}`"
            class="mb-1 flex justify-between text-xs"
          >
            <span :style="{ color: themeColors.NEUTRAL }">{{ c.label }}</span>
            <span class="font-mono">{{ c.from }} → {{ c.to }}</span>
          </div>
        </div>

        <div
          v-if="violations.length > 0"
          class="rounded p-3 text-xs"
          :style="{
            background: 'hsl(var(--status-warning) / 0.08)',
            color: 'hsl(var(--status-warning))',
          }"
        >
          <div class="mb-1 font-medium">参数越界</div>
          <div v-for="(v, i) in violations" :key="`v-${i}`" class="mb-1">
            {{ v }}
          </div>
        </div>

        <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
          <strong>生效说明：</strong>
          保存后立即生效，下次计算窗口按新参数执行；未覆盖参数回落算法默认值。
        </div>
      </div>

      <template #footer>
        <div class="flex justify-end gap-2">
          <Button @click="drawerOpen = false">取消</Button>
          <Button
            v-permission="['ADMIN']"
            type="primary"
            :loading="saving"
            :disabled="!hasChanges || !isValid"
            @click="handleSave"
          >
            保存
          </Button>
        </div>
      </template>
    </Drawer>
  </Page>
</template>
