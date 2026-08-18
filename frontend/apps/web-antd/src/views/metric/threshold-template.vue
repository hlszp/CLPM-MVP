<script lang="ts" setup>
/**
 * 诊断阈值模板化与自适应（P3-02）
 *
 * 对齐 UI/UX v6.1 + 实现契约 v2.4 §诊断阈值
 * 两个子区：
 *  A. 阈值模板库管理 — 按 loop_type 查看/编辑预置模板（ADMIN 可编辑，其他只读）
 *  B. 回路推荐套用 — 选择回路查看四级合并视图，一键套用模板或微调回路级阈值
 *
 * 权限边界（Poka-Yoke）：
 *  - ADMIN：可编辑模板库（loop_type scope）+ 装置级覆盖（plant scope）+ 回路级（loop scope）
 *  - IC_ENGINEER：仅可微调回路级（loop scope），模板库只读
 *  - 其他角色：全部只读
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { LoopApi } from '#/api/loop';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Empty,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  SelectOption,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { getLoopListApi } from '#/api/loop';
import {
  ClpmDangerConfirmModal,
  ClpmEmptyState,
  ClpmInfoTip,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useTableDensity } from '#/composables/use-table-density';

defineOptions({ name: 'MetricThresholdTemplate' });

const { isAdmin } = useClpmRoles();
const { themeColors } = useClpmTheme();

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } = useTableDensity(
  'metric-threshold-template',
);

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const LOOP_TYPES = [
  { value: 'FLOW', label: '流量 (FLOW)' },
  { value: 'TEMPERATURE', label: '温度 (TEMPERATURE)' },
  { value: 'PRESSURE', label: '压力 (PRESSURE)' },
  { value: 'LEVEL', label: '液位 (LEVEL)' },
  { value: 'ANALYSIS', label: '分析 (ANALYSIS)' },
  { value: 'SPEED', label: '转速 (SPEED)' },
  { value: 'OTHER', label: '其他 (OTHER)' },
];

const SCOPE_COLOR: Record<string, string> = {
  loop_type: 'blue',
  plant: 'orange',
  loop: 'green',
};

// ---------------------------------------------------------------------------
// 算法元数据（diag_code → 中文名 + 阈值键名）
// ---------------------------------------------------------------------------

const metaMap = reactive<
  Map<string, { labelName: string; thresholdKeys: string[] }>
>(new Map());

async function loadAlgorithmMeta() {
  // 诊断模块已删除，算法元数据暂不可用
}

function diagName(diagCode: string): string {
  return metaMap.get(diagCode)?.labelName ?? diagCode;
}

// ===========================================================================
// 子区 A：阈值模板库管理
// ===========================================================================

const selectedLoopType = ref<string>('FLOW');
const templates = ref<any[]>([]);
const loadingTemplates = ref(false);

/** 当前 loop_type 的模板（按 diag_code 排序） */
const filteredTemplates = computed(() =>
  templates.value
    .filter((t) => t.scopeId === selectedLoopType.value)
    .toSorted((a, b) => a.diagCode.localeCompare(b.diagCode)),
);

async function loadTemplates() {
  // 诊断模块已删除，阈值模板暂不可用
  loadingTemplates.value = false;
}

/** 阈值键值对渲染为紧凑标签文本 */
function thresholdText(threshold: Record<string, number>): string {
  return Object.entries(threshold)
    .map(([k, v]) => `${k}=${v}`)
    .join('， ');
}

// ----- 模板编辑 Modal -----
const editModalVisible = ref(false);
const editModalTitle = ref('');
const editForm = reactive<{
  diagCode: string;
  scopeId: string;
  scopeType: string;
  threshold: Record<string, number>;
}>({ diagCode: '', scopeType: 'loop_type', scopeId: '', threshold: {} });

