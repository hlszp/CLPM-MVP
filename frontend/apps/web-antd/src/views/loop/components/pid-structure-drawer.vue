<script lang="ts" setup>
/**
 * PID 结构模板编辑抽屉
 *
 * 从 system/pid-template/index.vue 迁移，改为 props/emits 组件契约。
 * 在「链路配置 → DCS 型号映射」Tab 中，点击 PID 状态 Tag 打开此抽屉。
 */
import type { DcsApi } from '#/api/dcs';

import { computed, reactive, ref, watch } from 'vue';

import {
  Button,
  Drawer,
  message,
  Select,
  Switch,
  Textarea,
} from 'ant-design-vue';

import { deletePidStructureApi, upsertPidStructureApi } from '#/api/dcs';
import { ClpmDangerConfirmModal } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'PidStructureDrawer' });

const props = defineProps<{
  model: null | PidModelRow;
  open: boolean;
}>();

const emit = defineEmits<{
  deleted: [modelId: string];
  success: [data: DcsApi.PidStructure];
  'update:open': [v: boolean];
}>();

// ---------------------------------------------------------------------------
// Props & Emits
// ---------------------------------------------------------------------------

interface PidModelRow {
  id: string;
  code: string;
  name: string;
  vendorName?: null | string;
  structure?: DcsApi.PidStructure | null;
}

const { themeColors } = useClpmTheme();

// ---------------------------------------------------------------------------
// 枚举元数据
// ---------------------------------------------------------------------------

const P_TYPE_OPTIONS = [
  { label: '增益（PROPORTION）', value: 'PROPORTION' },
  { label: '比例度（PROPORTION_BAND）', value: 'PROPORTION_BAND' },
];

const UNIT_OPTIONS = [
  { label: '秒（SECONDS）', value: 'SECONDS' },
  { label: '分（MINUTES）', value: 'MINUTES' },
];

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const saving = ref(false);

const form = reactive<{
  description: string;
  dFilterEnabled: boolean;
  dFilterMultiplier: boolean;
  dFilterUnit: 'MINUTES' | 'SECONDS' | undefined;
  dUnit: DcsApi.PidStructure['dUnit'];
  iUnit: DcsApi.PidStructure['iUnit'];
  pType: DcsApi.PidStructure['pType'];
}>({
  pType: 'PROPORTION',
  iUnit: 'SECONDS',
  dUnit: 'SECONDS',
  dFilterEnabled: false,
  dFilterUnit: undefined,
  dFilterMultiplier: false,
  description: '',
});

/** 删除确认 */
const deleteOpen = ref(false);

// ---------------------------------------------------------------------------
// 校验
// ---------------------------------------------------------------------------

/** 启用微分滤波时单位必填（与后端 CHECK 约束一致） */
const violation = computed<null | string>(() => {
  if (form.dFilterEnabled && !form.dFilterUnit) {
    return '启用微分滤波时必须选择滤波单位';
  }
  return null;
});

// ---------------------------------------------------------------------------
// model 变化时重置 form
// ---------------------------------------------------------------------------

watch(
  () => props.model,
  (model) => {
    if (!model) return;
    const s = model.structure;
    form.pType = s?.pType ?? 'PROPORTION';
    form.iUnit = s?.iUnit ?? 'SECONDS';
    form.dUnit = s?.dUnit ?? 'SECONDS';
    form.dFilterEnabled = s?.dFilterEnabled ?? false;
    form.dFilterUnit = s?.dFilterUnit ?? undefined;
    form.dFilterMultiplier = s?.dFilterMultiplier ?? false;
    form.description = s?.description ?? '';
  },
  { immediate: true },
);

// ---------------------------------------------------------------------------
// 保存
// ---------------------------------------------------------------------------

