<script lang="ts" setup>
/**
 * 数据可信度阈值配置（v6.1）
 *
 * 对齐算法说明 §3.7.2 + UI/UX v6.1
 * - 5 级可信度阈值（A/B/C/D/E）
 * - 每行可编辑 minRate（0~1）
 * - 颜色与描述固定不可编辑
 * - 双命名展示：以"A级 (A)"格式
 * - 严格递减校验（level N 的 minRate > level N+1 的 minRate）
 * - level 5 (E) 的 minRate 固定为 0，不可编辑
 * - "保存"按钮 + 二次确认弹窗
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
  Tooltip,
} from 'ant-design-vue';

import {
  getConfidenceThresholdsApi,
  saveConfidenceThresholdsApi,
} from '#/api/metric';
import { ClpmToolbarButton } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricConfidenceThreshold' });

const { themeColors, confidenceColors } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);
const list = ref<MetricApi.ConfidenceThresholdItem[]>([]);

/** 5 级可信度元数据（名称、中文等级、描述；颜色走 levelColor 单源） */
const LEVEL_META: Record<
  number,
  { cnLabel: string; description: string; name: string }
> = {
  1: {
    name: 'A',
    cnLabel: 'A级',
    description: '数据充分',
  },
  2: {
    name: 'B',
    cnLabel: 'B级',
    description: '数据较充分',
  },
  3: {
    name: 'C',
    cnLabel: 'C级',
    description: '数据一般',
  },
  4: {
    name: 'D',
    cnLabel: 'D级',
    description: '数据不足',
  },
  5: {
    name: 'E',
    cnLabel: 'E级',
    description: '可信度不足（INCONCLUSIVE）',
  },
};

/**
 * 等级默认展示色：单源 confidenceColors（A-E），随明暗主题响应。
 * 颜色固定不可编辑，保存时同样以此落库。
 */
function levelColor(level: number): string {
  const key = (LEVEL_META[level]?.name ??
    'E') as keyof typeof confidenceColors.value;
  return confidenceColors.value[key] ?? themeColors.value.NEUTRAL;
}

/** 编辑态：以 level 为 key 存储编辑中的 minRate 值 */
const editState = reactive<Record<number, { minRate: number }>>({});

/** 获取编辑态（保证非 undefined，用于模板 v-model） */
function editStateOf(level: number): { minRate: number } {
  if (!editState[level]) {
    editState[level] = { minRate: 0 };
  }
  return editState[level] ?? { minRate: 0 };
}

const columns: TableColumnsType = [
  { title: '等级', dataIndex: 'level', key: 'level', width: 160 },
  {
    title: '最低有效数据率 (minRate)',
    dataIndex: 'minRate',
    key: 'minRate',
    width: 220,
  },
  { title: '描述', dataIndex: 'description', key: 'description', width: 220 },
  {
    title: '颜色（固定）',
    dataIndex: 'color',
    key: 'color',
    width: 140,
  },
  { title: '校验', key: 'validation', width: 180 },
];

