<script setup lang="ts">
/**
 * 建议详情抽屉（批次 C，右侧 520px）
 *
 * 承接 /handling/suggestions?focus={suggestionId} 深链接与 orders 路由
 * 404 回落（旧 focus=建议id 存量链接的智能识别）。
 *
 * 三段式：建议信息（来源/依据/审核留痕）→ 审核操作区（PENDING：接受/驳回/忽略）
 * → 流转时间线。审核操作仅 ADMIN / IC_ENGINEER / PE_ENGINEER 可见可动
 * （canOperate 由父页按角色下发）。
 *
 * 加载方式：后端无 GET /suggestions/{id} 单查端点，按清单接口分页扫描定位
 * （PENDING 优先排序，深链接目标通常首页命中；最多扫描 5 页 × 100 条）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  message,
  Spin,
  Tag,
  Textarea,
  Timeline,
  TimelineItem,
} from 'ant-design-vue';

import {
  acceptSuggestionApi,
  getHandlingSuggestionsApi,
  ignoreSuggestionApi,
  rejectSuggestionApi,
} from '#/api/handling';
import { formatLocalTime } from '#/utils/format';

import { SOURCE_TEXT, SUGGESTION_STATUS_COLOR } from '../constants';

const props = defineProps<{
  canOperate: boolean;
  open: boolean;
  suggestionId: null | string;
}>();

const emit = defineEmits<{
  'update:open': [boolean];
  updated: [];
}>();

const router = useRouter();

const loading = ref(false);
const detail = ref<HandlingApi.SuggestionItem | null>(null);
const loadError = ref('');

const drawerOpen = computed({
  get: () => props.open,
  set: (v: boolean) => emit('update:open', v),
});

/** 按 id 分页扫描清单定位建议（无单查端点的降级方案） */
async function findSuggestionById(id: string) {
  for (let page = 1; page <= 5; page++) {
    const res = await getHandlingSuggestionsApi({ page, pageSize: 100 });
    const hit = res.items.find((item) => item.id === id);
    if (hit) return hit;
    if (res.items.length < 100) break;
  }
  return null;
}

