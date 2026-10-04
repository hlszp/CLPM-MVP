<!--
  R1 回路头（workbench360 P1；P4-5 主题切换 / P4-6 关注铃铛接入）
  原型 #hdr 的生产映射（v3 §2）：位于 vben Layout 内容区顶部的页面级页头。
  展示：页面名 + 回路位号 + 控制模式 + 描述/装置路径 + 实时值（WS 联动）+ 数据新鲜度
  + 适用性徽章（P2-5）+ 🔔 关注抽屉入口（P4-6，全局通知性质）+ ◐ 主题切换（P4-5，
  切 vben 全局主题——原型 html[data-theme=dark] 令牌映射到 vben preferences.theme.mode）。
-->
<script setup lang="ts">
import type { LoopApi } from '#/api/loop';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { updatePreferences, usePreferences } from '@vben/preferences';

const props = defineProps<{
  /** WS 连接状态（useLoopRealtime.connectionStatus） */
  connectionStatus: string;
  /**
   * 适用性等级（P2-5，G1 接入位）：最新快照 fitnessLevel（L0~L4）。
   * 后端 G1 落地前恒 null → 徽章显示显式"待接"提示（诚实化，禁编造等级）。
   */
  fitnessLevel: null | string;
  /** 最近一条实时消息时间 */
  lastMessageAt: Date | null;
  /** 当前选中回路（含 WS 局部更新的实时值） */
  loop: LoopApi.MonitorListItem | null;
  /** 显示返回按钮（回路监视页改版 P1-2：路由精简模式 embed=1） */
  showBack?: boolean;
}>();

const emit = defineEmits<{
  /** 🔔 打开本回路关注抽屉（P4-6） */
  (e: 'openAttention'): void;
  /** ← 返回来源页（P1-2 精简模式，由页面处理 from query / router.back） */
  (e: 'back'): void;
}>();

/* 主题切换（P4-5）：vben 全局主题深/浅往返（v3 §16.7 基线） */
const { isDark } = usePreferences();

function toggleTheme() {
  updatePreferences({ theme: { mode: isDark.value ? 'light' : 'dark' } });
}

const now = ref(Date.now());
let timer: null | ReturnType<typeof setInterval> = null;
onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

/** 数据新鲜度（秒；null=尚无实时消息） */
const freshSeconds = computed(() => {
  if (!props.lastMessageAt) return null;
  return Math.max(
    0,
    Math.round((now.value - props.lastMessageAt.getTime()) / 1000),
  );
});

const modeTag = computed(() => {
  const label = props.loop?.currentValues.modeLabel ?? null;
  if (label === 'Auto') return { cls: 't-ok', label };
  if (label === 'Cascade') return { cls: 't-info', label: 'CASCADE' };
  if (label === 'Manual') return { cls: 't-danger', label };
  return { cls: 't-gray', label: label ?? 'Unknown' };
});

const fmt = (v: null | number | undefined, digits = 1) =>
  v === null || v === undefined ? '--' : v.toFixed(digits);

/* 适用性徽章（P2-5）：L0/L1=不适用（红），L2=警告，L3/L4=就绪（蓝）；
 * null=后端 G1 缺口 → 显式"待接"占位（诚实化） */
const fitnessTag = computed(() => {
  const lv = props.fitnessLevel;
  if (!lv) {
    return {
      cls: 't-gray',
      label: '适用性待接',
      tip: '适用性（fitness）数据出口待后端 G1 补齐，暂无法展示等级',
    };
  }
  if (lv === 'L0' || lv === 'L1')
    return { cls: 't-danger', label: `适用性 ${lv} 不适用`, tip: '' };
  if (lv === 'L2') return { cls: 't-gray', label: '适用性 L2', tip: '' };
  return { cls: 't-info', label: `适用性 ${lv} 就绪`, tip: '' };
});
</script>

