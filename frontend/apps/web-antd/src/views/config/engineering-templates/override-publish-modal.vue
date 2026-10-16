<script lang="ts" setup>
/**
 * override-publish-modal — 模板层阈值覆盖发布向导（Flow C）
 *
 * 流程（真实 dry-run → 确认 → 真实发布）：
 * edit（指标/参数/原因）→ previewing（POST /configs/publish/overrides/preview
 * 零写入预检）→ previewed（校验错误含跨层颠倒 / before-after 差异 / 影响
 * 回路+样本 / 合并视图 / wouldConflict 提示）→ publishing（expectedRevision
 * 乐观锁，ADMIN）→ done（revision/回退引用）| failed（409 显式提示）。
 *
 * 参数键全集来自服务端注册表（/configs/algorithm-params paramMeta，P2-01
 * 单源参数目录），本地不维护第二份键清单；读取与发布分离：预览只读三角色
 * 可用，真实发布按钮由父页按 config:publish 权限控制入口。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EngineeringTemplateApi } from '#/api/engineering-template';
import type { MetricApi } from '#/api/metric';

import { computed, ref, watch } from 'vue';

import {
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Select,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getConfigRevisionApi,
  previewConfigOverrideApi,
  publishConfigOverrideApi,
} from '#/api/engineering-template';
import { getAlgorithmParamsApi } from '#/api/metric';

import ProfileDiffView from './profile-diff-view.vue';
import {
  canConfirmPublish,
  diffProfileEntries,
  type PublishFlowStep,
  reducePublishFlow,
} from './template-utils';

interface ParamRow {
  key: string | undefined;
  value: number | undefined;
}

interface Props {
  open: boolean;
  template: EngineeringTemplateApi.TemplateItem | null;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'published'): void;
}>();

const CONTROL_TYPE_OPTIONS = [
  { label: '全部控制类型（逐类合并视图）', value: '' },
  { label: 'STABLE（平稳）', value: 'STABLE' },
  { label: 'SLOW（慢速）', value: 'SLOW' },
  { label: 'FAST（快速）', value: 'FAST' },
  { label: 'LOGIC（逻辑）', value: 'LOGIC' },
];

const open = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

const step = ref<PublishFlowStep>('edit');
const loading = ref(false);
const editError = ref('');
const flowError = ref('');

const metrics = ref<MetricApi.AlgorithmParamsMetricGroup[]>([]);
const metricCode = ref<string>();
const controlType = ref('');
const paramRows = ref<ParamRow[]>([{ key: undefined, value: undefined }]);
const reason = ref('');
const currentRevision = ref<null | number>(null);

const preview = ref<EngineeringTemplateApi.OverridePreviewResult | null>(null);
const publishResult = ref<EngineeringTemplateApi.OverridePublishResult | null>(
  null,
);

const metricOptions = computed(() =>
  metrics.value.map((m) => ({
    label: `${m.metricName}（${m.metricCode}）`,
    value: m.metricCode,
  })),
);

/** 当前指标的参数元数据（键全集 = 注册表） */
const paramMeta = computed<Record<string, MetricApi.AlgorithmParamMeta>>(() => {
  const metric = metrics.value.find((m) => m.metricCode === metricCode.value);
  return metric?.paramMeta ?? {};
});

const paramKeyOptions = computed(() =>
  Object.entries(paramMeta.value).map(([key, meta]) => ({
    label: meta.label ? `${meta.label}（${key}）` : key,
    value: key,
  })),
);

const beforeAfterDiff = computed(() =>
  preview.value
    ? diffProfileEntries(preview.value.before, preview.value.after)
    : [],
);

/** 合并视图仅展示本次编辑键的 base→result（噪音控制） */
const mergeRows = computed(() => {
  if (!preview.value) return [];
  const editedKeys = Object.keys(preview.value.after);
  const rows: {
    base: unknown;
    controlType: string;
    key: string;
    result: unknown;
  }[] = [];
  for (const item of preview.value.mergePreview) {
    for (const key of editedKeys) {
      rows.push({
        controlType: item.controlType,
        key,
        base: item.base[key],
        result: item.result[key],
      });
    }
  }
  return rows;
});

