<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 阈值微调弹窗（P3-02）
 *
 * 在诊断详情页就近微调当前回路的诊断阈值。
 * - 展示该回路某 diag_code 的四级阈值合并视图（全局默认 → 类型模板 → 装置覆盖 → 回路覆盖）
 * - 支持套用类型模板为回路级覆盖起点
 * - ic_engineer 可编辑回路级（loop scope），ADMIN 同样可操作
 * - 保存 = upsert loop scope 覆盖；重置 = 删除 loop scope 覆盖
 *
 * Props:
 *  - loopId: 目标回路 ID
 *  - diagCode: 指定诊断项（可选，默认展示第一个有阈值的诊断项）
 *  - tagName: 回路位号（展示用）
 *  - loopType: 回路类型（展示用）
 */
import { computed, ref, watch } from 'vue';

import {
  Button,
  InputNumber,
  message,
  Modal,
  Select,
  SelectOption,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  applyThresholdTemplateApi,
  deleteThresholdOverrideApi,
  getThresholdOverridesApi,
  getThresholdRecommendationsApi,
  upsertThresholdOverrideApi,
} from '#/api/diagnosis';

defineOptions({ name: 'ClpmThresholdTuneModal' });

const props = withDefaults(
  defineProps<{
    diagCode?: string;
    loopId: string;
    loopType?: string;
    tagName?: string;
    visible?: boolean;
  }>(),
  {
    diagCode: '',
    tagName: '',
    loopType: '',
    visible: false,
  },
);

const emit = defineEmits<{
  success: [];
  'update:visible': [val: boolean];
}>();

const loading = ref(false);
const saving = ref(false);
const recommendation = ref<DiagnosisApi.ThresholdRecommendationResult | null>(
  null,
);
const activeDiagCode = ref<string>('');

/** 当前选中的诊断项推荐 */
const activeRec = computed(
  () =>
    recommendation.value?.recommendations.find(
      (r) => r.diagCode === activeDiagCode.value,
    ) ?? null,
);

/** 微调表单：回路级覆盖阈值 */
const tuneThreshold = ref<Record<string, number>>({});
const hasExisting = ref(false);

/** 阈值键名列表（来自当前生效阈值，兜底用全局默认） */
const thresholdKeys = computed(() => {
  if (!activeRec.value) return [];
  const fromEffective = Object.keys(activeRec.value.effectiveThreshold);
  const fromLoop = activeRec.value.loopOverride
    ? Object.keys(activeRec.value.loopOverride)
    : [];
  return [...new Set([...fromEffective, ...fromLoop])];
});

/** 是否有类型模板可套用 */
const hasTemplate = computed(() => !!activeRec.value?.loopTypeTemplate);

async function loadRecommendation() {
  if (!props.loopId) return;
  loading.value = true;
  try {
    recommendation.value = await getThresholdRecommendationsApi(props.loopId);
    // 确定默认选中的 diag_code
    const codes = recommendation.value.recommendations.map((r) => r.diagCode);
    if (props.diagCode && codes.includes(props.diagCode)) {
      activeDiagCode.value = props.diagCode;
    } else if (codes.length > 0 && !codes.includes(activeDiagCode.value)) {
      activeDiagCode.value = codes[0] ?? '';
    }
    syncTuneForm();
  } catch (error) {
    message.error((error as Error).message ?? '加载阈值推荐失败');
  } finally {
    loading.value = false;
  }
}

/** 同步微调表单为当前 diag_code 的回路级覆盖（或生效阈值起点） */
function syncTuneForm() {
  if (!activeRec.value) {
    tuneThreshold.value = {};
    hasExisting.value = false;
    return;
  }
  hasExisting.value = !!activeRec.value.loopOverride;
  tuneThreshold.value = {
    ...(activeRec.value.loopOverride ?? activeRec.value.effectiveThreshold),
  };
}

function onDiagCodeChange() {
  syncTuneForm();
}

/** 套用类型模板为回路级覆盖 */
async function applyTemplate() {
  if (!activeRec.value) return;
  Modal.confirm({
    title: '套用类型模板？',
    content: `将把 ${props.loopType || ''} 类型的 ${activeRec.value.diagCode} 模板阈值复制为回路级覆盖。已有回路级覆盖将被更新。`,
    okText: '确认套用',
    cancelText: '取消',
    onOk: async () => {
      try {
        await applyThresholdTemplateApi({
          loopId: props.loopId,
          diagCode: activeDiagCode.value,
          targetScope: 'loop',
        });
        message.success('模板已套用');
        await loadRecommendation();
      } catch (error) {
        message.error((error as Error).message ?? '套用失败');
      }
    },
  });
}

/** 保存回路级覆盖 */
async function handleSave() {
  saving.value = true;
  try {
    await upsertThresholdOverrideApi({
      diagCode: activeDiagCode.value,
      scopeType: 'loop',
      scopeId: props.loopId,
      threshold: { ...tuneThreshold.value },
    });
    message.success('回路级阈值已保存');
    emit('success');
    await loadRecommendation();
  } catch (error) {
    message.error((error as Error).message ?? '保存失败');
  } finally {
    saving.value = false;
  }
}

