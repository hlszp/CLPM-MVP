<script lang="ts" setup>
/**
 * 关注队列·回路组详情抽屉（列表页标杆：详情一律右侧抽屉）
 *
 * 替代原行内展开子项：点击主表行打开本抽屉，展示该回路组
 * 的全部关注子项及就地操作（详情/工作台/确认/处置/误报）。
 *
 * 清爽视觉约束：无底色单元格、无加粗字体，层级靠色阶与徽章表达。
 */
import type { MenuProps } from 'ant-design-vue';

import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';

import {
  Button,
  Divider,
  Drawer,
  Dropdown,
  Space,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  fitnessTagToLabel,
  PRIORITY_LABEL,
  PRIORITY_TO_STATUS,
  statusTokenToAntdColor,
} from '#/constants/clpm-ui';
import { formatTime, normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'AttentionGroupDrawer' });

const props = defineProps<{
  group: MonitorApi.AttentionGroup | null;
}>();

const emit = defineEmits<{
  (e: 'childAction', item: MonitorApi.AttentionItem, action: MonitorApi.AttentionAction): void;
  (e: 'childDetail', item: MonitorApi.AttentionItem): void;
  (e: 'resolve', item: MonitorApi.AttentionItem): void;
}>();

const open = defineModel<boolean>('open', { default: false });

const router = useRouter();

const SOURCE_LABEL: Record<MonitorApi.AttentionSource, string> = {
  ALERT: '活跃预警',
  DEGRADATION: '评分恶化',
  DATA_QUALITY: '数据质量',
  FITNESS_ABNORMAL: '适用性异常',
  HANDLING: '处置工单',
};

const SOURCE_COLOR: Record<MonitorApi.AttentionSource, string> = {
  ALERT: 'error',
  DEGRADATION: 'warning',
  DATA_QUALITY: 'default',
  FITNESS_ABNORMAL: 'purple',
  HANDLING: 'processing',
};

const STATUS_LABEL: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: '待处理',
  ACKNOWLEDGED: '已确认',
  SUPPRESSED: '已抑制',
  IN_PROGRESS: '处理中',
};

const STATUS_COLOR: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: 'error',
  ACKNOWLEDGED: 'warning',
  SUPPRESSED: 'default',
  IN_PROGRESS: 'processing',
};

const priorityColor = (priority: string) =>
  statusTokenToAntdColor(PRIORITY_TO_STATUS[priority] ?? 'neutral');

function formatRelative(ts: null | string | undefined): string {
  if (!ts) return '-';
  try {
    return dayjs(normalizeUtcTimestamp(ts)).fromNow();
  } catch {
    return formatTime(ts);
  }
}

function fitnessTip(
  level: null | string | undefined,
  tags: null | string[] | undefined,
): string {
  const tagText =
    tags && tags.length > 0 ? tags.map((t) => fitnessTagToLabel(t)).join('、') : '适用性异常';
  return `适用性异常（${level ?? 'NA'}）：${tagText}`;
}

/** 组内子项排序已在服务端完成，这里直接展示 */
const children = computed(() => props.group?.children ?? []);

function executeNav(action: MonitorApi.AttentionAction) {
  if (!action.enabled) {
    return;
  }
  if (action.target) {
    router.push({
      path: action.target.route,
      query: { ...action.target.query, from: '/monitor/attention' },
    });
  }
}

function handleAction(item: MonitorApi.AttentionItem, action: MonitorApi.AttentionAction) {
  if (action.type === 'RESOLVE') {
    emit('resolve', item);
    return;
  }
  if (action.type === 'VIEW_DETAIL') {
    emit('childDetail', item);
    return;
  }
  if (action.target) {
    executeNav(action);
    return;
  }
  emit('childAction', item, action);
}

