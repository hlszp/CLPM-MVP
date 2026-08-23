<script setup lang="ts">
/**
 * 处置档案抽屉（右侧 640px，批次 C 双段全史）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.3
 * 上部回路摘要（/handling/loops 聚合行）+ 下部双段全史：
 * - 建议段：GET /handling/suggestions?loopId=（审核全史）
 * - 工单段：GET /handling/orders?loopId=（执行全史）
 * 两段 Promise.all 并行拉取（pageSize 上限 100），各段内按时间倒序。
 * 「查看详情」按深链接契约跳对应入口（档案只读，流转操作回工作台）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Empty,
  Spin,
  Tag,
} from 'ant-design-vue';

import {
  getHandlingOrdersApi,
  getHandlingSuggestionsApi,
} from '#/api/handling';
import { formatLocalTime } from '#/utils/format';

import {
  ORDER_SOURCE_TEXT,
  ORDER_STATUS_COLOR,
  SOURCE_TEXT,
  SUGGESTION_STATUS_COLOR,
} from '../constants';

const props = defineProps<{
  loop: HandlingApi.LoopAggregateItem | null;
}>();

const open = defineModel<boolean>('open', { default: false });

const router = useRouter();
const loading = ref(false);
const suggestions = ref<HandlingApi.SuggestionItem[]>([]);
const orders = ref<HandlingApi.OrderItem[]>([]);
const loadError = ref('');

const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'YYYY-MM-DD HH:mm');

/** 闭环率（后端 closeRate = closed / 已验证，null 显 —） */
const closeRateText = computed(() => {
  const rate = props.loop?.closeRate;
  return rate == null ? '—' : `${Math.round(rate * 100)}%`;
});

