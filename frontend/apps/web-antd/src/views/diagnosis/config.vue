<script lang="ts" setup>
/**
 * 诊断配置页（单页合并版）
 *
 * - 原两 Tab（诊断指标字典 / 诊断配置管理）合并为单页列表：
 *   主表 = 诊断配置（CRUD），算子字典信息（家族/输出指标/默认阈值参数/所需信号）
 *   通过 diagCode 关联内嵌到「详情」弹窗与算子列展示
 * - 顶部动作：帮助符号 / 版本（生效—失效时间 + 回滚）/ 刷新 / 新增配置
 * - 版本管理：每次增删改自动归档全量快照为新版本（后端 sys_config 快照模式）
 * - 仅 ADMIN 可写；非 ADMIN 只读
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
  Textarea,
} from 'ant-design-vue';

import {
  createDiagnosisConfigApi,
  deleteDiagnosisConfigApi,
  type DiagnosisApi,
  type DiagnosisConfigApi,
  getDiagnosisConfigHistoryApi,
  getDiagnosisConfigsApi,
  getDiagnosisOperatorsApi,
  getDiagnosisReviewFeedbackApi,
  rollbackDiagnosisConfigApi,
  updateDiagnosisConfigsApi,
} from '#/api/diagnosis';
import {
  ClpmHelpIcon,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmVersionHistoryModal,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

// 16 号文 F6：复核反馈统计（色阶/分类元数据复用诊断常量表，零新增 hex）
import {
  CATEGORY_META,
  confirmRateColor,
  REVIEW_HINT_COLOR,
} from './constants';

defineOptions({ name: 'DiagnosisConfig' });

const { isAdmin } = useClpmRoles();

// ---------------------------------------------------------------------------
// 常量：症状码（diag_code）与算子家族的中文映射
// ---------------------------------------------------------------------------

const DIAG_CODE_TEXT: Record<string, string> = {
  EXTERNAL_DISTURBANCE: '外扰频繁',
  MANUAL_REVIEW: '人工复核',
  OSCILLATION: '振荡',
  OUTPUT_SATURATION: '输出饱和',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  QUALITY_ABNORMAL: 'PV 质量异常',
  VALVE_STICTION: '阀门粘滞',
};

const FAMILY_TEXT: Record<string, { color: string; label: string }> = {
  disturbance: { color: 'purple', label: '外扰' },
  link: { color: 'red', label: '链路' },
  oscillation: { color: 'orange', label: '振荡' },
  saturation: { color: 'gold', label: '饱和' },
  sensor: { color: 'cyan', label: '仪表' },
  stiction: { color: 'geekblue', label: '粘滞' },
  tuning: { color: 'blue', label: '整定' },
};

function diagCodeText(code: null | string): string {
  return code ? (DIAG_CODE_TEXT[code] ?? code) : '-';
}

function familyMeta(family: string) {
  return FAMILY_TEXT[family] ?? { color: 'default', label: family || '-' };
}

/** 诊断代码枚举（后端 DiagnosisLabel Literal；新增仅可从中选择） */
const DIAG_CODE_OPTIONS = Object.keys(DIAG_CODE_TEXT);

/** 帮助内容（汇总原两 Tab 说明） */
const HELP_CONTENT = [
  '诊断配置页（单页）：管理 8 类诊断标签的全局默认配置（算法类型/计算方法/算法参数/阈值/启停），支持新增、编辑、删除与行内启停，仅 ADMIN 可操作。',
  '· 诊断指标（算子）为 11 个诊断元算子的代码级注册表（家族/输出指标/默认阈值参数/所需信号），不可通过配置新增算子——其完整信息见每行「详情」弹窗。',
  '· 生效优先级：全局默认 < 回路类型模板 < 装置 < 回路（修改全局默认不影响已配置的覆盖层）。',
  '· 每次增删改自动归档全量快照为新版本并立即生效；「版本」入口可查看各版本生效—失效时间并回滚。',
].join('\n');

/** 键值对 schema → 紧凑展示串（k=v；中文说明）；对象值 JSON 序列化防 [object Object] */
function schemaEntriesText(
  schema: null | Record<string, unknown> | undefined,
): string {
  if (!schema) return '-';
  const entries = Object.entries(schema);
  if (entries.length === 0) return '-';
  return entries
    .map(([k, v]) => {
      const val =
        v !== null && typeof v === 'object' ? JSON.stringify(v) : String(v);
      return `${k}=${val}`;
    })
    .join('；');
}

