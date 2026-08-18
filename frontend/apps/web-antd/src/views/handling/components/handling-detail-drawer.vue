<script setup lang="ts">
/**
 * 处置详情抽屉（右侧 520px，§8.3）
 *
 * 三段式：建议信息（来源/依据/关联诊断）→ 流转操作区（按状态渲染）→ 时间线。
 * 流转操作仅 ADMIN / IC_ENGINEER / PE_ENGINEER 可见可动（canOperate 由父页按角色下发）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Input,
  InputNumber,
  message,
  Popconfirm,
  Select,
  Spin,
  Tag,
  Textarea,
  Timeline,
  TimelineItem,
} from 'ant-design-vue';

import {
  getHandlingItemApi,
  getKpiComparisonApi,
  ignoreHandlingApi,
  startHandlingApi,
  submitHandlingApi,
  verifyHandlingApi,
} from '#/api/handling';
import { formatLocalTime } from '#/utils/format';

import {
  ACTION_DETAIL_FIELDS,
  ACTION_TYPE_OPTIONS,
  ACTION_TYPE_TEXT,
  SOURCE_TEXT,
  STATUS_COLOR,
} from '../constants';

const props = defineProps<{
  canOperate: boolean;
  itemId: null | string;
  open: boolean;
}>();

const emit = defineEmits<{
  'update:open': [boolean];
  updated: [];
}>();

const router = useRouter();

const loading = ref(false);
const detail = ref<HandlingApi.Detail | null>(null);

const drawerOpen = computed({
  get: () => props.open,
  set: (v: boolean) => emit('update:open', v),
});

async function load() {
  if (!props.itemId) return;
  loading.value = true;
  detail.value = null;
  try {
    detail.value = await getHandlingItemApi(props.itemId);
    initForms(detail.value);
    if (detail.value.status === 'VERIFYING') loadKpiPreview();
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.itemId],
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

/** 开始处置表单（PENDING/REOPENED） */
const startForm = reactive({
  actionType: undefined as HandlingApi.ActionType | undefined,
  handler: '',
  detail: {} as Record<string, any>,
  pidBefore: { p: undefined, i: undefined, d: undefined } as PidFormValues,
});

/** 提交验证表单（HANDLING） */
const submitForm = reactive({
  detail: {} as Record<string, any>,
  pidAfter: { p: undefined, i: undefined, d: undefined } as PidFormValues,
});

/** 验证结论表单（VERIFYING） */
const verifyForm = reactive({ verifyNote: '', verifyRunId: '' });

/** 忽略表单（PENDING） */
const ignoreForm = reactive({ ignoreReason: '' });

/** 表单分区展开状态 */
const startPanelOpen = ref(false);
const ignorePanelOpen = ref(false);

function initForms(d: HandlingApi.Detail) {
  startForm.actionType = d.actionType ?? undefined;
  startForm.handler = '';
  startForm.detail = {};
  startForm.pidBefore = { p: undefined, i: undefined, d: undefined };
  const existing = d.actionDetail ?? {};
  submitForm.detail = { ...existing };
  const pa = (existing.pidAfter ?? {}) as HandlingApi.PidValues;
  submitForm.pidAfter = {
    p: pa.p ?? undefined,
    i: pa.i ?? undefined,
    d: pa.d ?? undefined,
  };
  verifyForm.verifyNote = '';
  verifyForm.verifyRunId = '';
  ignoreForm.ignoreReason = '';
  startPanelOpen.value = false;
  ignorePanelOpen.value = false;
}

/** 当前类型的详情字段 schema（TUNING 的 P/I/D 组单独渲染） */
const detailFields = computed(() =>
  startForm.actionType ? ACTION_DETAIL_FIELDS[startForm.actionType] : [],
);
const submitDetailFields = computed(() =>
  detail.value?.actionType ? ACTION_DETAIL_FIELDS[detail.value.actionType] : [],
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
  fn: () => Promise<HandlingApi.Detail>,
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
  if (!props.itemId || !startForm.actionType) {
    message.warning('请选择处置类型');
    return;
  }
  const body: HandlingApi.StartBody = {
    actionType: startForm.actionType,
    handler: startForm.handler.trim() || undefined,
    actionDetail: cleanDetail(startForm.detail),
  };
  if (startForm.actionType === 'TUNING' && pidProvided(startForm.pidBefore)) {
    body.pidBefore = cleanDetail(startForm.pidBefore) as HandlingApi.PidValues;
  }
  runAction(() => startHandlingApi(props.itemId!, body), '已开始处置');
}

function handleSubmit() {
  if (!props.itemId || !detail.value) return;
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
    () => submitHandlingApi(props.itemId!, { actionDetail: detailBody }),
    '已提交验证',
  );
}

