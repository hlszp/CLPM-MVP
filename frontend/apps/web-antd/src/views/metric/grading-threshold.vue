<script lang="ts" setup>
/**
 * 性能定级阈值配置（P5-T3）
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.4
 * - 5 级定级表（EXCELLENT/GOOD/FAIR/WARNING/POOR）
 * - 每行可编辑 minScore/maxScore
 * - 颜色固定不可编辑
 * - 双命名展示：以"一级 (EXCELLENT)"格式
 * - 严格递增校验（level N minScore == level N+1 maxScore）
 * - "保存为新版本"按钮 + 二次确认弹窗
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Button,
  InputNumber,
  message,
  Modal,
  Table,
  Tag,
} from 'ant-design-vue';

import { ClpmToolbarButton } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { getGradingThresholdsApi, saveGradingThresholdsApi } from '#/api/metric';

defineOptions({ name: 'MetricGradingThreshold' });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
const list = ref<MetricApi.GradingThresholdItem[]>([]);

/** 5 级定级元数据（名称、颜色、中文等级） */
const LEVEL_META: Record<
  number,
  { color: string; cnLabel: string; name: string }
> = {
  1: { name: 'EXCELLENT', cnLabel: '一级', color: '#52c41a' },
  2: { name: 'GOOD', cnLabel: '二级', color: '#1890ff' },
  3: { name: 'FAIR', cnLabel: '三级', color: '#faad14' },
  4: { name: 'WARNING', cnLabel: '四级', color: '#fa8c16' },
  5: { name: 'POOR', cnLabel: '五级', color: '#f5222d' },
};

/** 编辑态：以 level 为 key 存储编辑中的值 */
const editState = reactive<
  Record<number, { maxScore: number; minScore: number }>
>({});

/** 获取编辑态（保证非 undefined，用于模板 v-model） */
function editStateOf(level: number): { maxScore: number; minScore: number } {
  if (!editState[level]) {
    editState[level] = { minScore: 0, maxScore: 0 };
  }
  return editState[level] ?? { minScore: 0, maxScore: 0 };
}

const columns: TableColumnsType = [
  { title: '等级', dataIndex: 'level', key: 'level', width: 160 },
  {
    title: '最低分 (minScore)',
    dataIndex: 'minScore',
    key: 'minScore',
    width: 180,
  },
  {
    title: '最高分 (maxScore)',
    dataIndex: 'maxScore',
    key: 'maxScore',
    width: 180,
  },
  {
    title: '颜色（固定）',
    dataIndex: 'color',
    key: 'color',
    width: 140,
  },
  { title: '校验', key: 'validation', width: 180 },
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
        minScore: item.minScore,
        maxScore: item.maxScore,
      };
    }
    // 补全 5 级（后端可能未返回全部）
    const defaultLevels = [1, 2, 3, 4, 5];
    const defaults: Record<number, { max: number; min: number }> = {
      1: { min: 90, max: 100 },
      2: { min: 80, max: 90 },
      3: { min: 70, max: 80 },
      4: { min: 60, max: 70 },
      5: { min: 0, max: 60 },
    };
    for (const lv of defaultLevels) {
      if (!list.value.some((it) => it.level === lv)) {
        const placeholder: MetricApi.GradingThresholdItem = {
          level: lv,
          name: LEVEL_META[lv]?.name ?? `L${lv}`,
          minScore: defaults[lv]?.min ?? 0,
          maxScore: defaults[lv]?.max ?? 100,
          color: LEVEL_META[lv]?.color,
        };
        list.value.push(placeholder);
        editState[lv] = {
          minScore: placeholder.minScore,
          maxScore: placeholder.maxScore,
        };
      }
    }
    // 按 level 降序（一级在上）
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
  const sorted = [...list.value].sort((a, b) => a.level - b.level);
  const violations: number[] = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const cur = editState[sorted[i]!.level];
    const next = editState[sorted[i + 1]!.level];
    if (cur && next) {
      // 当前等级的 minScore 应等于下一等级的 maxScore
      if (cur.minScore !== next.maxScore) {
        violations.push(sorted[i]!.level);
        violations.push(sorted[i + 1]!.level);
      }
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
    message.warning('定级阈值校验未通过：相邻等级 minScore/maxScore 须严格衔接');
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
      minScore: editState[item.level]?.minScore ?? item.minScore,
      maxScore: editState[item.level]?.maxScore ?? item.maxScore,
      color: LEVEL_META[item.level]?.color ?? item.color,
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
</script>

<template>
  <div class="metric-grading-threshold">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
        配置 5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR）。
        相邻等级的分数区间须严格衔接（即 level N 的 minScore == level N+1 的 maxScore）。
      </p>
      <div class="flex gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <Button
          v-permission="['ADMIN']"
          type="primary"
          :loading="saving"
          :disabled="!isValid"
          @click="handleSave"
        >
          保存为新版本
        </Button>
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
          <Tag :color="LEVEL_META[record.level]?.color ?? 'default'">
            {{ LEVEL_META[record.level]?.cnLabel ?? `L${record.level}` }}
            ({{ record.name }})
          </Tag>
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
            <span
              class="inline-block h-4 w-6 rounded"
              :style="{
                background: LEVEL_META[record.level]?.color ?? record.color,
              }"
            />
            <span class="font-mono text-xs" :style="{ color: themeColors.NEUTRAL }">
              {{ LEVEL_META[record.level]?.color ?? record.color }}
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
          <span v-else class="text-xs" :style="{ color: themeColors.SUCCESS }">✓ 衔接正确</span>
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
        <strong>颜色说明：</strong>
        颜色由国标定义，不可编辑。一级绿/二级蓝/三级黄/四级橙/五级红。
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
          <div class="rounded p-3" :style="{ border: '1px solid hsl(var(--border))', background: 'hsl(var(--muted) / 42%)' }">
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
          <p class="rounded p-2 text-xs" :style="{ background: 'hsl(var(--status-warning) / 0.08)', color: 'hsl(var(--status-warning))' }">
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
  background-color: #fff1f0;
}
:deep(.row-violated:hover > td) {
  background-color: #ffe7e5 !important;
}
</style>