/** 编辑模板阈值键名列表（来自算法元数据，兜底用已有 threshold 键） */
const editKeys = computed(() => {
  const fromMeta = metaMap.get(editForm.diagCode)?.thresholdKeys ?? [];
  const fromExisting = Object.keys(editForm.threshold);
  return [...new Set([...fromMeta, ...fromExisting])];
});

function openEditModal(item: any) {
  editForm.diagCode = item.diagCode;
  editForm.scopeType = item.scopeType;
  editForm.scopeId = item.scopeId;
  editForm.threshold = { ...item.threshold };
  editModalTitle.value = `编辑模板：${diagName(item.diagCode)}（${item.scopeId}）`;
  editModalVisible.value = true;
}

async function saveTemplate() {
  message.warning('诊断模块已移除，模板保存功能暂不可用');
  editModalVisible.value = false;
}

// ===========================================================================
// 子区 B：回路推荐套用
// ===========================================================================

const loopKeyword = ref('');
const loopOptions = ref<LoopApi.LoopListItem[]>([]);
const selectedLoopId = ref<string | undefined>(undefined);
const searchingLoops = ref(false);

const recommendation = ref<any | null>(null);
const loadingRecommendation = ref(false);

const selectedLoop = computed(() =>
  loopOptions.value.find((l) => l.loopId === selectedLoopId.value),
);

async function searchLoops() {
  searchingLoops.value = true;
  try {
    const res = await getLoopListApi({
      keyword: loopKeyword.value || undefined,
      page: 1,
      pageSize: 50,
    });
    loopOptions.value = res.items ?? [];
  } finally {
    searchingLoops.value = false;
  }
}

async function loadRecommendation() {
  if (!selectedLoopId.value) {
    recommendation.value = null;
    return;
  }
  // 诊断模块已删除，阈值推荐暂不可用
  loadingRecommendation.value = false;
  recommendation.value = null;
}

function onLoopChange() {
  loadRecommendation();
}

/** 套用模板到回路级（ic_engineer 可用，可逆轻操作走 Popconfirm 确认） */
async function applyTemplate(_diagCode: string) {
  if (!selectedLoopId.value) return;
  message.warning('诊断模块已移除，阈值模板套用功能暂不可用');
}

/** 删除回路级覆盖（恢复模板/默认，可逆轻操作走 Popconfirm 确认） */
async function resetLoopOverride(_diagCode: string) {
  if (!selectedLoopId.value) return;
  message.warning('诊断模块已移除，阈值重置功能暂不可用');
}

// ----- 回路级微调 Modal -----
const tuneModalVisible = ref(false);
const tuneModalTitle = ref('');
const tuneForm = reactive<{
  diagCode: string;
  hasExisting: boolean;
  loopId: string;
  scopeId: string;
  threshold: Record<string, number>;
}>({
  diagCode: '',
  loopId: '',
  scopeId: '',
  threshold: {},
  hasExisting: false,
});

const tuneKeys = computed(() => {
  const fromMeta = metaMap.get(tuneForm.diagCode)?.thresholdKeys ?? [];
  const fromExisting = Object.keys(tuneForm.threshold);
  return [...new Set([...fromMeta, ...fromExisting])];
});

function openTuneModal(diagCode: string) {
  if (!selectedLoopId.value) return;
  const rec = recommendation.value?.recommendations.find(
    (r: any) => r.diagCode === diagCode,
  );
  tuneForm.diagCode = diagCode;
  tuneForm.loopId = selectedLoopId.value;
  tuneForm.scopeId = selectedLoopId.value;
  // 微调起点：已有 loop 覆盖则编辑之，否则以生效阈值（模板/默认）为起点
  tuneForm.hasExisting = !!rec?.loopOverride;
  tuneForm.threshold = {
    ...(rec?.loopOverride ?? rec?.effectiveThreshold),
  };
  tuneModalTitle.value = `微调回路级阈值：${diagName(diagCode)}（${
    selectedLoop.value?.tagName
  }）`;
  tuneModalVisible.value = true;
}

async function saveTune() {
  message.warning('诊断模块已移除，回路级阈值保存功能暂不可用');
  tuneModalVisible.value = false;
}