// ---------------------------------------------------------------------------
// 数据加载：算子字典 + 诊断配置
// ---------------------------------------------------------------------------

const operatorsLoading = ref(false);
const operators = ref<DiagnosisApi.OperatorInfo[]>([]);

async function loadOperators() {
  operatorsLoading.value = true;
  try {
    operators.value = await getDiagnosisOperatorsApi();
  } finally {
    operatorsLoading.value = false;
  }
}

/** 按 diagCode 关联算子（一个配置对应一个或多个算子；展示首个 + 数量） */
function operatorsOf(diagKey: null | string): DiagnosisApi.OperatorInfo[] {
  if (!diagKey) return [];
  return operators.value.filter((op) => op.diagCode === diagKey);
}

const configsLoading = ref(false);
const configs = ref<DiagnosisConfigApi.ConfigItem[]>([]);
const currentVersion = ref(0);

/** 已存在的诊断代码（新增时禁用，防重复提交 409） */
const existingDiagKeys = computed(
  () => new Set(configs.value.map((c) => c.diagKey ?? '')),
);

async function loadConfigs() {
  configsLoading.value = true;
  try {
    const resp = await getDiagnosisConfigsApi();
    configs.value = resp.items ?? [];
    // 版本号从历史接口头部获取（轻量：仅列表页首次加载时同步一次）
    void syncVersion();
  } finally {
    configsLoading.value = false;
  }
}

async function syncVersion() {
  try {
    const data = await getDiagnosisConfigHistoryApi();
    currentVersion.value = data.currentVersion ?? 0;
  } catch {
    // 非管理员或接口异常时静默
  }
}

// ---------------------------------------------------------------------------
// 主表格：诊断配置列表（含算子关联列）
// ---------------------------------------------------------------------------

const configColumns: TableColumnsType = [
  {
    title: '诊断代码',
    dataIndex: 'diagKey',
    key: 'diagKey',
    width: 150,
    customRender: ({ text }) => diagCodeText(text),
  },
  { title: '名称', dataIndex: 'diagName', key: 'diagName', width: 120 },
  {
    title: '算子（家族）',
    key: 'operators',
    width: 170,
    ellipsis: true,
  },
  { title: '算法类型', dataIndex: 'algorithmType', key: 'algorithmType', width: 120 },
  { title: '计算方法', dataIndex: 'calcMethod', key: 'calcMethod', width: 150 },
  {
    title: '启用',
    dataIndex: 'isEnabled',
    key: 'isEnabled',
    width: 90,
  },
  {
    title: '更新',
    key: 'updated',
    width: 150,
  },
  { title: '操作', key: 'actions', width: 150, fixed: 'right' },
];

// ---------------------------------------------------------------------------
// 详情弹窗（配置 + 关联算子字典）
// ---------------------------------------------------------------------------

const detailOpen = ref(false);
const detailTarget = ref<DiagnosisConfigApi.ConfigItem | null>(null);

function handleDetail(record: DiagnosisConfigApi.ConfigItem) {
  detailTarget.value = record;
  detailOpen.value = true;
}

// ---------------------------------------------------------------------------
// 编辑 / 新增（共用 Modal 表单 + JSON 文本域）
// ---------------------------------------------------------------------------

type EditMode = 'create' | 'update';

const modalVisible = ref(false);
const modalMode = ref<EditMode>('update');
const saving = ref(false);

const form = reactive({
  diagId: '',
  diagKey: '',
  diagName: '',
  algorithmType: '',
  calcMethod: '',
  isEnabled: true,
  paramsText: '{}',
  thresholdText: '{}',
});

/** JSON 文本域解析错误（Poka-Yoke：错误前置提示，禁止提交） */
const paramsError = ref('');
const thresholdError = ref('');

function parseJsonText(text: string, errRef: { value: string }): null | Record<string, any> {
  const trimmed = text.trim();
  if (!trimmed) {
    errRef.value = '';
    return null;
  }
  try {
    const parsed = JSON.parse(trimmed);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      errRef.value = '必须是 JSON 对象（如 {"key": value}）';
      return null;
    }
    errRef.value = '';
    return parsed;
  } catch {
    errRef.value = 'JSON 格式错误，请检查括号/引号/逗号';
    return null;
  }
}