/** 加载可信度阈值 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getConfidenceThresholdsApi();
    list.value = data.thresholds ?? [];
    // 同步编辑态
    for (const item of list.value) {
      editState[item.level] = {
        minRate: item.minRate,
      };
    }
    // 补全 5 级（后端可能未返回全部）
    const defaultLevels = [1, 2, 3, 4, 5];
    const defaults: Record<number, number> = {
      1: 0.95,
      2: 0.8,
      3: 0.6,
      4: 0.2,
      5: 0,
    };
    for (const lv of defaultLevels) {
      if (!list.value.some((it) => it.level === lv)) {
        const placeholder: MetricApi.ConfidenceThresholdItem = {
          level: lv,
          name: LEVEL_META[lv]?.name ?? `L${lv}`,
          minRate: defaults[lv] ?? 0,
          description: LEVEL_META[lv]?.description,
          color: levelColor(lv),
        };
        list.value.push(placeholder);
        editState[lv] = {
          minRate: placeholder.minRate,
        };
      }
    }
    // 按 level 升序（A级在上）
    list.value.sort((a, b) => a.level - b.level);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/**
 * 校验：严格递减
 * 约束：level N 的 minRate > level N+1 的 minRate
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
      next &&
      // 当前等级的 minRate 必须严格大于下一等级的 minRate
      cur.minRate <= next.minRate
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
      '可信度阈值校验未通过：相邻等级 minRate 须严格递减（A > B > C > D > E）',
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
      minRate: editState[item.level]?.minRate ?? item.minRate,
      description: LEVEL_META[item.level]?.description ?? item.description,
      color: levelColor(item.level),
    }));
    await saveConfidenceThresholdsApi({ thresholds });
    message.success('可信度阈值保存成功');
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
  <div class="metric-confidence-threshold">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
        配置 5 级数据可信度阈值（A/B/C/D/E）。 相邻等级的 minRate 须严格递减（A
        > B > C > D > E）， E 级 minRate 固定为 0（可信度不足，评估结果为
        INCONCLUSIVE）。
      </p>
      <div class="flex gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <!-- P3-07：disabled 时增加 Tooltip 说明原因 -->
        <Tooltip :title="!isValid ? '存在无效输入，请检查阈值范围' : ''">
          <Button
            v-permission="['ADMIN']"
            type="primary"
            :loading="saving"
            :disabled="!isValid"
            @click="handleSave"
          >
            保存
          </Button>
        </Tooltip>
      </div>
    </div>

    <Table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="false"
      :row-key="(record: MetricApi.ConfidenceThresholdItem) => record.level"
      :row-class-name="
        (record: MetricApi.ConfidenceThresholdItem) =>
          violatedLevels.includes(record.level) ? 'row-violated' : ''
      "
      :scroll="{ x: 960 }"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'level'">
          <Tag :color="levelColor(record.level)">
            {{ LEVEL_META[record.level]?.cnLabel ?? `L${record.level}` }}
            ({{ record.name }})
          </Tag>
        </template>
        <template v-else-if="column.key === 'minRate'">
          <InputNumber
            v-if="record.level !== 5"
            v-model:value="editStateOf(record.level).minRate"
            :min="0"
            :max="1"
            :step="0.01"
            :precision="2"
            size="small"
            style="width: 160px"
            :status="
              violatedLevels.includes(record.level) ? 'error' : undefined
            "
          />
          <span
            v-else
            class="font-mono text-sm"
            :style="{ color: themeColors.NEUTRAL }"
          >
            0.00（固定）
          </span>
        </template>
        <template v-else-if="column.key === 'description'">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }">
            {{ LEVEL_META[record.level]?.description ?? record.description }}
          </span>
        </template>
        <template v-else-if="column.key === 'color'">
          <div class="flex items-center gap-2">
            <span
              class="inline-block h-4 w-6 rounded"
              :style="{
                background: levelColor(record.level),
              }"
            ></span>
            <span
              class="font-mono text-xs"
              :style="{ color: themeColors.NEUTRAL }"
            >
              {{ levelColor(record.level) }}
            </span>
          </div>
        </template>
        <template v-else-if="column.key === 'validation'">
          <span
            v-if="violatedLevels.includes(record.level)"
            class="text-xs"
            :style="{ color: themeColors.DANGER }"
          >
            ✗ 未严格递减
          </span>
          <span v-else class="text-xs" :style="{ color: themeColors.SUCCESS }">
            ✓ 递减正确
          </span>
        </template>
      </template>
    </Table>

    <div class="mt-3 text-xs" :style="{ color: themeColors.NEUTRAL }">
      <p>
        <strong>校验规则：</strong>
        相邻等级须满足「level N 的 minRate > level N+1 的 minRate」， 例如：A级
        minRate=0.95，则 B 级 minRate 须 &lt; 0.95。
      </p>
      <p class="mt-1">
        <strong>颜色说明：</strong>
        颜色由算法规范定义，不可编辑。A级绿/B级蓝/C级黄/D级橙/E级红。
      </p>
      <p class="mt-1">
        <strong>E 级说明：</strong>
        E 级 minRate 固定为 0，当有效数据率低于 D 级阈值时判定为 E 级，
        评估结果标记为 INCONCLUSIVE（可信度不足，不输出评分）。
      </p>
    </div>

    <!-- 保存确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认保存可信度阈值"
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
                minRate =
                {{
                  item.level === 5
                    ? '0.00（固定）'
                    : (editState[item.level]?.minRate ?? item.minRate).toFixed(
                        2,
                      )
                }}
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
            保存后立即生效，所有回路的可信度判定将在下次评估时按新阈值划分。
            有效数据率低于 D 级阈值的回路将被判定为 E 级（INCONCLUSIVE），
            不输出综合评分。
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
</style>
