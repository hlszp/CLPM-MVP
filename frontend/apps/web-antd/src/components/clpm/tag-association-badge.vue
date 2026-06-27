<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';

import { computed, ref } from 'vue';

import { Modal, Tag } from 'ant-design-vue';

const SLOT_META: Array<{
  key: keyof LoopApi.LoopTagMapping;
  label: string;
  required: boolean;
}> = [
  { key: 'pv', label: 'PV', required: true },
  { key: 'sp', label: 'SP', required: true },
  { key: 'op', label: 'OP', required: true },
  { key: 'mode', label: 'MODE', required: true },
  { key: 'pid_p', label: 'PID_P', required: false },
  { key: 'pid_i', label: 'PID_I', required: false },
  { key: 'pid_d', label: 'PID_D', required: false },
];

defineOptions({ name: 'ClpmTagAssociationBadge' });

interface Props {
  /** 完整 mapping（含 tagName），与 status 二选一；同时提供时优先 mapping */
  mapping?: LoopApi.LoopTagMapping | null;
  /** 简化状态（仅 associated booleans），列表行使用 */
  status?: LoopApi.TagMappingStatus | null;
}

const props = defineProps<Props>();

const detailOpen = ref(false);

/** 统一的 slot 视图模型 */
const slots = computed(() =>
  SLOT_META.map((slot) => {
    const mappingItem = props.mapping?.[slot.key];
    const statusBool = props.status?.[slot.key];
    const associated = mappingItem?.associated === true || statusBool === true;
    return {
      ...slot,
      associated,
      tagName: mappingItem?.tagName ?? null,
    };
  }),
);

const associatedCount = computed(() => slots.value.filter((s) => s.associated).length);
const requiredMissing = computed(() => slots.value.filter((s) => s.required && !s.associated).length);

const hasData = computed(() => !!props.mapping || !!props.status);

const statusColor = computed<'default' | 'error' | 'success' | 'warning'>(() => {
  if (!hasData.value) return 'default';
  if (requiredMissing.value > 0) return 'error';
  if (associatedCount.value < slots.value.length) return 'warning';
  return 'success';
});

const statusText = computed(() => {
  if (!hasData.value) return 'Tag 关联 —';
  if (requiredMissing.value > 0) return `${associatedCount.value}/7，缺 ${requiredMissing.value} 个必填`;
  if (associatedCount.value < slots.value.length) return `${associatedCount.value}/7 部分关联`;
  return '7/7 已关联';
});
</script>

<template>
  <span class="clpm-tag-association">
    <Tag :color="statusColor" class="m-0 cursor-pointer" @click="detailOpen = true">
      {{ statusText }}
    </Tag>
    <button class="clpm-tag-association__link" type="button" @click="detailOpen = true">
      查看
    </button>

    <Modal v-model:open="detailOpen" title="Tag 关联详情" :footer="null" width="720px">
      <div class="clpm-tag-association__grid">
        <div
          v-for="slot in slots"
          :key="slot.key"
          class="clpm-tag-association__slot"
          :class="{
            'is-associated': slot.associated,
            'is-required-missing': slot.required && !slot.associated,
          }"
        >
          <div class="clpm-tag-association__slot-head">
            <span class="clpm-tag-association__slot-label">{{ slot.label }}</span>
            <Tag v-if="slot.required" color="blue" class="m-0">必填</Tag>
            <Tag v-else color="default" class="m-0">可选</Tag>
          </div>
          <div class="clpm-tag-association__slot-value">
            <template v-if="slot.tagName">{{ slot.tagName }}</template>
            <template v-else-if="slot.associated">已关联</template>
            <template v-else>未关联</template>
          </div>
        </div>
      </div>
    </Modal>
  </span>
</template>

<style scoped>
.clpm-tag-association {
  align-items: center;
  display: inline-flex;
  gap: 6px;
}

.clpm-tag-association__link {
  background: transparent;
  border: 0;
  color: hsl(var(--primary));
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.clpm-tag-association__grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.clpm-tag-association__slot {
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  padding: 10px;
}

.clpm-tag-association__slot.is-associated {
  background: hsl(var(--success) / 8%);
  border-color: hsl(var(--success) / 35%);
}

.clpm-tag-association__slot.is-required-missing {
  background: hsl(var(--destructive) / 8%);
  border-color: hsl(var(--destructive) / 35%);
}

.clpm-tag-association__slot-head {
  align-items: center;
  display: flex;
  gap: 6px;
  justify-content: space-between;
}

.clpm-tag-association__slot-label,
.clpm-tag-association__slot-value {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
}

.clpm-tag-association__slot-label {
  color: hsl(var(--foreground));
  font-weight: 700;
}

.clpm-tag-association__slot-value {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  margin-top: 8px;
}
</style>
