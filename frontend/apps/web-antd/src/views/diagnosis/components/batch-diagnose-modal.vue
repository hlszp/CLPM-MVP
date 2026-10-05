<script setup lang="ts">
/**
 * 批量诊断发起弹窗（2026-10-05 1009：诊断任务页新增）
 *
 * 后端 POST /diagnosis/trigger 原生支持 loopIds 数组；本弹窗补齐前端入口：
 * 左侧装置树（单选节点=范围）→ 右侧该范围回路多选（默认全选，可搜索）+
 * 时间窗预设 + 算子组（快速组/全部），提交后提示受理数（含 L2 条件警告数）。
 *
 * 操作权限：回路工作台剖面四角色（D1）；SPONSOR 无入口（页面层控制）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, ref, watch } from 'vue';

import { Alert, Checkbox, Input, message, Modal, Spin, Tree } from 'ant-design-vue';

import { triggerDiagnosisApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';

const emit = defineEmits<{ submitted: [] }>();

const open = defineModel<boolean>('open', { default: false });

// ===== 装置树 =====
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
}
const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(
  nodes: PlantNodeApi.PlantNode[],
): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

function collectExpandableKeys(nodes: PlantTreeNode[], acc: string[] = []): string[] {
  for (const n of nodes) {
    if (n.children?.length) {
      acc.push(n.key);
      collectExpandableKeys(n.children, acc);
    }
  }
  return acc;
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = collectExpandableKeys(plantTreeData.value);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

// ===== 回路多选（所选节点范围，默认全选） =====
interface LoopOption {
  description: null | string;
  loopId: string;
  tagName: string;
}
const loopOptions = ref<LoopOption[]>([]);
const loopLoading = ref(false);
const checkedIds = ref<string[]>([]);
const loopKeyword = ref('');

async function loadLoops(): Promise<void> {
  loopLoading.value = true;
  checkedIds.value = [];
  try {
    const all: LoopOption[] = [];
    let page = 1;
    let total = 0;
    do {
      const res = await getLoopListApi({
        page,
        pageSize: 100,
        plantNodeId: selectedPlantNodeId.value,
      });
      for (const it of res.items ?? []) {
        all.push({
          loopId: it.loopId,
          tagName: it.tagName,
          description: it.description ?? null,
        });
      }
      total = res.total ?? 0;
      page += 1;
    } while ((page - 1) * 100 < total);
    loopOptions.value = all;
    checkedIds.value = all.map((l) => l.loopId); // 默认全选
  } catch {
    loopOptions.value = [];
  } finally {
    loopLoading.value = false;
  }
}

const filteredLoopOptions = computed(() => {
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return loopOptions.value;
  return loopOptions.value.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

const allVisibleChecked = computed(
  () =>
    filteredLoopOptions.value.length > 0 &&
    filteredLoopOptions.value.every((l) => checkedIds.value.includes(l.loopId)),
);

function toggleCheckAll(): void {
  const visibleIds = filteredLoopOptions.value.map((l) => l.loopId);
  if (allVisibleChecked.value) {
    checkedIds.value = checkedIds.value.filter(
      (id) => !visibleIds.includes(id),
    );
  } else {
    const merged = new Set([...checkedIds.value, ...visibleIds]);
    // 保持 loopOptions 原始顺序，稳定展示
    checkedIds.value = loopOptions.value
      .map((l) => l.loopId)
      .filter((id) => merged.has(id));
  }
}

function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  void loadLoops();
}

// ===== 时间窗 + 算子组 =====
const preset = ref<DiagnosisApi.TimeWindowPreset>('last_24h');
const operatorGroup = ref<DiagnosisApi.OperatorGroup>('full');

const presetOptions: Array<{ key: DiagnosisApi.TimeWindowPreset; label: string }> = [
  { key: 'last_24h', label: '近 24 小时' },
  { key: 'last_7d', label: '近 7 天' },
  { key: 'last_30d', label: '近 30 天' },
];

// ===== 提交 =====
const submitting = ref(false);

async function submit(): Promise<void> {
  if (checkedIds.value.length === 0) {
    message.warning('请至少勾选一个回路');
    return;
  }
  submitting.value = true;
  try {
    const res = await triggerDiagnosisApi({
      loopIds: checkedIds.value,
      operatorGroup: operatorGroup.value,
      timeWindow: { preset: preset.value },
    });
    const warnCount = res.conditionWarning?.length ?? 0;
    message.success(
      `批量诊断已提交：受理 ${res.accepted ?? checkedIds.value.length} 个回路` +
        (warnCount > 0 ? `（${warnCount} 个回路存在 L2 条件警告，详见任务执行）` : ''),
      5,
    );
    open.value = false;
    emit('submitted');
  } catch (error) {
    const msg =
      (error as { response?: { data?: { message?: string } } })?.response?.data
        ?.message ?? '提交失败，请稍后重试';
    message.error(msg, 5);
  } finally {
    submitting.value = false;
  }
}

watch(open, (v) => {
  if (v && plantTreeData.value.length === 0) void loadPlantTree();
  if (v && loopOptions.value.length === 0) void loadLoops();
});
</script>

<template>
  <Modal
    v-model:open="open"
    :confirm-loading="submitting"
    :ok-button-props="{ disabled: checkedIds.length === 0 }"
    ok-text="发起批量诊断"
    title="发起批量诊断"
    width="760px"
    @ok="submit"
  >
    <div class="batch-diag-layout">
      <!-- 左：装置树（范围） -->
      <div class="batch-diag-tree">
        <div class="batch-diag-tree__title">
          <span>装置范围</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="batch-diag-tree__clear"
            @click="handlePlantTreeSelect([])"
          >
            全厂
          </button>
        </div>
        <Spin :spining="false" :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="batch-diag-tree__body"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="batch-diag-tree__empty">暂无装置数据</div>
        </Spin>
      </div>

      <!-- 右：回路多选 -->
      <div class="batch-diag-loops">
        <div class="batch-diag-loops__bar">
          <Checkbox
            :checked="allVisibleChecked"
            :indeterminate="!allVisibleChecked && checkedIds.length > 0"
            @click.prevent="toggleCheckAll"
          >
            全选（当前列表）
          </Checkbox>
          <span class="text-xs text-neutral-400">
            已选 {{ checkedIds.length }}/{{ loopOptions.length }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <Spin :spinning="loopLoading" size="small">
          <div class="batch-diag-loops__list">
            <Checkbox.Group v-model:value="checkedIds" class="batch-diag-loops__group">
              <Checkbox
                v-for="l in filteredLoopOptions"
                :key="l.loopId"
                :value="l.loopId"
                :title="l.description ?? l.tagName"
              >
                {{ l.tagName }}
              </Checkbox>
            </Checkbox.Group>
            <div
              v-if="!loopLoading && filteredLoopOptions.length === 0"
              class="batch-diag-tree__empty"
            >
              暂无回路
            </div>
          </div>
        </Spin>
      </div>
    </div>

    <!-- 时间窗 + 算子组 -->
    <div class="batch-diag-opts">
      <div class="batch-diag-opts__row">
        <span class="batch-diag-opts__label">时间窗</span>
        <div class="batch-diag-opts__seg">
          <button
            v-for="opt in presetOptions"
            :key="opt.key"
            class="batch-diag-opts__btn"
            :class="{ 'batch-diag-opts__btn--active': preset === opt.key }"
            @click="preset = opt.key"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
      <div class="batch-diag-opts__row">
        <span class="batch-diag-opts__label">算子组</span>
        <div class="batch-diag-opts__seg">
          <button
            class="batch-diag-opts__btn"
            :class="{ 'batch-diag-opts__btn--active': operatorGroup === 'fast' }"
            @click="operatorGroup = 'fast'"
          >
            快速组
          </button>
          <button
            class="batch-diag-opts__btn"
            :class="{ 'batch-diag-opts__btn--active': operatorGroup === 'full' }"
            @click="operatorGroup = 'full'"
          >
            全部算子
          </button>
        </div>
      </div>
      <Alert
        type="info"
        show-icon
        :message="`将按整点回算口径对已选 ${checkedIds.length} 个回路发起诊断；L0（数据严重不足）回路会被后端拒绝并计入任务结果。`"
      />
    </div>
  </Modal>
</template>

<style scoped>
.batch-diag-layout {
  display: flex;
  gap: 12px;
  min-height: 320px;
}

.batch-diag-tree {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  width: 200px;
}

.batch-diag-tree__title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.batch-diag-tree__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.batch-diag-tree__body {
  max-height: 300px;
  overflow: auto;
  font-size: 12px;
}

.batch-diag-tree__body :deep(.ant-tree-node-content-wrapper) {
  min-height: 26px;
  line-height: 26px;
}

.batch-diag-tree__empty {
  padding: 12px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

.batch-diag-loops {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
}

.batch-diag-loops__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.batch-diag-loops__list {
  height: 300px;
  overflow: auto;
  padding: 4px;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.batch-diag-loops__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
}

.batch-diag-loops__group :deep(label.ant-checkbox-wrapper) {
  margin: 0;
  padding: 0 2px;
  font-size: 12px;
}

.batch-diag-opts {
  margin-top: 12px;
}

.batch-diag-opts__row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.batch-diag-opts__label {
  width: 48px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.batch-diag-opts__seg {
  display: flex;
  gap: 4px;
}

.batch-diag-opts__btn {
  padding: 2px 10px;
  font-size: 12px;
  color: hsl(var(--foreground) / 75%);
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.batch-diag-opts__btn--active {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 10%);
  border-color: hsl(var(--primary) / 40%);
}
</style>
