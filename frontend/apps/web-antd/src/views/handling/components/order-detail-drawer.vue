<script setup lang="ts">
/**
 * 工单详情抽屉（v2.0，§8.1 Tab2 行点击打开；右侧 520px）
 *
 * 三段式：工单信息（编号/标题/回路/来源建议）→ 流转操作区（按六态渲染）→ 时间线。
 * 流转操作仅 ADMIN / IC_ENGINEER / PE_ENGINEER 可见可动（canOperate 由父页按角色下发）；
 * 只读角色显示提示条，其余信息完整可见。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { useUserStore } from '@vben/stores';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Spin,
  Tag,
  Textarea,
  Timeline,
  TimelineItem,
} from 'ant-design-vue';

import {
  cancelOrderApi,
  feedbackOrderApi,
  getHandlingOrderApi,
  getOrderKpiComparisonApi,
  startOrderApi,
  submitOrderApi,
  verifyOrderApi,
} from '#/api/handling';
import { useModules } from '#/composables/use-modules';
import { formatLocalTime } from '#/utils/format';

import {
  ACTION_DETAIL_FIELDS,
  ACTION_TYPE_TEXT,
  ORDER_SOURCE_TEXT,
  ORDER_STATUS_COLOR,
  SUGGESTION_STATUS_COLOR,
  VERIFY_RESULT_TEXT,
} from '../constants';

const props = defineProps<{
  canOperate: boolean;
  open: boolean;
  orderId: null | string;
}>();

const emit = defineEmits<{
  'update:open': [boolean];
  updated: [];
}>();

const router = useRouter();
const { moduleEnabled } = useModules();
const userStore = useUserStore();

const loading = ref(false);
const detail = ref<HandlingApi.OrderDetail | null>(null);
const loadError = ref('');

const drawerOpen = computed({
  get: () => props.open,
  set: (v: boolean) => emit('update:open', v),
});

/** 当前用户名（开工表单处置人缺省值提示） */
const currentUserName = computed(
  () => userStore.userInfo?.realName || userStore.userInfo?.username || '—',
);

async function load() {
  if (!props.orderId) return;
  loading.value = true;
  detail.value = null;
  loadError.value = '';
  try {
    detail.value = await getHandlingOrderApi(props.orderId);
    initForms(detail.value);
    if (detail.value.status === 'VERIFYING') loadKpiPreview();
  } catch (error: any) {
    loadError.value = error?.message ?? '工单加载失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.orderId],
  ([open]) => {
    if (open) load();
  },
);

// ---------------------------------------------------------------------------
// 流转操作区状态
// ---------------------------------------------------------------------------

const acting = ref(false);

/** PID 输入组（InputNumber 不接受 null，空值用 undefined） */
type PidFormValues = { d?: number; i?: number; p?: number };

/** 开工表单（PENDING/REOPENED） */
const startForm = reactive({
  handler: '',
  detail: {} as Record<string, any>,
  pidBefore: { p: undefined, i: undefined, d: undefined } as PidFormValues,
});

/** 执行反馈输入（EXECUTING） */
const feedbackText = ref('');

/** 提交验证表单（EXECUTING） */
const submitForm = reactive({
  detail: {} as Record<string, any>,
  pidAfter: { p: undefined, i: undefined, d: undefined } as PidFormValues,
});

/** 验证结论表单（VERIFYING） */
const verifyForm = reactive({ verifyNote: '', verifyRunId: '' });

/** 作废 Modal（PENDING） */
const cancelOpen = ref(false);
const cancelReason = ref('');

/** 开工表单展开状态（PENDING/REOPENED） */
const startPanelOpen = ref(false);

function initForms(d: HandlingApi.OrderDetail) {
  startForm.handler = d.handler ?? '';
  startForm.detail = {};
  const existing = d.actionDetail ?? {};
  const pb = (existing.pidBefore ?? {}) as HandlingApi.PidValues;
  startForm.pidBefore = {
    p: pb.p ?? undefined,
    i: pb.i ?? undefined,
    d: pb.d ?? undefined,
  };
  submitForm.detail = { ...existing };
  const pa = (existing.pidAfter ?? {}) as HandlingApi.PidValues;
  submitForm.pidAfter = {
    p: pa.p ?? undefined,
    i: pa.i ?? undefined,
    d: pa.d ?? undefined,
  };
  feedbackText.value = '';
  verifyForm.verifyNote = '';
  verifyForm.verifyRunId = '';
  cancelReason.value = '';
  startPanelOpen.value = false;
}

/** 当前类型的详情字段 schema（TUNING 的 P/I/D 组单独渲染） */
const detailFields = computed(() =>
  detail.value?.actionType
    ? ACTION_DETAIL_FIELDS[detail.value.actionType]
    : [],
);

function pidProvided(pid: PidFormValues): boolean {
  return pid.p != null || pid.i != null || pid.d != null;
}

function cleanDetail(raw: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(raw)) {
    if (v !== null && v !== undefined && v !== '') out[k] = v;
  }
  return out;
}

