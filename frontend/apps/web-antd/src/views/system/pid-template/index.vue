<script lang="ts" setup>
/**
 * PID 结构模板配置（P5）
 *
 * 按 DCS 型号管理 PID 结构参数（P 类型 / I·D 单位 / 微分滤波）。
 * 每个型号 1:1 至多一条结构定义；未配置时整定按默认假设处理。
 * - 列表区：型号 + 品牌 + PID 结构摘要 + 覆盖状态
 * - 编辑区：Drawer 内表单（Select 枚举 + Switch 布尔 + 描述）
 *
 * 后端：/api/v1/dcs/pid-structures、/dcs/models/{id}/pid-structure
 */
import type { TableColumnsType } from 'ant-design-vue';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Drawer,
  message,
  Select,
  Switch,
  Table,
  Tag,
  Textarea,
} from 'ant-design-vue';

import {
  deletePidStructureApi,
  getModelsApi,
  getPidStructuresApi,
  upsertPidStructureApi,
  type DcsApi,
} from '#/api/dcs';
import {
  ClpmDataCanvas,
  ClpmDangerConfirmModal,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'SystemPidTemplate' });

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

function pTypeLabel(v?: null | string) {
  return P_TYPE_OPTIONS.find((o) => o.value === v)?.label ?? v ?? '—';
}

