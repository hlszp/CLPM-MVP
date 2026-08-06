<script lang="ts" setup>
/**
 * 回路工作台 · 处置时间线 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路处置全生命周期时间轴 —— 一眼看清"这个回路经历过哪些处置动作"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则。
 *
 * 三区：
 * ① 跳转入口：去诊断详情（完整 Tracker）/ 去 Action Tracker
 * ② 摘要区：事件总数 + 当前跟踪状态 + 最近事件时间
 * ③ 主图：ClpmDispositionTimeline 组件（已聚合诊断/整定/实施/验证/MOC 全事件）
 *
 * 数据来源：本 Tab 自行加载 getLoopTimelineApi(loopId)（后端已聚合单回路全事件）
 * 切到 Tab 才请求，概览不需要。后端零改动。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Button, Descriptions, DescriptionsItem, Empty, Spin, Tag } from 'ant-design-vue';

import { getLoopTimelineApi } from '#/api/diagnosis';
import {
  ClpmDataCanvas,
  ClpmDispositionTimeline,
} from '#/components/clpm';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchTimelineTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();

// ===== 数据状态 =====
const loading = ref(false);
const timelineData = ref<DiagnosisApi.TimelineData | null>(null);

/** 当前跟踪状态 → 中文标签 */
const ACTION_STATUS_META: Record<string, { color: string; label: string }> = {
  CLOSED: { color: 'green', label: '已闭环' },
  IGNORED: { color: 'default', label: '已忽略' },
  IMPLEMENTED: { color: 'orange', label: '已实施' },
  PENDING: { color: 'default', label: '待处理' },
  REOPENED: { color: 'red', label: '已重开' },
  VERIFYING: { color: 'processing', label: '验证中' },
};

/** 事件总数 */
const eventCount = computed(() => timelineData.value?.events?.length ?? 0);

/** 最近事件时间 */
const lastEventTime = computed(() => {
  const events = timelineData.value?.events ?? [];
  if (events.length === 0) return null;
  const sorted = [...events].toSorted((a, b) =>
    b.timestamp.localeCompare(a.timestamp),
  );
  return sorted[0]?.timestamp ?? null;
});

/** 当前状态信息 */
const currentStatusInfo = computed(() => {
  const s = timelineData.value?.currentStatus;
  if (!s) return null;
  return ACTION_STATUS_META[s] || { color: 'default', label: s };
});

// ===== 数据加载 =====
async function loadData() {
  loading.value = true;
  timelineData.value = null;
  try {
    timelineData.value = await getLoopTimelineApi(props.loopId).catch(
      () => null,
    );
  } finally {
    loading.value = false;
  }
}

// ===== 跳转入口 =====
function goDiagnosisDetail() {
  router.push(`/diagnosis/detail/${props.loopId}`);
}

function goTracker() {
  router.push('/diagnosis/tracker');
}

// ===== 生命周期 =====
onMounted(() => {
  loadData();
});

watch(
  () => props.loopId,
  () => {
    loadData();
  },
);
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">处置跟踪：</span>
      <Button type="primary" size="small" @click="goDiagnosisDetail">
        查看诊断详情
      </Button>
      <Button size="small" @click="goTracker">Action Tracker</Button>
    </div>

    <!-- ② 摘要区 -->
    <ClpmDataCanvas
      title="处置摘要"
      :loading="loading"
      :empty="!loading && !timelineData"
      empty-text="暂无处置记录"
      empty-reason="可能原因：该回路尚未触发诊断异常，或无 Tracker 记录。"
      empty-action-text="去诊断中心"
      @empty-action="router.push('/diagnosis/overview')"
    >
      <Spin :spinning="loading">
        <Descriptions
          v-if="timelineData"
          :column="{ xs: 1, sm: 2, md: 4 }"
          size="small"
          bordered
        >
          <DescriptionsItem label="回路位号">
            {{ timelineData.tagName || props.loopId }}
          </DescriptionsItem>
          <DescriptionsItem label="事件总数">
            <span class="font-semibold text-blue-600">{{ eventCount }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="当前状态">
            <Tag v-if="currentStatusInfo" :color="currentStatusInfo.color">
              {{ currentStatusInfo.label }}
            </Tag>
            <span v-else class="text-xs text-gray-400">无跟踪</span>
          </DescriptionsItem>
          <DescriptionsItem label="最近事件">
            {{ lastEventTime ? formatTime(lastEventTime) : '—' }}
          </DescriptionsItem>
        </Descriptions>
      </Spin>
    </ClpmDataCanvas>

    <!-- ③ 主图：处置时间线 -->
    <ClpmDataCanvas
      title="处置时间线"
      description="诊断发现 → 认领 → 整定 → 实施 → 验证全生命周期事件。"
      :loading="loading"
      :empty="!loading && eventCount === 0"
      empty-text="暂无处置事件"
    >
      <ClpmDispositionTimeline
        v-if="eventCount > 0"
        :events="timelineData!.events as any"
        :current-status="timelineData!.currentStatus"
        :pending-verification-at="timelineData!.pendingVerificationAt"
      >
        <template #verify-now>
          <Button
            type="link"
            size="small"
            @click="goDiagnosisDetail"
          >
            立即验证（去诊断详情）
          </Button>
        </template>
      </ClpmDispositionTimeline>
      <Empty
        v-else-if="!loading"
        description="暂无处置事件"
        class="py-8"
      />
    </ClpmDataCanvas>
  </div>
</template>