const modalTitle = computed(() =>
  modalMode.value === 'create' ? '新增诊断配置' : '编辑诊断配置',
);

/** 新增时从关联算子带出默认算法信息 */
function applyOperatorDefaults(diagKey: string) {
  const op = operatorsOf(diagKey)[0];
  if (op) {
    form.diagName = form.diagName || diagCodeText(diagKey);
    form.algorithmType = form.algorithmType || op.family.toUpperCase();
    form.thresholdText = JSON.stringify(op.thresholdSchema ?? {}, null, 2);
  }
}

function openEditModal(record: DiagnosisConfigApi.ConfigItem) {
  modalMode.value = 'update';
  form.diagId = record.diagId;
  form.diagKey = record.diagKey ?? '';
  form.diagName = record.diagName ?? '';
  form.algorithmType = record.algorithmType ?? '';
  form.calcMethod = record.calcMethod ?? '';
  form.isEnabled = record.isEnabled;
  form.paramsText = JSON.stringify(record.params ?? {}, null, 2);
  form.thresholdText = JSON.stringify(record.threshold ?? {}, null, 2);
  paramsError.value = '';
  thresholdError.value = '';
  modalVisible.value = true;
}

function openCreateModal() {
  modalMode.value = 'create';
  form.diagId = '';
  form.diagKey = '';
  form.diagName = '';
  form.algorithmType = '';
  form.calcMethod = '';
  form.isEnabled = true;
  form.paramsText = '{}';
  form.thresholdText = '{}';
  paramsError.value = '';
  thresholdError.value = '';
  modalVisible.value = true;
}

async function handleSave() {
  if (modalMode.value === 'create' && !form.diagKey) {
    Modal.warning({ content: '请选择诊断代码（8 类标签之一）', title: '缺少诊断代码' });
    return;
  }
  const params = parseJsonText(form.paramsText, paramsError);
  if (paramsError.value) return;
  const threshold = parseJsonText(form.thresholdText, thresholdError);
  if (thresholdError.value) return;

  saving.value = true;
  try {
    if (modalMode.value === 'create') {
      await createDiagnosisConfigApi({
        algorithmType: form.algorithmType,
        calcMethod: form.calcMethod || null,
        diagKey: form.diagKey,
        diagName: form.diagName,
        isEnabled: form.isEnabled,
        params,
        threshold,
      });
      Modal.success({
        content: `已新增诊断配置 ${form.diagKey}（生成新版本）`,
        title: '新增成功',
      });
    } else {
      await updateDiagnosisConfigsApi([
        {
          algorithmType: form.algorithmType,
          calcMethod: form.calcMethod || null,
          diagId: form.diagId,
          isEnabled: form.isEnabled,
          params,
          threshold,
        },
      ]);
      Modal.success({
        content: `已保存诊断配置 ${form.diagKey}（生成新版本）`,
        title: '保存成功',
      });
    }
    modalVisible.value = false;
    await loadConfigs();
  } finally {
    saving.value = false;
  }
}

async function handleDelete(record: DiagnosisConfigApi.ConfigItem) {
  await deleteDiagnosisConfigApi(record.diagId);
  Modal.success({
    content: `已删除诊断配置 ${record.diagKey}（生成新版本；阈值覆盖等关联配置不受影响）`,
    title: '删除成功',
  });
  await loadConfigs();
}

/** 表格行内直接切换启用状态（管理员） */
async function handleToggleEnabled(
  record: DiagnosisConfigApi.ConfigItem,
  enabled: boolean,
) {
  await updateDiagnosisConfigsApi([
    { diagId: record.diagId, isEnabled: enabled },
  ]);
  record.isEnabled = enabled;
  Modal.success({
    content: `${record.diagKey} 已${enabled ? '启用' : '停用'}（生成新版本）`,
    title: enabled ? '已启用' : '已停用',
  });
  void syncVersion();
}

// ---------------------------------------------------------------------------
// 版本历史
// ---------------------------------------------------------------------------

