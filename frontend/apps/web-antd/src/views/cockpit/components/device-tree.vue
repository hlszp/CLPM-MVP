<script lang="ts" setup>
/**
 * 驾驶舱页2 · 装置树（方案 11 §6.1 左栏，215px）
 *
 * 工厂 → 装置 → 单元三层树（GET /cockpit/node-tree），紧凑化：
 * 头 34px、行距压缩；节点右侧回路计数角标（loopCount）；点击选中联动
 * 卡片墙与右侧聚合面板；默认选中「全厂」根（由父级 loops.vue 解析）。
 */
import type { CockpitApi } from '#/api/cockpit';

import { computed } from 'vue';

defineOptions({ name: 'CockpitDeviceTree' });

const props = defineProps<{
  loading?: boolean;
  nodes: CockpitApi.NodeTreeNode[];
  selectedId: null | string;
}>();

const emit = defineEmits<{
  select: [node: CockpitApi.NodeTreeNode];
}>();

interface FlatRow {
  depth: number;
  node: CockpitApi.NodeTreeNode;
}

/** 三层树恒展开，拍平为行（工厂/装置/单元数量在 MVP 规模下有限） */
const rows = computed<FlatRow[]>(() => {
  const out: FlatRow[] = [];
  const walk = (list: CockpitApi.NodeTreeNode[], depth: number) => {
    for (const n of list) {
      out.push({ depth, node: n });
      if (n.children?.length) walk(n.children, depth + 1);
    }
  };
  walk(props.nodes, 0);
  return out;
});

const TYPE_ICON: Record<CockpitApi.NodeTreeNode['type'], string> = {
  FACTORY: '▦',
  AREA: '▤',
  UNIT: '▫',
};
</script>

<template>
  <div class="dtree">
    <div class="dtree__hd">
      装置树
      <span class="sub">工厂 → 装置 → 单元</span>
    </div>
    <div class="dtree__bd">
      <div v-if="loading" class="dtree__hint">加载中…</div>
      <div v-else-if="rows.length === 0" class="dtree__hint">暂无工厂模型</div>
      <template v-else>
        <button
          v-for="row in rows"
          :key="row.node.nodeId"
          type="button"
          class="dtree__row"
          :class="{ active: selectedId === row.node.nodeId }"
          :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
          @click="emit('select', row.node)"
        >
          <span class="dtree__icon">{{ TYPE_ICON[row.node.type] }}</span>
          <span class="dtree__name" :title="row.node.name">{{
            row.node.name
          }}</span>
          <span class="dtree__count">{{ row.node.loopCount }}</span>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.dtree {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* 紧凑头 34px */
.dtree__hd {
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;
  height: 34px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ck-text);
  border-bottom: 1px solid var(--ck-border);
}

.dtree__hd .sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--ck-text-3);
}

.dtree__bd {
  flex: 1;
  min-height: 0;
  padding: 6px;
  overflow-y: auto;
}

.dtree__hint {
  padding: 18px 10px;
  font-size: 12px;
  color: var(--ck-text-3);
  text-align: center;
}

.dtree__row {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 100%;
  height: 30px;
  padding-right: 8px;
  margin: 0 0 2px;
  font-size: 12px;
  color: var(--ck-text-2);
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: 6px;
}

.dtree__row:hover {
  color: var(--ck-text);
  background: var(--ck-hover);
}

.dtree__row.active {
  font-weight: 600;
  color: var(--ck-text);
  background: var(--ck-panel-3);
  box-shadow: inset 2px 0 0 var(--ck-accent);
}

.dtree__icon {
  flex: none;
  font-size: 11px;
  color: var(--ck-text-3);
}

.dtree__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
  white-space: nowrap;
}

.dtree__count {
  flex: none;
  min-width: 20px;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--ck-text-3);
  text-align: center;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.dtree__row.active .dtree__count {
  color: var(--ck-accent);
  border-color: var(--ck-border-2);
}
</style>