/** 子项更多菜单（排除已在行内呈现的动作） */
function buildMenu(item: MonitorApi.AttentionItem): MenuProps {
  const items: MenuProps['items'] = item.actions
    .filter(
      (a) =>
        a.type !== 'OPEN_WORKBENCH' &&
        a.type !== 'VIEW_DETAIL' &&
        a.type !== 'BACK_TO_OVERVIEW',
    )
    .map((a) => ({
      key: a.type,
      label: a.label + (a.disabledReason ? `（${a.disabledReason}）` : ''),
      disabled: !a.enabled,
      onClick: () => handleAction(item, a),
    }));
  return { items };
}
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="null"
    width="620"
    :destroy-on-close="true"
    class="attention-group-drawer"
  >
    <template v-if="group">
      <!-- 头部：优先级徽章 + 回路号 + 位置 + 状态 -->
      <div class="mb-1 flex items-center gap-2">
        <Tag
          :color="priorityColor(group.priority)"
          class="!mr-0"
          style="font-size: 11px"
        >
          {{ group.priorityLabel }}
        </Tag>
        <span class="font-mono text-base text-gray-800">{{ group.tagName }}</span>
        <Tag
          :color="STATUS_COLOR[group.status as keyof typeof STATUS_COLOR]"
          class="!mr-0"
          style="font-size: 11px"
        >
          {{ STATUS_LABEL[group.status as keyof typeof STATUS_LABEL] }}
        </Tag>
        <span v-if="group.isOverdue" class="text-xs text-red-500">已超期</span>
      </div>
      <div class="mb-3 flex items-center gap-3 text-xs text-gray-400">
        <span>{{ group.unitName || '未知装置·单元' }}</span>
        <span>{{ group.itemCount }} 个关注项</span>
        <Tooltip v-if="group.updatedAt" :title="formatTime(group.updatedAt)">
          <span>更新于 {{ formatRelative(group.updatedAt) }}</span>
        </Tooltip>
      </div>

      <!-- 来源 chips -->
      <div class="mb-3 flex flex-wrap items-center gap-1.5">
        <Tooltip
          v-for="s in group.sources"
          :key="s"
          :title="
            s === 'FITNESS_ABNORMAL'
              ? fitnessTip(group.fitnessLevel, group.fitnessTags)
              : undefined
          "
        >
          <Tag
            :color="SOURCE_COLOR[s as keyof typeof SOURCE_COLOR]"
            class="!mr-0"
            style="font-size: 11px"
          >
            {{ SOURCE_LABEL[s as keyof typeof SOURCE_LABEL] }}
          </Tag>
        </Tooltip>
      </div>

      <Divider class="!my-2" />

      <!-- 子项列表：浅色分隔线，无底色卡片 -->
      <div class="divide-y divide-gray-100">
        <div v-for="child in children" :key="child.attentionId" class="py-3">
          <div class="flex items-center gap-2">
            <Tooltip
              v-if="child.source === 'FITNESS_ABNORMAL'"
              :title="fitnessTip(child.fitnessLevel, child.fitnessTags)"
            >
              <Tag
                :color="SOURCE_COLOR[child.source as keyof typeof SOURCE_COLOR]"
                class="!mr-0"
                style="font-size: 11px"
              >
                {{ SOURCE_LABEL[child.source as keyof typeof SOURCE_LABEL] }}
              </Tag>
            </Tooltip>
            <Tag
              v-else
              :color="SOURCE_COLOR[child.source as keyof typeof SOURCE_COLOR]"
              class="!mr-0"
              style="font-size: 11px"
            >
              {{ SOURCE_LABEL[child.source as keyof typeof SOURCE_LABEL] }}
            </Tag>
            <Tag
              :color="priorityColor(child.priority)"
              class="!mr-0"
              style="font-size: 11px"
            >
              {{ PRIORITY_LABEL[child.priority as keyof typeof PRIORITY_LABEL] }}
            </Tag>
            <Tag
              :color="STATUS_COLOR[child.status as keyof typeof STATUS_COLOR]"
              class="!mr-0"
              style="font-size: 11px"
            >
              {{ STATUS_LABEL[child.status as keyof typeof STATUS_LABEL] }}
            </Tag>
            <Tooltip :title="formatTime(child.updatedAt || child.occurredAt)">
              <span class="ml-auto text-xs text-gray-400 whitespace-nowrap tabular-nums">
                {{ formatRelative(child.updatedAt || child.occurredAt) }}
              </span>
            </Tooltip>
          </div>

          <div class="mt-1.5 text-sm text-gray-700">{{ child.summary }}</div>

          <div v-if="child.rankReasons?.length" class="mt-1.5 flex flex-wrap gap-1">
            <Tag
              v-for="(r, i) in child.rankReasons"
              :key="i"
              color="default"
              class="!mr-0"
              style="font-size: 11px"
            >
              {{ r }}
            </Tag>
          </div>

          <div class="mt-2 flex items-center gap-1">
            <Button type="link" size="small" class="!px-1" @click="emit('childDetail', child)">
              详情
            </Button>
            <Button
              type="link"
              size="small"
              class="!px-1"
              :disabled="!child.primaryAction?.enabled"
              @click="executeNav(child.primaryAction)"
            >
              工作台
            </Button>
            <Dropdown
              v-if="child.actions?.length > 2"
              :menu="buildMenu(child)"
              trigger="click"
            >
              <Button type="link" size="small" class="!px-1">
                <IconifyIcon icon="lucide:more-horizontal" :size="16" />
              </Button>
            </Dropdown>
          </div>
        </div>
      </div>

      <div v-if="children.length === 0" class="py-8 text-center text-sm text-gray-400">
        该回路组暂无关注子项
      </div>
    </template>

    <!-- 底部固定：主动作 + 安全边界 -->
    <template #footer>
      <div class="flex items-center justify-between gap-3">
        <span class="inline-flex items-center gap-1 text-xs text-gray-400">
          <IconifyIcon icon="lucide:shield-alert" :size="13" />
          平台只输出建议与证据，参数由授权人员人工实施并留痕
        </span>
        <Space>
          <Button @click="open = false">关闭</Button>
          <Button
            v-if="group?.primaryAction"
            type="primary"
            :disabled="!group.primaryAction.enabled"
            @click="executeNav(group.primaryAction)"
          >
            {{ group.primaryAction.label || '进入回路工作台' }}
          </Button>
        </Space>
      </div>
    </template>
  </Drawer>
</template>
