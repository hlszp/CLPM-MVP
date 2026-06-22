<script lang="ts" setup>
/**
 * S2-LOOP-007 工厂层级配置页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.1 ~ §2.2.4
 * - 左侧 Ant Design Tree 展示工厂层级（工厂→装置→单元）
 * - 右侧显示选中节点详情
 * - 支持新增/编辑/删除节点（Modal + Form）
 * - 删除有子节点时弹窗提示
 * - RBAC: 仅 ADMIN 可写
 */
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Tree,
} from 'ant-design-vue';

import {
  createPlantNodeApi,
  deletePlantNodeApi,
  getPlantNodeTreeApi,
  updatePlantNodeApi,
} from '#/api/plant-node';

defineOptions({ name: 'LoopFactory' });

interface TreeNode {
  children?: TreeNode[];
  key: string | number;
  node: PlantNodeApi.PlantNode;
  title: string;
}

const treeData = ref<TreeNode[]>([]);
const selectedNode = ref<null | PlantNodeApi.PlantNode>(null);
const loading = ref(false);

// Modal state
const modalVisible = ref(false);
const modalMode = ref<'add' | 'edit'>('add');
const modalLoading = ref(false);
const formRef = ref();
const formState = reactive({
  name: '',
  type: 'FACTORY' as PlantNodeApi.NodeType,
  parentId: null as null | string,
});

const nodeTypeOptions = [
  { label: '工厂', value: 'FACTORY' },
  { label: '装置/单元', value: 'UNIT' },
  { label: '设备', value: 'EQUIPMENT' },
];

const nodeTypeLabel: Record<PlantNodeApi.NodeType, string> = {
  EQUIPMENT: '设备',
  FACTORY: '工厂',
  UNIT: '装置/单元',
};

/** 将后端 PlantNode 转为 Ant Design Tree 节点 */
function toTreeNode(node: PlantNodeApi.PlantNode): TreeNode {
  return {
    children: node.children?.map((child) => toTreeNode(child)),
    key: node.id,
    node,
    title: node.name,
  };
}

