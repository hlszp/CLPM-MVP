<script lang="ts" setup>
/**
 * 工厂模型筛选器（TreeSelect 形态）
 *
 * 列表页统一规范组件：用于筛选区按工厂模型（FACTORY/AREA/UNIT）
 * 选择范围，value 为 plantNodeId。区别于侧栏浏览组件 PlantNodeTree。
 *
 * 使用方式：
 * <ClpmPlantNodeTreeSelect v-model:value="query.plantNodeId" style="width: 220px" />
 */
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, ref } from 'vue';

import { TreeSelect } from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';

interface TreeOption {
  children?: TreeOption[];
  label: string;
  value: string;
}

defineOptions({ name: 'ClpmPlantNodeTreeSelect' });

withDefaults(defineProps<Props>(), {
  placeholder: '全部范围',
  width: '220px',
});

const value = defineModel<null | string>('value', { default: null });

interface Props {
  placeholder?: string;
  width?: string;
}

const loading = ref(false);
const treeData = ref<TreeOption[]>([]);

function toTreeOptions(nodes: PlantNodeApi.PlantNode[]): TreeOption[] {
  return nodes.map((node) => ({
    label: node.name,
    value: node.id,
    children: node.children?.length ? toTreeOptions(node.children) : undefined,
  }));
}

onMounted(async () => {
  loading.value = true;
  try {
    const nodes = await getPlantNodeTreeApi();
    treeData.value = toTreeOptions(nodes);
  } catch {
    // 静默降级：筛选器可用但无选项，不阻塞页面
    treeData.value = [];
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <TreeSelect
    v-model:value="value"
    allow-clear
    show-search
    tree-default-expand-all
    :loading="loading"
    :placeholder="placeholder"
    :style="{ width }"
    :tree-data="treeData"
    tree-node-filter-prop="label"
  />
</template>
