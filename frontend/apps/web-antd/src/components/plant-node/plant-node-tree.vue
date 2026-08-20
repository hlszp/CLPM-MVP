<script lang="ts" setup>
/**
 * 统一工厂模型树组件（UI/UX v6.1 §15 工业风格改造）
 *
 * 改造要点（对齐 ZL IndustrialDesignReference.html §1 状态语义色）：
 * - 节点视觉升级：IconifyIcon 按类型区分（工厂/装置/单元）
 * - 类型徽章：ZL 语义色（bg-*-50/text-*-700/border-*-200）
 * - 节点尾部显示回路数（外部传入 loopCounts，递归累加子节点）
 * - 树头部统计栏：工厂/装置/单元/回路总数（可选，showStats）
 *
 * 工厂结构三层：FACTORY（工厂）→ AREA（装置/车间）→ UNIT（单元）
 * 回路挂在 UNIT 节点下。
 *
 * 纯浏览组件：数据加载 / 搜索过滤 / 展开折叠 / 选中事件。
 * 节点管理（CRUD/导入导出）统一在「工厂配置」页（views/factory/config.vue）。
 *
 * 使用方式：
 * <PlantNodeTree card-title="工厂模型" :width="280"
 *   :loop-counts="loopCounts" :show-stats="true"
 *   @select="onTreeSelect" @load-complete="onTreeLoaded" />
 */
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref, watch } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Card, Input, Spin, Tooltip, Tree } from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';

interface TreeNode {
  children?: TreeNode[];
  key: number | string;
  node: PlantNodeApi.PlantNode;
  title: string;
}

/** 节点类型配置：图标 + 中文标签 + ZL 语义色徽章 */
interface NodeTypeConfig {
  badgeClass: string;
  icon: string;
  iconColor: string;
  label: string;
}

const props = withDefaults(defineProps<Props>(), {
  cardTitle: '工厂模型',
  width: 280,
  showSearch: true,
  showCollapseButtons: true,
  defaultExpandLevel: 1,
  maxHeight: 'calc(100vh - 300px)',
  showStats: false,
  loopCounts: () => ({}),
});

const emit = defineEmits<{
  (e: 'loadComplete', treeData: PlantNodeApi.PlantNode[]): void;
  (e: 'select', node: null | PlantNodeApi.PlantNode): void;
}>();

const NODE_TYPE_CONFIG: Record<string, NodeTypeConfig> = {
  AREA: {
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
    icon: 'ant-design:appstore-outlined',
    iconColor: 'var(--color-amber-600)',
    label: '装置',
  },
  FACTORY: {
    badgeClass:
      'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30',
    icon: 'ant-design:home-outlined',
    iconColor: 'var(--color-blue-600)',
    label: '工厂',
  },
  UNIT: {
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
    icon: 'ant-design:database-outlined',
    iconColor: 'var(--color-slate-600)',
    label: '单元',
  },
};

/** 默认配置（未知类型） */
const DEFAULT_NODE_TYPE_CONFIG: NodeTypeConfig = {
  badgeClass:
    'bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  icon: 'ant-design:folder-outlined',
  iconColor: 'var(--color-slate-500)',
  label: '节点',
};

function getNodeTypeConfig(type?: string): NodeTypeConfig {
  return NODE_TYPE_CONFIG[type ?? ''] ?? DEFAULT_NODE_TYPE_CONFIG;
}

interface Props {
  /** 卡片标题 */
  cardTitle?: string;
  /** 卡片宽度（px） */
  width?: number;
  /** 是否显示搜索框 */
  showSearch?: boolean;
  /** 是否显示展开/折叠按钮 */
  showCollapseButtons?: boolean;
  /** 默认展开层级（0=不展开，1=展开第一层） */
  defaultExpandLevel?: number;
  /** 树容器最大高度 */
  maxHeight?: string;
  /** 是否显示头部统计栏（工厂/装置/单元/回路数） */
  showStats?: boolean;
  /**
   * 各节点直接挂载的回路数映射
   * key=plantNodeId, value=该节点直接挂载的回路数（通常仅 UNIT 节点有值）
   * 内部会递归累加子节点得到 AREA/FACTORY 的回路总数
   */
  loopCounts?: Record<string, number>;
}

const treeData = ref<TreeNode[]>([]);
const treeLoading = ref(false);
const treeSearchKeyword = ref('');
const expandedKeys = ref<(number | string)[]>([]);
const autoExpandParent = ref(true);
const treeCollapsed = ref(false);
const selectedNode = ref<null | TreeNode>(null);
const selectedKeys = ref<(number | string)[]>([]);