const versionOpen = ref(false);
const versionLoading = ref(false);
const versionItems = ref<MetricApi.VersionHistoryItem[]>([]);
const rollingBack = ref(false);

async function openVersionHistory() {
  versionOpen.value = true;
  versionLoading.value = true;
  try {
    const data = await getDiagnosisConfigHistoryApi();
    versionItems.value = data.items ?? [];
    currentVersion.value = data.currentVersion ?? 0;
  } catch {
    versionItems.value = [];
  } finally {
    versionLoading.value = false;
  }
}

async function handleRollback(version: number) {
  rollingBack.value = true;
  try {
    await rollbackDiagnosisConfigApi(version);
    Modal.success({
      content: `已回滚到版本 v${version}（生成新版本）`,
      title: '回滚成功',
    });
    await Promise.all([loadConfigs(), openVersionHistory()]);
  } catch {
    // 错误已由拦截器处理
  } finally {
    rollingBack.value = false;
  }
}

// ---------------------------------------------------------------------------
// 16 号文 F6：算子表现（复核反馈统计与阈值调优提示，仅 ADMIN）
// 安全边界红线：仅"建议复核阈值"提示，调参走现有四级阈值覆盖人工操作，
// 本区块不提供任何自动调参入口。
// ---------------------------------------------------------------------------

const feedbackLoading = ref(false);
const feedback = ref<DiagnosisApi.ReviewFeedbackResult | null>(null);

async function loadFeedback() {
  if (!isAdmin.value) return; // 非管理员不请求（后端亦 403 收口）
  feedbackLoading.value = true;
  try {
    feedback.value = await getDiagnosisReviewFeedbackApi();
  } catch {
    feedback.value = null; // 错误提示由请求拦截器统一弹出
  } finally {
    feedbackLoading.value = false;
  }
}

const feedbackOperators = computed(() => feedback.value?.operators ?? []);
const feedbackCategories = computed(() => feedback.value?.categories ?? []);

function fmtPct(v: null | number): string {
  return v === null ? '—' : `${Math.round(v * 100)}%`;
}

function categoryLabel(code: null | string): string {
  return code ? (CATEGORY_META[code as DiagnosisApi.Category]?.label ?? code) : '—';
}

/** 分类色（antd bodyCell record 为 any，此处集中收口类型断言） */
function categoryColor(code: null | string): string | undefined {
  return code
    ? CATEGORY_META[code as DiagnosisApi.Category]?.color
    : undefined;
}

function overturnTopText(items: DiagnosisApi.ReviewOverturnTopItem[]): string {
  if (items.length === 0) return '—';
  return items.map((t) => `${categoryLabel(t.category)} ×${t.count}`).join('；');
}

/** D4 样本不足占位文案（"样本不足（N<10），暂不统计"） */
function insufficientText(row: any): string {
  return `样本不足（N=${row.sampleSize}<${feedback.value?.sampleMin ?? 10}），暂不统计`;
}

/** 改判率 >40% 行标琥珀（rowClassName 用） */
function feedbackRowClass(row: any): string {
  return row.tuningHint ? 'fb-row-hint' : '';
}

/**
 * 阈值调优入口：链接到该算子的阈值配置区——复用本页现有编辑交互
 * （全局默认层阈值 + 生效优先级说明），不新建交互、不自动调参。
 */
function openThresholdForOperator(row: any) {
  const cfg = configs.value.find((c) => c.diagKey === row.diagCode);
  if (!cfg) {
    Modal.info({
      title: '无对应全局默认配置',
      content: `算子「${row.displayName}」（${row.diagCode}）暂无全局默认配置，可先在上方「新增配置」创建后再调整阈值。`,
    });
    return;
  }
  openEditModal(cfg);
}

const feedbackColumns = [
  { title: '算子', key: 'operator', width: 200 },
  { title: '归因分类', key: 'category', width: 110 },
  { title: '检出次数', key: 'detectedCount', width: 80 },
  { title: '复核率', key: 'reviewRate', width: 80 },
  { title: '确认率', key: 'confirmRate', width: 90 },
  { title: '改判率', key: 'overturnRate', width: 90 },
  { title: '改判去向 Top3', key: 'overturnTop', width: 220, ellipsis: true },
  { title: '操作', key: 'actions', width: 110, fixed: 'right' as const },
];

