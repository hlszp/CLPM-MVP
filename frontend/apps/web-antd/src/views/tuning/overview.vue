<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * 整定总览（2026-10-04 D3 转型；2026-10-05 1008 纯列表化改版）
 *
 * - 纯列表页：全回路总览表（客户端分页 50/页 + 本地搜索），无左脊柱
 *   （装置树/回路清单/整定建议列表已删，用户裁决 1008）
 * - 数据源：runs/latest（共享缓存，自带 fitnessLevel/tuneLevel/fitnessTags/
 *   unitName/处置建议 actionSuggest）+ getLoopsRuntimeParamsApi 批量 PID
 * - PID 三列分立（P/I/D），实时值经全局 WS 推送更新
 * - 「调参优化」以右侧抽屉推入参数整定四步流程（辨识→矩阵→仿真→确认，
 *   复用 use-tuning-workbench + 四 section，L0/L1 门禁保留在打开前）
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  message,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { getLoopMonitorListApi, getLoopsRuntimeParamsApi } from '#/api/loop';
import ClpmFitnessBadge from '#/components/clpm/fitness-badge.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { useLatestOverviewCache } from '#/composables/use-latest-overview-cache';
import { bindLoopInterest, useLoopRealtime } from '#/composables/use-loop-realtime';
import { fitnessTagToLabel } from '#/constants/clpm-ui';

import ConfirmSection from './components/confirm-section.vue';
import IdentifySection from './components/identify-section.vue';
import MatrixSection from './components/matrix-section.vue';
import SimulateSection from './components/simulate-section.vue';
import { useTuningWorkbench } from './composables/use-tuning-workbench';
import {
  fmtNum2,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  scoreGrade,
} from './constants';

defineOptions({ name: 'TuningOverview' });

const userStore = useUserStore();

/** 整定操作角色（D1：回路工作台剖面四角色；SPONSOR 只读） */
const TUNING_OPERATE_ROLES = [
  'ADMIN',
  'EXPERT',
  'IC_ENGINEER',
  'PE_ENGINEER',
] as const;
const canOperateTuning = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) =>
    (TUNING_OPERATE_ROLES as readonly string[]).includes(r),
  );
});

const tuningTagsToText = (tags: string[]) =>
  tags.map((t) => fitnessTagToLabel(t)).join('、');

// ===== 总览数据（latest 共享缓存 + 批量 PID） =====
interface OverviewRow {
  loopId: string;
  tagName: string;
  description: null | string;
  importanceLevel: null | number;
  latestScore: null | number;
  primaryCategoryLabel: null | string;
  /** 待处理处置建议（PENDING/ACCEPTED 最新一条 + 计数） */
  actionSuggest: null | string;
  actionSuggestCount: number;
  fitnessLevel: null | string;
  fitnessTags: string[];
  /** 实时值容器（P/I/D 经 WS 推送更新；结构对齐 useLoopRealtime） */
  currentValues: {
    mode: null | number;
    modeLabel: null | string;
    op: null | number;
    pidD: null | number;
    pidI: null | number;
    pidP: null | number;
    pv: null | number;
    pvQuality: null | string;
    readAt: null | string;
    sp: null | number;
  };
}

const overviewLoading = ref(false);
const overviewRows = ref<OverviewRow[]>([]);

const latestCache = useLatestOverviewCache();

async function loadOverview(): Promise<void> {
  overviewLoading.value = true;
  try {
    await latestCache.load(undefined);
    const items = latestCache.items.value;
    overviewRows.value = items.map((l) => ({
      loopId: l.loopId,
      tagName: l.loopTagName,
      // 名称三重兜底（1008：绝不留空）——台账描述 → 所属单元 → 位号
      description: l.loopDescription ?? l.unitName ?? l.loopTagName ?? null,
      importanceLevel: l.importanceLevel ?? null,
      latestScore: l.latestScore ?? null,
      primaryCategoryLabel: l.primaryCategoryLabel ?? null,
      actionSuggest: l.actionSuggest ?? null,
      actionSuggestCount: l.actionSuggestCount ?? 0,
      fitnessLevel: l.fitnessLevel ?? null,
      fitnessTags: Array.isArray(l.fitnessTags) ? l.fitnessTags : [],
      currentValues: {
        mode: null,
        modeLabel: null,
        op: null,
        pidD: null,
        pidI: null,
        pidP: null,
        pv: null,
        pvQuality: null,
        readAt: null,
        sp: null,
      },
    }));
    // P/I/D 初值：一次批量拉全部回路运行参数（0934 优化口径保持）
    try {
      const rpMap = await getLoopsRuntimeParamsApi(undefined);
      for (const row of overviewRows.value) {
        const rp = rpMap[row.loopId];
        if (!rp) continue;
        row.currentValues.pidP = rp.pidP ?? null;
        row.currentValues.pidI = rp.pidI ?? null;
        row.currentValues.pidD = rp.pidD ?? null;
      }
    } catch {
      // 批量失败不阻断总览
    }
  } catch {
    overviewRows.value = [];
  } finally {
    overviewLoading.value = false;
  }
}