/** 将后端 PlantNode 转为 Ant Design Tree 节点 */
function toTreeNode(node: PlantNodeApi.PlantNode): TreeNode {
  return {
    children: node.children?.map((child) => toTreeNode(child)),
    key: node.id,
    node,
    title: node.name,
  };
}

/**
 * 递归累加子节点的回路数，得到每个节点的回路总数
 * UNIT 节点：直接挂载的回路数（来自 loopCounts prop）
 * AREA/FACTORY 节点：所有子节点的回路数之和
 */
const loopCountByNode = computed<Record<string, number>>(() => {
  const result: Record<string, number> = {};
  function accumulate(nodes: TreeNode[]): number {
    let total = 0;
    for (const n of nodes) {
      const directCount = props.loopCounts[n.key as string] ?? 0;
      const childrenCount = n.children ? accumulate(n.children) : 0;
      const nodeTotal = directCount + childrenCount;
      result[n.key as string] = nodeTotal;
      total += nodeTotal;
    }
    return total;
  }
  accumulate(treeData.value);
  return result;
});

/** 头部统计栏数据 */
const treeStats = computed(() => {
  let factoryCount = 0;
  let areaCount = 0;
  let unitCount = 0;
  let totalLoops = 0;
  function walk(nodes: TreeNode[]) {
    for (const n of nodes) {
      const type = n.node?.type;
      switch (type) {
        case 'AREA': {
          areaCount++;
          break;
        }
        case 'FACTORY': {
          factoryCount++;
          break;
        }
        case 'UNIT': {
          unitCount++;
          break;
        }
      }
      // 只累加根节点的回路数（loopCountByNode 已递归包含子节点）
      // 避免父节点 + 子节点重复累加导致 totalLoops 放大
      if (type === 'FACTORY') {
        totalLoops += loopCountByNode.value[n.key as string] ?? 0;
      }
      if (n.children) walk(n.children);
    }
  }
  walk(treeData.value);
  // 无 FACTORY 时（仅有 AREA/UNIT），累加 AREA 层
  if (factoryCount === 0) {
    totalLoops = 0;
    function walkArea(nodes: TreeNode[]) {
      for (const n of nodes) {
        const type = n.node?.type;
        if (type === 'AREA') {
          totalLoops += loopCountByNode.value[n.key as string] ?? 0;
        }
        if (n.children) walkArea(n.children);
      }
    }
    walkArea(treeData.value);
  }
  // 既无 FACTORY 也无 AREA 时（仅有 UNIT），累加 UNIT 层
  if (factoryCount === 0 && areaCount === 0) {
    totalLoops = 0;
    function walkUnit(nodes: TreeNode[]) {
      for (const n of nodes) {
        const type = n.node?.type;
        if (type === 'UNIT') {
          totalLoops += loopCountByNode.value[n.key as string] ?? 0;
        }
        if (n.children) walkUnit(n.children);
      }
    }
    walkUnit(treeData.value);
  }
  return {
    areaCount,
    factoryCount,
    totalLoops,
    unitCount,
  };
});

/** 加载工厂模型树 */
async function loadTree() {
  treeLoading.value = true;
  try {
    const data = await getPlantNodeTreeApi();
    const newTreeData = data.map((node) => toTreeNode(node));
    treeData.value = newTreeData;

    // 清理无效的 expandedKeys：只保留新树中仍然存在的节点 key
    // 避免删除/移动节点后，旧 key 残留导致 Ant Tree 内部状态不一致
    const validKeys = new Set<number | string>();
    function collectKeys(nodes: TreeNode[]) {
      for (const n of nodes) {
        validKeys.add(n.key);
        if (n.children) collectKeys(n.children);
      }
    }
    collectKeys(newTreeData);
    expandedKeys.value = expandedKeys.value.filter((k) => validKeys.has(k));

    // 仅在之前有选中节点但被清理时才 emit select(null)，
    // 首次加载 selectedKeys 本就为空，不应触发父组件重复加载
    const hadSelection = selectedKeys.value.length > 0;
    selectedKeys.value = selectedKeys.value.filter((k) => validKeys.has(k));
    if (hadSelection && selectedKeys.value.length === 0) {
      selectedNode.value = null;
      emit('select', null);
    }

    // 默认展开第一层（仅初次加载或全部折叠后重新加载时）
    if (props.defaultExpandLevel >= 1 && expandedKeys.value.length === 0) {
      expandedKeys.value = newTreeData.map((n) => n.key);
    }
    autoExpandParent.value = true;
    emit('loadComplete', data);
  } catch {
    // 错误已由拦截器处理
  } finally {
    treeLoading.value = false;
  }
}