async function handleSave() {
  const model = props.model;
  if (!model) return;
  if (violation.value) {
    message.warning(violation.value);
    return;
  }
  saving.value = true;
  try {
    const data = await upsertPidStructureApi(model.id, {
      pType: form.pType,
      iUnit: form.iUnit,
      dUnit: form.dUnit,
      dFilterEnabled: form.dFilterEnabled,
      dFilterUnit: form.dFilterEnabled ? (form.dFilterUnit ?? null) : null,
      dFilterMultiplier: form.dFilterMultiplier,
      description: form.description || null,
    });
    message.success('PID 结构模板保存成功');
    emit('success', data);
    emit('update:open', false);
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

// ---------------------------------------------------------------------------
// 删除
// ---------------------------------------------------------------------------

function handleAskDelete() {
  deleteOpen.value = true;
}

async function handleConfirmDelete() {
  const model = props.model;
  if (!model) return;
  try {
    await deletePidStructureApi(model.id);
    message.success('已删除 PID 结构模板');
    emit('deleted', model.id);
    deleteOpen.value = false;
    emit('update:open', false);
  } catch {
    // 错误已由拦截器处理
  }
}

function handleOpenChange(v: boolean) {
  emit('update:open', v);
}
</script>

<template>
  <Drawer
    :open="open"
    :title="`PID 结构模板 · ${model?.code ?? ''}`"
    :width="520"
    :destroy-on-close="true"
    @update:open="handleOpenChange"
  >
    <div
      v-if="model"
      class="mb-4 text-sm"
      :style="{ color: themeColors.NEUTRAL }"
    >
      型号：{{ model.name }}（{{ model.code }}）
    </div>

    <div class="space-y-4">
      <div>
        <div class="mb-1 text-sm">比例项类型</div>
        <Select
          v-model:value="form.pType"
          :options="P_TYPE_OPTIONS"
          style="width: 100%"
        />
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <div class="mb-1 text-sm">积分时间单位</div>
          <Select
            v-model:value="form.iUnit"
            :options="UNIT_OPTIONS"
            style="width: 100%"
          />
        </div>
        <div>
          <div class="mb-1 text-sm">微分时间单位</div>
          <Select
            v-model:value="form.dUnit"
            :options="UNIT_OPTIONS"
            style="width: 100%"
          />
        </div>
      </div>

      <div
        class="rounded p-3"
        :style="{ background: 'hsl(var(--muted) / 42%)' }"
      >
        <div class="mb-2 flex items-center justify-between">
          <span class="text-sm font-medium">微分滤波</span>
          <Switch v-model:checked="form.dFilterEnabled" />
        </div>
        <div v-if="form.dFilterEnabled" class="space-y-3">
          <div>
            <div class="mb-1 text-xs" :style="{ color: themeColors.NEUTRAL }">
              滤波单位
            </div>
            <Select
              v-model:value="form.dFilterUnit"
              :options="UNIT_OPTIONS"
              placeholder="请选择单位"
              style="width: 100%"
            />
          </div>
          <div class="flex items-center justify-between">
            <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              乘法因子（True=乘法，False=加法）
            </span>
            <Switch v-model:checked="form.dFilterMultiplier" />
          </div>
        </div>
        <div
          v-if="violation"
          class="mt-2 text-xs"
          :style="{ color: 'hsl(var(--status-warning))' }"
        >
          {{ violation }}
        </div>
      </div>

      <div>
        <div class="mb-1 text-sm">描述</div>
        <Textarea
          v-model:value="form.description"
          :rows="3"
          :maxlength="500"
          placeholder="可选，记录该型号 PID 结构的特殊说明"
        />
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between">
        <Button v-if="model?.structure" danger @click="handleAskDelete">
          删除
        </Button>
        <div v-else></div>
        <div class="flex gap-2">
          <Button @click="handleOpenChange(false)">取消</Button>
          <Button type="primary" :loading="saving" @click="handleSave">
            保存
          </Button>
        </div>
      </div>
    </template>

    <!-- 删除确认 -->
    <ClpmDangerConfirmModal
      v-model:open="deleteOpen"
      title="删除 PID 结构模板"
      action="删除"
      :target="model?.code ?? ''"
      impact-scope="该型号将恢复为默认 PID 结构假设（增益/秒），可重新配置恢复"
      rollback-tip="此操作可逆，重新进入配置即可恢复"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      confirm-text="删除"
      @confirm="handleConfirmDelete"
    />
  </Drawer>
</template>