function unitLabel(v?: null | string) {
  return UNIT_OPTIONS.find((o) => o.value === v)?.label ?? v ?? '—';
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const loading = ref(false);
const saving = ref(false);

interface ModelRow {
  id: string;
  vendorName?: null | string;
  code: string;
  name: string;
  structure?: null | DcsApi.PidStructure;
}

const rows = ref<ModelRow[]>([]);

/** 编辑态 */
const editOpen = ref(false);
const editingModel = ref<ModelRow | null>(null);
const form = reactive<{
  pType: DcsApi.PidStructure['pType'];
  iUnit: DcsApi.PidStructure['iUnit'];
  dUnit: DcsApi.PidStructure['dUnit'];
  dFilterEnabled: boolean;
  // Select 不接受 null，编辑态用 undefined，保存时转 null
  dFilterUnit: 'SECONDS' | 'MINUTES' | undefined;
  dFilterMultiplier: boolean;
  description: string;
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
const deletingModel = ref<ModelRow | null>(null);

// ---------------------------------------------------------------------------
// 表格列
// ---------------------------------------------------------------------------

const columns: TableColumnsType = [
  { title: '型号代码', dataIndex: 'code', key: 'code', width: 180 },
  { title: '型号名称', dataIndex: 'name', key: 'name', width: 180 },
  {
    title: '品牌',
    dataIndex: 'vendorName',
    key: 'vendorName',
    width: 120,
  },
  {
    title: '比例项类型',
    key: 'pType',
    width: 160,
    align: 'center',
  },
  {
    title: 'I/D 单位',
    key: 'units',
    width: 160,
    align: 'center',
  },
  {
    title: '微分滤波',
    key: 'dFilter',
    width: 120,
    align: 'center',
  },
  { title: '状态', key: 'status', width: 100, align: 'center' },
  { title: '操作', key: 'action', width: 140, fixed: 'right', align: 'center' },
];

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

async function loadData() {
  loading.value = true;
  try {
    const [models, structures] = await Promise.all([
      getModelsApi(),
      getPidStructuresApi(),
    ]);
    const structMap = new Map(
      (structures ?? []).map((s) => [s.dcsModelId, s]),
    );
    rows.value = (models ?? []).map((m) => ({
      id: m.id,
      code: m.code,
      name: m.name,
      vendorName: m.vendorName,
      structure: structMap.get(m.id) ?? null,
    }));
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 编辑
// ---------------------------------------------------------------------------

function handleOpenEdit(row: ModelRow) {
  editingModel.value = row;
  const s = row.structure;
  form.pType = s?.pType ?? 'PROPORTION';
  form.iUnit = s?.iUnit ?? 'SECONDS';
  form.dUnit = s?.dUnit ?? 'SECONDS';
  form.dFilterEnabled = s?.dFilterEnabled ?? false;
  form.dFilterUnit = s?.dFilterUnit ?? undefined;
  form.dFilterMultiplier = s?.dFilterMultiplier ?? false;
  form.description = s?.description ?? '';
  editOpen.value = true;
}

/** 启用微分滤波时单位必填（与后端 CHECK 约束一致） */
const violation = computed<string | null>(() => {
  if (form.dFilterEnabled && !form.dFilterUnit) {
    return '启用微分滤波时必须选择滤波单位';
  }
  return null;
});

async function handleSave() {
  const model = editingModel.value;
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
    // 局部刷新该行
    model.structure = data;
    message.success('PID 结构模板保存成功');
    editOpen.value = false;
    editingModel.value = null;
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

// ---------------------------------------------------------------------------
// 删除
// ---------------------------------------------------------------------------

function handleAskDelete(row: ModelRow) {
  deletingModel.value = row;
  deleteOpen.value = true;
}

async function handleConfirmDelete() {
  const model = deletingModel.value;
  if (!model) return;
  try {
    await deletePidStructureApi(model.id);
    model.structure = null;
    message.success('已删除 PID 结构模板');
    deleteOpen.value = false;
    deletingModel.value = null;
  } catch {
    // 错误已由拦截器处理
  }
}

onMounted(() => {
  loadData();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="PID 结构模板"
      subtitle="按 DCS 型号管理 PID 结构参数（P 类型 / I·D 单位 / 微分滤波）。未配置型号按默认假设（增益/秒）处理。"
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

    <div class="mt-4">
      <ClpmDataCanvas :loading="loading" title="DCS 型号 PID 结构">
        <Table
          :columns="columns"
          :data-source="rows"
          :pagination="false"
          :row-key="(record: ModelRow) => record.id"
          size="middle"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'pType'">
              <span v-if="record.structure">
                {{ pTypeLabel(record.structure.pType) }}
              </span>
              <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>

            <template v-else-if="column.key === 'units'">
              <span v-if="record.structure">
                I: {{ unitLabel(record.structure.iUnit) }}<br />
                D: {{ unitLabel(record.structure.dUnit) }}
              </span>
              <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>

            <template v-else-if="column.key === 'dFilter'">
              <Tag v-if="record.structure?.dFilterEnabled" color="blue">
                已启用
              </Tag>
              <Tag v-else-if="record.structure" color="default">未启用</Tag>
              <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
            </template>

            <template v-else-if="column.key === 'status'">
              <Tag v-if="record.structure" color="green">已配置</Tag>
              <Tag v-else color="orange">未配置</Tag>
            </template>

            <template v-else-if="column.key === 'action'">
              <Button
                v-permission="['ADMIN']"
                type="link"
                size="small"
                @click="handleOpenEdit(record as ModelRow)"
              >
                {{ record.structure ? '编辑' : '配置' }}
              </Button>
              <Button
                v-if="record.structure"
                v-permission="['ADMIN']"
                type="link"
                size="small"
                danger
                @click="handleAskDelete(record as ModelRow)"
              >
                删除
              </Button>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>

    <!-- 编辑 Drawer -->
    <Drawer
      v-model:open="editOpen"
      :title="`PID 结构模板 · ${editingModel?.code ?? ''}`"
      :width="520"
      :destroy-on-close="true"
    >
      <div v-if="editingModel" class="mb-4 text-sm" :style="{ color: themeColors.NEUTRAL }">
        型号：{{ editingModel.name }}（{{ editingModel.code }}）
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

        <div class="rounded p-3" :style="{ background: 'hsl(var(--muted) / 42%)' }">
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
        <div class="flex justify-end gap-2">
          <Button @click="editOpen = false">取消</Button>
          <Button type="primary" :loading="saving" @click="handleSave">
            保存
          </Button>
        </div>
      </template>
    </Drawer>

    <!-- 删除确认 -->
    <ClpmDangerConfirmModal
      v-model:open="deleteOpen"
      title="删除 PID 结构模板"
      action="删除"
      :target="deletingModel?.code ?? ''"
      impact-scope="该型号将恢复为默认 PID 结构假设（增益/秒），可重新配置恢复"
      rollback-tip="此操作可逆，重新进入配置即可恢复"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      confirm-text="删除"
      @confirm="handleConfirmDelete"
    />
  </Page>
</template>