async function loadHistory() {
  if (!props.loop) return;
  loading.value = true;
  suggestions.value = [];
  orders.value = [];
  loadError.value = '';
  try {
    // 双段并行：建议段 + 工单段（字段以后端返回为准）
    const [sugRes, orderRes] = await Promise.all([
      getHandlingSuggestionsApi({
        loopId: props.loop.loopId,
        page: 1,
        pageSize: 100,
      }),
      getHandlingOrdersApi({
        loopId: props.loop.loopId,
        page: 1,
        pageSize: 100,
      }),
    ]);
    suggestions.value = [...sugRes.items].toSorted((a, b) =>
      (b.suggestedAt ?? '').localeCompare(a.suggestedAt ?? ''),
    );
    orders.value = [...orderRes.items].toSorted((a, b) =>
      (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''),
    );
  } catch (error: any) {
    loadError.value = error?.message ?? '处置全史加载失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => [open.value, props.loop?.loopId],
  ([isOpen]) => {
    if (isOpen) loadHistory();
  },
);

/** 「查看详情」：建议 → /handling/suggestions?focus=（深链接契约） */
function gotoSuggestion(id: string) {
  open.value = false;
  router.push({ path: '/handling/suggestions', query: { focus: id } });
}

/** 「查看详情」：工单 → /handling/orders?focus=（深链接契约） */
function gotoOrder(id: string) {
  open.value = false;
  router.push({ path: '/handling/orders', query: { focus: id } });
}
</script>

<template>
  <Drawer v-model:open="open" :width="640" placement="right" title="处置档案">
    <Spin :spinning="loading">
      <!-- 上部 · 回路摘要（双实体口径） -->
      <Descriptions v-if="loop" :column="2" bordered size="small">
        <DescriptionsItem :span="2" label="回路">
          <span class="font-medium">{{ loop.loopTagName }}</span>
          <span
            v-if="loop.loopDescription"
            class="ml-2 text-xs text-neutral-500"
          >
            {{ loop.loopDescription }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="装置.单元">{{
          loop.unitPath ?? '—'
        }}</DescriptionsItem>
        <DescriptionsItem label="累计建议">{{
          loop.suggestionTotal
        }}</DescriptionsItem>
        <DescriptionsItem label="累计工单">{{
          loop.orderTotal
        }}</DescriptionsItem>
        <DescriptionsItem label="闭环率">{{ closeRateText }}</DescriptionsItem>
        <DescriptionsItem label="最近处置人">{{
          loop.lastHandledBy ?? '—'
        }}</DescriptionsItem>
      </Descriptions>

      <div
        v-if="loadError && !loading"
        class="mt-4 p-6 text-center text-sm text-neutral-500"
      >
        {{ loadError }}
        <Button size="small" type="link" @click="loadHistory">重试</Button>
      </div>

      <template v-else>
        <!-- 下部 · 建议段（审核全史，倒序） -->
        <div class="mt-4">
          <div class="mb-2 text-xs font-medium">
            处置建议（{{ suggestions.length }} 条，倒序）
          </div>
          <Empty
            v-if="!loading && suggestions.length === 0"
            class="py-4"
            description="暂无建议记录"
          />
          <div
            v-for="it in suggestions"
            :key="it.id"
            class="mb-2 rounded border border-neutral-200 p-3 dark:border-neutral-700"
          >
            <div class="flex items-center gap-2">
              <Tag
                :color="
                  SUGGESTION_STATUS_COLOR[
                    it.status as HandlingApi.SuggestionStatus
                  ]
                "
              >
                {{ it.statusLabel }}
              </Tag>
              <span v-if="it.categoryLabel" class="text-xs">
                {{ it.categoryLabel }}
              </span>
              <span class="ml-auto text-xs text-neutral-500">
                {{ fmt(it.suggestedAt) }}
              </span>
            </div>
            <div class="mt-1 text-xs">{{ it.content }}</div>
            <div class="mt-1 flex items-center gap-3 text-xs text-neutral-500">
              <span>{{ SOURCE_TEXT[it.source as HandlingApi.Source] }}</span>
              <span v-if="it.suggestedBy">建议人 {{ it.suggestedBy }}</span>
              <span v-if="it.convertedOrderNo" class="text-emerald-600">
                已转工单 {{ it.convertedOrderNo }}
              </span>
            </div>
            <div class="mt-1 text-right">
              <Button size="small" type="link" @click="gotoSuggestion(it.id)">
                查看详情
              </Button>
            </div>
          </div>
        </div>

        <!-- 下部 · 工单段（执行全史，倒序） -->
        <div class="mt-4">
          <div class="mb-2 text-xs font-medium">
            处置工单（{{ orders.length }} 条，倒序）
          </div>
          <Empty
            v-if="!loading && orders.length === 0"
            class="py-4"
            description="暂无工单记录"
          />
          <div
            v-for="it in orders"
            :key="it.id"
            class="mb-2 rounded border border-neutral-200 p-3 dark:border-neutral-700"
          >
            <div class="flex items-center gap-2">
              <span class="font-mono text-xs font-medium">{{
                it.orderNo
              }}</span>
              <Tag
                :color="ORDER_STATUS_COLOR[it.status as HandlingApi.OrderStatus]"
              >
                {{ it.statusLabel }}
              </Tag>
              <span v-if="it.actionTypeLabel" class="text-xs">
                {{ it.actionTypeLabel }}
              </span>
              <span class="ml-auto text-xs text-neutral-500">
                {{ fmt(it.updatedAt) }}
              </span>
            </div>
            <div class="mt-1 text-xs">{{ it.title }}</div>
            <div class="mt-1 flex items-center gap-3 text-xs text-neutral-500">
              <span>{{ ORDER_SOURCE_TEXT[it.source] }}</span>
              <span v-if="it.handler">处置人 {{ it.handler }}</span>
              <span
                v-if="it.verifyResult"
                :class="
                  it.verifyResult === 'EFFECTIVE'
                    ? 'text-emerald-600'
                    : 'text-rose-600'
                "
              >
                验证{{ it.verifyResult === 'EFFECTIVE' ? '有效' : '无效' }}
              </span>
            </div>
            <div class="mt-1 text-right">
              <Button size="small" type="link" @click="gotoOrder(it.id)">
                查看详情
              </Button>
            </div>
          </div>
        </div>
      </template>
    </Spin>
  </Drawer>
</template>
