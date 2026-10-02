<!--
  左脊柱（workbench360 P1，P1-3）
  原型 #spine：装置/单元树（回路计数，unit 可选）+ 搜索 + 等级筛选 chips +
  回路清单（定高虚拟滚动，use-virtual-list）+ 底部计数。
  宽度由页面 vsplit 拖拽控制（--spine-w，180-420px）。
-->
<script setup lang="ts">
import type { GradeFilter, SpineTreeNode } from '../composables/use-wb360-loop';

import type { LoopApi } from '#/api/loop';

import { computed, ref } from 'vue';

import { useVirtualList } from '#/composables/use-virtual-list';
import { GRADE_THRESHOLDS } from '#/constants/clpm-ui';

const props = defineProps<{
  /** 等级筛选当前值（'all' = 不过滤；'none' = 无评分） */
  gradeFilter: 'all' | GradeFilter;
  keyword: string;
  /** 过滤后的清单（三重过滤：单元/关键词/等级） */
  loops: LoopApi.MonitorListItem[];
  /** 清单加载失败/截断提示（诚实化） */
  loopsError: null | string;
  loopsLoading: boolean;
  selectedLoopId: null | string;
  selectedUnit: null | string;
  tree: SpineTreeNode[];
  unitTotal: number;
}>();

const emit = defineEmits<{
  (e: 'selectLoop', loopId: string): void;
  (e: 'selectUnit', unit: null | string): void;
  (e: 'update:gradeFilter', v: 'all' | GradeFilter): void;
  (e: 'update:keyword', v: string): void;
}>();

const GRADE_CHIPS: Array<{ key: 'all' | GradeFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'A', label: 'A' },
  { key: 'B', label: 'B' },
  { key: 'C', label: 'C' },
  { key: 'D', label: 'D' },
  { key: 'E', label: 'E' },
  { key: 'none', label: '?' },
];

/** plant 折叠态（默认全展开；对齐原型两级树） */
const collapsed = ref(new Set<string>());
function togglePlant(id: string) {
  const next = new Set(collapsed.value);
  next.has(id) ? next.delete(id) : next.add(id);
  collapsed.value = next;
}

function selectUnit(unitName: null | string) {
  emit('selectUnit', props.selectedUnit === unitName ? null : unitName);
}

/** 行内评分等级类（g1 优秀~g5 不合格；无评分=无类=默认色）。
 *  阈值单源 GRADE_THRESHOLDS，颜色语义对齐 use-score-color 降级
 *  （优秀 SUCCESS / 良好 INFO / 合格 WARNING / 警告·不合格 DANGER）。 */
function gradeCls(score: null | number | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return '';
  const hit = GRADE_THRESHOLDS.find(
    (t) => score >= (t.minScore ?? 0) && score < (t.maxScore ?? 100),
  );
  return `g${hit?.level ?? 5}`;
}

/** 定高虚拟滚动（30px 行） */
const loopsRef = computed(() => props.loops);
const { containerRef, onScroll, offsetY, totalHeight, visibleItems } =
  useVirtualList({ itemHeight: 30, items: loopsRef });

const footerText = computed(() => {
  const shown = props.loops.length;
  return props.selectedUnit
    ? `${props.selectedUnit} · ${shown} / ${props.unitTotal} 回路`
    : `全部单元 · ${shown} 回路`;
});
</script>