const mergeColumns: TableColumnsType = [
  { title: '控制类型', dataIndex: 'controlType', key: 'controlType', width: 110 },
  { title: '参数键', dataIndex: 'key', key: 'key', width: 180 },
  { title: '合并基线（该层以下）', key: 'base' },
  { title: '叠加后', key: 'result' },
];

function errorMessage(error: any): string {
  return (
    error?.response?.data?.message ??
    error?.data?.message ??
    error?.message ??
    '请求失败'
  );
}

async function loadContext() {
  loading.value = true;
  flowError.value = '';
  try {
    const [registry, revisionInfo] = await Promise.all([
      getAlgorithmParamsApi(),
      getConfigRevisionApi(),
    ]);
    metrics.value = registry.metrics;
    currentRevision.value = revisionInfo.revision;
    metricCode.value = registry.metrics[0]?.metricCode ?? undefined;
  } catch (error: any) {
    flowError.value = `加载参数注册表失败：${errorMessage(error)}`;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (value) => {
    if (value) {
      step.value = 'edit';
      editError.value = '';
      flowError.value = '';
      preview.value = null;
      publishResult.value = null;
      paramRows.value = [{ key: undefined, value: undefined }];
      reason.value = '';
      controlType.value = '';
      loadContext();
    }
  },
  { immediate: true },
);

function addParamRow() {
  paramRows.value.push({ key: undefined, value: undefined });
}

function removeParamRow(index: number) {
  paramRows.value.splice(index, 1);
}

/** 组装 params（键唯一；bool 元数据按 0/1 数值口径） */
function buildParams(): { error?: string; ok: boolean; params?: Record<string, any>; } {
  const params: Record<string, any> = {};
  for (const row of paramRows.value) {
    if (row.key === undefined && row.value === undefined) continue;
    if (!row.key) return { ok: false, error: '存在未选择参数键的行' };
    if (row.value === undefined || Number.isNaN(row.value)) {
      return { ok: false, error: `参数 ${row.key} 未填写值` };
    }
    if (Object.prototype.hasOwnProperty.call(params, row.key)) {
      return { ok: false, error: `参数 ${row.key} 重复` };
    }
    const meta = paramMeta.value[row.key];
    params[row.key] =
      meta?.type === 'int' || meta?.type === 'bool'
        ? Math.trunc(row.value)
        : row.value;
  }
  if (Object.keys(params).length === 0) {
    return { ok: false, error: '请至少填写一个参数（整组替换语义）' };
  }
  return { ok: true, params };
}

/** 步骤 1→2：真实 dry-run 预览（零写入；校验/差异/影响回路/合并视图） */
async function startPreview() {
  if (!props.template || !metricCode.value) return;
  const built = buildParams();
  if (!built.ok) {
    editError.value = built.error ?? '参数非法';
    return;
  }
  if (!reason.value.trim()) {
    editError.value = '请填写发布原因（入审计）';
    return;
  }
  editError.value = '';
  const next = reducePublishFlow(step.value, 'START_PREVIEW');
  if (next === step.value) return;
  step.value = next;
  loading.value = true;
  try {
    preview.value = await previewConfigOverrideApi({
      layer: 'TEMPLATE',
      scopeId: props.template.templateKey,
      metricCode: metricCode.value,
      params: built.params ?? {},
      ...(controlType.value ? { controlType: controlType.value } : {}),
      ...(currentRevision.value === null
        ? {}
        : { expectedRevision: currentRevision.value }),
    });
    step.value = reducePublishFlow(step.value, 'PREVIEW_OK');
  } catch (error: any) {
    flowError.value = `覆盖预览失败：${errorMessage(error)}`;
    step.value = reducePublishFlow(step.value, 'PREVIEW_ERROR');
  } finally {
    loading.value = false;
  }
}

function backToEdit() {
  step.value = reducePublishFlow(step.value, 'BACK_TO_EDIT');
}

/** 步骤 3→4/5：真实发布（ADMIN；expectedRevision 乐观锁） */
async function confirmPublish() {
  if (!props.template || !metricCode.value || currentRevision.value === null) {
    return;
  }
  if (!canConfirmPublish(step.value)) return;
  const built = buildParams();
  if (!built.ok) {
    step.value = reducePublishFlow(step.value, 'BACK_TO_EDIT');
    editError.value = built.error ?? '参数非法';
    return;
  }
  step.value = reducePublishFlow(step.value, 'CONFIRM_PUBLISH');
  try {
    publishResult.value = await publishConfigOverrideApi({
      layer: 'TEMPLATE',
      scopeId: props.template.templateKey,
      metricCode: metricCode.value,
      params: built.params ?? {},
      expectedRevision: currentRevision.value,
      reason: reason.value.trim(),
      ...(controlType.value ? { controlType: controlType.value } : {}),
    });
    step.value = reducePublishFlow(step.value, 'PUBLISH_OK');
    emit('published');
  } catch (error: any) {
    const status = error?.response?.status ?? error?.status;
    flowError.value =
      status === 409
        ? '配置版本冲突（409）：发布期间已有其他发布生效，请返回编辑并重新预览。'
        : `发布失败：${errorMessage(error)}`;
    step.value = reducePublishFlow(step.value, 'PUBLISH_ERROR');
  }
}

function handleClose() {
  open.value = false;
}

/** 发布闸门：预览校验不通过（含跨层颠倒）或 revision 已漂移时不允许确认 */
const confirmDisabled = computed(() => {
  if (!preview.value) return true;
  return !preview.value.validation.ok || preview.value.wouldConflict;
});
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`阈值覆盖发布 — ${template?.name ?? ''}（TEMPLATE 层 · ${template?.templateKey ?? ''}）`"
    :footer="null"
    width="800px"
    @cancel="handleClose"
  >
    <Spin :spinning="loading && step !== 'publishing'">
      <Alert
        v-if="flowError && step === 'edit'"
        type="error"
        :message="flowError"
        class="mb-3"
        show-icon
      />

      <!-- 步骤 1：编辑覆盖参数 -->
      <div v-if="step === 'edit'" data-testid="override-edit">
        <div class="mb-3 flex items-center gap-2">
          <span class="w-20 text-sm">评估指标</span>
          <Select
            v-model:value="metricCode"
            :options="metricOptions"
            style="width: 320px"
            placeholder="选择指标"
            data-testid="metric-select"
          />
        </div>
        <div class="mb-3 flex items-center gap-2">
          <span class="w-20 text-sm">控制类型</span>
          <Select
            v-model:value="controlType"
            :options="CONTROL_TYPE_OPTIONS"
            style="width: 320px"
          />
        </div>
        <div class="mb-1 flex items-center justify-between">
          <span class="text-sm font-medium">覆盖参数（整组替换语义）</span>
          <Button size="small" data-testid="btn-add-param" @click="addParamRow">
            添加参数
          </Button>
        </div>
        <div
          v-for="(row, index) in paramRows"
          :key="index"
          class="mb-2 flex items-center gap-2"
        >
          <Select
            v-model:value="row.key"
            :options="paramKeyOptions"
            style="width: 360px"
            placeholder="参数键（注册表单源）"
            show-search
            option-filter-prop="label"
          />
          <InputNumber
            v-model:value="row.value"
            :precision="paramMeta[row.key ?? '']?.type === 'float' ? undefined : 0"
            :min="paramMeta[row.key ?? '']?.min"
            :max="paramMeta[row.key ?? '']?.max"
            style="width: 160px"
            placeholder="值"
          />
          <span class="text-xs text-gray-400">
            {{ paramMeta[row.key ?? '']?.unit ?? '' }}
          </span>
          <Button
            size="small"
            type="link"
            danger
            :disabled="paramRows.length <= 1"
            @click="removeParamRow(index)"
          >
            移除
          </Button>
        </div>
        <div class="mb-1 mt-3 text-sm font-medium">发布原因（必填，入审计）</div>
        <Input
          v-model:value="reason"
          :maxlength="500"
          placeholder="如：按标定结果收紧振荡率相似度阈值"
          data-testid="override-reason"
        />
        <Alert
          v-if="editError"
          type="warning"
          :message="editError"
          class="mt-2"
          show-icon
        />
      </div>

      <!-- 步骤 2：预览结果 -->
      <div v-if="step === 'previewed' && preview" data-testid="override-preview">
        <Alert
          v-if="!preview.validation.ok"
          type="error"
          show-icon
          class="mb-2"
          message="参数校验未通过（含跨层组合约束），发布已禁用："
          :description="preview.validation.errors.join('；')"
          data-testid="validation-errors"
        />
        <Alert
          v-else
          type="success"
          show-icon
          class="mb-2"
          message="参数校验通过（含跨层组合约束）。"
        />
        <Alert
          v-if="preview.wouldConflict"
          type="warning"
          show-icon
          class="mb-2"
          message="配置 revision 已漂移（期间有他人发布）——请返回编辑重新预览后再发布。"
          data-testid="would-conflict"
        />
        <div class="mb-2 text-sm" data-testid="override-affected">
          影响回路：
          <span v-if="preview.affectedLoops !== null" class="font-bold">
            {{ preview.affectedLoops }}
          </span>
          <span v-else class="text-orange-500">未统计</span>
          条
          <span v-if="preview.note" class="ml-2 text-xs text-gray-500">
            {{ preview.note }}
          </span>
        </div>
        <div v-if="preview.affectedLoopSample.length > 0" class="mb-2 flex flex-wrap gap-1">
          <Tag v-for="loop in preview.affectedLoopSample" :key="loop.loopId">
            {{ loop.tagName }}
          </Tag>
        </div>
        <div class="mb-1 text-sm font-medium">before / after 差异</div>
        <ProfileDiffView
          :entries="beforeAfterDiff"
          base-label="既有覆盖"
          compare-label="本次发布"
        />
        <div class="mb-1 mt-3 text-sm font-medium">合并视图（该层以下 → 叠加后）</div>
        <Table
          :columns="mergeColumns"
          :data-source="mergeRows"
          :pagination="false"
          :row-key="(r: any) => `${r.controlType}-${r.key}`"
          size="small"
          :scroll="{ y: 200 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'base' || column.key === 'result'">
              <span class="font-mono text-xs">
                {{ (record as any)[column.key as string] ?? '—' }}
              </span>
            </template>
          </template>
        </Table>
      </div>

      <!-- 步骤 4：发布结果 -->
      <div v-if="step === 'done' && publishResult" data-testid="override-done">
        <Alert
          type="success"
          show-icon
          class="mb-3"
          :message="`TEMPLATE 层覆盖已发布（revision ${publishResult.revision}）`"
        />
        <div class="space-y-1 text-sm">
          <div>
            影响回路：{{ publishResult.affectedLoops ?? '未统计' }} 条
            <span v-if="publishResult.note" class="ml-2 text-xs text-gray-500">
              {{ publishResult.note }}
            </span>
          </div>
          <div>
            回退引用 rollbackRevision=
            <span class="font-mono">{{ publishResult.rollbackRevision }}</span>
            （如需回退：发布账本 Tab 对本次 revision 执行回退）
          </div>
        </div>
      </div>

      <!-- 步骤 5：失败 -->
      <div v-if="step === 'failed'" data-testid="override-failed">
        <Alert type="error" show-icon :message="flowError" />
      </div>

      <!-- 底部操作条 -->
      <div class="mt-4 flex justify-end gap-2">
        <Button v-if="step === 'edit'" @click="handleClose">取消</Button>
        <Button
          v-if="step === 'edit'"
          type="primary"
          :disabled="!metricCode"
          :loading="loading"
          data-testid="btn-override-preview"
          @click="startPreview"
        >
          dry-run 覆盖预览
        </Button>
        <Button v-if="step === 'previewed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'previewed'"
          type="primary"
          :disabled="confirmDisabled"
          data-testid="btn-override-publish"
          @click="confirmPublish"
        >
          确认发布
        </Button>
        <Button v-if="step === 'publishing'" type="primary" loading disabled>
          发布中…
        </Button>
        <Button v-if="step === 'failed'" @click="backToEdit">返回修改</Button>
        <Button
          v-if="step === 'failed'"
          type="primary"
          data-testid="btn-override-repreview"
          @click="startPreview"
        >
          重新预览
        </Button>
        <Button v-if="step === 'done'" type="primary" @click="handleClose">完成</Button>
      </div>
    </Spin>
  </Modal>
</template>
