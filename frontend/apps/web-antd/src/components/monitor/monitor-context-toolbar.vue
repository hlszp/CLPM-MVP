/** * 共享监控工具栏（MW-P1-02） * * 工作台和批量表格共用同一筛选对象，URL
为真相源。 * - 装置/单元、回路类型、关键词、保存视图 * - "只看关注项"在 Phase 2
API 就绪前不渲染（attentionOnlyHidden prop 控制） * - 搜索 300ms
防抖；下拉变化立即更新 URL；回车立即查询 * - 保存视图复用
use-clpm-preferences.ts * * 对齐整改方案 §9.1/§9.4。 */
<script lang="ts" setup>
import { onMounted, ref, watch } from 'vue';

import { Input, Select } from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { LOOP_TYPE_LABEL_MAP } from '#/composables/use-loop-palettes';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { useSavedView } from '#/composables/use-saved-view';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'MonitorContextToolbar' });

const props = withDefaults(
  defineProps<{
    /** "只看关注项"筛选是否可见（Phase 2 API 就绪后传 true） */
    attentionOnlyHidden?: boolean;
    /** 保存视图的 pageKey */
    pageKey?: string;
    /** 是否显示装置/单元选择器（控制台 Tab 传 false 隐藏） */
    showPlantNode?: boolean;
    /** 是否显示回路类型选择器（控制台 Tab 传 false 隐藏） */
    showLoopType?: boolean;
    /** 是否显示搜索框（控制台 Tab 传 false 隐藏） */
    showSearch?: boolean;
    /** 是否显示保存视图下拉（控制台 Tab 传 false 隐藏） */
    showSavedView?: boolean;
  }>(),
  {
    attentionOnlyHidden: true,
    pageKey: 'monitor-workbench',
    showPlantNode: true,
    showLoopType: true,
    showSearch: true,
    showSavedView: true,
  },
);

const emit = defineEmits<{
  /** 筛选条件变化（已写入 URL，调用方可 watch context 触发加载） */
  (e: 'filterChange'): void;
}>();

const monitorCtx = useMonitorContext();
const { savedFilters, saveCurrentView, applyView } = useSavedView(
  props.pageKey,
);

// ===== 装置/单元选项 =====
const plantNodeOptions = ref<{ label: string; value: string }[]>([
  { label: '全部装置', value: '' },
]);

async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    const flat = flattenNodes(tree);
    plantNodeOptions.value = [
      { label: '全部装置', value: '' },
      ...flat.map((n) => ({ label: n.name, value: n.id })),
    ];
  } catch {
    // 保持默认"全部装置"
  }
}

// ===== 回路类型选项 =====
const loopTypeOptions = [
  { label: '全部类型', value: '' },
  ...Object.entries(LOOP_TYPE_LABEL_MAP).map(([value, label]) => ({
    label,
    value,
  })),
];

// ===== 本地绑定（防抖搜索用） =====
const localKeyword = ref(monitorCtx.keyword.value);
let searchTimer: null | ReturnType<typeof setTimeout> = null;

// ===== 筛选操作 =====
function handleKeywordInput() {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    monitorCtx.update({ keyword: localKeyword.value });
    emit('filterChange');
  }, 300);
}

function handleKeywordEnter() {
  if (searchTimer) clearTimeout(searchTimer);
  monitorCtx.update({ keyword: localKeyword.value });
  emit('filterChange');
}

function handlePlantNodeChange(value: any) {
  const v = typeof value === 'string' ? value : String(value ?? '');
  monitorCtx.update({ plantNodeId: v || null });
  emit('filterChange');
}

function handleLoopTypeChange(value: any) {
  const v = typeof value === 'string' ? value : String(value ?? '');
  monitorCtx.update({ loopType: v || null });
  emit('filterChange');
}

function handleAttentionOnlyChange(checked: boolean) {
  monitorCtx.update({ attentionOnly: checked });
  emit('filterChange');
}

// ===== 保存视图（MW-P4-03）=====
// 保存视图包含模式、筛选和时间窗，不包含 eventId/trackerId/section（深链接上下文）
function handleSaveView() {
  const name = `预设 ${savedFilters.value.length + 1}`;
  saveCurrentView(name);
}

/**
 * 应用保存视图（MW-P4-03）。
 * 无权限字段被安全忽略：EXPERT/SPONSOR 不能使用 table 模式，
 * 应用 view=table 时回退到 workspace。
 */
function handleApplyPreset(value: any) {
  const presetId = typeof value === 'string' ? value : String(value ?? '');
  const ok = applyView(presetId);
  if (ok) emit('filterChange');
}

// ===== 同步 URL → 本地 keyword =====
watch(
  () => monitorCtx.keyword.value,
  (val) => {
    if (val !== localKeyword.value) {
      localKeyword.value = val;
    }
  },
);

onMounted(() => {
  loadPlantNodes();
});
</script>

<template>
  <div class="flex items-center gap-2">
    <!-- 装置/单元 -->
    <Select
      v-if="showPlantNode"
      :value="monitorCtx.plantNodeId.value ?? ''"
      :options="plantNodeOptions"
      size="small"
      class="w-40"
      placeholder="装置/单元"
      :allow-clear="true"
      @change="handlePlantNodeChange"
    />

    <!-- 回路类型 -->
    <Select
      v-if="showLoopType"
      :value="monitorCtx.loopType.value ?? ''"
      :options="loopTypeOptions"
      size="small"
      class="w-28"
      placeholder="类型"
      :allow-clear="true"
      @change="handleLoopTypeChange"
    />

    <!-- 关键词搜索（300ms 防抖，仅回路清单 Tab 显示） -->
    <Input
      v-if="showSearch"
      v-model:value="localKeyword"
      size="small"
      class="w-44"
      placeholder="搜索位号/名称..."
      allow-clear
      @input="handleKeywordInput"
      @press-enter="handleKeywordEnter"
    />

    <!-- 只看关注项（Phase 2 就绪后通过 attentionOnlyHidden=false 显示） -->
    <label
      v-if="!attentionOnlyHidden"
      class="flex cursor-pointer items-center gap-1 text-xs"
    >
      <input
        type="checkbox"
        :checked="monitorCtx.attentionOnly.value"
        class="h-3 w-3"
        @change="
          handleAttentionOnlyChange(($event.target as HTMLInputElement).checked)
        "
      />
      <span>只看关注项</span>
    </label>

    <!-- 保存视图（仅回路清单 Tab 显示） -->
    <Select
      v-if="showSavedView"
      :value="undefined"
      size="small"
      class="w-32"
      placeholder="保存视图"
      :options="
        savedFilters.map((p) => ({
          label: p.name,
          value: p.id,
        }))
      "
      @change="handleApplyPreset"
    >
      <template #dropdownRender>
        <div class="p-1">
          <button
            class="w-full rounded px-2 py-1 text-left text-xs hover:bg-gray-100"
            @click="handleSaveView"
          >
            + 保存当前筛选…
          </button>
          <hr v-if="savedFilters.length > 0" class="my-1" />
          <div
            v-for="preset in savedFilters"
            :key="preset.id"
            class="cursor-pointer rounded px-2 py-1 text-xs hover:bg-gray-100"
            @click="handleApplyPreset(preset.id)"
          >
            {{ preset.name }}
          </div>
        </div>
      </template>
    </Select>
  </div>
</template>
