<script lang="ts" setup>
import type { AasApi } from '#/api/aas';
/**
 * S2-LOOP-010 Tag 关联管理页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.12 ~ §2.2.13
 * - 7 个 Tag 槽位可视化展示（PV/SP/OP/MODE/PID_P/PID_I/PID_D）
 * - 每个槽位支持下拉选择 tag_registry 中的 Tag
 * - PV/SP/OP/MODE 必填标识（红色 *）
 * - 保存时前端校验必填项
 * - 保存成功后回路状态自动更新
 * - 先选择回路（从 URL query 或下拉），再展示 7 槽位
 */
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, message, Select, Spin, Tag } from 'ant-design-vue';

import { getAasTagsApi } from '#/api/aas';
import {
  getLoopListApi,
  getLoopTagsApi,
  updateLoopTagMappingApi,
} from '#/api/loop';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import StatusBadge from '#/components/loop/status-badge.vue';

defineOptions({ name: 'LoopTagMapping' });

const route = useRoute();

const selectedLoopId = ref<string | undefined>(
  (route.query.loopId as string) || undefined,
);
const loopList = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);

const tagData = ref<LoopApi.LoopTagsResult | null>(null);
const tagLoading = ref(false);
const saving = ref(false);

// Available tags from AAS registry
const availableTags = ref<AasApi.AasTag[]>([]);
const tagSearchLoading = ref(false);

// 7 slot mapping state (uses undefined for Select compatibility, converts to null on save)
const slotState = reactive({
  pv: undefined as string | undefined,
  sp: undefined as string | undefined,
  op: undefined as string | undefined,
  mode: undefined as string | undefined,
  pid_p: undefined as string | undefined,
  pid_i: undefined as string | undefined,
  pid_d: undefined as string | undefined,
});

interface SlotConfig {
  key: keyof typeof slotState;
  label: string;
  required: boolean;
  color: string;
  description: string;
}

const slotConfigs: SlotConfig[] = [
  {
    color: 'blue',
    description: '过程变量测量值',
    key: 'pv',
    label: 'PV',
    required: true,
  },
  {
    color: 'green',
    description: '设定值',
    key: 'sp',
    label: 'SP',
    required: true,
  },
  {
    color: 'orange',
    description: '控制器输出值',
    key: 'op',
    label: 'OP',
    required: true,
  },
  {
    color: 'purple',
    description: '控制模式',
    key: 'mode',
    label: 'MODE',
    required: true,
  },
  {
    color: 'cyan',
    description: '比例参数',
    key: 'pid_p',
    label: 'PID_P',
    required: false,
  },
  {
    color: 'cyan',
    description: '积分参数',
    key: 'pid_i',
    label: 'PID_I',
    required: false,
  },
  {
    color: 'cyan',
    description: '微分参数',
    key: 'pid_d',
    label: 'PID_D',
    required: false,
  },
];

const selectedLoop = computed(() =>
  loopList.value.find((l) => l.loopId === selectedLoopId.value),
);

/** 加载回路列表（用于下拉选择） */
async function loadLoopList() {
  loopLoading.value = true;
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    loopList.value = data.items;
    // 如果 URL 带了 loopId 或默认选第一个
    if (!selectedLoopId.value && data.items.length > 0) {
      selectedLoopId.value = data.items[0]?.loopId ?? '';
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loopLoading.value = false;
  }
}

/** 加载可用 Tag 列表 */
async function loadAvailableTags(keyword?: string) {
  tagSearchLoading.value = true;
  try {
    const data = await getAasTagsApi({
      keyword: keyword || undefined,
      page: 1,
      pageSize: 10000, // 加载所有测点以支持自动关联
    });
    availableTags.value = data.items;
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagSearchLoading.value = false;
  }
}