<template>
  <header class="wb360-hdr">
    <button
      v-if="props.showBack"
      aria-label="返回来源页面"
      class="icon-btn back-btn"
      title="返回"
      type="button"
      @click="emit('back')"
    >
      ←
    </button>
    <div class="brand"><b>回路工作台</b></div>
    <div v-if="loop" class="loop-info">
      <div class="loop-line">
        <span class="loop-id">{{ loop.tagName }}</span>
        <span class="tag" :class="modeTag.cls"
          ><span class="dot"></span>{{ modeTag.label }}</span
        >
      </div>
      <div class="loop-desc">
        {{ loop.description || '（无描述）' }} ·
        <span class="crumb">{{ loop.unitName || '未配置单元' }}</span>
      </div>
    </div>
    <div v-else class="loop-info loop-empty">未选中回路</div>
    <div class="spacer"></div>
    <div v-if="loop" class="live">
      <span
        >PV <b>{{ fmt(loop.currentValues.pv) }}</b>
        {{ loop.currentValues.unit || loop.pvUnit || '' }}</span
      >
      <span
        >SP <b>{{ fmt(loop.currentValues.sp) }}</b></span
      >
      <span
        >OP <b>{{ fmt(loop.currentValues.op) }}</b>
        {{ loop.opUnit || '%' }}</span
      >
      <span
        :class="
          connectionStatus === 'online'
            ? 'fresh-ok'
            : connectionStatus === 'reconnecting'
              ? 'fresh-warn'
              : 'fresh-bad'
        "
      >
        <template v-if="connectionStatus === 'online'">
          {{
            freshSeconds === null
              ? '● 等待实时数据…'
              : `● 数据新鲜 ${freshSeconds}s`
          }}
        </template>
        <template v-else-if="connectionStatus === 'reconnecting'"
          >● 实时重连中…</template
        >
        <template v-else>● 实时连接离线</template>
      </span>
      <span
        class="tag"
        :class="fitnessTag.cls"
        :title="fitnessTag.tip || undefined"
      >
        <span class="dot"></span>{{ fitnessTag.label }}
      </span>
    </div>
    <button
      aria-label="打开本回路关注抽屉"
      class="icon-btn"
      title="本回路关注（预警 / 数据质量提醒）"
      type="button"
      @click="emit('openAttention')"
    >
      🔔
    </button>
    <button
      :aria-label="isDark ? '切换到浅色主题' : '切换到深色主题'"
      class="icon-btn"
      :title="isDark ? '切换到浅色主题' : '切换到深色主题'"
      type="button"
      @click="toggleTheme"
    >
      ◐
    </button>
  </header>
</template>

<style scoped>
.wb360-hdr {
  display: flex;
  flex: none;
  gap: 14px;
  align-items: center;
  height: 52px;
  padding: 0 14px;
  background: hsl(var(--card));
  border-bottom: 1px solid hsl(var(--border));
}

.brand {
  font-size: 14px;
  font-weight: 700;
}

.loop-info {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.loop-line {
  display: flex;
  gap: 8px;
  align-items: center;
}

.loop-id {
  font-family: var(--font-mono, monospace);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.loop-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.crumb {
  color: hsl(var(--muted-foreground) / 70%);
}

.loop-empty {
  justify-content: center;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
}

.tag {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 1px 8px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
  border-radius: 4px;
}

.tag .dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.t-ok {
  color: hsl(var(--success));
  background: hsl(var(--success) / 12%);
}

.t-ok .dot {
  background: hsl(var(--success));
}

.t-info {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 12%);
}

.t-info .dot {
  background: hsl(var(--primary));
}

.t-danger {
  color: hsl(var(--destructive));
  background: hsl(var(--destructive) / 12%);
}

.t-danger .dot {
  background: hsl(var(--destructive));
}

.t-gray {
  color: hsl(var(--muted-foreground));
  background: hsl(var(--accent) / 60%);
}

.t-gray .dot {
  background: hsl(var(--muted-foreground));
}

.spacer {
  flex: 1;
}

.live {
  display: flex;
  gap: 14px;
  align-items: center;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.live b {
  font-size: 13px;
  color: hsl(var(--foreground));
}

.fresh-ok {
  color: hsl(var(--success));
}

.fresh-warn {
  color: hsl(var(--warning));
}

.fresh-bad {
  color: hsl(var(--destructive));
}

/* 页头图标钮（原型 icon-btn：🔔 关注 / ◐ 主题） */
.icon-btn {
  flex: none;
  padding: 4px 7px;
  font-size: 14px;
  line-height: 1;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
}

.icon-btn:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--border));
}

/* 返回钮（P1-2 精简模式）：比图标钮稍宽，箭头加粗 */
.back-btn {
  padding: 4px 9px;
  font-size: 16px;
  font-weight: 700;
}
</style>