/** 搜索过滤：保留匹配节点及其祖先路径 */
const filteredTreeData = computed(() => {
  if (!treeSearchKeyword.value) return treeData.value;
  const kw = treeSearchKeyword.value.toLowerCase();
  function filterNodes(nodes: TreeNode[]): TreeNode[] {
    return nodes
      .map((n) => {
        const children = n.children ? filterNodes(n.children) : [];
        const matched =
          n.title.toLowerCase().includes(kw) || children.length > 0;
        if (matched) return { ...n, children };
        return null as unknown as TreeNode;
      })
      .filter(Boolean);
  }
  return filterNodes(treeData.value);
});

/**
 * 搜索交互优化（建议 9）：
 * 搜索时仅展开匹配节点的父级路径（从 filteredTreeData 收集），
 * 而非展开整棵树的所有节点。
 */
watch(treeSearchKeyword, (val) => {
  if (val) {
    const keysToExpand: (number | string)[] = [];
    function collectKeys(nodes: TreeNode[]) {
      for (const n of nodes) {
        keysToExpand.push(n.key);
        if (n.children) collectKeys(n.children);
      }
    }
    // 仅从过滤后的树收集（匹配节点 + 祖先），避免展开无关子树
    collectKeys(filteredTreeData.value);
    expandedKeys.value = keysToExpand;
    autoExpandParent.value = true;
  }
});

/** 选中树节点：点击即选中，除非选择其他节点 */
function onTreeSelect(keys: any[], info: any) {
  if (keys.length === 0) {
    return;
  }
  const node =
    keys.length > 0 && info.selectedNodes?.[0]
      ? ((info.selectedNodes[0] as any)?.node ?? null)
      : null;
  if (node) {
    selectedKeys.value = keys;
    selectedNode.value = info.selectedNodes[0];
    emit('select', node);
  }
}

/** 全部展开 */
function expandAll() {
  const allKeys: (number | string)[] = [];
  function collectKeys(nodes: TreeNode[]) {
    for (const n of nodes) {
      allKeys.push(n.key);
      if (n.children) collectKeys(n.children);
    }
  }
  collectKeys(treeData.value);
  expandedKeys.value = allKeys;
  autoExpandParent.value = true;
}

/** 全部折叠 */
function collapseAll() {
  expandedKeys.value = [];
  autoExpandParent.value = true;
}

/** 是否全部父节点均已展开（基于当前可见的过滤树判断） */
const isAllExpanded = computed(() => {
  const parentKeys: (number | string)[] = [];
  function collectParentKeys(nodes: TreeNode[]) {
    for (const n of nodes) {
      if (n.children && n.children.length > 0) {
        parentKeys.push(n.key);
        collectParentKeys(n.children);
      }
    }
  }
  collectParentKeys(filteredTreeData.value);
  if (parentKeys.length === 0) return false;
  return parentKeys.every((k) => expandedKeys.value.includes(k));
});

/** 全部展开/全部折叠 切换（单按钮合并） */
function toggleExpandAll() {
  if (isAllExpanded.value) {
    collapseAll();
  } else {
    expandAll();
  }
}

onMounted(() => {
  loadTree();
});

defineExpose({ loadTree, expandAll, collapseAll });
</script>

