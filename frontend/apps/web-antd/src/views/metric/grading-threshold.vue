<script lang="ts" setup>
/**
 * 性能定级阈值配置（P5-T3）
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.4
 * - 5 级定级表（EXCELLENT/GOOD/FAIR/WARNING/POOR）
 * - 每行可编辑 minScore/maxScore/label/color
 * - 颜色支持自定义（点击色块弹出颜色选择器）
 * - 双命名展示：以"一级 (EXCELLENT)"格式
 * - 严格递增校验（level N minScore == level N+1 maxScore）
 * - "保存为新版本"按钮 + 二次确认弹窗
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Button,
  Input,
  InputNumber,
  message,
  Modal,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getGradingThresholdsApi,
  saveGradingThresholdsApi,
} from '#/api/metric';
import { ClpmToolbarButton } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricGradingThreshold' });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
const list = ref<MetricApi.GradingThresholdItem[]>([]);

/** 5 级定级元数据（名称、中文等级；颜色走 levelColor 单源） */
const LEVEL_META: Record<number, { cnLabel: string; name: string }> = {
  1: { name: 'EXCELLENT', cnLabel: '优秀' },
  2: { name: 'GOOD', cnLabel: '良好' },
  3: { name: 'FAIR', cnLabel: '合格' },
  4: { name: 'WARNING', cnLabel: '警告' },
  5: { name: 'POOR', cnLabel: '不合格' },
};

/**
 * 等级默认展示色：与 use-score-color 的 fallbackByLevel 同口径
 * （SUCCESS/INFO/WARNING/DANGER/DANGER，随明暗主题响应）。
 * 颜色固定不可编辑，保存时同样以此落库。
 */
function levelColor(level: number): string {
  const fallbackByLevel: Record<number, string> = {
    1: themeColors.value.SUCCESS,
    2: themeColors.value.INFO,
    3: themeColors.value.WARNING,
    4: themeColors.value.DANGER,
    5: themeColors.value.DANGER,
  };
  return fallbackByLevel[level] ?? themeColors.value.NEUTRAL;
}

/** 编辑态：以 level 为 key 存储编辑中的值 */
const editState = reactive<
  Record<
    number,
    { color: string; label: string; maxScore: number; minScore: number }
  >
>({});

/** 获取编辑态（保证非 undefined，用于模板 v-model） */
function editStateOf(level: number): {
  color: string;
  label: string;
  maxScore: number;
  minScore: number;
} {
  if (!editState[level]) {
    editState[level] = {
      color: levelColor(level),
      label: '',
      minScore: 0,
      maxScore: 0,
    };
  }
  return (
    editState[level] ?? {
      color: levelColor(level),
      label: '',
      minScore: 0,
      maxScore: 0,
    }
  );
}

const columns: TableColumnsType = [
  { title: '等级', dataIndex: 'level', key: 'level', width: 140 },
  {
    title: '中文显示名',
    dataIndex: 'label',
    key: 'label',
    width: 140,
  },
  {
    title: '最低分 (minScore)',
    dataIndex: 'minScore',
    key: 'minScore',
    width: 160,
  },
  {
    title: '最高分 (maxScore)',
    dataIndex: 'maxScore',
    key: 'maxScore',
    width: 160,
  },
  {
    title: '颜色',
    dataIndex: 'color',
    key: 'color',
    width: 160,
  },
  { title: '校验', key: 'validation', width: 120 },
];