<template>
  <aside aria-label="回路清单脊柱" class="wb360-spine">
    <div class="spine-h">
      <div class="row row-title"><span>装置 / 单元</span></div>
      <div class="tree">
        <template v-for="plant in tree" :key="plant.id">
          <div class="tnode plant" @click="togglePlant(plant.id)">
            <span class="tw">{{ collapsed.has(plant.id) ? '▸' : '▾' }}</span>
            <span class="tname">{{ plant.name }}</span>
            <span class="cnt">{{ plant.count }}</span>
          </div>
          <template v-if="!collapsed.has(plant.id)">
            <div
              v-for="unit in plant.units ?? []"
              :key="unit.id"
              class="tnode unit"
              :class="{ on: selectedUnit === unit.name }"
              @click="selectUnit(unit.name)"
            >
              <span class="tw"></span>
              <span class="tname">{{ unit.name }}</span>
              <span class="cnt">{{ unit.count }}</span>
            </div>
          </template>
        </template>
        <div v-if="tree.length === 0" class="tree-empty">装置树加载中或为空</div>
      </div>
    </div>

    <div class="spine-h">
      <div class="row">
        <input
          :value="keyword"
          aria-label="搜索回路位号"
          placeholder="搜索回路位号…"
          type="text"
          @input="emit('update:keyword', ($event.target as HTMLInputElement).value)"
        />
      </div>
      <div class="grade-chips">
        <span
          v-for="chip in GRADE_CHIPS"
          :key="chip.key"
          class="gchip"
          :class="{ on: gradeFilter === chip.key }"
          @click="emit('update:gradeFilter', chip.key)"
        >
          {{ chip.label }}
        </span>
      </div>
    </div>

    <div class="loops" :aria-busy="loopsLoading">
      <div
        :ref="(el) => (containerRef = el as HTMLDivElement | null)"
        class="loops-scroll"
        @scroll="onScroll"
      >
        <div :style="{ height: `${totalHeight}px`, position: 'relative' }">
          <div :style="{ transform: `translateY(${offsetY}px)` }">
            <div
              v-for="{ item } in visibleItems"
              :key="item.loopId"
              class="lrow"
              :class="{ on: selectedLoopId === item.loopId }"
              @click="emit('selectLoop', item.loopId)"
            >
              <span class="id" :title="item.tagName">{{ item.tagName }}</span>
              <span class="sc" :class="gradeCls(item.score)">
                {{
                  item.score === null || item.score === undefined
                    ? '?'
                    : item.score.toFixed(1)
                }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <div v-if="loopsLoading" class="loops-overlay">清单加载中…</div>
      <div v-else-if="loopsError" class="loops-overlay loops-error">
        {{ loopsError }}
      </div>
      <div v-else-if="loops.length === 0" class="loops-overlay">
        无匹配回路（调整搜索/筛选条件）
      </div>
    </div>

    <div class="spine-f">{{ footerText }}</div>
  </aside>
</template>

<style scoped>
.wb360-spine {
  background: hsl(var(--card));
  border-right: 1px solid hsl(var(--border));
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.spine-h {
  border-bottom: 1px solid hsl(var(--border));
  padding: 8px 10px;
}

.row-title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 4px;
}

.tree {
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
}

.tnode {
  align-items: center;
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  display: flex;
  gap: 6px;
  overflow: hidden;
  padding: 3px 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tnode:hover {
  background: hsl(var(--accent) / 50%);
}

.tnode.plant {
  color: hsl(var(--foreground));
  font-weight: 600;
}

.tnode.unit.on {
  background: hsl(var(--primary) / 12%);
  color: hsl(var(--primary));
  font-weight: 600;
}

.tnode .tw {
  flex: none;
  width: 10px;
}

.tnode .tname {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tnode .cnt {
  color: hsl(var(--muted-foreground) / 80%);
  flex: none;
  font-family: var(--font-mono, monospace);
  font-size: 11px;
  margin-left: auto;
}

.tree-empty {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  padding: 4px 8px;
}

.row input[type='text'] {
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--foreground));
  flex: 1;
  font-size: 12px;
  outline: none;
  padding: 3px 8px;
}

.row input[type='text']:focus {
  border-color: hsl(var(--primary));
}

.grade-chips {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}

.gchip {
  background: hsl(var(--accent) / 60%);
  border: 1px solid transparent;
  border-radius: 9px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 11px;
  line-height: 18px;
  padding: 0 7px;
}

.gchip.on {
  background: hsl(var(--primary));
  color: hsl(var(--primary-foreground));
}

.loops {
  flex: 1;
  min-height: 0;
  position: relative;
}

.loops-scroll {
  height: 100%;
  overflow: auto;
}

.lrow {
  align-items: center;
  border-left: 2px solid transparent;
  cursor: pointer;
  display: flex;
  font-size: 12px;
  gap: 8px;
  height: 30px;
  padding: 0 10px;
}

.lrow:hover {
  background: hsl(var(--accent) / 40%);
}

.lrow.on {
  background: hsl(var(--primary) / 12%);
  border-left-color: hsl(var(--primary));
}

.lrow .id {
  flex: 1;
  font-family: var(--font-mono, monospace);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lrow .sc {
  flex: none;
  font-family: var(--font-mono, monospace);
  font-weight: 600;
}

.g1 {
  color: hsl(var(--success));
}

.g2 {
  color: hsl(var(--primary));
}

.g3 {
  color: hsl(var(--warning));
}

.g4,
.g5 {
  color: hsl(var(--destructive));
}

.loops-overlay {
  align-items: center;
  bottom: 0;
  color: hsl(var(--muted-foreground));
  display: flex;
  font-size: 12px;
  justify-content: center;
  left: 0;
  padding: 10px;
  pointer-events: none;
  position: absolute;
  right: 0;
  text-align: center;
  top: 0;
}

.loops-error {
  align-items: flex-start;
  color: hsl(var(--destructive));
  justify-content: flex-start;
  padding-top: 20px;
}

.spine-f {
  border-top: 1px solid hsl(var(--border));
  color: hsl(var(--muted-foreground));
  font-size: 11px;
  padding: 5px 10px;
}
</style>
