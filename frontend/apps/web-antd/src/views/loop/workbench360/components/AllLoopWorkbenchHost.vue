<!--
  全回路树宿主 AllLoopWorkbenchHost（P6-02a O02-CORE，2026-10-14）

  职责（01a §4.2 + 09 §2/§3）：
  - 左脊柱：装置—单元—回路树（稳定 nodeId 组织，A21 同名节点不混）+ 搜索 +
    等级筛选 + 清单（按需渐进加载，不预载全厂分析组件，右侧一次挂载一个回路）；
  - 对象选择：选中回路以同一 SingleLoopWorkbench 核心挂载（不复制四剖面/
    不创建第二套业务状态）；选择变化经 select-loop 上报 Adapter 写
    `mode=all&loopId=…`（本组件不写 URL）；
  - 无选中：显示选择提示（关注/最近入口属 05/06 后续分片，不编造）；
  - URL 携带对象在目录加载完成后校验：不存在/不在授权范围明确提示，
    不回退首行（A12/A21）。
-->
<script setup lang="ts">
import type {
  Wb360NavigateRequest,
  Wb360ResultChangedPayload,
  Wb360SectionExternal,
  WorkbenchContext,
} from '../workbench-context';

import { computed, onMounted, ref, watch } from 'vue';

import { useWb360Directory } from '../composables/use-wb360-directory';
import {
  SPINE_DEFAULT_W,
  SPINE_MAX_W,
  SPINE_MIN_W,
} from '../composables/use-wb360-layout';
import LoopSpine from './LoopSpine.vue';
import SingleLoopWorkbench from './SingleLoopWorkbench.vue';

defineOptions({ name: 'AllLoopWorkbenchHost' });

const props = withDefaults(
  defineProps<{
    /** 上下文携带项（window/record/work/scope/profile/configRevision，Adapter 解析下发） */
    contextExtras?: Partial<
      Pick<
        WorkbenchContext,
        'configRevision' | 'evidenceRefs' | 'profileRef' | 'scope' | 'window' | 'workRef'
      >
    >;
    /** URL 携带的预选对象（mode=all&loopId=…；null=未指定） */
    initialLoopId?: null | string;
    /** 初始剖面（对外名；一次性消费语义由 Adapter 保证） */
    section?: Wb360SectionExternal;
  }>(),
  { contextExtras: () => ({}), initialLoopId: null, section: undefined },
);

const emit = defineEmits<{
  /** 核心事件转发（本组件不消费、不改写） */
  (e: 'navigateRequest', payload: Wb360NavigateRequest): void;
  (e: 'resultChanged', payload: Wb360ResultChangedPayload): void;
  /** 选中变化（Adapter 写 mode=all&loopId=…；null=清除选中） */
  (e: 'selectLoop', loopId: null | string): void;
}>();

const directory = useWb360Directory();

/* ── 选中状态（URL 预选 → 本地选中；树点击 → 上报 Adapter） ── */
const selectedLoopId = ref<null | string>(props.initialLoopId);

watch(
  () => props.initialLoopId,
  (id) => {
    selectedLoopId.value = id;
  },
);

function onSelectLoop(loopId: string) {
  if (loopId === selectedLoopId.value) return;
  selectedLoopId.value = loopId;
  emit('selectLoop', loopId);
}

/* ── 预选对象校验（目录加载完成后；不存在/越权明确提示，不回退首行） ── */
const selectionError = computed(() => {
  const id = selectedLoopId.value;
  if (!id) return null;
  if (!directory.loopsComplete.value) return null; // 渐进加载中不做结论
  if (directory.loopsError.value) {
    return `回路清单加载失败，无法验证指定对象：${directory.loopsError.value}`;
  }
  if (!directory.hasLoop(id)) {
    return `指定回路（${id}）不存在或不在当前授权范围（未回退到其他回路，请从左侧清单重新选择）`;
  }
  return null;
});

/* ── 核心上下文（选中对象 + 携带项；keyed 会话由核心自身保证） ── */
const coreContext = computed<null | WorkbenchContext>(() => {
  const id = selectedLoopId.value;
  if (!id || selectionError.value) return null;
  return {
    loopId: id,
    ...(props.section ? { section: props.section } : {}),
    ...props.contextExtras,
  };
});