/** 加载定级阈值 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getGradingThresholdsApi();
    list.value = data.thresholds ?? [];
    // 同步编辑态
    for (const item of list.value) {
      editState[item.level] = {
        color: item.color ?? levelColor(item.level),
        label:
          item.label ?? LEVEL_META[item.level]?.cnLabel ?? `L${item.level}`,
        minScore: item.minScore,
        maxScore: item.maxScore,
      };
    }
    // 补全 5 级（后端可能未返回全部）
    const defaultLevels = [1, 2, 3, 4, 5];
    const defaults: Record<
      number,
      { color: string; label: string; max: number; min: number }
    > = {
      1: { color: '#52c41a', label: '优秀', min: 90, max: 100 },
      2: { color: '#1890ff', label: '良好', min: 80, max: 90 },
      3: { color: '#faad14', label: '合格', min: 60, max: 80 },
      4: { color: '#fa8c16', label: '警告', min: 40, max: 60 },
      5: { color: '#f5222d', label: '不合格', min: 0, max: 40 },
    };
    for (const lv of defaultLevels) {
      if (!list.value.some((it) => it.level === lv)) {
        const placeholder: MetricApi.GradingThresholdItem = {
          level: lv,
          name: LEVEL_META[lv]?.name ?? `L${lv}`,
          label: defaults[lv]?.label ?? `L${lv}`,
          minScore: defaults[lv]?.min ?? 0,
          maxScore: defaults[lv]?.max ?? 100,
          color: defaults[lv]?.color ?? levelColor(lv),
        };
        list.value.push(placeholder);
        editState[lv] = {
          color: placeholder.color ?? levelColor(lv),
          label: placeholder.label ?? '',
          minScore: placeholder.minScore,
          maxScore: placeholder.maxScore,
        };
      }
    }
    // 按 level 降序（优秀在上）
    list.value.sort((a, b) => a.level - b.level);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/**
 * 校验：严格递增
 * 约束：level N 的 minScore == level N+1 的 maxScore
 * 违反时返回违规 level 列表
 */
const violatedLevels = computed<number[]>(() => {
  const sorted = [...list.value].toSorted((a, b) => a.level - b.level);
  const violations: number[] = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const currentLevel = sorted[i];
    const nextLevel = sorted[i + 1];
    if (!currentLevel || !nextLevel) continue;
    const cur = editState[currentLevel.level];
    const next = editState[nextLevel.level];
    if (
      cur &&
      next && // 当前等级的 minScore 应等于下一等级的 maxScore
      cur.minScore !== next.maxScore
    ) {
      violations.push(currentLevel.level, nextLevel.level);
    }
  }
  return [...new Set(violations)];
});

const isValid = computed(() => violatedLevels.value.length === 0);

/** 保存确认弹窗 */
const confirmVisible = ref(false);
const confirmLoading = ref(false);

function handleSave() {
  if (!isValid.value) {
    message.warning(
      '定级阈值校验未通过：相邻等级 minScore/maxScore 须严格衔接',
    );
    return;
  }
  confirmVisible.value = true;
}

async function confirmSave() {
  confirmLoading.value = true;
  saving.value = true;
  try {
    const thresholds = list.value.map((item) => ({
      level: item.level,
      name: item.name,
      label: editState[item.level]?.label ?? item.label ?? '',
      minScore: editState[item.level]?.minScore ?? item.minScore,
      maxScore: editState[item.level]?.maxScore ?? item.maxScore,
      color: editState[item.level]?.color ?? item.color ?? levelColor(item.level),
    }));
    await saveGradingThresholdsApi({ thresholds });
    message.success('定级阈值保存成功（已生成新版本）');
    confirmVisible.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
    saving.value = false;
  }
}

onMounted(() => {
  loadList();
});

/** P3-01：子组件暴露 refresh() 替代父组件 tabKey 强制重建 */
function refresh() {
  return loadList();
}

defineExpose({ refresh });
</script>