function handleVerify(result: HandlingApi.VerifyResult) {
  if (!props.itemId) return;
  const body: HandlingApi.VerifyBody = {
    verifyResult: result,
    verifyNote: verifyForm.verifyNote.trim() || undefined,
    verifyRunId: verifyForm.verifyRunId.trim() || undefined,
  };
  runAction(
    () => verifyHandlingApi(props.itemId!, body),
    result === 'EFFECTIVE' ? '已闭环' : '已重开，可再次处置',
  );
}

function handleIgnore() {
  if (!props.itemId || !ignoreForm.ignoreReason.trim()) {
    message.warning('请填写忽略原因');
    return;
  }
  runAction(
    () =>
      ignoreHandlingApi(props.itemId!, {
        ignoreReason: ignoreForm.ignoreReason.trim(),
      }),
    '已忽略',
  );
}

// ---------------------------------------------------------------------------
// KPI 前后对比预览（VERIFYING，不落库）
// ---------------------------------------------------------------------------

const kpiLoading = ref(false);
const kpiPreview = ref<HandlingApi.KpiComparison | null>(null);

async function loadKpiPreview() {
  if (!props.itemId) return;
  kpiLoading.value = true;
  try {
    kpiPreview.value = await getKpiComparisonApi(props.itemId);
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

/** 复诊入口：跳诊断工作台对该回路复诊（§8.3） */
function goRevisit() {
  if (!detail.value) return;
  router.push({
    path: '/diagnosis/workbench',
    query: { loopId: detail.value.loopId },
  });
}

// ---------------------------------------------------------------------------
// 时间线（§8.3 下半：建议 → 开始处置 → 提交验证 → 验证）
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
      label: `建议产生（${SOURCE_TEXT[d.source]}${d.priority ? ` R${d.priority}` : ''}）`,
      time: d.suggestedAt,
      who: d.suggestedBy,
    },
  ];
  if (d.handledAt) {
    nodes.push({
      color: 'blue',
      label: `开始处置（${d.actionType ? ACTION_TYPE_TEXT[d.actionType] : '—'}）`,
      time: d.handledAt,
      who: d.handledBy,
    });
  }
  if (d.submittedAt) {
    nodes.push({ color: 'blue', label: '提交验证', time: d.submittedAt });
  }
  if (d.verifiedAt) {
    nodes.push({
      color: d.verifyResult === 'EFFECTIVE' ? 'green' : 'red',
      label: `验证${d.verifyResultLabel ?? ''}`,
      time: d.verifiedAt,
      who: d.verifiedBy,
      extra: d.verifyNote,
    });
  }
  if (d.status === 'IGNORED') {
    nodes.push({ color: 'gray', label: '已忽略', extra: d.ignoreReason });
  }
  return nodes;
});

const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'MM-DD HH:mm');
</script>

