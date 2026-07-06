<script lang="ts" setup>
/**
 * 统一工厂模型树组件（UI/UX v6.1 §15 工业风格改造）
 *
 * 改造要点（对齐 ZL IndustrialDesignReference.html §1 状态语义色 + §2 hover-reveal）：
 * - 节点视觉升级：IconifyIcon 按类型区分（工厂/装置/单元）
 * - 类型徽章：ZL 语义色（bg-*-50/text-*-700/border-*-200）
 * - 节点尾部显示回路数（外部传入 loopCounts，递归累加子节点）
 * - 树头部统计栏：工厂/装置/单元/回路总数（可选，showStats）
 *
 * 工厂结构三层：FACTORY（工厂）→ AREA（装置/车间）→ UNIT（单元）
 * 回路挂在 UNIT 节点下。
 *
 * 抽象自 loop/manage.vue 和 metric/dashboard.vue 的公共树逻辑：
 * - 数据加载（getPlantNodeTreeApi）
 * - 搜索过滤（仅展开匹配节点的父级路径，非全量展开）
 * - 展开/折叠控制（全部展开 / 全部折叠按钮）
 * - 选中事件 emit
 * - CRUD 操作（新增、编辑、删除节点）
 * - 导入/导出 Excel
 *
 * 使用方式：
 * <PlantNodeTree card-title="工厂模型" :width="280"
 *   :loop-counts="loopCounts" :show-stats="true"
 *   @select="onTreeSelect" @load-complete="onTreeLoaded" />
 */
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref, watch } from 'vue';

import {
  Button,
  Card,
  Dropdown,
  Input,
  Menu,
  MenuItem,
  Modal,
  Spin,
  Tooltip,
  Tree,
  Upload,
  message,
} from 'ant-design-vue';

import { IconifyIcon } from '@vben/icons';

import { ClpmDangerConfirmModal } from '#/components/clpm';
import {
  createPlantNodeApi,
  deletePlantNodeApi,
  exportPlantNodesApi,
  getPlantNodeTreeApi,
  importPlantNodesApi,
  updatePlantNodeApi,
} from '#/api/plant-node';

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

const NODE_TYPE_CONFIG: Record<string, NodeTypeConfig> = {
  AREA: {
    badgeClass:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30',
    icon: 'ant-design:appstore-outlined',
    iconColor: 'var(--color-amber-600, #d97706)',
    label: '装置',
  },
  FACTORY: {
    badgeClass:
      'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/30',
    icon: 'ant-design:home-outlined',
    iconColor: 'var(--color-blue-600, #2563eb)',
    label: '工厂',
  },
  UNIT: {
    badgeClass:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
    icon: 'ant-design:database-outlined',
    iconColor: 'var(--color-slate-600, #475569)',
    label: '单元',
  },
};

