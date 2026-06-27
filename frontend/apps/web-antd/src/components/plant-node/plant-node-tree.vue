<script lang="ts" setup>
/**
 * 统一工厂模型树组件（UI/UX 建议 6 + 建议 9）
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
  message,
  Popconfirm,
  Spin,
  Tree,
  Upload,
} from 'ant-design-vue';

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
}

const props = withDefaults(defineProps<Props>(), {
  cardTitle: '工厂模型',
  width: 280,
  showSearch: true,
  showCollapseButtons: true,
  showCrudButtons: false,
  defaultExpandLevel: 1,
  maxHeight: 'calc(100vh - 300px)',
});

const emit = defineEmits<{
  (e: 'select', node: PlantNodeApi.PlantNode | null): void;
  (e: 'load-complete', treeData: PlantNodeApi.PlantNode[]): void;
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
const crudFormType = ref<'FACTORY' | 'AREA' | 'UNIT'>('UNIT');
const crudFormParentId = ref<string | null>(null);
const crudLoading = ref(false);

/** 将后端 PlantNode 转为 Ant Design Tree 节点 */
function toTreeNode(node: PlantNodeApi.PlantNode): TreeNode {
  return {
    children: node.children?.map((child) => toTreeNode(child)),
    key: node.id,
    node,
    title: node.name,
  };
}

/** 节点类型中文标签 */
function getPlantNodeTypeLabel(type?: string) {
  return (
    {
      AREA: '装置',
      EQUIPMENT: '设备',
      FACTORY: '工厂',
      UNIT: '单元',
    }[type ?? ''] ?? ''
  );
}

/** 加载工厂模型树 */
async function loadTree() {
  treeLoading.value = true;
  try {
    const data = await getPlantNodeTreeApi();
    treeData.value = data.map((node) => toTreeNode(node));
    // 默认展开第一层
    if (props.defaultExpandLevel >= 1) {
      expandedKeys.value = treeData.value.map((n) => n.key);
    }
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
  crudFormParentId.value = parentNode?.key as string ?? null;
  // 根据父节点类型设置默认子节点类型
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
  crudFormType.value = node.node.type as 'FACTORY' | 'AREA' | 'UNIT';
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
        type: crudFormType.value as PlantNodeApi.NodeType,
        parentId: crudFormParentId.value,
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

/** 删除节点 */
async function handleDeleteNode(node: TreeNode) {
  try {
    await deletePlantNodeApi(node.key as string);
    message.success('删除成功');
    loadTree();
  } catch {
    // 错误已由拦截器处理
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
    class="shrink-0"
    :style="{ width: `${width}px` }"
    size="small"
    :body-style="{ padding: '8px' }"
  >
    <template #title>
      <span class="text-sm">{{ cardTitle }}</span>
    </template>
    <template #extra>
      <div class="flex gap-1">
        <!-- 刷新按钮 -->
        <Button
          type="text"
          size="small"
          :loading="treeLoading"
          @click="loadTree"
        >
          <span class="text-xs">⟳</span>
        </Button>
        <!-- 展开/折叠按钮 -->
        <template v-if="showCollapseButtons">
          <Button type="text" size="small" @click="expandAll">
            <span class="text-xs">⊞</span>
          </Button>
          <Button type="text" size="small" @click="collapseAll">
            <span class="text-xs">⊟</span>
          </Button>
        </template>
        <!-- CRUD 按钮 -->
        <template v-if="showCrudButtons">
          <Dropdown>
            <Button type="text" size="small">
              <span class="text-xs">+</span>
            </Button>
            <template #overlay>
              <Menu>
                <MenuItem key="create-factory" @click="openCreateModal()">
                  新增工厂
                </MenuItem>
                <MenuItem key="export" @click="handleExport">
                  导出 Excel
                </MenuItem>
                <MenuItem key="import">
                  <Upload
                    :custom-request="handleImport"
                    :show-upload-list="false"
                    accept=".xlsx"
                  >
                    导入 Excel
                  </Upload>
                </MenuItem>
              </Menu>
            </template>
          </Dropdown>
        </template>
      </div>
    </template>

    <!-- 搜索框 -->
    <div v-if="showSearch" class="mb-2 px-1">
      <Input
        v-model:value="treeSearchKeyword"
        placeholder="搜索工厂/装置/单元"
        allow-clear
        size="small"
      />
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
              <span class="inline-flex items-center gap-1">
                <span class="text-gray-500">📁</span>
                <span>{{ nodeData.title }}</span>
                <span class="text-xs text-gray-400">
                  {{ getPlantNodeTypeLabel((nodeData as any).node?.type) }}
                </span>
              </span>
              <template #overlay>
                <Menu>
                  <MenuItem
                    key="create"
                    @click="openCreateModal(nodeData as TreeNode)"
                  >
                    新增子节点
                  </MenuItem>
                  <MenuItem key="edit" @click="openEditModal(nodeData as TreeNode)">
                    编辑
                  </MenuItem>
                  <MenuItem key="delete">
                    <Popconfirm
                      title="确定删除该节点？"
                      @confirm="handleDeleteNode(nodeData as TreeNode)"
                    >
                      删除
                    </Popconfirm>
                  </MenuItem>
                </Menu>
              </template>
            </Dropdown>
            <span v-else class="inline-flex items-center gap-1">
              <span class="text-gray-500">📁</span>
              <span>{{ nodeData.title }}</span>
              <span class="text-xs text-gray-400">
                {{ getPlantNodeTypeLabel((nodeData as any).node?.type) }}
              </span>
            </span>
          </template>
        </Tree>
        <div v-else class="py-8 text-center text-xs text-gray-400">
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
          <label class="text-sm text-gray-600">节点名称：</label>
          <Input
            v-model:value="crudFormName"
            placeholder="请输入节点名称"
            class="mt-1"
          />
        </div>
        <div v-if="crudModalMode === 'create'">
          <label class="text-sm text-gray-600">节点类型：</label>
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
  </Card>
</template>

<style scoped>
.plant-node-tree :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
}
</style>