/** 加载工厂层级树 */
async function loadTree() {
  loading.value = true;
  try {
    const data = await getPlantNodeTreeApi();
    treeData.value = data.map((node) => toTreeNode(node));
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 选中节点 */
function onSelect(keys: any[], info: any) {
  selectedNode.value =
    keys.length > 0 && info.selectedNodes?.[0]
      ? (info.selectedNodes[0] as any)?.node ?? null
      : null;
}

/** 打开新增 Modal */
function handleAdd(parentNode?: PlantNodeApi.PlantNode) {
  modalMode.value = 'add';
  formState.name = '';
  if (parentNode) {
    // 在子节点下新增：根据父节点类型推断子节点类型
    if (parentNode.type === 'FACTORY') {
      formState.type = 'UNIT';
    } else if (parentNode.type === 'UNIT') {
      formState.type = 'EQUIPMENT';
    } else {
      formState.type = 'EQUIPMENT';
    }
    formState.parentId = parentNode.id;
  } else {
    formState.type = 'FACTORY';
    formState.parentId = null;
  }
  modalVisible.value = true;
}

/** 打开编辑 Modal */
function handleEdit(node: PlantNodeApi.PlantNode) {
  modalMode.value = 'edit';
  formState.name = node.name;
  formState.type = node.type;
  formState.parentId = node.parentId;
  modalVisible.value = true;
}

/** 提交表单 */
async function handleSubmit() {
  await formRef.value?.validate();
  modalLoading.value = true;
  try {
    if (modalMode.value === 'add') {
      await createPlantNodeApi({
        name: formState.name,
        parentId: formState.parentId,
        type: formState.type,
      });
      message.success('节点创建成功');
    } else {
      if (selectedNode.value) {
        await updatePlantNodeApi(selectedNode.value.id, {
          name: formState.name,
        });
        message.success('节点更新成功');
      }
    }
    modalVisible.value = false;
    await loadTree();
  } catch {
    // 错误已由拦截器处理
  } finally {
    modalLoading.value = false;
  }
}

/** 删除节点 */
async function handleDelete(node: PlantNodeApi.PlantNode) {
  if (node.children && node.children.length > 0) {
    message.warning('该节点存在子节点，无法删除');
    return;
  }
  try {
    await deletePlantNodeApi(node.id);
    message.success('节点删除成功');
    if (selectedNode.value?.id === node.id) {
      selectedNode.value = null;
    }
    await loadTree();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 查找节点在树中的路径 */
function findNodePath(
  nodes: PlantNodeApi.PlantNode[],
  id: string,
  path: PlantNodeApi.PlantNode[] = [],
): null | PlantNodeApi.PlantNode[] {
  for (const node of nodes) {
    const currentPath = [...path, node];
    if (node.id === id) return currentPath;
    if (node.children) {
      const found = findNodePath(node.children, id, currentPath);
      if (found) return found;
    }
  }
  return null;
}

const nodePath = computed(() => {
  if (!selectedNode.value) return [];
  const allNodes = (treeData.value || []).map((n) => n.node);
  return findNodePath(allNodes, selectedNode.value.id) || [];
});

onMounted(() => {
  loadTree();
});
</script>

<template>
  <Page title="工厂层级配置">
    <div class="flex gap-4" style="height: calc(100vh - 140px)">
      <!-- 左侧树 -->
      <Card class="w-1/2 min-w-300px" title="工厂层级">
        <template #extra>
          <Button
            v-permission="['ADMIN']"
            type="primary"
            size="small"
            @click="handleAdd()"
          >
            新增顶层节点
          </Button>
        </template>
        <Tree
          :tree-data="treeData"
          :loading="loading"
          :default-expand-all="true"
          :show-line="true"
          class="loop-factory-tree"
          @select="onSelect"
        >
          <template #title="nodeData">
            <span class="inline-flex items-center gap-2">
              <span>{{ nodeData.title }}</span>
              <span class="text-xs text-gray-400">
                {{
                  nodeTypeLabel[
                    (nodeData as any).node.type as PlantNodeApi.NodeType
                  ]
                }}
              </span>
              <span class="ml-2 inline-flex gap-1">
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  @click.stop="handleAdd((nodeData as any).node)"
                >
                  新增子节点
                </Button>
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  @click.stop="handleEdit((nodeData as any).node)"
                >
                  编辑
                </Button>
                <Popconfirm
                  v-permission="['ADMIN']"
                  title="确认删除该节点？"
                  @confirm="handleDelete((nodeData as any).node)"
                >
                  <Button type="link" size="small" danger @click.stop>
                    删除
                  </Button>
                </Popconfirm>
              </span>
            </span>
          </template>
        </Tree>
        <div
          v-if="!loading && (!treeData || treeData.length === 0)"
          class="py-8 text-center text-gray-400"
        >
          暂无工厂层级数据，请点击"新增顶层节点"创建
        </div>
      </Card>

      <!-- 右侧详情 -->
      <Card class="w-1/2" title="节点详情">
        <div v-if="selectedNode" class="space-y-4">
          <div class="border-b pb-3">
            <div class="mb-1 text-xs text-gray-400">节点名称</div>
            <div class="text-lg font-medium">{{ selectedNode.name }}</div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <div class="mb-1 text-xs text-gray-400">节点类型</div>
              <div>{{ nodeTypeLabel[selectedNode.type] }}</div>
            </div>
            <div>
              <div class="mb-1 text-xs text-gray-400">节点 ID</div>
              <div class="break-all text-sm text-gray-600">
                {{ selectedNode.id }}
              </div>
            </div>
            <div>
              <div class="mb-1 text-xs text-gray-400">父节点 ID</div>
              <div class="break-all text-sm text-gray-600">
                {{ selectedNode.parentId || '—（顶层节点）' }}
              </div>
            </div>
            <div>
              <div class="mb-1 text-xs text-gray-400">子节点数量</div>
              <div>{{ selectedNode.children?.length || 0 }}</div>
            </div>
          </div>
          <div>
            <div class="mb-1 text-xs text-gray-400">层级路径</div>
            <div class="flex flex-wrap items-center gap-1 text-sm">
              <template v-for="(n, i) in nodePath" :key="n.id">
                <span>{{ n.name }}</span>
                <span v-if="i < nodePath.length - 1" class="text-gray-400">
                  /
                </span>
              </template>
            </div>
          </div>
          <div class="flex gap-2 pt-2">
            <Button
              v-permission="['ADMIN']"
              type="primary"
              @click="handleEdit(selectedNode)"
            >
              编辑节点
            </Button>
            <Button v-permission="['ADMIN']" @click="handleAdd(selectedNode)">
              新增子节点
            </Button>
            <Popconfirm
              v-permission="['ADMIN']"
              title="确认删除该节点？"
              @confirm="handleDelete(selectedNode)"
            >
              <Button v-permission="['ADMIN']" danger>删除节点</Button>
            </Popconfirm>
          </div>
        </div>
        <div
          v-else
          class="flex h-full items-center justify-center text-gray-400"
        >
          请在左侧选择一个节点查看详情
        </div>
      </Card>
    </div>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="modalMode === 'add' ? '新增节点' : '编辑节点'"
      :confirm-loading="modalLoading"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <FormItem
          name="name"
          label="节点名称"
          :rules="[{ required: true, message: '请输入节点名称' }]"
        >
          <Input v-model:value="formState.name" placeholder="请输入节点名称" />
        </FormItem>
        <FormItem v-if="modalMode === 'add'" name="type" label="节点类型">
          <Select v-model:value="formState.type" :options="nodeTypeOptions" />
        </FormItem>
        <div v-if="modalMode === 'add'" class="text-xs text-gray-400">
          父节点：{{ formState.parentId || '—（顶层节点）' }}
        </div>
      </Form>
    </Modal>
  </Page>
</template>

<style scoped>
.min-w-300px {
  min-width: 300px;
}

.loop-factory-tree :deep(.ant-tree-node-content-wrapper) {
  flex: 1;
}
</style>