/** 删除回路级覆盖：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） */
const deleteTuneOpen = ref(false);
const deleteTuneLoading = ref(false);

function deleteTune() {
  deleteTuneOpen.value = true;
}

async function handleDeleteTuneConfirm() {
  message.warning('诊断模块已移除，回路级覆盖删除功能暂不可用');
  deleteTuneLoading.value = false;
  deleteTuneOpen.value = false;
  tuneModalVisible.value = false;
}

// ===========================================================================
// 推荐表格列定义
// ===========================================================================

const recommendColumns = computed<TableColumnsType>(() => [
  {
    title: '诊断项',
    dataIndex: 'diagCode',
    key: 'diagCode',
    width: 160,
    customRender: ({ record }) => diagName(record.diagCode),
  },
  {
    title: '全局默认',
    key: 'globalDefault',
    width: 180,
    customRender: ({ record }) => thresholdText(record.globalDefault) || '—',
  },
  {
    title: '类型模板',
    key: 'loopTypeTemplate',
    width: 180,
    customRender: ({ record }) =>
      record.loopTypeTemplate ? thresholdText(record.loopTypeTemplate) : '—',
  },
  {
    title: '装置覆盖',
    key: 'plantOverride',
    width: 160,
    customRender: ({ record }) =>
      record.plantOverride ? thresholdText(record.plantOverride) : '—',
  },
  {
    title: '回路覆盖',
    key: 'loopOverride',
    width: 160,
    customRender: ({ record }) =>
      record.loopOverride ? thresholdText(record.loopOverride) : '—',
  },
  {
    title: '生效阈值',
    key: 'effectiveThreshold',
    width: 200,
    customRender: ({ record }) =>
      h(
        Tooltip,
        {
          title: record.scopeChain
            .map((s: any) => `${s.source}${s.isApplied ? ' ✓' : ''}`)
            .join(' → '),
        },
        () =>
          h(
            Tag,
            { color: themeColors.value.INFO },
            () => thresholdText(record.effectiveThreshold) || '—',
          ),
      ),
  },
  {
    title: '操作',
    key: 'action',
    width: 200,
    fixed: 'right',
  },
]);

const templateColumns = computed<TableColumnsType>(() => [
  {
    title: '诊断项',
    dataIndex: 'diagCode',
    key: 'diagCode',
    width: 160,
    customRender: ({ record }) => diagName(record.diagCode),
  },
  {
    title: '阈值',
    dataIndex: 'threshold',
    key: 'threshold',
    customRender: ({ record }) => thresholdText(record.threshold),
  },
  {
    title: '更新人',
    dataIndex: 'updatedBy',
    key: 'updatedBy',
    width: 100,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 170,
    customRender: ({ value }) => value ?? '—',
  },
  {
    title: '操作',
    key: 'action',
    width: 100,
    fixed: 'right',
  },
]);

// ===========================================================================
// 生命周期
// ===========================================================================

onMounted(async () => {
  await loadAlgorithmMeta();
  await loadTemplates();
  await searchLoops();
});

/** P3-01：子组件暴露 refresh() 替代父组件 tabKey 强制重建 */
async function refresh() {
  await loadAlgorithmMeta();
  await loadTemplates();
  await searchLoops();
}

defineExpose({ refresh });
</script>