/** 默认配置（未知类型） */
const DEFAULT_NODE_TYPE_CONFIG: NodeTypeConfig = {
  badgeClass:
    'bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
  icon: 'ant-design:folder-outlined',
  iconColor: 'var(--color-slate-500, #64748b)',
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
  /** 是否显示 CRUD 操作按钮 */
  showCrudButtons?: boolean;
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

const props = withDefaults(defineProps<Props>(), {
  cardTitle: '工厂模型',
  width: 280,
  showSearch: true,
  showCollapseButtons: true,
  showCrudButtons: false,
  defaultExpandLevel: 1,
  maxHeight: 'calc(100vh - 300px)',
  showStats: false,
  loopCounts: () => ({}),
});

const emit = defineEmits<{
  (e: 'load-complete', treeData: PlantNodeApi.PlantNode[]): void;
  (e: 'select', node: PlantNodeApi.PlantNode | null): void;
}>();

const treeData = ref<TreeNode[]>([]);
const treeLoading = ref(false);
const treeSearchKeyword = ref('');
const expandedKeys = ref<(number | string)[]>([]);
const autoExpandParent = ref(true);
const selectedNode = ref<TreeNode | null>(null);

// CRUD Modal
const crudModalVisible = ref(false);
const crudModalMode = ref<'create' | 'edit'>('create');
const crudFormName = ref('');
const crudFormType = ref<'AREA' | 'FACTORY' | 'UNIT'>('UNIT');
const crudFormParentId = ref<null | string>(null);
const crudLoading = ref(false);

// 删除确认模态框（ZL §9 高危操作二次确认）
const deleteModalVisible = ref(false);
const deleteTargetNode = ref<TreeNode | null>(null);
const deleteLoading = ref(false);
/** 删除操作的影响范围描述 */
const deleteImpactScope = computed(() => {
  if (!deleteTargetNode.value) return '';
  const node = deleteTargetNode.value;
  const loopCount = loopCountByNode.value[node.key as string] ?? 0;
  const childCount = node.children?.length ?? 0;
  // 后端策略：有子节点或关联回路的节点不允许删除，必须先清理
  if (childCount > 0 && loopCount > 0) {
    return `当前节点包含 ${childCount} 个子节点和 ${loopCount} 个回路，需先删除子节点和迁移回路后才能删除`;
  }
  if (childCount > 0) {
    return `当前节点包含 ${childCount} 个子节点，需先删除所有子节点后才能删除`;
  }
  if (loopCount > 0) {
    return `当前节点关联 ${loopCount} 个回路，需先迁移回路到其他单元后才能删除`;
  }
  return '仅删除当前节点，不可恢复';
});

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
      if (type === 'FACTORY') factoryCount++;
      else if (type === 'AREA') areaCount++;
      else if (type === 'UNIT') unitCount++;
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
    const validKeys = new Set<string | number>();
    function collectKeys(nodes: TreeNode[]) {
      for (const n of nodes) {
        validKeys.add(n.key);
        if (n.children) collectKeys(n.children);
      }
    }
    collectKeys(newTreeData);
    expandedKeys.value = expandedKeys.value.filter((k) =>
      validKeys.has(k),
    );

    // 默认展开第一层（仅初次加载或全部折叠后重新加载时）
    if (props.defaultExpandLevel >= 1 && expandedKeys.value.length === 0) {
      expandedKeys.value = newTreeData.map((n) => n.key);
    }
    autoExpandParent.value = true;
    emit('load-complete', data);
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

/** 选中树节点 */
function onTreeSelect(keys: any[], info: any) {
  const node =
    keys.length > 0 && info.selectedNodes?.[0]
      ? ((info.selectedNodes[0] as any)?.node ?? null)
      : null;
  selectedNode.value = node ? info.selectedNodes[0] : null;
  emit('select', node);
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

/** 打开新增节点弹窗 */
function openCreateModal(parentNode?: TreeNode) {
  crudModalMode.value = 'create';
  crudFormName.value = '';
  crudFormParentId.value = (parentNode?.key as string) ?? null;
  // 根据父节点类型设置默认子节点类型（FACTORY → AREA → UNIT）
  const parentType: string | undefined = parentNode?.node?.type;
  if (parentType === 'FACTORY') {
    crudFormType.value = 'AREA';
  } else if (parentType === 'AREA') {
    crudFormType.value = 'UNIT';
  } else {
    crudFormType.value = 'UNIT';
  }
  crudModalVisible.value = true;
}

/** 打开编辑节点弹窗 */
function openEditModal(node: TreeNode) {
  crudModalMode.value = 'edit';
  crudFormName.value = node.node.name;
  crudFormType.value = node.node.type as 'AREA' | 'FACTORY' | 'UNIT';
  crudFormParentId.value = node.node.parentId ?? null;
  selectedNode.value = node;
  crudModalVisible.value = true;
}

/** 提交 CRUD 表单 */
async function handleCrudSubmit() {
  if (!crudFormName.value.trim()) {
    message.warning('请输入节点名称');
    return;
  }

  crudLoading.value = true;
  try {
    if (crudModalMode.value === 'create') {
      await createPlantNodeApi({
        name: crudFormName.value.trim(),
        parentId: crudFormParentId.value,
        type: crudFormType.value as PlantNodeApi.NodeType,
      });
      message.success('创建成功');
    } else {
      if (!selectedNode.value?.key) return;
      await updatePlantNodeApi(selectedNode.value.key as string, {
        name: crudFormName.value.trim(),
      });
      message.success('更新成功');
    }
    crudModalVisible.value = false;
    loadTree();
  } catch {
    // 错误已由拦截器处理
  } finally {
    crudLoading.value = false;
  }
}

/** 打开删除确认模态框（ZL §9 高危操作二次确认） */
function openDeleteModal(node: TreeNode) {
  deleteTargetNode.value = node;
  deleteModalVisible.value = true;
}

/** 确认删除节点 */
async function handleConfirmDelete() {
  if (!deleteTargetNode.value) return;
  deleteLoading.value = true;
  try {
    await deletePlantNodeApi(deleteTargetNode.value.key as string);
    message.success('删除成功');
    deleteModalVisible.value = false;
    deleteTargetNode.value = null;
    loadTree();
  } catch {
    // 错误已由拦截器处理
  } finally {
    deleteLoading.value = false;
  }
}

/** 导出 Excel */
async function handleExport() {
  try {
    const blob = await exportPlantNodesApi();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'plant_nodes_export.xlsx';
    a.click();
    window.URL.revokeObjectURL(url);
    message.success('导出成功');
  } catch {
    // 错误已由拦截器处理
  }
}

/** 导入 Excel */
async function handleImport(options: any) {
  const { file } = options;
  try {
    const result = await importPlantNodesApi(file);
    message.success(
      `导入完成：新增 ${result.inserted} 条，更新 ${result.updated} 条`,
    );
    loadTree();
  } catch {
    // 错误已由拦截器处理
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
    :style="{ width: `${width}px` }"
    size="small"
    :body-style="{ padding: '8px' }"
  >
    <template #title>
      <span class="text-sm font-semibold">{{ cardTitle }}</span>
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
        <!-- 展开/折叠按钮 -->
        <template v-if="showCollapseButtons">
          <Tooltip title="全部展开">
            <Button type="text" size="small" @click="expandAll">
              <template #icon>
                <IconifyIcon icon="ant-design:node-expand-outlined" />
              </template>
            </Button>
          </Tooltip>
          <Tooltip title="全部折叠">
            <Button type="text" size="small" @click="collapseAll">
              <template #icon>
                <IconifyIcon icon="ant-design:node-collapse-outlined" />
              </template>
            </Button>
          </Tooltip>
        </template>
        <!-- CRUD 按钮：新增工厂 + 导入 + 导出 -->
        <template v-if="showCrudButtons">
          <Tooltip title="新增工厂">
            <Button
              type="text"
              size="small"
              class="text-blue-600"
              @click="openCreateModal()"
            >
              <template #icon>
                <IconifyIcon icon="ant-design:plus-outlined" />
              </template>
            </Button>
          </Tooltip>
          <Tooltip title="导入 Excel">
            <Upload
              :custom-request="handleImport"
              :show-upload-list="false"
              accept=".xlsx"
            >
              <Button type="text" size="small">
                <template #icon>
                  <IconifyIcon icon="ant-design:upload-outlined" />
                </template>
              </Button>
            </Upload>
          </Tooltip>
          <Tooltip title="导出 Excel">
            <Button type="text" size="small" @click="handleExport">
              <template #icon>
                <IconifyIcon icon="ant-design:download-outlined" />
              </template>
            </Button>
          </Tooltip>
        </template>
      </div>
    </template>

    <!-- 头部统计栏（ZL 高密度排版风格） -->
    <div
      v-if="showStats"
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
    </div>

    <!-- 搜索框 -->
    <div v-if="showSearch" class="mb-2 px-1">
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

    <Spin :spinning="treeLoading">
      <div class="overflow-auto" :style="{ maxHeight: maxHeight }">
        <Tree
          v-if="filteredTreeData.length > 0"
          :tree-data="filteredTreeData"
          :expanded-keys="expandedKeys"
          :auto-expand-parent="autoExpandParent"
          :show-line="true"
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
            <Dropdown :trigger="['contextmenu']" v-if="showCrudButtons">
              <div class="plant-node-tree__node group">
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
                  :class="[
                    'plant-node-tree__type-badge',
                    getNodeTypeConfig((nodeData as any).node?.type).badgeClass,
                  ]"
                >
                  {{ getNodeTypeConfig((nodeData as any).node?.type).label }}
                </span>
                <span
                  v-if="(loopCountByNode[nodeData.key as string] ?? 0) > 0"
                  class="plant-node-tree__loop-count"
                >
                  {{ loopCountByNode[nodeData.key as string] }}
                </span>
                <!-- hover reveal 操作按钮组（ZL §2） -->
                <div
                  v-if="showCrudButtons"
                  class="plant-node-tree__actions"
                >
                  <Tooltip title="新增子节点">
                    <Button
                      type="text"
                      size="small"
                      class="plant-node-tree__action-btn"
                      @click.stop="openCreateModal(nodeData as TreeNode)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:plus-outlined" />
                      </template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="编辑">
                    <Button
                      type="text"
                      size="small"
                      class="plant-node-tree__action-btn"
                      @click.stop="openEditModal(nodeData as TreeNode)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:edit-outlined" />
                      </template>
                    </Button>
                  </Tooltip>
                  <Tooltip title="删除">
                    <Button
                      type="text"
                      size="small"
                      danger
                      class="plant-node-tree__action-btn"
                      @click.stop="openDeleteModal(nodeData as TreeNode)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:delete-outlined" />
                      </template>
                    </Button>
                  </Tooltip>
                </div>
              </div>
              <template #overlay>
                <Menu>
                  <MenuItem
                    key="create"
                    @click="openCreateModal(nodeData as TreeNode)"
                  >
                    新增子节点
                  </MenuItem>
                  <MenuItem
                    key="edit"
                    @click="openEditModal(nodeData as TreeNode)"
                  >
                    编辑
                  </MenuItem>
                  <MenuItem
                    key="delete"
                    @click="openDeleteModal(nodeData as TreeNode)"
                  >
                    删除
                  </MenuItem>
                </Menu>
              </template>
            </Dropdown>
            <div v-else class="plant-node-tree__node">
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
                :class="[
                  'plant-node-tree__type-badge',
                  getNodeTypeConfig((nodeData as any).node?.type).badgeClass,
                ]"
              >
                {{ getNodeTypeConfig((nodeData as any).node?.type).label }}
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

    <!-- CRUD Modal -->
    <Modal
      v-model:open="crudModalVisible"
      :title="crudModalMode === 'create' ? '新增节点' : '编辑节点'"
      :confirm-loading="crudLoading"
      @ok="handleCrudSubmit"
    >
      <div class="flex flex-col gap-4">
        <div>
          <label class="text-sm text-slate-600 dark:text-slate-300">
            节点名称：
          </label>
          <Input
            v-model:value="crudFormName"
            placeholder="请输入节点名称"
            class="mt-1"
          />
        </div>
        <div v-if="crudModalMode === 'create'">
          <label class="text-sm text-slate-600 dark:text-slate-300">
            节点类型：
          </label>
          <div class="mt-1 flex gap-2">
            <Button
              :type="crudFormType === 'FACTORY' ? 'primary' : 'default'"
              size="small"
              @click="crudFormType = 'FACTORY'"
            >
              工厂
            </Button>
            <Button
              :type="crudFormType === 'AREA' ? 'primary' : 'default'"
              size="small"
              @click="crudFormType = 'AREA'"
            >
              装置
            </Button>
            <Button
              :type="crudFormType === 'UNIT' ? 'primary' : 'default'"
              size="small"
              @click="crudFormType = 'UNIT'"
            >
              单元
            </Button>
          </div>
        </div>
      </div>
    </Modal>

    <!-- 删除确认模态框（ZL §9 高危操作二次确认） -->
    <ClpmDangerConfirmModal
      v-model:open="deleteModalVisible"
      title="删除工厂节点"
      action="删除"
      :target="deleteTargetNode?.title ?? ''"
      :impact-scope="deleteImpactScope"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      :confirm-code="deleteTargetNode?.title ?? ''"
      confirm-code-placeholder="请输入节点名称以确认"
      require-reason
      :loading="deleteLoading"
      @confirm="handleConfirmDelete"
    />
  </Card>
</template>

<style scoped>
.plant-node-tree :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
}

/* 节点 hover 时增强视觉反馈 */
.plant-node-tree :deep(.ant-tree-node-content-wrapper:hover) {
  background-color: hsl(var(--accent) / 0.1);
}

/* 选中节点高亮 */
.plant-node-tree :deep(.ant-tree-node-selected) {
  background-color: hsl(var(--accent) / 0.15) !important;
}

/* —— 节点容器 —— */
.plant-node-tree__node {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 2px 4px;
  border-radius: 4px;
  transition: background-color 0.15s ease;
}

.plant-node-tree__node:hover {
  background-color: hsl(var(--accent) / 0.06);
}

/* 节点图标 */
.plant-node-tree__node-icon {
  font-size: 14px;
  flex-shrink: 0;
}

/* 节点标题 */
.plant-node-tree__node-title {
  font-size: 13px;
  color: hsl(var(--foreground));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 类型徽章统一最小宽度对齐 */
.plant-node-tree__type-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: 1px solid;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 500;
  line-height: 1.2;
  min-width: 32px;
  flex-shrink: 0;
}

/* 回路数（右侧灰色数字） */
.plant-node-tree__loop-count {
  margin-left: auto;
  padding-left: 8px;
  font-family: var(--font-mono);
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* —— hover reveal 操作按钮组（ZL §2）—— */
.plant-node-tree__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: 8px;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease;
}

.plant-node-tree__node:hover .plant-node-tree__actions {
  opacity: 1;
  visibility: visible;
}

/* 操作按钮样式：默认透明，hover 时浅灰背景 */
.plant-node-tree__action-btn {
  height: 22px !important;
  padding: 0 4px !important;
  font-size: 12px !important;
  border-radius: 3px !important;
}

.plant-node-tree__action-btn:hover {
  background-color: hsl(var(--accent) / 0.1) !important;
}

.plant-node-tree__action-btn.ant-btn-dangerous:hover {
  background-color: hsl(var(--destructive) / 0.1) !important;
}

/* 暗色模式徽章适配 */
:deep(.dark) .plant-node-tree__type-badge {
  border-style: solid;
  border-width: 1px;
}
</style>
