<script lang="ts" setup>
/**
 * 8 类异常值检测参数配置
 *
 * 对齐算法说明 §3.4.3-3.4.4 + PRD §5.5.2-5.5.3
 * - 上半区：8 类检测启停开关（中文名/英文名/用途说明/启用 Switch，默认开）
 * - 下半区：按控制类型的检测参数表（5 行 × 7 参数可编辑）
 * - 保存：变更摘要 + 二次确认弹窗（对齐权重/可信度 Tab 交互）
 * - 未覆盖的参数回落后端 thresholds.py 算法默认值
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Button,
  InputNumber,
  message,
  Modal,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import { getOutlierParamsApi, saveOutlierParamsApi } from '#/api/metric';
import { ClpmToolbarButton } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricOutlierParams' });

const { themeColors } = useClpmTheme();

// ---------------------------------------------------------------------------
// 元数据
// ---------------------------------------------------------------------------

/** 8 类检测元数据（中文名/英文名/用途说明） */
const DETECTOR_META: Record<
  string,
  { cnName: string; description: string; enName: string }
> = {
  nan: {
    cnName: 'NaN/空值检测',
    enName: 'NAN',
    description: '识别信号中的 NaN/Inf/NULL 值，命中点置 valid=False',
  },
  out_of_range: {
    cnName: '超量程检测',
    enName: 'OUT_OF_RANGE',
    description: 'PV/SP/OP 超出量程上下限，命中点置 valid=False',
  },
  frozen: {
    cnName: '冻结值检测',
    enName: 'FROZEN',
    description: '滑动窗口内标准差低于阈值（信号卡死），命中点置 valid=False',
  },
  jump: {
    cnName: '跳变检测',
    enName: 'JUMP',
    description: '相邻采样点变化幅度超过跳变阈值×量程，命中点置 valid=False',
  },
  spike: {
    cnName: '尖峰检测',
    enName: 'SPIKE',
    description: '单点突变且前后点回落，命中点置 valid=False',
  },
  ts_anomaly: {
    cnName: '时间戳异常检测',
    enName: 'TS_ANOMALY',
    description: '重复/逆序/间隔异常时间戳，仅标记不置 valid=False',
  },
  qc_bad: {
    cnName: '质量码异常检测',
    enName: 'QC_BAD',
    description: 'OPC 质量码为 Bad/Uncertain，命中点置 valid=False',
  },
  hf_noise: {
    cnName: '高频噪声检测',
    enName: 'HF_NOISE',
    description: '超过截止频率的能量占比过高，仅标记不置 valid=False',
  },
};

/** 检测键展示顺序（与后端 DETECTOR_KEYS 一致） */
const DETECTOR_KEYS = [
  'nan',
  'out_of_range',
  'frozen',
  'jump',
  'spike',
  'ts_anomaly',
  'qc_bad',
  'hf_noise',
] as const;

type DetectorKey = (typeof DETECTOR_KEYS)[number];

/** 控制类型元数据（FC/PC/TC/LC/CC） */
const CONTROL_TYPE_META: Record<string, { cnLabel: string; color: string }> = {
  FC: { cnLabel: '流量', color: '#1890ff' },
  PC: { cnLabel: '压力', color: '#722ed1' },
  TC: { cnLabel: '温度', color: '#fa8c16' },
  LC: { cnLabel: '液位', color: '#13c2c2' },
  CC: { cnLabel: '成分', color: '#eb2f96' },
};

const CONTROL_TYPES = ['FC', 'PC', 'TC', 'LC', 'CC'] as const;

type ControlTypeKey = (typeof CONTROL_TYPES)[number];

/** 参数列元数据（校验边界与后端 schema 一致） */
const PARAM_META = [
  {
    key: 'baseSamplingFreq',
    title: '采样率(秒)',
    min: 1,
    max: 3600,
    step: 1,
    precision: 0,
  },
  {
    key: 'frozenWindowPoints',
    title: '冻结窗口点数',
    min: 2,
    max: 10_000,
    step: 1,
    precision: 0,
  },
  {
    key: 'frozenStdPct',
    title: '冻结标准差阈值',
    min: 0,
    max: 1,
    step: 0.0005,
    precision: 4,
  },
  {
    key: 'jumpThresholdPct',
    title: '跳变阈值',
    min: 0,
    max: 1,
    step: 0.05,
    precision: 2,
  },
  {
    key: 'spikeThresholdPct',
    title: '尖峰阈值',
    min: 0,
    max: 1,
    step: 0.05,
    precision: 2,
  },
  {
    key: 'noiseCutoffHz',
    title: '噪声截止频率(Hz)',
    min: 0.001,
    max: 1000,
    step: 0.01,
    precision: 3,
  },
  {
    key: 'minConsecutivePoints',
    title: '连续有效最短段',
    min: 2,
    max: 100_000,
    step: 1,
    precision: 0,
  },
] as const;