<template>
  <Page>
    <div class="flex flex-col gap-4">
      <!-- 子区 A：阈值模板库管理 -->
      <Card title="阈值模板库管理" size="small">
        <template #extra>
          <ClpmInfoTip
            tip="按回路类型预置的差异化阈值模板。ADMIN 可编辑，其他角色只读。模板生效优先级：全局默认 < 类型模板 < 装置覆盖 < 回路覆盖。"
          />
        </template>
        <div class="mb-3 flex items-center gap-3">
          <span class="text-sm text-muted-foreground">回路类型：</span>
          <Select
            v-model:value="selectedLoopType"
            style="width: 220px"
            @change="loadTemplates"
          >
            <SelectOption
              v-for="lt in LOOP_TYPES"
              :key="lt.value"
              :value="lt.value"
            >
              {{ lt.label }}
            </SelectOption>
          </Select>
          <Tag :color="SCOPE_COLOR.loop_type">回路类型模板</Tag>
          <span class="text-xs text-muted-foreground">
            共 {{ filteredTemplates.length }} 条模板
          </span>
          <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
          <ClpmToolbarButton
            class="ml-auto"
            icon="ant-design:column-height-outlined"
            :label="`密度：${densityLabel}`"
            :tooltip="`密度：${densityLabel}（点击切换）`"
            @click="cycleDensity"
          />
        </div>

        <Table
          :columns="templateColumns"
          :data-source="filteredTemplates"
          :loading="loadingTemplates"
          :pagination="false"
          row-key="overrideId"
          :size="tableSize"
          :scroll="{ x: 700 }"
        >
          <template #emptyText>
            <ClpmEmptyState
              description="该回路类型暂无预置模板，将使用全局默认阈值"
            />
          </template>
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'action'">
              <ClpmToolbarButton
                v-if="isAdmin"
                type="link"
                size="small"
                @click="openEditModal(record as any)"
              >
                编辑
              </ClpmToolbarButton>
              <span v-else class="text-xs text-muted-foreground">只读</span>
            </template>
          </template>
        </Table>
      </Card>

      <!-- 子区 B：回路推荐套用 -->
      <Card title="回路推荐套用" size="small">
        <template #extra>
          <ClpmInfoTip
            tip="选择回路查看其四级阈值合并视图。可一键套用类型模板为回路级覆盖，或微调回路级阈值。ic_engineer 仅可操作回路级。"
          />
        </template>
        <div class="mb-3 flex items-center gap-3">
          <span class="text-sm text-muted-foreground">选择回路：</span>
          <Select
            v-model:value="selectedLoopId"
            show-search
            :filter-option="
              (input: string, option: any) => option?.label?.includes(input)
            "
            placeholder="搜索位号选择回路"
            style="width: 320px"
            :loading="searchingLoops"
            :options="
              loopOptions.map((l) => ({
                value: l.loopId,
                label: l.tagName,
              }))
            "
            @change="onLoopChange"
          />
          <Button size="small" @click="searchLoops">刷新列表</Button>
        </div>

        <!-- 回路基本信息 -->
        <div
          v-if="recommendation"
          class="mb-3 flex flex-wrap items-center gap-2 text-sm"
        >
          <Tag color="blue">{{ recommendation.tagName }}</Tag>
          <Tag>{{ recommendation.loopType }}</Tag>
          <Tag v-if="recommendation.plantName" color="cyan">
            {{ recommendation.plantName }}
          </Tag>
          <span class="text-muted-foreground">
            生效阈值 = 全局默认 → 类型模板 → 装置覆盖 → 回路覆盖（后者覆盖前者）
          </span>
        </div>

        <Table
          v-if="recommendation"
          :columns="recommendColumns"
          :data-source="recommendation.recommendations"
          :loading="loadingRecommendation"
          :pagination="false"
          row-key="diagCode"
          :size="tableSize"
          :scroll="{ x: 1100 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'action'">
              <div class="flex gap-1">
                <Popconfirm
                  :title="`确认把 ${diagName(record.diagCode)} 模板阈值套用为回路级覆盖？已有回路级覆盖将被更新。`"
                  ok-text="确认套用"
                  cancel-text="取消"
                  @confirm="applyTemplate(record.diagCode)"
                >
                  <!-- P3-07：disabled 时增加 Tooltip 说明原因 -->
                  <ClpmToolbarButton
                    type="link"
                    size="small"
                    :disabled="!record.loopTypeTemplate"
                    :disabled-reason="
                      !record.loopTypeTemplate ? '该回路无匹配模板' : ''
                    "
                  >
                    套用模板
                  </ClpmToolbarButton>
                </Popconfirm>
                <ClpmToolbarButton
                  type="link"
                  size="small"
                  @click="openTuneModal(record.diagCode)"
                >
                  {{ record.loopOverride ? '编辑微调' : '微调' }}
                </ClpmToolbarButton>
                <Popconfirm
                  :title="`将删除 ${diagName(record.diagCode)} 的回路级覆盖，恢复为模板/全局默认值？`"
                  ok-text="确认重置"
                  ok-type="danger"
                  cancel-text="取消"
                  @confirm="resetLoopOverride(record.diagCode)"
                >
                  <ClpmToolbarButton
                    v-if="record.loopOverride"
                    type="link"
                    size="small"
                    danger
                  >
                    重置
                  </ClpmToolbarButton>
                </Popconfirm>
              </div>
            </template>
          </template>
        </Table>
        <Empty
          v-else-if="!selectedLoopId"
          description="请选择回路查看阈值推荐"
        />
      </Card>
    </div>

    <!-- 模板编辑 Modal -->
    <Modal
      v-model:open="editModalVisible"
      :title="editModalTitle"
      :ok-text="isAdmin ? '保存' : '关闭'"
      cancel-text="取消"
      width="520px"
      @ok="isAdmin ? saveTemplate() : (editModalVisible = false)"
    >
      <div class="flex flex-col gap-3 py-2">
        <div v-for="key in editKeys" :key="key" class="flex items-center gap-3">
          <span class="w-56 text-sm">{{ key }}</span>
          <InputNumber
            v-model:value="editForm.threshold[key]"
            style="width: 200px"
            :step="0.01"
            class="flex-1"
          />
        </div>
        <div v-if="editKeys.length === 0" class="text-muted-foreground">
          该诊断项无阈值键
        </div>
      </div>
    </Modal>

    <!-- 回路级微调 Modal -->
    <Modal
      v-model:open="tuneModalVisible"
      :title="tuneModalTitle"
      ok-text="保存"
      cancel-text="取消"
      width="560px"
      @ok="saveTune"
    >
      <div class="mb-3 flex items-center gap-2">
        <ClpmInfoTip
          v-if="!tuneForm.hasExisting"
          tip="当前无回路级覆盖，以生效阈值（模板/默认）为起点微调。保存后将创建回路级覆盖。"
        />
        <Tag v-else color="green">编辑已有回路级覆盖</Tag>
      </div>
      <div class="flex flex-col gap-3 py-2">
        <div v-for="key in tuneKeys" :key="key" class="flex items-center gap-3">
          <span class="w-56 text-sm">{{ key }}</span>
          <InputNumber
            v-model:value="tuneForm.threshold[key]"
            style="width: 200px"
            :step="0.01"
            class="flex-1"
          />
        </div>
        <div v-if="tuneKeys.length === 0" class="text-muted-foreground">
          该诊断项无阈值键
        </div>
      </div>
      <template #footer>
        <Button @click="tuneModalVisible = false">取消</Button>
        <Button v-if="tuneForm.hasExisting" danger @click="deleteTune">
          删除覆盖
        </Button>
        <Button type="primary" @click="saveTune">保存</Button>
      </template>
    </Modal>

    <!-- 删除回路级覆盖：危险确认弹窗（UIUX v6.1 §9.8 / §14 P-01） -->
    <ClpmDangerConfirmModal
      v-model:open="deleteTuneOpen"
      title="删除回路级覆盖"
      action="删除"
      :target="tuneForm.diagCode"
      impact-scope="删除后该回路的阈值将恢复为模板/全局默认值"
      rollback-tip="此操作不可逆，如需恢复需重新套用模板或微调"
      require-confirm-code
      confirm-code-placeholder="请输入诊断项代码以确认"
      :loading="deleteTuneLoading"
      @confirm="handleDeleteTuneConfirm"
    />
  </Page>
</template>