<template>
  <Card
    class="plant-node-tree-card shrink-0"
    :style="{ width: treeCollapsed ? '48px' : `${width}px` }"
    size="small"
    :body-style="{ padding: treeCollapsed ? '8px 4px' : '8px' }"
  >
    <template #title>
      <span v-if="!treeCollapsed" class="text-sm font-semibold">{{
        cardTitle
      }}</span>
    </template>
    <template #extra>
      <div class="flex gap-1">
        <!-- 刷新按钮 -->
        <Tooltip title="刷新">
          <Button
            type="text"
            size="small"
            :loading="treeLoading"
            @click="loadTree"
          >
            <template #icon>
              <IconifyIcon icon="ant-design:reload-outlined" />
            </template>
          </Button>
        </Tooltip>
        <!-- 折叠整个树形面板（向左收起） -->
        <Tooltip :title="treeCollapsed ? '展开树面板' : '折叠树面板'">
          <Button
            type="text"
            size="small"
            @click="treeCollapsed = !treeCollapsed"
          >
            <template #icon>
              <IconifyIcon
                :icon="
                  treeCollapsed
                    ? 'ant-design:right-outlined'
                    : 'ant-design:left-outlined'
                "
              />
            </template>
          </Button>
        </Tooltip>
      </div>
    </template>

    <!-- 头部统计栏（ZL 高密度排版风格） -->
    <div
      v-if="showStats && !treeCollapsed"
      class="mx-1 mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-solid border-slate-200 pb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500 dark:border-slate-700 dark:text-slate-400"
    >
      <span>
        工厂
        <span class="font-mono tabular-nums text-slate-700 dark:text-slate-300">
          {{ treeStats.factoryCount }}
        </span>
      </span>
      <span>
        装置
        <span class="font-mono tabular-nums text-slate-700 dark:text-slate-300">
          {{ treeStats.areaCount }}
        </span>
      </span>
      <span>
        单元
        <span class="font-mono tabular-nums text-slate-700 dark:text-slate-300">
          {{ treeStats.unitCount }}
        </span>
      </span>
      <span>
        回路
        <span class="font-mono tabular-nums text-blue-600 dark:text-blue-400">
          {{ treeStats.totalLoops }}
        </span>
      </span>
      <!-- 统计栏右侧：展开/折叠切换按钮（最右侧，单击在全部展开/全部折叠间切换） -->
      <template v-if="showCollapseButtons">
        <span class="ml-auto"></span>
        <Tooltip :title="isAllExpanded ? '全部折叠' : '全部展开'">
          <Button type="text" size="small" @click="toggleExpandAll">
            <template #icon>
              <IconifyIcon
                :icon="
                  isAllExpanded
                    ? 'ant-design:node-collapse-outlined'
                    : 'ant-design:node-expand-outlined'
                "
              />
            </template>
          </Button>
        </Tooltip>
      </template>
    </div>

    <!-- 搜索框 -->
    <div v-if="showSearch && !treeCollapsed" class="mb-2 px-1">
      <Input
        v-model:value="treeSearchKeyword"
        placeholder="搜索工厂/装置/单元"
        allow-clear
        size="small"
      >
        <template #prefix>
          <IconifyIcon
            icon="ant-design:search-outlined"
            class="text-slate-400"
          />
        </template>
      </Input>
    </div>

    <Spin v-if="!treeCollapsed" :spinning="treeLoading">
      <div class="overflow-auto" :style="{ maxHeight }">
        <Tree
          v-if="filteredTreeData.length > 0"
          :tree-data="filteredTreeData"
          :expanded-keys="expandedKeys"
          :auto-expand-parent="autoExpandParent"
          :show-line="false"
          :selected-keys="selectedKeys"
          class="plant-node-tree"
          @select="onTreeSelect"
          @expand="
            (keys) => {
              expandedKeys = keys;
              autoExpandParent = false;
            }
          "
        >
          <template #title="nodeData">
            <div class="plant-node-tree__node">
              <IconifyIcon
                :icon="getNodeTypeConfig((nodeData as any).node?.type).icon"
                class="plant-node-tree__node-icon"
                :style="{
                  color: getNodeTypeConfig((nodeData as any).node?.type)
                    .iconColor,
                }"
              />
              <span class="plant-node-tree__node-title">
                {{ nodeData.title }}
              </span>
              <span
                v-if="(loopCountByNode[nodeData.key as string] ?? 0) > 0"
                class="plant-node-tree__loop-count"
              >
                {{ loopCountByNode[nodeData.key as string] }}
              </span>
            </div>
          </template>
        </Tree>
        <div v-else class="py-8 text-center text-xs text-slate-400">
          {{ treeSearchKeyword ? '未找到匹配节点' : '暂无工厂模型数据' }}
        </div>
      </div>
    </Spin>
  </Card>
</template>

<style scoped>
.plant-node-tree :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
}

/* 节点 hover 时增强视觉反馈 */
.plant-node-tree :deep(.ant-tree-node-content-wrapper:hover) {
  background-color: hsl(var(--accent) / 10%);
}

/* 选中节点高亮 */
.plant-node-tree :deep(.ant-tree-node-selected) {
  background-color: hsl(var(--accent) / 15%) !important;
}

/* —— 节点容器 —— */
.plant-node-tree__node {
  display: flex;
  gap: 6px;
  align-items: center;
  width: 100%;
  padding: 2px 4px;
  border-radius: 4px;
  transition: background-color 0.15s ease;
}

.plant-node-tree__node:hover {
  background-color: hsl(var(--accent) / 6%);
}

/* 节点图标 */
.plant-node-tree__node-icon {
  flex-shrink: 0;
  font-size: 14px;
}

/* 节点标题 */
.plant-node-tree__node-title {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

/* 回路数（右侧灰色数字） */
.plant-node-tree__loop-count {
  padding-left: 8px;
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  color: hsl(var(--muted-foreground));
}

/* 暗色模式徽章适配 */
:deep(.dark) .plant-node-tree__type-badge {
  border-style: solid;
  border-width: 1px;
}
</style>