type ParamKey = (typeof PARAM_META)[number]['key'];
type ParamMeta = (typeof PARAM_META)[number];

const PARAM_KEY_SET = new Set<string>(PARAM_META.map((p) => p.key));

function paramMetaOf(key: string): ParamMeta {
  return PARAM_META.find((p) => p.key === key) ?? PARAM_META[0];
}

/** 算法默认参数（对齐 backend thresholds.py _THRESHOLDS，用于覆盖diff与占位） */
const DEFAULT_PARAMS: Record<ControlTypeKey, Record<ParamKey, number>> = {
  FC: {
    baseSamplingFreq: 1,
    frozenWindowPoints: 5,
    frozenStdPct: 0.001,
    jumpThresholdPct: 0.8,
    spikeThresholdPct: 0.5,
    noiseCutoffHz: 0.2,
    minConsecutivePoints: 30,
  },
  PC: {
    baseSamplingFreq: 2,
    frozenWindowPoints: 5,
    frozenStdPct: 0.001,
    jumpThresholdPct: 0.5,
    spikeThresholdPct: 0.3,
    noiseCutoffHz: 0.1,
    minConsecutivePoints: 20,
  },
  TC: {
    baseSamplingFreq: 5,
    frozenWindowPoints: 6,
    frozenStdPct: 0.0005,
    jumpThresholdPct: 0.3,
    spikeThresholdPct: 0.2,
    noiseCutoffHz: 0.05,
    minConsecutivePoints: 15,
  },
  LC: {
    baseSamplingFreq: 5,
    frozenWindowPoints: 6,
    frozenStdPct: 0.001,
    jumpThresholdPct: 0.3,
    spikeThresholdPct: 0.2,
    noiseCutoffHz: 0.05,
    minConsecutivePoints: 15,
  },
  CC: {
    baseSamplingFreq: 10,
    frozenWindowPoints: 6,
    frozenStdPct: 0.0005,
    jumpThresholdPct: 0.2,
    spikeThresholdPct: 0.1,
    noiseCutoffHz: 0.02,
    minConsecutivePoints: 10,
  },
};

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const loading = ref(false);
const saving = ref(false);
const updatedAt = ref<null | string>(null);
const updatedBy = ref<null | string>(null);

/** 参数合并视图行（含 overridden 标记） */
const thresholdRows = ref<MetricApi.OutlierThresholdViewItem[]>([]);

/** 开关表行 */
const switchRows = ref<{ key: DetectorKey }[]>(
  DETECTOR_KEYS.map((key) => ({ key })),
);

/** 编辑态：参数（controlType → paramKey → value） */
const editParams = reactive<Record<string, Record<string, number>>>({});
/** 编辑态：开关（detectorKey → enabled） */
const editSwitches = reactive<Record<string, boolean>>({});
/** 已加载快照（变更摘要 diff 基准） */
const loadedParams = reactive<Record<string, Record<string, number>>>({});
const loadedSwitches = reactive<Record<string, boolean>>({});

function defaultParamRow(ct: string): Record<string, number> {
  return { ...DEFAULT_PARAMS[ct as ControlTypeKey] };
}

/** 获取参数编辑行（保证非 undefined，用于模板 v-model） */
function editParamRow(ct: string): Record<string, number> {
  if (!editParams[ct]) {
    editParams[ct] = defaultParamRow(ct);
  }
  return editParams[ct] ?? defaultParamRow(ct);
}

// ---------------------------------------------------------------------------
// 表格列
// ---------------------------------------------------------------------------

const switchColumns: TableColumnsType = [
  { title: '检测类型', dataIndex: 'key', key: 'cnName', width: 180 },
  { title: '英文名', dataIndex: 'key', key: 'enName', width: 160 },
  { title: '用途说明', dataIndex: 'key', key: 'description' },
  { title: '启用', dataIndex: 'key', key: 'enabled', width: 100 },
];

const paramColumns: TableColumnsType = [
  {
    title: '控制类型',
    dataIndex: 'controlType',
    key: 'controlType',
    width: 150,
    fixed: 'left',
  },
  ...PARAM_META.map((p) => ({
    title: p.title,
    dataIndex: ['params', p.key],
    key: p.key as string,
    width: 150,
  })),
];

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