const feedbackCategoryColumns = [
  { title: '分类', key: 'category', width: 200 },
  { title: '检出次数', key: 'detectedCount', width: 90 },
  { title: '复核率', key: 'reviewRate', width: 90 },
  { title: '确认率', key: 'confirmRate', width: 100 },
  { title: '改判率', key: 'overturnRate', width: 100 },
  { title: '改判去向 Top3', key: 'overturnTop', ellipsis: true },
];

// ---------------------------------------------------------------------------
// 工具栏（刷新 / 帮助）
// ---------------------------------------------------------------------------

const loading = computed(
  () => configsLoading.value || operatorsLoading.value,
);

async function handleRefresh() {
  await Promise.all([loadOperators(), loadConfigs(), loadFeedback()]);
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
}));

onMounted(() => {
  void loadOperators();
  void loadConfigs();
  void loadFeedback();
});

defineExpose({
  refresh: handleRefresh,
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="诊断配置"
      subtitle="诊断配置管理（算子字典内嵌详情）"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <div class="mt-4">
      <Card size="small">
        <!-- 头部：简短说明 + 帮助符号 + 动作 -->
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div class="flex items-center text-sm text-muted-foreground">
            <span>
              已配置 {{ configs.length }}/8 类诊断标签（全局默认层，
              生效优先级：全局默认 &lt; 模板 &lt; 装置 &lt; 回路）
            </span>
            <ClpmHelpIcon title="诊断配置 帮助" :content="HELP_CONTENT" />
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Tag class="mr-1">当前版本 v{{ currentVersion }}</Tag>
            <Button size="small" @click="openVersionHistory"> 版本 </Button>
            <Button
              v-if="isAdmin"
              type="primary"
              size="small"
              @click="openCreateModal()"
            >
              新增配置
            </Button>
          </div>
        </div>

        <!-- 诊断配置列表（含算子关联列） -->
        <Table
          :columns="configColumns"
          :data-source="configs"
          :loading="configsLoading"
          :pagination="false"
          row-key="diagId"
          size="small"
          :scroll="{ x: 1200 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'operators'">
              <template
                v-if="operatorsOf((record as any).diagKey).length > 0"
              >
                <Tag
                  v-for="op in operatorsOf((record as any).diagKey).slice(0, 2)"
                  :key="op.name"
                  :color="familyMeta(op.family).color"
                  class="mr-1"
                >
                  {{ op.displayName }}
                </Tag>
                <span
                  v-if="operatorsOf((record as any).diagKey).length > 2"
                  class="text-xs text-muted-foreground"
                >
                  +{{ operatorsOf((record as any).diagKey).length - 2 }}
                </span>
              </template>
              <span v-else class="text-xs text-muted-foreground">—</span>
            </template>
            <template v-else-if="column.key === 'isEnabled'">
              <Switch
                v-if="isAdmin"
                :checked="(record as any).isEnabled"
                checked-children="启"
                size="small"
                un-checked-children="停"
                @change="(v: any) => handleToggleEnabled(record as any, !!v)"
              />
              <Tag v-else :color="(record as any).isEnabled ? 'green' : 'default'">
                {{ (record as any).isEnabled ? '启用' : '停用' }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'updated'">
              <span class="text-xs text-muted-foreground">
                <template v-if="(record as any).updatedAt">
                  {{ formatTime((record as any).updatedAt) }}
                  <template v-if="(record as any).updatedBy">
                    （{{ (record as any).updatedBy }}）
                  </template>
                </template>
                <template v-else>—</template>
              </span>
            </template>
            <template v-else-if="column.key === 'actions'">
              <div class="flex items-center gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="handleDetail(record as any)"
                >
                  详情
                </Button>
                <template v-if="isAdmin">
                  <Button
                    type="link"
                    size="small"
                    @click="openEditModal(record as any)"
                  >
                    编辑
                  </Button>
                  <Popconfirm
                    :title="`确认删除诊断配置 ${diagCodeText((record as any).diagKey)}？全局默认将失效，回路阈值覆盖不受影响。`"
                    @confirm="handleDelete(record as any)"
                  >
                    <Button type="link" size="small" danger> 删除 </Button>
                  </Popconfirm>
                </template>
                <span v-if="!isAdmin" class="text-xs text-muted-foreground">只读</span>
              </div>
            </template>
          </template>
        </Table>
      </Card>

      <!-- 16 号文 F6：算子表现（复核反馈统计与阈值调优提示，仅 ADMIN 可见） -->
      <Card v-if="isAdmin" size="small" class="mt-3">
        <template #title>
          算子表现（复核反馈）
          <span class="text-xs font-normal text-muted-foreground">
            已复核 {{ feedback?.reviewedRuns ?? 0 }}/{{ feedback?.totalRuns ?? 0 }}
            次诊断
          </span>
        </template>
        <!-- 常驻口径说明（§4 F6.4） -->
        <div class="mb-2 text-xs text-muted-foreground">
          统计范围为全部历史已复核 run，改判=复核结论不含机器主分类；样本
          &lt;{{ feedback?.sampleMin ?? 10 }} 显示占位不给出比例；算子按症状标签映射分类归因，pending_review
          命中不计入改判分母。阈值调整走四级覆盖人工操作（回路&gt;装置&gt;类型&gt;全局），平台不自动调参。
        </div>
        <Spin :spinning="feedbackLoading" size="small">
          <Empty
            v-if="!feedbackLoading && !feedback"
            :image="Empty.PRESENTED_IMAGE_SIMPLE"
            description="复核反馈统计加载失败"
          >
            <Button size="small" @click="loadFeedback">重试</Button>
          </Empty>
          <div
            v-else-if="!feedbackLoading && feedback && feedback.reviewedRuns === 0"
            class="py-3 text-xs text-muted-foreground"
          >
            尚无已复核诊断记录，统计随人工复核积累生成
          </div>
          <template v-else-if="feedback">
            <!-- 按算子 -->
            <div class="fb-sec-title">按算子（{{ feedbackOperators.length }} 个）</div>
            <Table
              :columns="feedbackColumns"
              :data-source="feedbackOperators"
              :pagination="false"
              :row-class-name="feedbackRowClass"
              row-key="operator"
              size="small"
              :scroll="{ x: 1020 }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'operator'">
                  <div class="text-xs font-medium">{{ record.displayName }}</div>
                  <div class="font-mono text-xs text-muted-foreground">
                    {{ record.operator }}
                  </div>
                </template>
                <template v-else-if="column.key === 'category'">
                  <span
                    v-if="record.category"
                    :style="{ color: categoryColor(record.category) }"
                  >
                    {{ categoryLabel(record.category) }}
                  </span>
                  <span v-else class="text-muted-foreground">—</span>
                </template>
                <template v-else-if="column.key === 'detectedCount'">
                  <span class="tabular-nums">{{ record.detectedCount }}</span>
                  <span
                    v-if="record.pendingExcludedCount > 0"
                    class="ml-1 text-xs text-muted-foreground"
                    :title="`其中 ${record.pendingExcludedCount} 次命中 pending_review（机器已降级待复核），不计入改判分母`"
                  >
                    （排除待复核 {{ record.pendingExcludedCount }}）
                  </span>
                </template>
                <template v-else-if="column.key === 'reviewRate'">
                  <span class="tabular-nums">{{ fmtPct(record.reviewRate) }}</span>
                </template>
                <template v-else-if="column.key === 'confirmRate'">
                  <span
                    v-if="!record.insufficientSample"
                    class="tabular-nums"
                    :style="{ color: confirmRateColor(record.confirmRate) }"
                  >
                    {{ fmtPct(record.confirmRate) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">
                    {{ insufficientText(record) }}
                  </span>
                </template>
                <template v-else-if="column.key === 'overturnRate'">
                  <span
                    v-if="!record.insufficientSample"
                    class="tabular-nums"
                    :style="{
                      color: record.tuningHint ? REVIEW_HINT_COLOR : undefined,
                      fontWeight: record.tuningHint ? 600 : undefined,
                    }"
                  >
                    {{ fmtPct(record.overturnRate) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
                <template v-else-if="column.key === 'overturnTop'">
                  <span v-if="!record.insufficientSample" class="text-xs">
                    {{ overturnTopText(record.overturnTop) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <Button
                    v-if="record.tuningHint"
                    type="link"
                    size="small"
                    :style="{ color: REVIEW_HINT_COLOR }"
                    title="建议复核阈值（当前四级覆盖：回路>装置>类型>全局）；点击打开该算子的阈值配置"
                    @click="openThresholdForOperator(record)"
                  >
                    建议复核阈值
                  </Button>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
              </template>
            </Table>

            <!-- 按分类（8 类） -->
            <div class="fb-sec-title mt-3">按分类（8 类）</div>
            <Table
              :columns="feedbackCategoryColumns"
              :data-source="feedbackCategories"
              :pagination="false"
              row-key="category"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'category'">
                  <span :style="{ color: categoryColor(record.category) }">
                    {{ categoryLabel(record.category) }}
                  </span>
                  <span class="ml-1 font-mono text-xs text-muted-foreground">
                    {{ record.category }}
                  </span>
                </template>
                <template v-else-if="column.key === 'detectedCount'">
                  <span class="tabular-nums">{{ record.detectedCount }}</span>
                </template>
                <template v-else-if="column.key === 'reviewRate'">
                  <span class="tabular-nums">{{ fmtPct(record.reviewRate) }}</span>
                </template>
                <template v-else-if="column.key === 'confirmRate'">
                  <span
                    v-if="!record.insufficientSample"
                    class="tabular-nums"
                    :style="{ color: confirmRateColor(record.confirmRate) }"
                  >
                    {{ fmtPct(record.confirmRate) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">
                    {{ insufficientText(record) }}
                  </span>
                </template>
                <template v-else-if="column.key === 'overturnRate'">
                  <span v-if="!record.insufficientSample" class="tabular-nums">
                    {{ fmtPct(record.overturnRate) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
                <template v-else-if="column.key === 'overturnTop'">
                  <span v-if="!record.insufficientSample" class="text-xs">
                    {{ overturnTopText(record.overturnTop) }}
                  </span>
                  <span v-else class="text-xs text-muted-foreground">—</span>
                </template>
              </template>
            </Table>
          </template>
        </Spin>
      </Card>
    </div>

    <!-- 详情弹窗（配置 + 关联算子字典） -->
    <Modal
      v-model:open="detailOpen"
      title="诊断配置详情"
      :footer="null"
      width="720px"
    >
      <Descriptions
        v-if="detailTarget"
        :column="1"
        bordered
        size="small"
        class="pt-2"
      >
        <DescriptionsItem label="诊断代码">
          {{ diagCodeText(detailTarget.diagKey) }}
          <span class="ml-1 font-mono text-xs">{{ detailTarget.diagKey }}</span>
        </DescriptionsItem>
        <DescriptionsItem label="名称">
          {{ detailTarget.diagName ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="算法类型 / 计算方法">
          {{ detailTarget.algorithmType ?? '—' }} /
          {{ detailTarget.calcMethod ?? '—' }}
        </DescriptionsItem>
        <DescriptionsItem label="状态">
          {{ detailTarget.isEnabled ? '启用' : '停用' }}
        </DescriptionsItem>
        <DescriptionsItem label="算法参数（全局默认）">
          <span class="font-mono text-xs">
            {{ schemaEntriesText(detailTarget.params) }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="阈值（全局默认）">
          <span class="font-mono text-xs">
            {{ schemaEntriesText(detailTarget.threshold) }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="最近更新">
          <span class="text-xs">
            <template v-if="detailTarget.updatedAt">
              {{ formatTime(detailTarget.updatedAt) }}
              <template v-if="detailTarget.updatedBy">
                （{{ detailTarget.updatedBy }}）
              </template>
            </template>
            <template v-else>—</template>
          </span>
        </DescriptionsItem>
        <DescriptionsItem
          v-if="operatorsOf(detailTarget.diagKey).length > 0"
          label="关联算子（代码级注册表，只读）"
        >
          <div
            v-for="op in operatorsOf(detailTarget.diagKey)"
            :key="op.name"
            class="mb-2 border-b pb-2 text-xs last:mb-0 last:border-b-0 last:pb-0"
          >
            <div class="mb-1 flex items-center gap-2">
              <Tag :color="familyMeta(op.family).color">
                {{ familyMeta(op.family).label }}
              </Tag>
              <strong>{{ op.displayName }}</strong>
              <span class="font-mono text-muted-foreground">{{ op.name }}</span>
            </div>
            <div class="text-muted-foreground">{{ op.description }}</div>
            <div class="mt-1">
              输出指标：<span class="font-mono">{{
                Object.entries(op.outputsSchema ?? {})
                  .map(([k, v]) => `${k}(${v})`)
                  .join('；') || '—'
              }}</span>
            </div>
            <div class="mt-1">
              默认阈值参数：<span class="font-mono">{{
                schemaEntriesText(op.thresholdSchema)
              }}</span>
            </div>
            <div class="mt-1">
              所需信号：{{ (op.requiredSignals ?? []).join(' / ') || '—' }}
            </div>
          </div>
        </DescriptionsItem>
      </Descriptions>
    </Modal>

    <!-- 编辑 / 新增 Modal -->
    <Modal
      v-model:open="modalVisible"
      :confirm-loading="saving"
      :ok-button-props="{ disabled: !!paramsError || !!thresholdError }"
      :ok-text="isAdmin ? '保存' : '关闭'"
      cancel-text="取消"
      :title="modalTitle"
      width="640px"
      @ok="isAdmin ? handleSave() : (modalVisible = false)"
    >
      <div class="flex flex-col gap-3 py-2">
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">诊断代码</span>
          <Select
            v-if="modalMode === 'create'"
            v-model:value="form.diagKey"
            class="flex-1"
            placeholder="选择诊断代码（8 类标签之一）"
            @change="(v: any) => applyOperatorDefaults(v as string)"
          >
            <Select.Option
              v-for="code in DIAG_CODE_OPTIONS"
              :key="code"
              :disabled="existingDiagKeys.has(code)"
              :value="code"
            >
              {{ code }}（{{ diagCodeText(code) }}）{{ existingDiagKeys.has(code) ? '· 已存在' : '' }}
            </Select.Option>
          </Select>
          <Input v-else v-model:value="form.diagKey" class="flex-1" disabled />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">名称</span>
          <Input v-model:value="form.diagName" placeholder="诊断中文名" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">算法类型</span>
          <Input v-model:value="form.algorithmType" placeholder="如 IAE_FFT / SCATTER_FIT" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">计算方法</span>
          <Input v-model:value="form.calcMethod" placeholder="如 IAE_ZERO_CROSSING" class="flex-1" />
        </div>
        <div class="flex items-center gap-3">
          <span class="w-28 shrink-0 text-sm">启用</span>
          <Switch v-model:checked="form.isEnabled" checked-children="启" un-checked-children="停" />
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-sm">算法参数（JSON 对象）</span>
          <Textarea
            v-model:value="form.paramsText"
            :rows="5"
            :class="{ 'border-red-500': !!paramsError }"
            placeholder="{&quot;param&quot;: value}"
          />
          <span v-if="paramsError" class="text-xs text-red-500">{{ paramsError }}</span>
        </div>
        <div class="flex flex-col gap-1">
          <span class="text-sm">阈值（JSON 对象，全局默认层）</span>
          <Textarea
            v-model:value="form.thresholdText"
            :rows="8"
            :class="{ 'border-red-500': !!thresholdError }"
            placeholder="{&quot;threshold_key&quot;: value}"
          />
          <span v-if="thresholdError" class="text-xs text-red-500">{{ thresholdError }}</span>
          <span class="text-xs text-muted-foreground">
            修改全局默认不影响已配置的回路类型模板/装置/回路级覆盖（生效优先级：全局默认 &lt; 模板 &lt; 装置 &lt; 回路）；保存后自动生成新版本
          </span>
        </div>
      </div>
    </Modal>

    <!-- 版本历史 -->
    <ClpmVersionHistoryModal
      v-model:open="versionOpen"
      title="诊断配置 版本历史"
      :items="versionItems"
      :loading="versionLoading"
      :rolling-back="rollingBack"
      :rollbackable="isAdmin"
      @rollback="handleRollback"
    />
  </Page>
</template>

<style scoped>
/* 16 号文 F6：算子表现区块（琥珀提示色走 CSS 变量，零新增 hex） */
.fb-sec-title {
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

/* 改判率 >40% 行标琥珀（§4 F6.3） */
:deep(.fb-row-hint) td {
  background: color-mix(in srgb, var(--color-amber-500) 8%, transparent);
}
</style>
