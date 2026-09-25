<script setup lang="ts">
/**
 * 工作台 TabBar 右侧工具片段（原 §5.1 F-GL-02 顶栏，合并至 TabBar 行节省 56px）
 *
 * 内容：范围选择器 + 时间胶囊（24h/7d/30d）+ 可信徽章 + 通知铃铛
 * 删除：用户名（外壳 layout 已显示，工作台不重复）
 *
 * - 范围选择器：层级下拉（全厂 → 工厂 → 装置），接 store.setScope 联动
 * - 时间胶囊：接 store.setWindow，跨 Tab 联动
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { useWorkbenchStore } from '#/store/workbench';

const store = useWorkbenchStore();
const router = useRouter();

const WINDOWS: { label: string; value: '7d' | '24h' | '30d' }[] = [
  { label: '近 24h', value: '24h' },
  { label: '近 7d', value: '7d' },
  { label: '近 30d', value: '30d' },
];

// ============ 范围选择器层级下拉 ============
const scopeOpen = ref(false);
const scopeRef = ref<HTMLElement | null>(null);

/** 工厂列表（顶层节点） */
const factories = computed(() =>
  store.scopeTree.filter((n) => n.type === 'FACTORY'),
);

/** 装置列表，按 parent_source_id 分组 */
const areasByFactory = computed(() => {
  const map = new Map<number, WorkbenchApi.ScopeNode[]>();
  for (const n of store.scopeTree) {
    if (n.type === 'AREA' && n.parent_source_id != null) {
      const list = map.get(n.parent_source_id) ?? [];
      list.push(n);
      map.set(n.parent_source_id, list);
    }
  }
  return map;
});

/** 当前选中范围的显示名称 */
const scopeLabel = computed(() => {
  if (store.scopeType === 'GLOBAL') return '全厂';
  const node = store.scopeTree.find((n) => n.id === store.scopeId);
  return node?.name ?? store.scopeType;
});

function selectGlobal() {
  store.setScope('GLOBAL', null);
  scopeOpen.value = false;
}

function selectNode(node: WorkbenchApi.ScopeNode) {
  store.setScope(node.type, node.id);
  scopeOpen.value = false;
}

function onDocumentClick(e: MouseEvent) {
  if (scopeRef.value && !scopeRef.value.contains(e.target as Node)) {
    scopeOpen.value = false;
  }
}

onMounted(() => document.addEventListener('click', onDocumentClick));
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick));

function onBellClick() {
  // TODO: M2 接未读计数端点后改为打开铃铛抽屉。
  // 在抽屉就绪前不允许空点击（DESIGN.md：任何主按钮必须有状态/上下文/路由变化）：
  // 先跳到关注队列——它正是「待我处理」的权威来源。
  router.push({ path: '/monitor/attention' });
}
</script>