// ===== 搜索 + 客户端分页（1209 行本地过滤零压力） =====
const keyword = ref('');
const filteredRows = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return overviewRows.value;
  return overviewRows.value.filter(
    (r) =>
      r.tagName.toLowerCase().includes(kw) ||
      (r.description ?? '').toLowerCase().includes(kw),
  );
});

const overviewColumns: TableColumnsType = [
  { key: 'tagName', title: '回路编号', width: 150 },
  { key: 'description', title: '回路名称', width: 150, ellipsis: true },
  { key: 'importanceLevel', title: '等级', width: 54, align: 'center' },
  { key: 'latestScore', title: '性能评分', width: 76, align: 'center' },
  { key: 'scoreGrade', title: '性能等级', width: 72, align: 'center' },
  { key: 'fitness', title: '可整定性', width: 90, align: 'center' },
  { key: 'diagnosis', title: '诊断结论', width: 120, ellipsis: true },
  { key: 'suggestion', title: '处置建议', width: 150, ellipsis: true },
  { key: 'pidP', title: 'P', width: 68, align: 'center' },
  { key: 'pidI', title: 'I', width: 68, align: 'center' },
  { key: 'pidD', title: 'D', width: 68, align: 'center' },
  { key: 'action', title: '操作', width: 88 },
];

// ===== 实时更新（P/I/D 由全局 WS 推送，初值批量拉取） =====
const { applyMessage, onMessage, start, stop } = useLoopRealtime();

bindLoopInterest(() => overviewRows.value.map((r) => r.tagName));

onMessage((msg) => {
  applyMessage(msg, overviewRows.value as never[]);
});

// ===== 整定抽屉（1008：调参优化改右侧抽屉，四步流程内嵌） =====
const tuneDrawerOpen = ref(false);
const tuneDrawerLoop = ref<null | OverviewRow>(null);
const ctx = useTuningWorkbench();

const drawerAnchors = [
  { href: '#tune-dr-identify', label: '① 辨识' },
  { href: '#tune-dr-matrix', label: '② 矩阵' },
  { href: '#tune-dr-simulate', label: '③ 仿真' },
  { href: '#tune-dr-confirm', label: '④ 确认' },
];

function scrollDrawerTo(href: string) {
  document.querySelector(href)?.scrollIntoView({
    behavior: 'smooth',
    block: 'start',
  });
}

/** 打开整定抽屉（先过 L0/L1 门禁，L2 警告放行） */
async function openTuneDrawer(record: OverviewRow) {
  if (!canOperateTuning.value) {
    message.warning('当前角色无整定操作权限');
    return;
  }
  const loopId = record.loopId;
  const tag = record.tagName || loopId;
  let level: null | string;
  let tags: string[];
  try {
    const res = await getLoopMonitorListApi({ loopId, page: 1, pageSize: 1 });
    const item = res.items?.[0];
    level = (item?.tuneLevel ?? item?.fitnessLevel) ?? null;
    tags = Array.isArray(item?.fitnessTags)
      ? (item.fitnessTags as string[])
      : [];
  } catch {
    level = null;
    tags = [];
  }
  if (level === 'L0' || level === 'L1') {
    const reason = tags.length > 0 ? tuningTagsToText(tags) : '适用性不足';
    message.error({
      content: `回路「${tag}」可整定等级不足（${level}），不建议做整定：${reason}。先消除异常来源后再操作。`,
      duration: 6,
    });
    return;
  }
  if (level === 'L2') {
    const reason = tags.length > 0 ? tuningTagsToText(tags) : '控制条件异常';
    message.warning({
      content: `【调参优化】L2 条件异常：${reason}。当前控制状态可能影响整定结论，建议先修正再做整定。`,
      duration: 5,
    });
  } else if (level === 'L3' || level === 'L4') {
    message.success(`当前可整定等级 = ${level}，可正常整定。`);
  } else {
    message.info('尚未评定适用性等级。');
  }
  tuneDrawerLoop.value = record;
  ctx.selectLoop(loopId);
  tuneDrawerOpen.value = true;
}

function onTuneDrawerClose() {
  ctx.clearLoop();
  tuneDrawerLoop.value = null;
}

onMounted(() => {
  start();
  void loadOverview();
});