/** 删除回路级覆盖 */
async function handleDelete() {
  Modal.confirm({
    title: '删除回路级覆盖？',
    content: '将恢复为模板/全局默认阈值。',
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        const overrides = await getThresholdOverridesApi({
          scopeType: 'loop',
          scopeId: props.loopId,
        });
        const target = overrides.find(
          (o: DiagnosisApi.ThresholdOverrideItem) =>
            o.diagCode === activeDiagCode.value,
        );
        if (!target) {
          message.warning('未找到回路级覆盖');
          return;
        }
        await deleteThresholdOverrideApi(target.overrideId);
        message.success('回路级覆盖已删除');
        emit('success');
        await loadRecommendation();
      } catch (error) {
        message.error((error as Error).message ?? '删除失败');
      }
    },
  });
}

function handleClose() {
  emit('update:visible', false);
}

/** 阈值键值对渲染为紧凑文本 */
function thresholdText(threshold?: null | Record<string, number>): string {
  if (!threshold) return '—';
  return Object.entries(threshold)
    .map(([k, v]) => `${k}=${v}`)
    .join('， ');
}

/** scopeChain 渲染为来源链文本 */
function scopeChainText(rec: DiagnosisApi.ThresholdRecommendationItem): string {
  return rec.scopeChain
    .map((s) => `${s.source}${s.isApplied ? ' ✓' : ''}`)
    .join(' → ');
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      loadRecommendation();
    }
  },
);

watch(
  () => props.loopId,
  () => {
    if (props.visible) {
      loadRecommendation();
    }
  },
);
</script>

<template>
  <Modal
    :open="visible"
    title="阈值微调"
    width="640px"
    :footer="null"
    @update:open="(v: boolean) => emit('update:visible', v)"
  >
    <div class="space-y-4 py-2">
      <!-- 回路信息 -->
      <div class="flex flex-wrap items-center gap-2 text-sm">
        <Tag v-if="tagName" color="blue">{{ tagName }}</Tag>
        <Tag v-if="loopType">{{ loopType }}</Tag>
        <span class="text-muted-foreground"
          >选择诊断项查看阈值并微调回路级覆盖</span
        >
      </div>

      <!-- 诊断项选择器 -->
      <div class="flex items-center gap-3">
        <span class="w-20 text-sm">诊断项：</span>
        <Select
          v-model:value="activeDiagCode"
          style="width: 280px"
          :loading="loading"
          @change="onDiagCodeChange"
        >
          <SelectOption
            v-for="rec in recommendation?.recommendations ?? []"
            :key="rec.diagCode"
            :value="rec.diagCode"
          >
            {{ rec.diagName ?? rec.diagCode }}
          </SelectOption>
        </Select>
      </div>

      <!-- 四级阈值合并视图 -->
      <div v-if="activeRec" class="rounded border border-border p-3 text-sm">
        <div class="mb-2 font-medium">阈值来源链</div>
        <Tooltip :title="scopeChainText(activeRec)">
          <div class="flex flex-wrap gap-2">
            <Tag>全局默认：{{ thresholdText(activeRec.globalDefault) }}</Tag>
            <Tag v-if="activeRec.loopTypeTemplate" color="blue">
              类型模板：{{ thresholdText(activeRec.loopTypeTemplate) }}
            </Tag>
            <Tag v-if="activeRec.plantOverride" color="orange">
              装置覆盖：{{ thresholdText(activeRec.plantOverride) }}
            </Tag>
            <Tag v-if="activeRec.loopOverride" color="green">
              回路覆盖：{{ thresholdText(activeRec.loopOverride) }}
            </Tag>
          </div>
        </Tooltip>
        <div class="mt-2">
          <Tag color="processing"
            >生效：{{ thresholdText(activeRec.effectiveThreshold) }}</Tag
          >
        </div>
      </div>

      <!-- 微调表单 -->
      <div v-if="activeRec" class="space-y-3">
        <div class="flex items-center justify-between">
          <span class="font-medium">
            回路级覆盖
            <Tag v-if="hasExisting" color="green" size="small">已存在</Tag>
            <Tag v-else size="small">新建</Tag>
          </span>
          <Button
            v-if="hasTemplate"
            size="small"
            type="link"
            @click="applyTemplate"
          >
            套用类型模板
          </Button>
        </div>
        <div
          v-for="key in thresholdKeys"
          :key="key"
          class="flex items-center gap-3"
        >
          <span class="w-56 text-sm">{{ key }}</span>
          <InputNumber
            v-model:value="tuneThreshold[key]"
            style="width: 200px"
            :step="0.01"
            class="flex-1"
          />
        </div>
        <div v-if="thresholdKeys.length === 0" class="text-muted-foreground">
          该诊断项无阈值键
        </div>
      </div>

      <!-- 操作按钮 -->
      <div class="flex justify-end gap-2 border-t border-border pt-3">
        <Button @click="handleClose">关闭</Button>
        <Button v-if="hasExisting" danger @click="handleDelete">
          删除覆盖
        </Button>
        <Button type="primary" :loading="saving" @click="handleSave">
          保存
        </Button>
      </div>
    </div>
  </Modal>
</template>