/* ── 左脊柱宽度拖拽（vsplit；宿主本地 UI 状态，不进业务会话） ── */
const spineWidth = ref(SPINE_DEFAULT_W);
function onVsplitDown(e: PointerEvent) {
  e.preventDefault();
  const sw = spineWidth.value;
  const sx = e.clientX;
  const mv = (ev: PointerEvent) => {
    spineWidth.value = Math.round(
      Math.min(SPINE_MAX_W, Math.max(SPINE_MIN_W, sw + (ev.clientX - sx))),
    );
  };
  const up = () => {
    window.removeEventListener('pointermove', mv);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', mv);
  window.addEventListener('pointerup', up);
}

/* ── 生命周期：目录加载（清单渐进 + 装置树并行，树/计数派生自动重算） ── */
onMounted(() => {
  void directory.loadLoops();
  void directory.loadTree();
});
</script>

<template>
  <!-- 左脊柱（经核心 #spine 插槽注入，保持页头全宽的原布局）
       不设 :key：切回路只换 context，脊柱（树/清单）与右侧主区同实例更新，
       避免整页重挂载导致左栏闪烁/滚动丢失 -->
  <SingleLoopWorkbench
    v-if="coreContext"
    :context="coreContext"
    presentation="page"
    @navigate-request="emit('navigateRequest', $event)"
    @result-changed="emit('resultChanged', $event)"
  >
    <template #spine>
      <LoopSpine
        :grade-filter="directory.gradeFilter.value"
        :keyword="directory.keyword.value"
        :loops="directory.filteredLoops.value"
        :loops-complete="directory.loopsComplete.value"
        :loops-error="directory.loopsError.value"
        :loops-loading="directory.loopsLoading.value"
        :loops-total="directory.loopsTotal.value"
        :selected-loop-id="selectedLoopId"
        :selected-unit-node-id="directory.selectedUnitNodeId.value"
        :style="{ width: `${spineWidth}px` }"
        :tree="directory.tree.value"
        :tree-error="directory.treeError.value"
        :unit-total="directory.loops.value.length"
        @retry-tree="directory.loadTree()"
        @select-loop="onSelectLoop($event)"
        @select-unit="directory.selectedUnitNodeId.value = $event"
        @update:grade-filter="directory.gradeFilter.value = $event"
        @update:keyword="directory.keyword.value = $event"
      />
      <div
        class="wb360-vsplit"
        title="拖动调整左脊柱宽度（180–420px）"
        @pointerdown="onVsplitDown"
      ></div>
    </template>
  </SingleLoopWorkbench>

  <!-- 无核心时的独立骨架：脊柱 + 提示区（选择提示 / 对象不可用原因） -->
  <div v-else class="wb360-host">
    <LoopSpine
      :grade-filter="directory.gradeFilter.value"
      :keyword="directory.keyword.value"
      :loops="directory.filteredLoops.value"
      :loops-complete="directory.loopsComplete.value"
      :loops-error="directory.loopsError.value"
      :loops-loading="directory.loopsLoading.value"
      :loops-total="directory.loopsTotal.value"
      :selected-loop-id="selectedLoopId"
      :selected-unit-node-id="directory.selectedUnitNodeId.value"
      :style="{ width: `${spineWidth}px` }"
      :tree="directory.tree.value"
      :tree-error="directory.treeError.value"
      :unit-total="directory.loops.value.length"
      @retry-tree="directory.loadTree()"
      @select-loop="onSelectLoop($event)"
      @select-unit="directory.selectedUnitNodeId.value = $event"
      @update:grade-filter="directory.gradeFilter.value = $event"
      @update:keyword="directory.keyword.value = $event"
    />
    <div
      class="wb360-vsplit"
      title="拖动调整左脊柱宽度（180–420px）"
      @pointerdown="onVsplitDown"
    ></div>
    <div class="wb360-host-main">
      <div v-if="selectionError" class="wb360-state wb360-state--error" role="alert">
        <div class="wb360-state-title">无法打开指定回路</div>
        <div class="wb360-state-body">{{ selectionError }}</div>
      </div>
      <div v-else-if="selectedLoopId" class="wb360-state">
        回路清单加载中，正在验证指定对象…
      </div>
      <div v-else class="wb360-state">
        <div class="wb360-state-title">请从左侧选择回路开始作业</div>
        <div class="wb360-state-body">
          支持按装置/单元定位、搜索位号与等级筛选；选中后在此开展
          监视-评估-诊断-整定-处置全部作业。
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 无核心骨架：与核心同高的整页固定视口行布局 */
.wb360-host {
  display: flex;
  height: calc(100vh - 110px);
  overflow: hidden;
}

.wb360-host-main {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

/* 脊柱-主区分隔（5px 垂直拖拽；与核心内样式一致） */
.wb360-vsplit {
  flex: none;
  width: 5px;
  cursor: col-resize;
  background: hsl(var(--accent) / 25%);
  border-right: 1px solid hsl(var(--border));
  border-left: 1px solid hsl(var(--border));
}

.wb360-vsplit:hover {
  background: hsl(var(--primary) / 12%);
}

/* 选择提示 / 对象不可用（整区显式呈现，不回退首行） */
.wb360-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  padding: 24px;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
}

.wb360-state--error {
  color: hsl(var(--destructive));
}

.wb360-state-title {
  font-size: 15px;
  font-weight: 700;
}

.wb360-state-body {
  max-width: 640px;
  line-height: 1.7;
  text-align: center;
  word-break: break-all;
}
</style>
