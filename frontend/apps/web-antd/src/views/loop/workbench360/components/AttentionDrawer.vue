<!--
  本回路关注抽屉（workbench360 P4-6；v3 §7 关注抽屉，原型 dr-att）

  唤起源：页头 🔔（全局通知性质，例外于剖面动线，v3 §7 注）。
  数据：GET /monitor/attention?loopId=（契约 §1.7）——按回路过滤的关注组，
  展平 children 为提醒列表（预警/数据质量/评分恶化/适用性异常/处置工单）。
  打开时才拉取（重查询 3~9s，不做回路切换预取；标题回显总数，诚实化）。
-->
<script setup lang="ts">
import type { MonitorApi } from '#/api/monitor';

import { ref, watch } from 'vue';

import { getAttentionListApi } from '#/api/monitor';
import { WB360_ATTENTION_PRIORITY_LABEL } from '#/constants/clpm-ui';
import { WB360_ATTENTION_SOURCE_LABEL } from '#/constants/clpm-ui';
import { formatLocalTime } from '#/utils/format';

import WbDrawer from './WbDrawer.vue';

const props = defineProps<{
  /** 当前回路 ID */
  loopId: null | string;
  /** 当前回路位号（标题） */
  loopTagName: null | string;
}>();

const open = defineModel<boolean>('open', { default: false });

const loading = ref(false);
const error = ref('');
const items = ref<MonitorApi.AttentionItem[]>([]);
const total = ref(0);
/** 关注组 total（按回路合并后的组数，与 items 展平口径区分） */
const totalGroups = ref(0);

watch(
  open,
  (o) => {
    if (o) void load();
  },
);

watch(
  () => props.loopId,
  () => {
    // 回路切换后旧数据失效，重新拉取（若抽屉开着）
    if (open.value) void load();
    else {
      items.value = [];
      total.value = 0;
      totalGroups.value = 0;
      error.value = '';
    }
  },
);

async function load() {
  const id = props.loopId;
  if (!id) {
    items.value = [];
    total.value = 0;
    totalGroups.value = 0;
    return;
  }
  loading.value = true;
  error.value = '';
  try {
    const res = await getAttentionListApi({
      loopId: id,
      page: 1,
      pageSize: 50,
    });
    items.value = res.items.flatMap((g) => g.children ?? []);
    total.value = res.totalItems ?? items.value.length;
    totalGroups.value = res.totalGroups ?? res.items.length;
  } catch (error_: any) {
    error.value = error_?.message ?? '关注列表加载失败';
    items.value = [];
    total.value = 0;
    totalGroups.value = 0;
  } finally {
    loading.value = false;
  }
}

const sourceLabel = (s: string) =>
  WB360_ATTENTION_SOURCE_LABEL[s] ?? s;

const priorityLabel = (p?: string) =>
  p ? (WB360_ATTENTION_PRIORITY_LABEL[p] ?? p) : '—';

function priorityCls(p?: string): string {
  if (p === 'URGENT' || p === 'HIGH') return 't-danger';
  if (p === 'MEDIUM') return 't-warn';
  return 't-gray';
}
</script>

<template>
  <WbDrawer
    :aria-label="`本回路关注 ${loopTagName ?? ''}`"
    :default-width="420"
    :open="open"
    :title="`本回路关注（${loading ? '加载中' : total}）`"
    @close="open = false"
  >
    <div v-if="error" class="error-line">
      {{ error }}
      <button class="link" type="button" @click="load">重试</button>
    </div>
    <div v-else-if="loading && items.length === 0" class="dim pad">
      关注列表加载中…（重查询约 3~9s）
    </div>
    <div v-else-if="items.length === 0" class="dim pad">
      本回路当前无未闭环关注项（预警/数据质量/评分恶化等触发后出现在此）。
    </div>
    <template v-else>
      <div
        v-for="it in items"
        :key="it.attentionId"
        class="att-item"
        :class="priorityCls(it.priority)"
      >
        <div class="att-head">
          <b>{{ sourceLabel(it.source) }}</b>
          <span class="tag" :class="priorityCls(it.priority)">
            <span class="dot"></span>{{ priorityLabel(it.priority) }}
          </span>
          <span class="mono dim time">{{ formatLocalTime(it.occurredAt) }}</span>
        </div>
        <div class="att-body">{{ it.summary || it.title }}</div>
      </div>
      <div class="foot-note dim">
        共 {{ total }} 项 · {{ totalGroups }} 组（按回路合并）· 完整队列见
        监控 → 关注队列
      </div>
    </template>
  </WbDrawer>
</template>

<style scoped>
.error-line {
  padding: 8px 12px;
  font-size: 12px;
  color: hsl(var(--destructive));
  border: 1px solid hsl(var(--destructive) / 35%);
  border-radius: 4px;
}

.link {
  padding: 0;
  margin-left: 8px;
  font-size: 12px;
  color: hsl(var(--destructive));
  cursor: pointer;
  background: none;
  border: none;
}

.dim {
  color: hsl(var(--muted-foreground) / 80%);
}

.pad {
  padding: 24px 0;
  text-align: center;
}

.mono {
  font-family: var(--font-mono, monospace);
}

.att-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  margin-bottom: 10px;
  background: hsl(var(--accent) / 35%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.att-head {
  display: flex;
  gap: 8px;
  align-items: center;
}

.att-head b {
  font-size: 13px;
}

.att-head .time {
  margin-left: auto;
  font-size: 11px;
}

.att-body {
  font-size: 12px;
  line-height: 1.6;
  color: hsl(var(--muted-foreground));
}

.foot-note {
  padding: 4px 2px;
  font-size: 11px;
}

.tag {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 1px 9px;
  font-size: 11px;
  white-space: nowrap;
  border: 1px solid transparent;
  border-radius: 10px;
}

.tag .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.t-warn {
  color: hsl(var(--warning));
  background: hsl(var(--warning) / 14%);
}

.t-warn .dot {
  background: hsl(var(--warning));
}

.t-gray {
  color: hsl(var(--muted-foreground));
  background: hsl(var(--muted-foreground) / 12%);
}

.t-gray .dot {
  background: hsl(var(--muted-foreground));
}

.t-danger {
  color: hsl(var(--destructive));
  background: hsl(var(--destructive) / 12%);
}

.t-danger .dot {
  background: hsl(var(--destructive));
}
</style>