async function loadData() {
  loading.value = true;
  try {
    const data = await getOutlierParamsApi();
    thresholdRows.value = data.thresholds ?? [];
    updatedAt.value = data.updatedAt ?? null;
    updatedBy.value = data.updatedBy ?? null;
    for (const item of thresholdRows.value) {
      const ct = item.controlType;
      const row = defaultParamRow(ct);
      for (const p of PARAM_META) {
        row[p.key] = item.params?.[p.key] ?? row[p.key] ?? 0;
      }
      editParams[ct] = { ...row };
      loadedParams[ct] = { ...row };
    }
    for (const key of DETECTOR_KEYS) {
      const enabled = data.switches?.[key] ?? true;
      editSwitches[key] = enabled;
      loadedSwitches[key] = enabled;
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 变更摘要
// ---------------------------------------------------------------------------

interface ChangeItem {
  label: string;
  from: string;
  to: string;
}

const paramChanges = computed<ChangeItem[]>(() => {
  const changes: ChangeItem[] = [];
  for (const ct of CONTROL_TYPES) {
    for (const p of PARAM_META) {
      const cur = editParams[ct]?.[p.key];
      const loaded = loadedParams[ct]?.[p.key];
      if (cur === undefined || loaded === undefined || cur === loaded) continue;
      changes.push({
        label: `${CONTROL_TYPE_META[ct]?.cnLabel ?? ct}(${ct}) · ${p.title}`,
        from: String(loaded),
        to: String(cur),
      });
    }
  }
  return changes;
});

const switchChanges = computed<ChangeItem[]>(() => {
  const changes: ChangeItem[] = [];
  for (const key of DETECTOR_KEYS) {
    const cur = editSwitches[key];
    const loaded = loadedSwitches[key];
    if (cur === undefined || loaded === undefined || cur === loaded) continue;
    changes.push({
      label: `${DETECTOR_META[key]?.cnName ?? key}（${DETECTOR_META[key]?.enName ?? key}）`,
      from: loaded ? '启用' : '停用',
      to: cur ? '启用' : '停用',
    });
  }
  return changes;
});

const hasChanges = computed(
  () => paramChanges.value.length > 0 || switchChanges.value.length > 0,
);

/** 参数取值校验（对齐后端 schema 边界） */
const paramViolations = computed<string[]>(() => {
  const violations: string[] = [];
  for (const ct of CONTROL_TYPES) {
    for (const p of PARAM_META) {
      const v = editParams[ct]?.[p.key];
      if (v === undefined || v === null) continue;
      if (v < p.min || v > p.max) {
        violations.push(
          `${CONTROL_TYPE_META[ct]?.cnLabel ?? ct}(${ct}) · ${p.title} 须在 [${p.min}, ${p.max}] 内`,
        );
      }
    }
  }
  return violations;
});

const isValid = computed(() => paramViolations.value.length === 0);

// ---------------------------------------------------------------------------
// 保存
// ---------------------------------------------------------------------------

const confirmVisible = ref(false);
const confirmLoading = ref(false);

function handleSave() {
  if (!hasChanges.value) {
    message.info('没有需要保存的变更');
    return;
  }
  if (!isValid.value) {
    message.warning(`参数校验未通过：${paramViolations.value[0]}`);
    return;
  }
  confirmVisible.value = true;
}

async function confirmSave() {
  confirmLoading.value = true;
  saving.value = true;
  try {
    // 仅提交与算法默认不同的参数（未覆盖参数回落后端默认值）
    const thresholds: Partial<
      Record<ControlTypeKey, MetricApi.OutlierThresholdParams>
    > = {};
    for (const ct of CONTROL_TYPES) {
      const row: Record<string, number> = {};
      for (const p of PARAM_META) {
        const v = editParamRow(ct)[p.key];
        if (v !== undefined && v !== DEFAULT_PARAMS[ct][p.key]) {
          row[p.key] = v;
        }
      }
      if (Object.keys(row).length > 0) {
        thresholds[ct] = row;
      }
    }
    const switches: Partial<Record<DetectorKey, boolean>> = {};
    for (const key of DETECTOR_KEYS) {
      switches[key] = editSwitches[key] ?? true;
    }
    await saveOutlierParamsApi({ thresholds, switches });
    message.success('异常值检测参数保存成功');
    confirmVisible.value = false;
    await loadData();
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
    saving.value = false;
  }
}

function overriddenCount(record: {
  overridden?: Record<string, boolean>;
}): number {
  return Object.values(record.overridden ?? {}).filter(Boolean).length;
}

onMounted(() => {
  loadData();
});
</script>

<template>
  <div class="metric-outlier-params">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
        配置 8 类异常值检测的启停开关与各控制类型的检测参数。
        未修改的参数回落算法默认值；保存后立即生效，无需重启。
        <span v-if="updatedAt" class="ml-2">
          最近更新：{{ updatedBy ?? '-' }} @ {{ updatedAt }}
        </span>
      </p>
      <div class="flex gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadData"
        />
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
    </div>

    <!-- 上半区：8 类检测启停开关 -->
    <div class="mb-2 text-sm font-medium">检测启停开关（默认全部启用）</div>
    <Table
      :columns="switchColumns"
      :data-source="switchRows"
      :loading="loading"
      :pagination="false"
      :row-key="(record: { key: string }) => record.key"
      size="middle"
      class="mb-6"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'cnName'">
          <span class="font-medium">
            {{ DETECTOR_META[record.key]?.cnName ?? record.key }}
          </span>
        </template>
        <template v-else-if="column.key === 'enName'">
          <Tag color="default" class="font-mono">
            {{ DETECTOR_META[record.key]?.enName ?? record.key }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'description'">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }">
            {{ DETECTOR_META[record.key]?.description }}
          </span>
        </template>
        <template v-else-if="column.key === 'enabled'">
          <Switch v-model:checked="editSwitches[record.key]" />
        </template>
      </template>
    </Table>

    <!-- 下半区：按控制类型的检测参数 -->
    <div class="mb-2 text-sm font-medium">
      按控制类型的检测参数（留默认值的列不参与覆盖）
    </div>
    <Table
      :columns="paramColumns"
      :data-source="thresholdRows"
      :loading="loading"
      :pagination="false"
      :row-key="
        (record: MetricApi.OutlierThresholdViewItem) => record.controlType
      "
      :scroll="{ x: 1200 }"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'controlType'">
          <Tag :color="CONTROL_TYPE_META[record.controlType]?.color">
            {{ CONTROL_TYPE_META[record.controlType]?.cnLabel }}
            ({{ record.controlType }})
          </Tag>
          <span
            v-if="overriddenCount(record) > 0"
            class="ml-1 text-xs"
            :style="{ color: themeColors.NEUTRAL }"
          >
            {{ overriddenCount(record) }} 项覆盖
          </span>
        </template>
        <template v-else-if="PARAM_KEY_SET.has(String(column.key))">
          <InputNumber
            v-model:value="editParamRow(record.controlType)[String(column.key)]"
            :min="paramMetaOf(String(column.key)).min"
            :max="paramMetaOf(String(column.key)).max"
            :step="paramMetaOf(String(column.key)).step"
            :precision="paramMetaOf(String(column.key)).precision"
            size="small"
            style="width: 120px"
          />
        </template>
      </template>
    </Table>

    <div class="mt-3 text-xs" :style="{ color: themeColors.NEUTRAL }">
      <p>
        <strong>校验规则：</strong>
        阈值类参数（冻结标准差/跳变/尖峰）为量程占比，须在 [0, 1] 内；
        冻结窗口点数与连续有效最短段 ≥ 2；噪声截止频率 &gt; 0。
      </p>
      <p class="mt-1">
        <strong>生效说明：</strong>
        停用的检测类型不参与异常判断和标记；TS_ANOMALY 与 HF_NOISE
        始终仅标记不置 valid=False（算法说明 §3.4.3）。
      </p>
      <p v-if="paramViolations.length > 0" class="mt-1">
        <strong :style="{ color: themeColors.DANGER }">参数越界：</strong>
        <span :style="{ color: themeColors.DANGER }">
          {{ paramViolations.join('；') }}
        </span>
      </p>
    </div>

    <!-- 保存确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认保存异常值检测参数"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="560px"
      @ok="confirmSave"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-2 font-medium">变更摘要</div>
          <div
            class="rounded p-3"
            :style="{
              border: '1px solid hsl(var(--border))',
              background: 'hsl(var(--muted) / 42%)',
            }"
          >
            <template v-if="switchChanges.length > 0">
              <div
                class="mb-1 text-xs font-medium"
                :style="{ color: themeColors.NEUTRAL }"
              >
                检测开关
              </div>
              <div
                v-for="(c, i) in switchChanges"
                :key="`sw-${i}`"
                class="mb-1 flex justify-between text-xs"
              >
                <span :style="{ color: themeColors.NEUTRAL }">
                  {{ c.label }}
                </span>
                <span class="font-mono">{{ c.from }} → {{ c.to }}</span>
              </div>
            </template>
            <template v-if="paramChanges.length > 0">
              <div
                class="mb-1 mt-2 text-xs font-medium"
                :style="{ color: themeColors.NEUTRAL }"
              >
                检测参数
              </div>
              <div
                v-for="(c, i) in paramChanges"
                :key="`pa-${i}`"
                class="mb-1 flex justify-between text-xs"
              >
                <span :style="{ color: themeColors.NEUTRAL }">
                  {{ c.label }}
                </span>
                <span class="font-mono">{{ c.from }} → {{ c.to }}</span>
              </div>
            </template>
          </div>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">影响范围</div>
          <p
            class="rounded p-2 text-xs"
            :style="{
              background: 'hsl(var(--status-warning) / 0.08)',
              color: 'hsl(var(--status-warning))',
            }"
          >
            保存后立即生效：所有回路的预处理 Pipeline
            将在下一计算窗口按新参数/开关执行异常值检测， 影响 valid
            标记、缺失率与可信度评估结果。
          </p>
        </div>
      </div>
    </Modal>
  </div>
</template>