<template>
  <div class="metric-grading-threshold">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
        配置 5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR）。
        相邻等级的分数区间须严格衔接（即 level N 的 minScore == level N+1 的
        maxScore）。
      </p>
      <div class="flex gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <!-- P3-07：disabled 时增加 Tooltip 说明原因 -->
        <Tooltip :title="!isValid ? '存在无效输入，请检查评分范围' : ''">
          <Button
            v-permission="['ADMIN']"
            type="primary"
            :loading="saving"
            :disabled="!isValid"
            @click="handleSave"
          >
            保存为新版本
          </Button>
        </Tooltip>
      </div>
    </div>

    <Table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="false"
      :row-key="(record: MetricApi.GradingThresholdItem) => record.level"
      :row-class-name="
        (record: MetricApi.GradingThresholdItem) =>
          violatedLevels.includes(record.level) ? 'row-violated' : ''
      "
      :scroll="{ x: 900 }"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'level'">
          <Tag :color="editStateOf(record.level).color">
            {{ LEVEL_META[record.level]?.cnLabel ?? `L${record.level}` }}
            ({{ record.name }})
          </Tag>
        </template>
        <template v-else-if="column.key === 'label'">
          <Input
            v-model:value="editStateOf(record.level).label"
            placeholder="如：优秀"
            size="small"
            style="width: 120px"
          />
        </template>
        <template v-else-if="column.key === 'minScore'">
          <InputNumber
            v-model:value="editStateOf(record.level).minScore"
            :min="0"
            :max="100"
            size="small"
            style="width: 140px"
            :status="
              violatedLevels.includes(record.level) ? 'error' : undefined
            "
          />
        </template>
        <template v-else-if="column.key === 'maxScore'">
          <InputNumber
            v-model:value="editStateOf(record.level).maxScore"
            :min="0"
            :max="100"
            size="small"
            style="width: 140px"
            :status="
              violatedLevels.includes(record.level) ? 'error' : undefined
            "
          />
        </template>
        <template v-else-if="column.key === 'color'">
          <div class="flex items-center gap-2">
            <label class="color-picker-trigger">
              <input
                type="color"
                :value="editStateOf(record.level).color"
                class="color-picker-input"
                @input="
                  (e: Event) =>
                    (editStateOf(record.level).color = (
                      e.target as HTMLInputElement
                    ).value)
                "
              />
              <span
                class="color-picker-swatch"
                :style="{ background: editStateOf(record.level).color }"
              ></span>
            </label>
            <span
              class="font-mono text-xs"
              :style="{ color: themeColors.NEUTRAL }"
            >
              {{ editStateOf(record.level).color }}
            </span>
          </div>
        </template>
        <template v-else-if="column.key === 'validation'">
          <span
            v-if="violatedLevels.includes(record.level)"
            class="text-xs"
            :style="{ color: themeColors.DANGER }"
          >
            ✗ 衔接断裂
          </span>
          <span v-else class="text-xs" :style="{ color: themeColors.SUCCESS }"
            >✓ 衔接正确</span
          >
        </template>
      </template>
    </Table>

    <div class="mt-3 text-xs" :style="{ color: themeColors.NEUTRAL }">
      <p>
        <strong>校验规则：</strong>
        相邻等级须满足「level N 的 minScore == level N+1 的 maxScore」，
        例如：一级 (EXCELLENT) minScore=90，则二级 (GOOD) maxScore 须=90。
      </p>
      <p class="mt-1">
        <strong>颜色配置：</strong>
        点击颜色方块可自定义各等级显示颜色，保存后全站生效。
      </p>
    </div>

    <!-- 保存确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认保存定级阈值（新版本）"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="520px"
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
            <div
              v-for="item in list"
              :key="item.level"
              class="mb-1 flex justify-between text-xs"
            >
              <span :style="{ color: themeColors.NEUTRAL }">
                {{ LEVEL_META[item.level]?.cnLabel }} ({{ item.name }})
              </span>
              <span class="font-mono">
                {{ editState[item.level]?.minScore ?? item.minScore }} ~
                {{ editState[item.level]?.maxScore ?? item.maxScore }}
              </span>
            </div>
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
            保存后将以新版本生效，所有回路的性能定级将在下次评估时按新阈值划分。
            可在「版本历史」Tab 查看历史版本并回滚。
          </p>
        </div>
      </div>
    </Modal>
  </div>
</template>

<style scoped>
:deep(.row-violated) {
  background-color: hsl(var(--status-error) / 8%);
}

:deep(.row-violated:hover > td) {
  background-color: hsl(var(--status-error) / 12%) !important;
}

.color-picker-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 24px;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.color-picker-trigger:hover {
  border-color: hsl(var(--primary));
}

.color-picker-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  padding: 0;
  cursor: pointer;
  border: 0;
  opacity: 0;
}

.color-picker-swatch {
  display: block;
  width: 22px;
  height: 16px;
  pointer-events: none;
  border-radius: 2px;
  box-shadow: inset 0 0 0 1px rgb(0 0 0 / 8%);
}
</style>