async function runAction(
  fn: () => Promise<HandlingApi.OrderDetail>,
  okText: string,
) {
  acting.value = true;
  try {
    const d = await fn();
    detail.value = d;
    initForms(d);
    message.success(okText);
    emit('updated');
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

function handleStart() {
  if (!props.orderId) return;
  const body: HandlingApi.StartOrderBody = {
    handler: startForm.handler.trim() || undefined,
    actionDetail: cleanDetail(startForm.detail),
  };
  if (detail.value?.actionType === 'TUNING' && pidProvided(startForm.pidBefore)) {
    body.pidBefore = cleanDetail(startForm.pidBefore) as HandlingApi.PidValues;
  }
  runAction(() => startOrderApi(props.orderId!, body), '已开工，进入执行中');
}

function openCancel() {
  cancelReason.value = '';
  cancelOpen.value = true;
}

function handleCancel() {
  if (!props.orderId) return;
  if (!cancelReason.value.trim()) {
    message.warning('请填写作废原因');
    return;
  }
  cancelOpen.value = false;
  runAction(
    () =>
      cancelOrderApi(props.orderId!, {
        cancelReason: cancelReason.value.trim(),
      }),
    '工单已作废',
  );
}

function handleFeedback() {
  if (!props.orderId) return;
  if (!feedbackText.value.trim()) {
    message.warning('请填写反馈内容');
    return;
  }
  runAction(
    () =>
      feedbackOrderApi(props.orderId!, {
        content: feedbackText.value.trim(),
      }),
    '反馈已追加',
  );
}

function handleSubmit() {
  if (!props.orderId || !detail.value) return;
  const detailBody = cleanDetail(submitForm.detail);
  if (detail.value.actionType === 'TUNING') {
    if (!pidProvided(submitForm.pidAfter)) {
      message.warning('参数整定类型必须填写调整后 P/I/D（pidAfter）');
      return;
    }
    detailBody.pidAfter = cleanDetail(
      submitForm.pidAfter,
    ) as HandlingApi.PidValues;
  }
  if (Object.keys(detailBody).length === 0) {
    message.warning('请填写处置详情');
    return;
  }
  runAction(
    () => submitOrderApi(props.orderId!, { actionDetail: detailBody }),
    '已提交验证',
  );
}

function handleVerify(result: HandlingApi.VerifyResult) {
  if (!props.orderId) return;
  const body: HandlingApi.VerifyOrderBody = {
    verifyResult: result,
    verifyNote: verifyForm.verifyNote.trim() || undefined,
    verifyRunId: verifyForm.verifyRunId.trim() || undefined,
  };
  runAction(
    () => verifyOrderApi(props.orderId!, body),
    result === 'EFFECTIVE' ? '已闭环' : '已重开，可再次处置',
  );
}

// ---------------------------------------------------------------------------
// KPI 前后对比预览（VERIFYING，不落库；CLOSED/REOPENED 用固化 kpiBefore/After）
// ---------------------------------------------------------------------------

const kpiLoading = ref(false);
const kpiPreview = ref<HandlingApi.KpiComparison | null>(null);

async function loadKpiPreview() {
  if (!props.orderId) return;
  kpiLoading.value = true;
  try {
    kpiPreview.value = await getOrderKpiComparisonApi(props.orderId);
  } catch {
    kpiPreview.value = null;
  } finally {
    kpiLoading.value = false;
  }
}

const KPI_ROWS: Array<{
  key: keyof HandlingApi.KpiSummary;
  label: string;
  percent?: boolean;
}> = [
  { key: 'score', label: '综合评分' },
  { key: 'effectiveAutoRate', label: '有效自控率', percent: true },
  { key: 'steadyRate', label: '平稳率', percent: true },
  { key: 'accuracyRate', label: '准确率', percent: true },
  { key: 'fastRate', label: '快速率', percent: true },
  { key: 'oscillationRate', label: '振荡率', percent: true },
  { key: 'saturationRate', label: '饱和率', percent: true },
  { key: 'goodValueRate', label: '好值率', percent: true },
];

function fmtKpi(
  side: HandlingApi.KpiSummary | null | undefined,
  key: string,
): string {
  const v = side?.[key as keyof HandlingApi.KpiSummary];
  if (v == null || typeof v !== 'number') return '—';
  return key === 'score' ? v.toFixed(1) : `${v.toFixed(1)}%`;
}

/** 复诊入口：跳诊断工作台对该回路复诊（§8.1） */
function goRevisit() {
  if (!detail.value) return;
  if (!moduleEnabled('diagnosis')) return;
  router.push({
    path: '/diagnosis/workbench',
    query: { loopId: detail.value.loopId },
  });
}

// ---------------------------------------------------------------------------
// 时间线（§8.1 下部：工单生成(含排程) → 开工 → 反馈×N → 提交验证 → 验证结论）
// ---------------------------------------------------------------------------

interface TimelineNode {
  color: string;
  label: string;
  time?: null | string;
  who?: null | string;
  extra?: null | string;
}

const timelineNodes = computed<TimelineNode[]>(() => {
  const d = detail.value;
  if (!d) return [];
  const nodes: TimelineNode[] = [
    {
      color: 'blue',
      label: `工单生成（${ORDER_SOURCE_TEXT[d.source]}${
        d.plannedAt ? ` · 排程 ${formatLocalTime(d.plannedAt)}` : ''
      }）`,
      extra: d.plannedBy,
    },
  ];
  if (d.startedAt) {
    nodes.push({
      color: 'blue',
      label: `开工（${d.actionType ? ACTION_TYPE_TEXT[d.actionType] : '—'}）`,
      time: d.startedAt,
      who: d.handler,
    });
  }
  for (const fb of d.feedbackLog ?? []) {
    nodes.push({ color: 'blue', label: '执行反馈', time: fb.at, who: fb.by, extra: fb.content });
  }
  if (d.submittedAt) {
    nodes.push({ color: 'blue', label: '提交验证', time: d.submittedAt });
  }
  if (d.verifiedAt) {
    nodes.push({
      color: d.verifyResult === 'EFFECTIVE' ? 'green' : 'red',
      label: `验证${d.verifyResult ? VERIFY_RESULT_TEXT[d.verifyResult] : ''}`,
      time: d.verifiedAt,
      who: d.verifiedBy,
      extra: d.verifyNote,
    });
  }
  if (d.status === 'CANCELLED') {
    nodes.push({ color: 'gray', label: '作废', extra: d.cancelReason });
  }
  return nodes;
});

/** 历史反馈倒序（最新在上） */
const feedbackList = computed(() =>
  detail.value?.feedbackLog ? detail.value.feedbackLog.toReversed() : [],
);

const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'MM-DD HH:mm');
</script>

<template>
  <Drawer v-model:open="drawerOpen" :width="520" placement="right" title="工单详情">
    <Spin :spinning="loading">
      <!-- 深链接按建议 id 定位等场景：工单不存在时优雅降级，不白屏 -->
      <div
        v-if="!loading && loadError"
        class="rounded border border-dashed border-neutral-200 p-4 text-center text-xs text-neutral-500 dark:border-neutral-700"
      >
        {{ loadError }}（该链接可能指向处置建议而非工单，请回到工单清单重新打开）
      </div>
      <div v-if="detail" class="flex flex-col gap-4">
        <!-- ============ 上部 · 工单信息 ============ -->
        <section>
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="font-mono text-base font-bold tracking-wide">{{
              detail.orderNo
            }}</span>
            <Tag :color="ORDER_STATUS_COLOR[detail.status]">{{
              detail.statusLabel
            }}</Tag>
            <span class="text-xs text-neutral-500">
              {{ ORDER_SOURCE_TEXT[detail.source] }} ·
              {{ detail.actionTypeLabel ?? ACTION_TYPE_TEXT[detail.actionType] }}
            </span>
          </div>
          <Descriptions :column="1" bordered size="small">
            <DescriptionsItem label="标题">{{
              detail.title
            }}</DescriptionsItem>
            <DescriptionsItem label="回路">
              <span class="font-medium">{{ detail.loopTagName }}</span>
              <span v-if="detail.loopDescription" class="ml-1 text-neutral-500">
                （{{ detail.loopDescription }}）
              </span>
            </DescriptionsItem>
            <DescriptionsItem label="装置.单元">{{
              detail.unitPath ?? '—'
            }}</DescriptionsItem>
            <DescriptionsItem v-if="detail.suggestions?.length" label="来源建议">
              <div class="flex flex-col gap-1">
                <div
                  v-for="s in detail.suggestions"
                  :key="s.id"
                  class="flex items-start gap-1"
                >
                  <Tag
                    :color="SUGGESTION_STATUS_COLOR[s.status]"
                    class="mt-0.5 shrink-0"
                    style="margin: 0; font-size: 11px"
                  >
                    {{ s.statusLabel }}
                  </Tag>
                  <span class="min-w-0 flex-1">{{ s.content }}</span>
                </div>
              </div>
            </DescriptionsItem>
          </Descriptions>
        </section>

        <!-- ============ 中部 · 流转操作区（按状态渲染） ============ -->
        <section
          v-if="canOperate"
          class="rounded border border-neutral-200 p-3 dark:border-neutral-700"
        >
          <!-- PENDING / REOPENED：排程回显 + 开工 + 作废 -->
          <template
            v-if="detail.status === 'PENDING' || detail.status === 'REOPENED'"
          >
            <div
              v-if="detail.status === 'REOPENED' && detail.verifyResult"
              class="mb-2 text-xs text-neutral-500"
            >
              上一轮验证：{{
                VERIFY_RESULT_TEXT[detail.verifyResult] ?? detail.verifyResult
              }}
              <span v-if="detail.verifyNote">（{{ detail.verifyNote }}）</span>
              <span v-if="detail.verifiedBy"> · {{ detail.verifiedBy }}</span>
            </div>
            <div v-if="detail.status === 'PENDING'" class="mb-2 text-xs text-neutral-500">
              计划时间：{{ fmt(detail.plannedAt) }} · 排程人：{{
                detail.plannedBy ?? '—'
              }}
              · 处置人：{{ detail.handler ?? '待开工确认' }}
            </div>
            <div v-if="!startPanelOpen" class="flex gap-2">
              <Button type="primary" @click="startPanelOpen = true">
                {{ detail.status === 'PENDING' ? '开工' : '再次开工' }}
              </Button>
              <Button v-if="detail.status === 'PENDING'" danger @click="openCancel">
                作废
              </Button>
            </div>
            <div v-else class="flex flex-col gap-2">
              <div class="flex items-center gap-2">
                <span class="w-16 shrink-0 text-xs text-neutral-500">处置人</span>
                <Input
                  v-model:value="startForm.handler"
                  :maxlength="64"
                  class="flex-1"
                  :placeholder="`缺省=当前用户（${currentUserName}），可填他人/班组`"
                />
              </div>
              <template v-if="detail.actionType === 'TUNING'">
                <div class="flex items-center gap-2">
                  <span class="w-16 shrink-0 text-xs text-neutral-500">调整前</span>
                  <InputNumber
                    v-model:value="startForm.pidBefore.p"
                    class="flex-1"
                    placeholder="P"
                  />
                  <InputNumber
                    v-model:value="startForm.pidBefore.i"
                    class="flex-1"
                    placeholder="I"
                  />
                  <InputNumber
                    v-model:value="startForm.pidBefore.d"
                    class="flex-1"
                    placeholder="D"
                  />
                </div>
              </template>
              <div
                v-for="f in detailFields"
                :key="f.key"
                class="flex items-center gap-2"
              >
                <span class="w-16 shrink-0 text-xs text-neutral-500">{{
                  f.label
                }}</span>
                <InputNumber
                  v-if="f.type === 'number'"
                  v-model:value="startForm.detail[f.key]"
                  class="flex-1"
                  :placeholder="f.placeholder"
                />
                <Input
                  v-else
                  v-model:value="startForm.detail[f.key]"
                  class="flex-1"
                  :placeholder="f.placeholder"
                />
              </div>
              <div class="flex gap-2">
                <Button :loading="acting" type="primary" @click="handleStart">
                  确认开工
                </Button>
                <Button @click="startPanelOpen = false">取消</Button>
              </div>
            </div>
          </template>

          <!-- EXECUTING：反馈追加 + 历史反馈 + 提交验证 -->
          <template v-else-if="detail.status === 'EXECUTING'">
            <div class="mb-2 text-xs text-neutral-500">
              处置人：{{ detail.handler ?? '—' }} · 开始于
              {{ fmt(detail.startedAt) }}
              <span v-if="detail.actionDetail?.pidBefore" class="ml-2">
                调整前 PID：{{ detail.actionDetail.pidBefore.p ?? '—' }} /
                {{ detail.actionDetail.pidBefore.i ?? '—' }} /
                {{ detail.actionDetail.pidBefore.d ?? '—' }}
              </span>
            </div>
            <!-- 反馈区 -->
            <div class="mb-3 flex flex-col gap-2">
              <div class="text-xs font-medium">执行反馈（可多次追加）</div>
              <Textarea
                v-model:value="feedbackText"
                :maxlength="500"
                :rows="2"
                placeholder="反馈处置进展（如：阀门已拆检，发现填料函泄漏，更换中）"
              />
              <div>
                <Button
                  :loading="acting"
                  size="small"
                  type="primary"
                  @click="handleFeedback"
                >
                  追加反馈
                </Button>
              </div>
            </div>
            <!-- 历史反馈（倒序，最新在上） -->
            <div v-if="feedbackList.length > 0" class="mb-3">
              <div class="mb-1 text-xs font-medium">
                历史反馈（{{ feedbackList.length }} 条）
              </div>
              <div class="flex flex-col gap-1">
                <div
                  v-for="(fb, i) in feedbackList"
                  :key="i"
                  class="rounded bg-neutral-50 px-2 py-1 text-xs dark:bg-neutral-800"
                >
                  <span class="text-neutral-500">
                    {{ fmt(fb.at) }} · {{ fb.by }}
                  </span>
                  <div>{{ fb.content }}</div>
                </div>
              </div>
            </div>
            <!-- 提交验证表单 -->
            <div class="flex flex-col gap-2">
              <div class="text-xs font-medium">提交验证</div>
              <template v-if="detail.actionType === 'TUNING'">
                <div class="flex items-center gap-2">
                  <span class="w-16 shrink-0 text-xs text-neutral-500">调整后</span>
                  <InputNumber
                    v-model:value="submitForm.pidAfter.p"
                    class="flex-1"
                    placeholder="P（必填）"
                  />
                  <InputNumber
                    v-model:value="submitForm.pidAfter.i"
                    class="flex-1"
                    placeholder="I（必填）"
                  />
                  <InputNumber
                    v-model:value="submitForm.pidAfter.d"
                    class="flex-1"
                    placeholder="D（必填）"
                  />
                </div>
              </template>
              <div
                v-for="f in detailFields"
                :key="f.key"
                class="flex items-center gap-2"
              >
                <span class="w-16 shrink-0 text-xs text-neutral-500">{{
                  f.label
                }}</span>
                <InputNumber
                  v-if="f.type === 'number'"
                  v-model:value="submitForm.detail[f.key]"
                  class="flex-1"
                  :placeholder="f.placeholder"
                />
                <Input
                  v-else
                  v-model:value="submitForm.detail[f.key]"
                  class="flex-1"
                  :placeholder="f.placeholder"
                />
              </div>
              <div>
                <Button :loading="acting" type="primary" @click="handleSubmit">
                  提交验证
                </Button>
              </div>
            </div>
          </template>

          <!-- VERIFYING：KPI 对比预览 + 有效/无效 + 复诊 -->
          <template v-else-if="detail.status === 'VERIFYING'">
            <Spin :spinning="kpiLoading">
              <div class="mb-2 rounded bg-neutral-50 p-2 dark:bg-neutral-800">
                <div class="mb-1 flex items-center justify-between">
                  <span class="text-xs font-medium">
                    KPI 前后对比（预览，验证时固化）
                  </span>
                  <Button size="small" type="link" @click="loadKpiPreview">
                    刷新
                  </Button>
                </div>
                <table class="w-full text-xs">
                  <thead>
                    <tr class="text-neutral-500">
                      <th class="py-0.5 text-left font-normal">指标</th>
                      <th class="py-0.5 text-right font-normal">处置前</th>
                      <th class="py-0.5 text-right font-normal">处置后</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="row in KPI_ROWS"
                      :key="row.key"
                      class="border-t border-neutral-200 dark:border-neutral-700"
                    >
                      <td class="py-0.5">{{ row.label }}</td>
                      <td class="py-0.5 text-right">
                        {{
                          kpiPreview?.kpiBefore
                            ? fmtKpi(kpiPreview.kpiBefore, row.key)
                            : '数据不足'
                        }}
                      </td>
                      <td class="py-0.5 text-right">
                        {{
                          kpiPreview?.kpiAfter
                            ? fmtKpi(kpiPreview.kpiAfter, row.key)
                            : '数据不足'
                        }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </Spin>
            <div class="flex flex-col gap-2">
              <Textarea
                v-model:value="verifyForm.verifyNote"
                :maxlength="500"
                :rows="2"
                placeholder="验证说明（无效时建议填写原因）"
              />
              <Input
                v-model:value="verifyForm.verifyRunId"
                placeholder="复诊诊断记录 ID（可选，复诊完成后回填）"
              />
              <div class="flex gap-2">
                <Popconfirm
                  title="确认验证有效并闭环？闭环后不可重开"
                  @confirm="handleVerify('EFFECTIVE')"
                >
                  <Button :loading="acting" type="primary">有效 · 闭环</Button>
                </Popconfirm>
                <Popconfirm
                  title="确认验证无效并重开？"
                  @confirm="handleVerify('INEFFECTIVE')"
                >
                  <Button :loading="acting" danger>无效 · 重开</Button>
                </Popconfirm>
                <Button @click="goRevisit">发起复诊</Button>
              </div>
            </div>
          </template>

          <!-- CLOSED：只读结论 -->
          <template v-else-if="detail.status === 'CLOSED'">
            <div class="text-xs text-neutral-500">
              验证有效已闭环 · {{ detail.verifiedBy ?? '—' }} ·
              {{ fmt(detail.verifiedAt) }}
              <span v-if="detail.verifyNote">（{{ detail.verifyNote }}）</span>
              <span v-if="detail.verifyRunId"> · 复诊 {{ detail.verifyRunId }}</span>
            </div>
          </template>

          <!-- CANCELLED：作废原因 -->
          <template v-else-if="detail.status === 'CANCELLED'">
            <div class="text-xs text-neutral-500">
              工单已作废：{{ detail.cancelReason ?? '—' }}
            </div>
          </template>
        </section>
        <section
          v-else
          class="rounded border border-dashed border-neutral-200 p-3 text-xs text-neutral-500 dark:border-neutral-700"
        >
          当前角色为只读（SPONSOR / EXPERT），流转操作由仪控/工艺工程师执行。
        </section>

        <!-- ============ KPI 固化展示（CLOSED/REOPENED 有值时） ============ -->
        <section v-if="detail.kpiBefore || detail.kpiAfter">
          <div class="mb-1 text-xs font-medium">验证时固化 KPI</div>
          <table class="w-full text-xs">
            <thead>
              <tr class="text-neutral-500">
                <th class="py-0.5 text-left font-normal">指标</th>
                <th class="py-0.5 text-right font-normal">处置前</th>
                <th class="py-0.5 text-right font-normal">处置后</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in KPI_ROWS"
                :key="row.key"
                class="border-t border-neutral-200 dark:border-neutral-700"
              >
                <td class="py-0.5">{{ row.label }}</td>
                <td class="py-0.5 text-right">
                  {{ fmtKpi(detail.kpiBefore, row.key) }}
                </td>
                <td class="py-0.5 text-right">
                  {{ fmtKpi(detail.kpiAfter, row.key) }}
                </td>
              </tr>
            </tbody>
          </table>
        </section>

        <!-- ============ 下部 · 时间线 ============ -->
        <section>
          <div class="mb-1 text-xs font-medium">流转时间线</div>
          <Timeline>
            <TimelineItem
              v-for="(n, i) in timelineNodes"
              :key="i"
              :color="n.color"
            >
              <div class="text-xs">
                <span class="font-medium">{{ n.label }}</span>
                <span v-if="n.time" class="ml-2 text-neutral-500">{{
                  fmt(n.time)
                }}</span>
                <span v-if="n.who" class="ml-1 text-neutral-500">
                  · {{ n.who }}
                </span>
                <div v-if="n.extra" class="mt-0.5 text-neutral-500">
                  {{ n.extra }}
                </div>
              </div>
            </TimelineItem>
          </Timeline>
        </section>
      </div>
    </Spin>

    <!-- 作废 Modal（原因必填） -->
    <Modal
      v-model:open="cancelOpen"
      cancel-text="取消"
      ok-text="确认作废"
      title="作废工单"
      @ok="handleCancel"
    >
      <div class="py-2">
        <p class="mb-2 text-sm text-neutral-600">
          作废后工单进入终态（已作废），不可恢复；请填写作废原因留痕。
        </p>
        <Textarea
          v-model:value="cancelReason"
          :maxlength="200"
          :rows="2"
          placeholder="作废原因（必填，如：计划变更/与在执行工单重复）"
          show-count
        />
      </div>
    </Modal>
  </Drawer>
</template>