<template>
  <div class="flex items-center gap-3">
    <!-- 范围选择器（层级下拉：全厂 → 工厂 → 装置） -->
    <div ref="scopeRef" class="relative">
      <button
        class="flex items-center gap-1.5 rounded border border-[var(--clpm-industrial-border)] px-2 py-1 text-sm hover:border-[var(--clpm-industrial-navy)]"
        :class="scopeOpen ? 'border-[var(--clpm-industrial-navy)]' : ''"
        @click="scopeOpen = !scopeOpen"
      >
        <span class="text-gray-400">范围</span>
        <span class="font-medium text-[var(--clpm-industrial-navy)]">{{ scopeLabel }}</span>
        <span class="text-[10px] text-gray-400">▼</span>
      </button>

      <!-- 层级下拉面板（右对齐，避免右侧溢出视口） -->
      <div
        v-if="scopeOpen"
        class="absolute top-full right-0 z-50 mt-1 max-h-[70vh] w-52 overflow-auto rounded border border-[var(--clpm-industrial-border)] bg-white shadow-lg"
      >
        <!-- 全厂 -->
        <button
          class="flex w-full items-center px-3 py-1.5 text-left text-sm hover:bg-blue-50"
          :class="
            store.scopeType === 'GLOBAL'
              ? 'bg-blue-50 font-medium text-[var(--clpm-industrial-navy)]'
              : 'text-gray-700'
          "
          @click="selectGlobal"
        >
          全厂
        </button>

        <!-- 工厂 → 装置 层级 -->
        <div
          v-for="f in factories"
          :key="f.id"
          class="border-t border-[var(--clpm-industrial-border)]"
        >
          <!-- 工厂行 -->
          <button
            class="flex w-full items-center px-3 py-1.5 text-left text-sm hover:bg-blue-50"
            :class="
              store.scopeType === 'FACTORY' && store.scopeId === f.id
                ? 'bg-blue-50 font-medium text-[var(--clpm-industrial-navy)]'
                : 'text-gray-700'
            "
            @click="selectNode(f)"
          >
            {{ f.name }}
          </button>
          <!-- 该工厂下的装置列表（缩进） -->
          <button
            v-for="a in areasByFactory.get(f.id) ?? []"
            :key="a.id"
            class="flex w-full items-center pl-6 pr-3 py-1.5 text-left text-sm hover:bg-blue-50"
            :class="
              store.scopeType === 'AREA' && store.scopeId === a.id
                ? 'bg-blue-50 font-medium text-[var(--clpm-industrial-navy)]'
                : 'text-gray-600'
            "
            @click="selectNode(a)"
          >
            <span class="text-gray-400">└</span>
            {{ a.name }}
          </button>
        </div>
      </div>
    </div>
    <span class="h-4 w-px bg-[var(--clpm-industrial-border)]" aria-hidden="true"></span>

    <!-- 时间胶囊（24h/7d/30d） -->
    <div class="flex items-center overflow-hidden rounded border border-[var(--clpm-industrial-border)] text-xs">
      <button
        v-for="(w, idx) in WINDOWS"
        :key="w.value"
        class="border-0 px-2.5 py-1"
        :class="[
          idx > 0 ? 'border-l border-[var(--clpm-industrial-border)]' : '',
          store.timeWindow === w.value
            ? 'bg-[var(--clpm-industrial-navy)] text-white'
            : 'bg-white text-gray-600 hover:text-[var(--clpm-industrial-navy)]',
        ]"
        @click="store.setWindow(w.value)"
      >
        {{ w.label }}
      </button>
    </div>
    <span class="h-4 w-px bg-[var(--clpm-industrial-border)]" aria-hidden="true"></span>

    <!-- 可信徽章 -->
    <span
      class="flex items-center gap-1 rounded border border-green-200 bg-green-50 px-2 py-0.5 text-xs text-green-700"
      title="数据可信度"
    >
      <span class="inline-block h-1.5 w-1.5 rounded-full bg-green-500"></span>
      数据可信
    </span>
    <span class="h-4 w-px bg-[var(--clpm-industrial-border)]" aria-hidden="true"></span>

    <!-- 驾驶舱入口（2026-09-24：驾驶舱与工作台 v2.0 都保留、互不替代——
         驾驶舱是管理层只读总览，工作台是工程闭环任务台；此处提供双向互通，
         驾驶舱顶栏已有「管理后台」回入口） -->
    <button
      class="flex items-center gap-1 rounded border border-[var(--clpm-industrial-border)] px-2 py-1 text-xs text-gray-600 hover:border-[var(--clpm-industrial-navy)] hover:text-[var(--clpm-industrial-navy)]"
      title="打开满屏驾驶舱（管理视角只读总览）"
      @click="router.push({ path: '/cockpit' })"
    >
      <span>🖥</span>
      <span>驾驶舱</span>
    </button>
    <span class="h-4 w-px bg-[var(--clpm-industrial-border)]" aria-hidden="true"></span>
    <!-- 通知铃铛（A-E5 未读红点，M1 桩） -->
    <button
      class="relative flex h-7 w-7 items-center justify-center rounded text-gray-600 hover:bg-gray-100"
      title="通知"
      @click="onBellClick"
    >
      <span class="text-base">🔔</span>
      <span
        v-if="store.unreadCount > 0"
        class="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] text-white"
      >
        {{ store.unreadCount > 99 ? '99+' : store.unreadCount }}
      </span>
    </button>
  </div>
</template>