/** 加载回路 Tag 关联详情 */
async function loadLoopTags() {
  if (!selectedLoopId.value) {
    tagData.value = null;
    return;
  }
  tagLoading.value = true;
  try {
    const data = await getLoopTagsApi(selectedLoopId.value);
    tagData.value = data;
    // 填充 slotState
    for (const tag of data.tags) {
      const key = tag.role.toLowerCase() as keyof typeof slotState;
      slotState[key] = tag.tagId ?? undefined;
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagLoading.value = false;
  }
}

/** Tag 下拉搜索 */
function handleTagSearch(value: string) {
  loadAvailableTags(value);
}

/** 保存 Tag 关联 */
async function handleSave() {
  if (!selectedLoopId.value) return;

  // 前端校验必填项
  const missing: string[] = [];
  for (const cfg of slotConfigs) {
    if (cfg.required && !slotState[cfg.key]) {
      missing.push(cfg.label);
    }
  }
  if (missing.length > 0) {
    message.warning(`以下必填 Tag 未关联：${missing.join('、')}`);
  }

  saving.value = true;
  try {
    const result = await updateLoopTagMappingApi(selectedLoopId.value, {
      pv: slotState.pv ?? null,
      sp: slotState.sp ?? null,
      op: slotState.op ?? null,
      mode: slotState.mode ?? null,
      pid_p: slotState.pid_p ?? null,
      pid_i: slotState.pid_i ?? null,
      pid_d: slotState.pid_d ?? null,
    });
    tagData.value = result;
    if (result.status === 'PARTIAL') {
      message.warning('保存成功，但回路状态为「部分关联」，请检查必填 Tag');
    } else if (result.status === 'READY') {
      message.success('保存成功，回路状态已更新为「就绪」');
    } else {
      message.success('保存成功');
    }
    // 刷新回路列表以更新状态
    await loadLoopList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

/** 清空某个槽位 */
function clearSlot(key: keyof typeof slotState) {
  slotState[key] = undefined;
}

/** 自动关联：根据回路位号匹配测点 */
async function handleAutoLink() {
  if (!selectedLoop.value?.tagName || !availableTags.value.length) {
    message.warning('请先选择回路并加载可用测点');
    return;
  }

  const loopTagName = selectedLoop.value.tagName;
  const roleToTagType: Record<string, string> = {
    pv: 'PV',
    sp: 'SP',
    op: 'OP',
    mode: 'MODE',
    pid_p: 'KP',
    pid_i: 'TI',
    pid_d: 'TD',
  };

  let matchedCount = 0;
  for (const key of Object.keys(slotState) as (keyof typeof slotState)[]) {
    const tagType = roleToTagType[key];
    if (!tagType) continue;

    const expectedTagName = `${loopTagName}_${tagType}`;
    const tag = availableTags.value.find((t) => t.tagName === expectedTagName);

    if (tag) {
      slotState[key] = tag.tagId;
      matchedCount++;
    }
  }

  if (matchedCount > 0) {
    message.success(`自动关联成功！匹配到 ${matchedCount} 个测点`);
  } else {
    message.info('未找到匹配的测点，请手动关联');
  }
}

watch(selectedLoopId, () => {
  loadLoopTags();
});

onMounted(() => {
  loadLoopList();
  loadAvailableTags();
  if (selectedLoopId.value) {
    loadLoopTags();
  }
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="Tag 关联管理"
      subtitle="按回路管理 7 个核心 Tag 槽位，并校验必填关联。"
    />
    <div class="mt-4 space-y-4">
      <!-- 回路选择区 -->
      <ClpmDataCanvas title="回路选择">
        <div class="flex items-center gap-4">
          <span class="whitespace-nowrap font-medium">选择回路：</span>
          <Select
            v-model:value="selectedLoopId"
            :loading="loopLoading"
            show-search
            placeholder="请选择回路"
            style="width: 360px"
            :options="
              loopList.map((l) => ({
                label: `${l.tagName} - ${l.description}`,
                value: l.loopId,
              }))
            "
            :filter-option="
              (input: string, option: any) => option.label.includes(input)
            "
          />
          <div v-if="selectedLoop" class="flex items-center gap-3">
            <span class="text-sm text-gray-500"> 当前状态： </span>
            <StatusBadge
              :status="selectedLoop.status"
              :is-active="selectedLoop.isActive"
            />
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- 7 槽位配置 -->
      <ClpmDataCanvas title="Tag 关联配置">
        <Spin :spinning="tagLoading">
          <div
            v-if="selectedLoopId"
            class="grid grid-cols-1 gap-4 md:grid-cols-2"
          >
            <div
              v-for="cfg in slotConfigs"
              :key="cfg.key"
              class="rounded border p-4"
              :class="cfg.required ? 'border-red-200' : 'border-gray-200'"
            >
              <div class="mb-2 flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <Tag :color="cfg.color">{{ cfg.label }}</Tag>
                  <span v-if="cfg.required" class="text-red-500">*</span>
                  <span class="text-xs text-gray-400">{{
                    cfg.description
                  }}</span>
                </div>
                <Button
                  v-if="slotState[cfg.key]"
                  type="link"
                  size="small"
                  danger
                  @click="clearSlot(cfg.key)"
                >
                  清除
                </Button>
              </div>
              <Select
                v-model:value="slotState[cfg.key]"
                show-search
                allow-clear
                placeholder="选择 Tag"
                style="width: 100%"
                :loading="tagSearchLoading"
                :options="
                  availableTags.map((t) => ({
                    label: `${t.tagName}${t.description ? ` (${t.description})` : ''}`,
                    value: t.tagId,
                  }))
                "
                :filter-option="false"
                @search="handleTagSearch"
              />
              <!-- 当前关联信息 -->
              <div v-if="tagData" class="mt-2 text-xs text-gray-400">
                <template v-for="t in tagData.tags" :key="t.role">
                  <div v-if="t.role.toLowerCase() === cfg.key">
                    <span v-if="t.associated">
                      已关联：{{ t.tagName }}
                      <span v-if="t.currentValue != null" class="ml-2">
                        当前值：{{ t.currentValue }}
                      </span>
                    </span>
                    <span v-else>未关联</span>
                  </div>
                </template>
              </div>
            </div>
          </div>
          <div v-else class="py-12 text-center text-gray-400">请先选择回路</div>
        </Spin>

        <div
          v-if="selectedLoopId"
          class="mt-4 flex justify-end gap-2 border-t pt-4"
        >
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="default"
            @click="handleAutoLink"
          >
            自动关联
          </Button>
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="primary"
            :loading="saving"
            @click="handleSave"
          >
            保存关联
          </Button>
        </div>
      </ClpmDataCanvas>
    </div>
  </Page>
</template>
