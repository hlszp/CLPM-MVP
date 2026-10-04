<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * 整定总览（整定模块主入口，09 设计方案 §6.2；2026-10-04 工作台规整 D3 转型）
 *
 * - 左脊柱：装置树 + 回路清单 + 整定建议列表（TUNING 类在途工单，待排程优先）
 * - 右主区：该节点下所有回路的总览表格（回路编号/名称/等级/性能评分/性能
 *   等级/可整定性/诊断结论/处置建议摘要/P·I·D 实时参数）
 * - 单回路整定流程（辨识→矩阵→仿真→确认）已让位回路工作台整定剖面：
 *   行点击/清单点击/「调参优化」→ /loop/workbench360?loopId=&section=tuning
 *   （入口门禁保留：L0/L1 阻止、L2 警告提示）
 *
 * P/I/D 初值批量拉取（getLoopsRuntimeParamsApi），随后由全局实时 WS 推送更新。
 */
import type { HandlingApi } from '#/api/handling';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Card,
  Empty,
  Input,
  message,
  Spin,
  Table,
  Tag,
  Tooltip,
  Tree,
} from 'ant-design-vue';

import { getDiagnosisRunsLatestApi } from '#/api/diagnosis';
import { getHandlingOrdersApi } from '#/api/handling';
import {
  getLoopListApi,
  getLoopMonitorListApi,
  getLoopsRuntimeParamsApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmFitnessBadge from '#/components/clpm/fitness-badge.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { bindLoopInterest, useLoopRealtime } from '#/composables/use-loop-realtime';
import { fitnessTagToLabel } from '#/constants/clpm-ui';

import {
  fmtNum2,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  scoreGrade,
} from './constants';

defineOptions({ name: 'TuningOverview' });

const route = useRoute();
const router = useRouter();

const userStore = useUserStore();

/** 整定操作角色（2026-10-04 D1：对齐回路工作台剖面四角色；SPONSOR 只读） */
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

// P2 IA优化：fitness tag 中文映射收敛单源（clpm-ui.ts FITNESS_TAG_LABEL）
const tuningTagsToText = (tags: string[]) =>
  tags.map((t) => fitnessTagToLabel(t)).join('、');

/** 「调参优化」/行点击/清单点击统一入口（2026-10-04 D3：跳回路工作台整定剖面）
 *  —— 先查可整定档（三性 R5：tuneLevel 回退综合档），L0/L1 阻止并弹 error；
 *     L2 弹 warning Toast；L3/L4/未评定 正常进入。
 */
async function gotoWorkbenchTuning(loopId: string, tagName?: string) {
  if (!canOperateTuning.value) {
    message.warning('当前角色无整定操作权限（可在回路工作台查看回路状态）');
    return;
  }
  const tag = tagName || loopId;
  let level: null | string;
  let tags: string[];
  try {
    const res = await getLoopMonitorListApi({ loopId, page: 1, pageSize: 1 });
    const item = res.items?.[0];
    level = (item?.tuneLevel ?? item?.fitnessLevel) ?? null;
    tags = Array.isArray(item?.fitnessTags) ? (item.fitnessTags as string[]) : [];
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
  // Toast 提示（G3 要求）
  if (level === 'L2') {
    const reason = tags.length > 0 ? tuningTagsToText(tags) : '控制条件异常';
    message.warning({
      content: `【调参优化】L2 条件异常：${reason}。当前控制状态可能影响整定结论，建议先修正再做整定。`,
      duration: 5,
    });
  } else if (level === 'L3' || level === 'L4') {
    message.success(`【调参优化】当前可整定等级 = ${level}，可正常整定。`);
  } else {
    message.info(`【调参优化】尚未评定适用性等级。`);
  }
  router.push({
    path: '/loop/workbench360',
    query: { loopId, section: 'tuning' },
  });
}

// ===== 左脊柱：装置树 =====
/** ant Tree 节点约定为 {key, title}（TreeSelect 才是 {value, label}） */
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

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置节点选中：重拉回路清单/建议/总览 */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  reloadForNode();
}

// ===== 左脊柱：回路清单（选中装置节点下的回路，单选进入整定） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
const loopKeyword = ref('');

const filteredLoops = computed(() => {
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return loopItems.value;
  return loopItems.value.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

async function loadLoops(): Promise<void> {
  loopLoading.value = true;
  try {
    // 0929 诚实化修复：此前只拉前 100 条且无截断提示，改全量循环分页
    const all: LoopApi.LoopListItem[] = [];
    let page = 1;
    let total = 0;
    do {
      const res = await getLoopListApi({
        page,
        pageSize: 100, // 后端 /loops pageSize 上限 le=100
        plantNodeId: selectedPlantNodeId.value,
      });
      all.push(...(res.items ?? []));
      total = res.total ?? 0;
      page += 1;
    } while ((page - 1) * 100 < total);
    loopItems.value = all;
  } catch {
    loopItems.value = [];
  } finally {
    loopLoading.value = false;
  }
}

// ===== 左脊柱：整定建议列表（TUNING 类在途处置工单，待排程优先） =====
const openItems = ref<HandlingApi.OrderItem[]>([]);
const openLoading = ref(false);

/** 状态排序权重：待排程 → 重开（验证失败需返工）→ 执行中 */
const SUGG_STATUS_ORDER: Record<string, number> = {
  PENDING: 0,
  REOPENED: 1,
  EXECUTING: 2,
};

const tuningSuggestions = computed(() =>
  openItems.value
    .filter((i) => i.actionType === 'TUNING')
    .toSorted(
      (a, b) =>
        (SUGG_STATUS_ORDER[a.status] ?? 9) - (SUGG_STATUS_ORDER[b.status] ?? 9) ||
        String(b.updatedAt ?? '').localeCompare(String(a.updatedAt ?? '')),
    ),
);

async function loadOpenItems(): Promise<void> {
  openLoading.value = true;
  try {
    // 工单口径状态映射（v1.x PENDING,HANDLING,REOPENED → PENDING,EXECUTING,REOPENED）；
    // 后端 /orders status 为单值，按状态并行请求后合并
    const statuses: HandlingApi.OrderStatus[] = [
      'PENDING',
      'EXECUTING',
      'REOPENED',
    ];
    const results = await Promise.all(
      statuses.map((status) =>
        getHandlingOrdersApi({
          page: 1,
          pageSize: 100, // 后端 /handling/orders pageSize 上限 le=100
          status,
          plantNodeId: selectedPlantNodeId.value,
        }),
      ),
    );
    openItems.value = results.flatMap((r) => r.items);
  } catch {
    openItems.value = [];
  } finally {
    openLoading.value = false;
  }
}

// ===== 右主区：回路总览表格 =====
interface OverviewRow {
  loopId: string;
  tagName: string;
  description: null | string;
  importanceLevel: null | number;
  latestScore: null | number;
  primaryCategoryLabel: null | string;
  suggCount: number;
  suggFirst: null | string;
  /** 适用性（C1：监控列表批量拉取，失败/无快照为 null → 徽标显示"待评估"） */
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

const overviewColumns: TableColumnsType = [
  { key: 'tagName', title: '回路编号', width: 130 },
  { key: 'description', title: '回路名称', ellipsis: true },
  { key: 'importanceLevel', title: '等级', width: 56, align: 'center' },
  { key: 'latestScore', title: '性能评分', width: 80, align: 'center' },
  { key: 'scoreGrade', title: '性能等级', width: 76, align: 'center' },
  { key: 'fitness', title: '可整定性', width: 92, align: 'center' },
  { key: 'diagnosis', title: '诊断结论', width: 120, ellipsis: true },
  { key: 'suggestion', title: '处置建议摘要', ellipsis: true },
  { key: 'pid', title: 'P / I / D 参数', width: 130, align: 'center' },
  { key: 'action', title: '操作', width: 72 },
];

async function loadOverview(): Promise<void> {
  overviewLoading.value = true;
  try {
    const latest = await getDiagnosisRunsLatestApi(selectedPlantNodeId.value);
    // 开放处置工单按回路分组（最新在前）
    const byLoop = new Map<string, HandlingApi.OrderItem[]>();
    for (const it of openItems.value) {
      const arr = byLoop.get(it.loopId) ?? [];
      arr.push(it);
      byLoop.set(it.loopId, arr);
    }
    // C1：适用性徽标数据源（监控列表循环分页拉全量；失败时整列"待评估"不阻断）
    const fitnessMap = new Map<
      string,
      { level: null | string; tags: string[] }
    >();
    try {
      let page = 1;
      let total = 0;
      do {
        const res = await getLoopMonitorListApi({
          page,
          pageSize: 100,
          plantNodeId: selectedPlantNodeId.value,
        });
        for (const item of res.items ?? []) {
          fitnessMap.set(item.loopId, {
            level: item.fitnessLevel ?? null,
            tags: Array.isArray(item.fitnessTags) ? item.fitnessTags : [],
          });
        }
        total = res.total ?? 0;
        page += 1;
      } while ((page - 1) * 100 < total);
    } catch {
      // 适用性列整体降级为"待评估"（诚实化：不猜测等级）
    }
    overviewRows.value = latest.items.map((l) => {
      const items = (byLoop.get(l.loopId) ?? []).toSorted((a, b) =>
        String(b.updatedAt ?? '').localeCompare(String(a.updatedAt ?? '')),
      );
      const fit = fitnessMap.get(l.loopId);
      return {
        loopId: l.loopId,
        tagName: l.loopTagName,
        description: l.loopDescription ?? null,
        importanceLevel: l.importanceLevel ?? null,
        latestScore: l.latestScore ?? null,
        primaryCategoryLabel: l.primaryCategoryLabel ?? null,
        fitnessLevel: fit?.level ?? null,
        fitnessTags: fit?.tags ?? [],
        suggCount: items.length,
        suggFirst: items[0]?.title ?? null,
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
      };
    });
    // P/I/D 初值：一次批量拉全部回路运行参数（原逐回路 /loops/{id}，
    // 961 回路时 6 并发槽位排队近 30s 且 axios 10s 超时整批报错）
    try {
      const rpMap = await getLoopsRuntimeParamsApi(
        selectedPlantNodeId.value || undefined,
      );
      for (const row of overviewRows.value) {
        const rp = rpMap[row.loopId];
        if (!rp) continue;
        row.currentValues.pidP = rp.pidP ?? null;
        row.currentValues.pidI = rp.pidI ?? null;
        row.currentValues.pidD = rp.pidD ?? null;
      }
    } catch {
      // 批量失败不阻断总览（与原单回路失败不阻断口径一致）
    }
  } catch {
    overviewRows.value = [];
  } finally {
    overviewLoading.value = false;
  }
}

function fmtPid(v: null | number | undefined): string {
  return fmtNum2(v);
}

// ===== 实时更新（P/I/D 由全局 WS 推送，初值批量拉取） =====
const { applyMessage, onMessage, start, stop } = useLoopRealtime();

// 服务端订阅过滤：仅接收当前概览行回路的位号
bindLoopInterest(() => overviewRows.value.map((r: any) => r.tagName));

onMessage((msg) => {
  applyMessage(msg, overviewRows.value as any[]);
});

// ===== 装载 =====
async function reloadForNode(): Promise<void> {
  await Promise.all([loadLoops(), loadOpenItems()]);
  await loadOverview();
}

onMounted(async () => {
  start();
  await Promise.all([loadPlantTree(), reloadForNode()]);
});

/** 旧书签定位（/tuning/workbench?loopId= redirect 透传）：高亮该行 */
const highlightLoopId = computed(() => {
  const id = route.query.loopId;
  return typeof id === 'string' && id ? id : null;
});

onBeforeUnmount(() => {
  stop();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="全回路可整定性总览与在途整定建议；单回路整定流程在回路工作台（点击行进入）"
      title="整定总览"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          :loading="overviewLoading || openLoading"
          @click="reloadForNode"
        />
      </template>
    </ClpmPageToolbar>

    <div class="tuning-layout">
      <!-- ===== 左脊柱：装置树 + 回路清单 + 整定建议 ===== -->
      <aside class="tuning-sidebar">
        <div class="tuning-sidebar__section-title">
          <span>装置</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="tuning-sidebar__clear"
            @click="handlePlantTreeSelect([])"
          >
            清除
          </button>
        </div>
        <Spin :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="tuning-plant-tree"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="tuning-sidebar__empty">暂无装置数据</div>
        </Spin>

        <div class="tuning-sidebar__section-title">
          <span>回路</span>
          <span class="text-xs text-neutral-400">
            {{ filteredLoops.length }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <div class="tuning-sidebar__list-wrap">
          <Spin :spinning="loopLoading" size="small">
            <div
              v-for="item in filteredLoops"
              :key="item.loopId"
              class="tuning-loop-item"
              :class="{
                'tuning-loop-item--active':
                  highlightLoopId === item.loopId,
              }"
              role="button"
              tabindex="0"
              :title="item.description || item.tagName"
              @click="gotoWorkbenchTuning(item.loopId, item.tagName)"
              @keydown.enter="gotoWorkbenchTuning(item.loopId, item.tagName)"
            >
              <span class="tuning-loop-item__tag">{{ item.tagName }}</span>
              <span class="tuning-loop-item__unit">{{ item.unitName }}</span>
            </div>
            <Empty
              v-if="!loopLoading && filteredLoops.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="tuning-sidebar__empty"
              description="暂无回路"
            />
          </Spin>
        </div>

        <div class="tuning-sidebar__section-title">
          <span>整定建议</span>
          <span class="text-xs text-neutral-400">
            {{ tuningSuggestions.length }} 项
          </span>
        </div>
        <div class="tuning-sidebar__sugg-wrap">
          <Spin :spinning="openLoading" size="small">
            <div
              v-for="item in tuningSuggestions"
              :key="item.id"
              class="tuning-sugg-item"
              :class="{
                'tuning-sugg-item--active': highlightLoopId === item.loopId,
              }"
              role="button"
              tabindex="0"
              :title="item.title"
              @click="gotoWorkbenchTuning(item.loopId, item.loopTagName)"
              @keydown.enter="gotoWorkbenchTuning(item.loopId, item.loopTagName)"
            >
              <span class="tuning-sugg-item__tag">{{ item.loopTagName }}</span>
              <span class="tuning-sugg-item__meta">
                <span
                  class="tuning-sugg-item__status"
                  :class="`is-${item.status.toLowerCase()}`"
                >
                  {{ item.statusLabel }}
                </span>
              </span>
            </div>
            <Empty
              v-if="!openLoading && tuningSuggestions.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="tuning-sidebar__empty"
              description="暂无整定建议"
            />
          </Spin>
        </div>
      </aside>

      <!-- ===== 右主区：该节点下所有回路总览（2026-10-04 D3：流程区已让位回路工作台） ===== -->
      <div class="tuning-main">
        <Card size="small">
          <template #title>
            <span class="section-title">回路总览</span>
            <span class="ml-2 text-xs font-normal text-neutral-400">
              {{ overviewRows.length }} 个回路 ·
              {{ canOperateTuning ? '点击行进入回路工作台整定' : '整定流程需操作角色' }}
            </span>
          </template>
          <Table
            :columns="overviewColumns"
            :data-source="overviewRows"
            :loading="overviewLoading"
            :pagination="false"
            size="small"
            row-key="loopId"
            :row-class-name="
              (record: any) =>
                record.loopId === highlightLoopId ? 'tuning-row--hl' : ''
            "
            :custom-row="
              (record: any) => ({
                onClick: () =>
                  gotoWorkbenchTuning(record.loopId, record.tagName),
              })
            "
            :custom-cell="() => ({ style: { cursor: 'pointer' } })"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <span class="font-medium">{{ record.tagName }}</span>
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
                  class="clpm-num font-medium"
                  :style="{
                    color: scoreGrade(record.latestScore)?.color,
                  }"
                >
                  {{ record.latestScore.toFixed(1) }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'scoreGrade'">
                <Tag
                  v-if="scoreGrade(record.latestScore)"
                  :color="scoreGrade(record.latestScore)?.color"
                  class="mr-0"
                >
                  {{ scoreGrade(record.latestScore)?.label }}
                </Tag>
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
                <span
                  v-if="record.primaryCategoryLabel"
                  :title="record.primaryCategoryLabel"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">未诊断</span>
              </template>
              <template v-else-if="column.key === 'suggestion'">
                <Tooltip
                  v-if="record.suggFirst"
                  :title="record.suggFirst"
                  placement="topLeft"
                >
                  <span class="text-xs">
                    {{ record.suggFirst }}
                    <Tag
                      v-if="record.suggCount > 1"
                      color="orange"
                      class="mr-0"
                    >
                      {{ record.suggCount }}
                    </Tag>
                  </span>
                </Tooltip>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'pid'">
                <span class="clpm-num text-xs">
                  {{ fmtPid(record.currentValues.pidP) }} /
                  {{ fmtPid(record.currentValues.pidI) }} /
                  {{ fmtPid(record.currentValues.pidD) }}
                </span>
              </template>
              <template v-else-if="column.key === 'action'">
                <Tooltip
                  v-if="canOperateTuning"
                  title="进入回路工作台整定剖面前会校验适用性（L0/L1 阻止，L2 提示）"
                  placement="top"
                >
                  <Button
                    type="link"
                    size="small"
                    class="p-0"
                    @click.stop="
                      gotoWorkbenchTuning(record.loopId, record.tagName)
                    "
                  >
                    调参优化
                  </Button>
                </Tooltip>
                <span v-else class="text-neutral-400">—</span>
              </template>
            </template>
          </Table>
        </Card>
      </div>
    </div>
  </Page>
</template>

<style scoped>
.tuning-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

/* ===== 左脊柱（对齐回路/诊断工作台） ===== */
.tuning-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  width: 248px;
  max-height: calc(100vh - 180px);
  padding: 10px 10px 8px;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.tuning-sidebar__section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 2px 0;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.tuning-sidebar__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.tuning-sidebar__empty {
  padding: 12px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 装置树：紧凑（28px 行高） */
.tuning-plant-tree {
  flex-shrink: 0;
  max-height: 140px;
  overflow: auto;
  font-size: 12px;
}

.tuning-plant-tree :deep(.ant-tree-node-content-wrapper) {
  min-height: 28px;
  line-height: 28px;
}

.tuning-plant-tree :deep(.ant-tree-treenode) {
  padding-top: 0;
  padding-bottom: 0;
}

/* 回路清单（主区，flex-1） */
.tuning-sidebar__list-wrap {
  flex: 1;
  min-height: 140px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

.tuning-loop-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.tuning-loop-item:hover {
  background: hsl(var(--accent));
}

.tuning-loop-item--active {
  background: hsl(var(--accent));
  box-shadow: inset 1px 0 0 hsl(var(--primary));
}

.tuning-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.tuning-loop-item__unit {
  flex-shrink: 0;
  max-width: 72px;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

/* 整定建议（底部固定高度区） */
.tuning-sidebar__sugg-wrap {
  flex-shrink: 0;
  max-height: 150px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

.tuning-sugg-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.tuning-sugg-item:hover {
  background: hsl(var(--accent));
}

.tuning-sugg-item--active {
  background: hsl(var(--accent));
  box-shadow: inset 1px 0 0 hsl(var(--primary));
}

.tuning-sugg-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.tuning-sugg-item__meta {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  margin-left: auto;
}

.tuning-sugg-item__prio {
  font-size: 10px;
  font-weight: 600;
  color: hsl(var(--destructive));
}

.tuning-sugg-item__status {
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.tuning-sugg-item__status.is-pending {
  color: hsl(var(--warning, #b45309));
}

.tuning-sugg-item__status.is-handling {
  color: hsl(var(--primary));
}

.tuning-sugg-item__status.is-reopened {
  color: hsl(var(--destructive));
}

/* ===== 右主区 ===== */
.tuning-main {
  flex: 1;
  min-width: 0;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
}

/* 旧书签 ?loopId 定位高亮行 */
:deep(.tuning-row--hl) > td {
  background: hsl(var(--accent)) !important;
}
</style>