async function load() {
  if (!props.suggestionId) return;
  loading.value = true;
  detail.value = null;
  loadError.value = '';
  try {
    const hit = await findSuggestionById(props.suggestionId);
    if (hit) {
      detail.value = hit;
    } else {
      loadError.value = '未找到该处置建议（可能已删除）';
    }
  } catch (error: any) {
    loadError.value = error?.message ?? '建议详情加载失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.suggestionId],
  ([open]) => {
    if (open) load();
  },
);

// ---------------------------------------------------------------------------
// 审核操作区（PENDING：接受 / 驳回 / 忽略）
// ---------------------------------------------------------------------------

const acting = ref(false);
const rejectPanelOpen = ref(false);
const ignorePanelOpen = ref(false);
const rejectReason = ref('');
const ignoreReasonText = ref('');

async function runReview(fn: () => Promise<unknown>, okText: string) {
  acting.value = true;
  try {
    await fn();
    message.success(okText);
    rejectPanelOpen.value = false;
    ignorePanelOpen.value = false;
    emit('updated');
    await load();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

function handleAccept() {
  if (!props.suggestionId) return;
  runReview(() => acceptSuggestionApi(props.suggestionId!), '已接受，可转工单');
}

function handleReject() {
  if (!props.suggestionId) return;
  if (!rejectReason.value.trim()) {
    message.warning('请填写驳回原因');
    return;
  }
  runReview(
    () =>
      rejectSuggestionApi(props.suggestionId!, {
        rejectedReason: rejectReason.value.trim(),
      }),
    '已驳回（终态）',
  );
}

function handleIgnore() {
  if (!props.suggestionId) return;
  if (!ignoreReasonText.value.trim()) {
    message.warning('请填写忽略原因');
    return;
  }
  runReview(
    () =>
      ignoreSuggestionApi(props.suggestionId!, {
        ignoreReason: ignoreReasonText.value.trim(),
      }),
    '已忽略',
  );
}

/** 转工单回链：跳处置工单入口并按工单 id focus */
function gotoConvertedOrder() {
  if (!detail.value?.convertedOrderId) return;
  drawerOpen.value = false;
  router.push({
    path: '/handling/orders',
    query: { focus: detail.value.convertedOrderId },
  });
}

// ---------------------------------------------------------------------------
// 流转时间线（建议产生 → 审核 → 转工单）
// ---------------------------------------------------------------------------

interface TimelineNode {
  color: string;
  extra?: null | string;
  label: string;
  time?: null | string;
  who?: null | string;
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
  if (d.reviewedAt) {
    nodes.push({
      color: d.status === 'REJECTED' ? 'red' : 'green',
      label: `审核：${d.statusLabel}`,
      time: d.reviewedAt,
      who: d.reviewedBy,
      extra: d.rejectedReason ?? null,
    });
  }
  if (d.convertedOrderNo) {
    nodes.push({ color: 'green', label: `已转工单 ${d.convertedOrderNo}` });
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
    title="建议详情"
  >
    <Spin :spinning="loading">
      <div
        v-if="loadError && !loading"
        class="p-8 text-center text-sm text-neutral-500"
      >
        {{ loadError }}
      </div>
      <div v-else-if="detail" class="flex flex-col gap-4">
        <!-- ============ 上半 · 建议信息 ============ -->
        <section>
          <div class="mb-2 flex items-center gap-2">
            <Tag
              :color="
                SUGGESTION_STATUS_COLOR[
                  detail.status as HandlingApi.SuggestionStatus
                ]
              "
            >
              {{ detail.statusLabel }}
            </Tag>
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
              {{ SOURCE_TEXT[detail.source as HandlingApi.Source] }}
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
            <DescriptionsItem
              v-if="detail.reviewedBy || detail.reviewedAt"
              label="审核人/时间"
            >
              {{ detail.reviewedBy ?? '—' }} · {{ fmt(detail.reviewedAt) }}
            </DescriptionsItem>
            <DescriptionsItem v-if="detail.rejectedReason" label="驳回原因">
              {{ detail.rejectedReason }}
            </DescriptionsItem>
            <DescriptionsItem v-if="detail.ignoreReason" label="忽略原因">
              {{ detail.ignoreReason }}
            </DescriptionsItem>
            <DescriptionsItem v-if="detail.convertedOrderNo" label="转工单">
              <Button size="small" type="link" @click="gotoConvertedOrder">
                {{ detail.convertedOrderNo }}（查看工单）
              </Button>
            </DescriptionsItem>
          </Descriptions>
        </section>

        <!-- ============ 中部 · 审核操作区（PENDING 且可操作） ============ -->
        <section
          v-if="canOperate && detail.status === 'PENDING'"
          class="rounded border border-neutral-200 p-3 dark:border-neutral-700"
        >
          <div class="flex flex-wrap gap-2">
            <Button :loading="acting" type="primary" @click="handleAccept">
              接受
            </Button>
            <Button danger @click="rejectPanelOpen = !rejectPanelOpen">
              驳回
            </Button>
            <Button @click="ignorePanelOpen = !ignorePanelOpen">忽略</Button>
          </div>
          <div v-if="rejectPanelOpen" class="mt-2 flex flex-col gap-2">
            <Textarea
              v-model:value="rejectReason"
              :maxlength="200"
              :rows="2"
              placeholder="驳回原因（必填；驳回为终态，不可重新审核）"
            />
            <div>
              <Button
                :loading="acting"
                danger
                size="small"
                type="primary"
                @click="handleReject"
              >
                确认驳回
              </Button>
            </div>
          </div>
          <div v-if="ignorePanelOpen" class="mt-2 flex flex-col gap-2">
            <Textarea
              v-model:value="ignoreReasonText"
              :maxlength="200"
              :rows="2"
              placeholder="忽略原因（必填，如：建议不适用/重复）"
            />
            <div>
              <Button
                :loading="acting"
                size="small"
                type="primary"
                @click="handleIgnore"
              >
                确认忽略
              </Button>
            </div>
          </div>
        </section>
        <section
          v-else-if="detail.status === 'PENDING'"
          class="rounded border border-dashed border-neutral-200 p-3 text-xs text-neutral-500 dark:border-neutral-700"
        >
          当前角色为只读（SPONSOR / EXPERT），审核操作由仪控/工艺工程师执行。
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
