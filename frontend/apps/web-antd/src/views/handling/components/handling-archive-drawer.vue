<script setup lang="ts">
/**
 * 处置档案抽屉（右侧 640px，Phase 1F 骨架）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.3（v1.1）
 * 上部回路摘要 + 下部跨 run 处置全史（倒序卡列表）。
 * 数据源：复用 GET /handling/items?loopId=（已交付）。
 * 「查看详情」跳工作台 focus 定位（流转操作统一回工作台，档案只读）。
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

import { getHandlingItemsApi } from '#/api/handling';
import { formatLocalTime } from '#/utils/format';

import { SOURCE_TEXT, STATUS_COLOR } from '../constants';

const props = defineProps<{
  loop: HandlingApi.LoopAggregateItem | null;
}>();

const open = defineModel<boolean>('open', { default: false });

const router = useRouter();
const loading = ref(false);
const items = ref<HandlingApi.ListItem[]>([]);

const fmt = (ts: null | string | undefined) => formatLocalTime(ts, 'YYYY-MM-DD HH:mm');

/** 闭环率（closed / 已进入处置的项：closed+reopened，无则 —） */
const closeRateText = computed(() => {
  const c = props.loop?.counts;
  if (!c) return '—';
  const verified = c.closed + c.reopened;
  if (verified === 0) return '—';
  return `${Math.round((c.closed / verified) * 100)}%`;
});

async function loadHistory() {
  if (!props.loop) return;
  loading.value = true;
  items.value = [];
  try {
    const res = await getHandlingItemsApi({
      loopId: props.loop.loopId,
      page: 1,
      pageSize: 100,
    });
    // 倒序：最近建议在前
    items.value = [...res.items].toSorted((a, b) =>
      (b.suggestedAt ?? '').localeCompare(a.suggestedAt ?? ''),
    );
  } catch {
    items.value = [];
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

/** 「查看详情」：跳工作台 focus 定位该处置项 */
function gotoWorkbench(itemId: string) {
  open.value = false;
  router.push({ path: '/handling/workbench', query: { focus: itemId } });
}
</script>

<template>
  <Drawer v-model:open="open" :width="640" placement="right" title="处置档案">
    <Spin :spinning="loading">
      <!-- 上部 · 回路摘要（§8.3） -->
      <Descriptions v-if="loop" :column="2" bordered size="small">
        <DescriptionsItem :span="2" label="回路">
          <span class="font-medium">{{ loop.loopTagName }}</span>
          <span v-if="loop.loopDescription" class="ml-2 text-xs text-neutral-500">
            {{ loop.loopDescription }}
          </span>
        </DescriptionsItem>
        <DescriptionsItem label="装置.单元">{{ loop.unitPath ?? '—' }}</DescriptionsItem>
        <DescriptionsItem label="累计处置">{{ loop.totalCount }}</DescriptionsItem>
        <DescriptionsItem label="闭环率">{{ closeRateText }}</DescriptionsItem>
        <DescriptionsItem label="最近处置人">{{ loop.lastHandledBy ?? '—' }}</DescriptionsItem>
      </Descriptions>

      <!-- 下部 · 跨 run 处置全史（倒序） -->
      <div class="mt-4">
        <div class="mb-2 text-xs font-medium">处置全史（{{ items.length }} 条，倒序）</div>
        <Empty v-if="!loading && items.length === 0" class="py-4" description="暂无处置记录" />
        <div
          v-for="it in items"
          :key="it.id"
          class="mb-2 rounded border border-neutral-200 p-3 dark:border-neutral-700"
        >
          <div class="flex items-center gap-2">
            <Tag :color="STATUS_COLOR[it.status as HandlingApi.Status]">{{ it.statusLabel }}</Tag>
            <span v-if="it.actionTypeLabel" class="text-xs">
              {{ it.actionTypeLabel }}
            </span>
            <span class="ml-auto text-xs text-neutral-500">{{ fmt(it.suggestedAt) }}</span>
          </div>
          <div class="mt-1 text-xs">
            {{ it.content }}
          </div>
          <div class="mt-1 flex items-center gap-3 text-xs text-neutral-500">
            <span>{{ SOURCE_TEXT[it.source as HandlingApi.Source] }}</span>
            <span v-if="it.handledBy">处置人 {{ it.handledBy }}</span>
            <span v-if="it.verifyResultLabel" :class="it.verifyResult === 'EFFECTIVE' ? 'text-emerald-600' : 'text-rose-600'">
              验证{{ it.verifyResultLabel }}
            </span>
          </div>
          <div class="mt-1 text-right">
            <Button size="small" type="link" @click="gotoWorkbench(it.id)">查看详情</Button>
          </div>
        </div>
      </div>
    </Spin>
  </Drawer>
</template>