onBeforeUnmount(() => {
  stop();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="全回路可整定性总览；点击行或「调参优化」在右侧抽屉进行参数整定（L0/L1 门禁）"
      title="整定总览"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          :loading="overviewLoading"
          @click="loadOverview()"
        />
      </template>
    </ClpmPageToolbar>

    <Card size="small">
      <template #title>
        <span class="text-[13px]">回路总览</span>
        <span class="ml-2 text-xs font-normal text-neutral-400">
          {{ filteredRows.length }} 个回路
        </span>
      </template>
      <template #extra>
        <Input
          v-model:value="keyword"
          allow-clear
          placeholder="搜索位号/名称..."
          size="small"
          style="width: 200px"
        />
      </template>
      <Table
        :columns="overviewColumns"
        :custom-cell="() => ({ style: { cursor: 'pointer' } })"
        :custom-row="
          (record: any) => ({
            onClick: () => openTuneDrawer(record as OverviewRow),
          })
        "
        :data-source="filteredRows"
        :loading="overviewLoading"
        :pagination="{
          pageSize: 50,
          pageSizeOptions: ['20', '50', '100'],
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
          size: 'small',
          showLessItems: true,
        }"
        row-key="loopId"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'tagName'">
            <span>{{ record.tagName }}</span>
          </template>
          <template v-else-if="column.key === 'description'">
            <span>{{ record.description || record.tagName }}</span>
          </template>
          <template v-else-if="column.key === 'importanceLevel'">
            <span
              v-if="record.importanceLevel"
              :style="{
                color: IMPORTANCE_LEVEL_COLOR[record.importanceLevel],
              }"
            >
              {{ IMPORTANCE_LEVEL_TEXT[record.importanceLevel] ?? '—' }}
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.key === 'latestScore'">
            <span
              v-if="record.latestScore != null"
              class="clpm-num"
              :style="{ color: scoreGrade(record.latestScore)?.color }"
            >
              {{ record.latestScore.toFixed(1) }}
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.key === 'scoreGrade'">
            <span
              v-if="scoreGrade(record.latestScore)"
              :style="{ color: scoreGrade(record.latestScore)?.color }"
            >
              {{ scoreGrade(record.latestScore)?.label }}
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.key === 'fitness'">
            <ClpmFitnessBadge
              :level="record.fitnessLevel"
              :tags="record.fitnessTags"
              size="sm"
            />
          </template>
          <template v-else-if="column.key === 'diagnosis'">
            <span v-if="record.primaryCategoryLabel">
              {{ record.primaryCategoryLabel }}
            </span>
            <span v-else class="text-neutral-400">未诊断</span>
          </template>
          <template v-else-if="column.key === 'suggestion'">
            <Tooltip
              v-if="record.actionSuggest"
              :title="record.actionSuggest"
              placement="topLeft"
            >
              <span>
                {{ record.actionSuggest }}
                <Tag
                  v-if="record.actionSuggestCount > 1"
                  color="orange"
                  class="mr-0"
                >
                  {{ record.actionSuggestCount }}
                </Tag>
              </span>
            </Tooltip>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.key === 'pidP'">
            <span class="clpm-num">{{ fmtNum2(record.currentValues.pidP) }}</span>
          </template>
          <template v-else-if="column.key === 'pidI'">
            <span class="clpm-num">{{ fmtNum2(record.currentValues.pidI) }}</span>
          </template>
          <template v-else-if="column.key === 'pidD'">
            <span class="clpm-num">{{ fmtNum2(record.currentValues.pidD) }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Tooltip
              v-if="canOperateTuning"
              title="打开前校验适用性（L0/L1 阻止，L2 提示）"
              placement="top"
            >
              <Button
                type="link"
                size="small"
                class="p-0"
                @click.stop="openTuneDrawer(record as OverviewRow)"
              >
                调参优化
              </Button>
            </Tooltip>
            <span v-else class="text-neutral-400">—</span>
          </template>
        </template>
        <template #emptyText>
          <Empty :image="Empty.PRESENTED_IMAGE_SIMPLE" description="暂无回路" />
        </template>
      </Table>
    </Card>

    <!-- 参数整定抽屉：四步流程内嵌（辨识→矩阵→仿真→确认） -->
    <Drawer
      v-model:open="tuneDrawerOpen"
      :title="`参数整定 — ${tuneDrawerLoop?.tagName ?? ''}`"
      destroy-on-close
      placement="right"
      width="88%"
      @close="onTuneDrawerClose"
    >
      <div class="tune-dr-anchor">
        <a
          v-for="a in drawerAnchors"
          :key="a.href"
          @click.prevent="scrollDrawerTo(a.href)"
        >
          {{ a.label }}
        </a>
      </div>
      <div id="tune-dr-identify"><IdentifySection :ctx="ctx" /></div>
      <div id="tune-dr-matrix"><MatrixSection :ctx="ctx" /></div>
      <div id="tune-dr-simulate"><SimulateSection :ctx="ctx" /></div>
      <div id="tune-dr-confirm"><ConfirmSection :ctx="ctx" /></div>
    </Drawer>
  </Page>
</template>

<style scoped>
.tune-dr-anchor {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  gap: 16px;
  padding: 6px 12px;
  margin-bottom: 8px;
  background: hsl(var(--background));
  border-bottom: 1px solid hsl(var(--border));
}

.tune-dr-anchor a {
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
}

.tune-dr-anchor a:hover {
  text-decoration: underline;
}

.tune-dr-anchor + div {
  scroll-margin-top: 48px;
}
</style>