<template>
  <Drawer
    v-model:open="drawerOpen"
    :width="520"
    placement="right"
    title="处置详情"
    @close="kpiPreview = null"
  >
    <Spin :spinning="loading">
      <div v-if="detail" class="flex flex-col gap-4">
        <!-- ============ 上半 · 建议信息 ============ -->
        <section>
          <div class="mb-2 flex items-center gap-2">
            <Tag :color="STATUS_COLOR[detail.status]">{{
              detail.statusLabel
            }}</Tag>
            <span class="text-base font-medium">{{ detail.loopTagName }}</span>
            <span class="text-xs text-neutral-500">{{
              detail.unitPath ?? '—'
            }}</span>
          </div>
          <Descriptions :column="1" bordered size="small">
            <DescriptionsItem label="建议内容">{{
              detail.content
            }}</DescriptionsItem>
            <DescriptionsItem label="来源">
              {{ SOURCE_TEXT[detail.source] }}
              <span v-if="detail.categoryLabel" class="text-neutral-500">
                （{{ detail.categoryLabel }}）
              </span>
            </DescriptionsItem>
            <DescriptionsItem v-if="detail.basis" label="依据">{{
              detail.basis
            }}</DescriptionsItem>
            <DescriptionsItem label="建议人/时间">
              {{ detail.suggestedBy }} · {{ fmt(detail.suggestedAt) }}
            </DescriptionsItem>
          </Descriptions>
        </section>

        <!-- ============ 中部 · 流转操作区（按状态渲染） ============ -->
        <section
          v-if="canOperate"
          class="rounded border border-neutral-200 p-3 dark:border-neutral-700"
        >
          <!-- PENDING / REOPENED：开始处置 + 忽略 -->
          <template
            v-if="detail.status === 'PENDING' || detail.status === 'REOPENED'"
          >
            <div
              v-if="detail.status === 'REOPENED' && detail.verifyResult"
              class="mb-2 text-xs text-neutral-500"
            >
              上一轮验证：{{ detail.verifyResultLabel }}
              <span v-if="detail.verifyNote">（{{ detail.verifyNote }}）</span>
              <span v-if="detail.verifiedBy"> · {{ detail.verifiedBy }}</span>
            </div>
            <div v-if="!startPanelOpen" class="flex gap-2">
              <Button type="primary" @click="startPanelOpen = true"
                >开始处置</Button
              >
              <Button
                v-if="detail.status === 'PENDING'"
                danger
                @click="ignorePanelOpen = !ignorePanelOpen"
              >
                忽略
              </Button>
            </div>
            <div v-else class="flex flex-col gap-2">
              <div class="flex items-center gap-2">
                <span class="w-16 shrink-0 text-xs text-neutral-500"
                  >处置类型</span
                >
                <Select
                  v-model:value="startForm.actionType"
                  :options="ACTION_TYPE_OPTIONS"
                  class="flex-1"
                  placeholder="选择处置类型（必填）"
                />
              </div>
              <div class="flex items-center gap-2">
                <span class="w-16 shrink-0 text-xs text-neutral-500"
                  >处置人</span
                >
                <Input
                  v-model:value="startForm.handler"
                  :maxlength="64"
                  class="flex-1"
                  placeholder="缺省=当前登录用户，可填他人/班组"
                />
              </div>
              <template v-if="startForm.actionType === 'TUNING'">
                <div class="flex items-center gap-2">
                  <span class="w-16 shrink-0 text-xs text-neutral-500"
                    >调整前</span
                  >
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
                <Button :loading="acting" type="primary" @click="handleStart"
                  >确认开始</Button
                >
                <Button @click="startPanelOpen = false">取消</Button>
              </div>
            </div>
            <div
              v-if="ignorePanelOpen && detail.status === 'PENDING'"
              class="mt-2 flex flex-col gap-2"
            >
              <Textarea
                v-model:value="ignoreForm.ignoreReason"
                :maxlength="200"
                :rows="2"
                placeholder="忽略原因（必填，如：建议不适用/与近期检修计划重复）"
              />
              <div>
                <Button
                  :loading="acting"
                  danger
                  size="small"
                  type="primary"
                  @click="handleIgnore"
                >
                  确认忽略
                </Button>
              </div>
            </div>
          </template>

          <!-- HANDLING：提交验证 -->
          <template v-else-if="detail.status === 'HANDLING'">
            <div class="mb-2 text-xs text-neutral-500">
              处置人：{{ detail.handledBy ?? '—' }} · 开始于
              {{ fmt(detail.handledAt) }}
              <span v-if="detail.actionDetail?.pidBefore" class="ml-2">
                调整前 PID：{{ detail.actionDetail.pidBefore.p ?? '—' }} /
                {{ detail.actionDetail.pidBefore.i ?? '—' }} /
                {{ detail.actionDetail.pidBefore.d ?? '—' }}
              </span>
            </div>
            <div class="flex flex-col gap-2">
              <template v-if="detail.actionType === 'TUNING'">
                <div class="flex items-center gap-2">
                  <span class="w-16 shrink-0 text-xs text-neutral-500"
                    >调整后</span
                  >
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
                v-for="f in submitDetailFields"
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
                <Button :loading="acting" type="primary" @click="handleSubmit"
                  >提交验证</Button
                >
              </div>
            </div>
          </template>

          <!-- VERIFYING：KPI 对比卡 + 有效/无效 -->
          <template v-else-if="detail.status === 'VERIFYING'">
            <Spin :spinning="kpiLoading">
              <div class="mb-2 rounded bg-neutral-50 p-2 dark:bg-neutral-800">
                <div class="mb-1 flex items-center justify-between">
                  <span class="text-xs font-medium"
                    >KPI 前后对比（预览，验证时固化）</span
                  >
                  <Button size="small" type="link" @click="loadKpiPreview"
                    >刷新</Button
                  >
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

          <!-- CLOSED / IGNORED：只读结论 -->
          <template v-else>
            <div class="text-xs text-neutral-500">
              <template v-if="detail.status === 'CLOSED'">
                验证有效已闭环 · {{ detail.verifiedBy }} ·
                {{ fmt(detail.verifiedAt) }}
                <span v-if="detail.verifyNote"
                  >（{{ detail.verifyNote }}）</span
                >
              </template>
              <template v-else>
                已忽略：{{ detail.ignoreReason ?? '—' }}
              </template>
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

        <!-- ============ 下半 · 时间线 ============ -->
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
                <span v-if="n.who" class="ml-1 text-neutral-500"
                  >· {{ n.who }}</span
                >
                <div v-if="n.extra" class="mt-0.5 text-neutral-500">
                  {{ n.extra }}
                </div>
              </div>
            </TimelineItem>
          </Timeline>
        </section>
      </div>
    </Spin>
  </Drawer>
</template>
