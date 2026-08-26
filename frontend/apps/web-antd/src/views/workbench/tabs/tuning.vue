<script setup lang="ts">
/**
 * 工作台 Tab4：参数整定 · V3.1 上下主结构（外壳不滚动，仅分区内滚动）
 *
 * 布局：
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ U1 flex-none ~46px：核心问题断言黄框（单行一句话总结）                │
 *   │ U2 flex-1 → 散点(flex-[1.5]) + 右侧(flex-1) 上下双图：              │
 *   │            · 适用性环形图(flex-1) + 劣化饼图(flex-1)                │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ 下部行动区 flex-[1.25] · 深蓝条 26px                                │
 *   │ LOW 清单 flex:1（内 overflow-auto 表格滚动） | ROW 详情 flex 1.4   │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * 滚动策略：
 *   外壳 overflow-hidden 链严格；分区内 overflow-auto / overflow-y-auto 处理溢出
 *
 * 数据流：
 * - A-04 getWorkbenchTuningApi(store.scopeParams) → tuning ref
 * - assertion / scatterBadges / selectedRow 均为 computed/ref 派生
 * - 后端 0 改动；仅 TuningLoopDetail 主趋势 P0 MVP 用占位 SVG
 *
 * 批次语义：A-04 batches 仅用于断言计算"完成回路数"等摘要，UI 端不渲染任何批次表/批次号
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, h, onMounted, ref, watch } from 'vue';

import { message, Modal } from 'ant-design-vue';

import { getWorkbenchTuningApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import DeltaScatter from '../components/DeltaScatter.vue';
import HelpBubble from '../components/HelpBubble.vue';
import TuneQueueRow from '../components/TuneQueueRow.vue';
import TuningFitnessCard from '../components/TuningFitnessCard.vue';
import TuningLoopDetail from '../components/TuningLoopDetail.vue';
import TuningRootCauseDist from '../components/TuningRootCauseDist.vue';

const store = useWorkbenchStore();

const tuning = ref<null | WorkbenchApi.TuningFullResult>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);
/** 单源：当前选中待整定行（清单点选更新此值） */
const selectedRow = ref<null | WorkbenchApi.TuneQueueItem>(null);

const queue = computed(() => tuning.value?.pending_queue ?? []);
const scatters = computed(() => tuning.value?.scatters ?? []);
const fitnessGates = computed(() => tuning.value?.fitness_gates ?? null);

/** U1 核心问题断言横幅（单行一句话） */
const assertion = computed(() => {
  const q = queue.value;
  const pc = { HIGH: 0, LOW: 0, MEDIUM: 0 };
  for (const r of q) pc[r.priority] += 1;
  const fallback = q.find((r) => /回退|rollback/i.test(r.source ?? ''));
  const f = fitnessGates.value;
  const pts = scatters.value;
  const effRate =
    pts.length > 0
      ? Math.round((pts.filter((p) => p.significance).length / pts.length) * 1000) / 10
      : null;
  const avgDelta =
    pts.length > 0
      ? Math.round((pts.reduce((s, p) => s + p.delta, 0) / pts.length) * 10) / 10
      : null;
  const level = f?.level ?? 'L3';
  const mainReason =
    f && f.gate_desc && f.gates_passed
      ? f.gate_desc.find((_, i) => !f.gates_passed[i]) ?? '激励不足'
      : '激励不足';
  const levelLabelMap: Record<string, string> = {
    L0: '阻塞', L1: '待确认', L2: '待数据', L3: '待激励', L4: '就绪',
  };
  const levelLabel = levelLabelMap[level] ?? '阻塞';
  return {
    avgDelta,
    effRate,
    fallbackTag: fallback?.loop_id ?? null,
    hi: pc.HIGH,
    level,
    levelLabel,
    lo: pc.LOW,
    mainReason,
    mi: pc.MEDIUM,
    pending: q.length,
  };
});

/** U2a 散点标题右侧 3 枚短注释（替代原右侧 3 统计卡） */
const scatterBadges = computed(() => {
  const pts = scatters.value;
  if (pts.length === 0) return { max: null, median: null, regress: 0 };
  const deltas = pts.map((p) => p.delta).toSorted((a, b) => a - b);
  const last = deltas.at(-1) ?? 0;
  const mid = deltas[deltas.length >> 1] ?? 0;
  return {
    max: Math.round(last * 10) / 10,
    median: Math.round(mid * 10) / 10,
    regress: deltas.filter((d) => d < 0).length,
  };
});

async function loadTuning() {
  loading.value = true;
  errorMsg.value = null;
  selectedRow.value = null; // scope 切换清空选中，避免联动错位
  try {
    const res = await getWorkbenchTuningApi(store.scopeParams);
    tuning.value = res;
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '整定数据加载失败';
    tuning.value = null;
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

function handleSim(row: WorkbenchApi.TuneQueueItem) {
  openSimConfirm(row);
}

/** 行动区 ? 帮助弹窗说明项 */
const actionHelpItems = [
  { text: '清单点击行 → 右侧趋势联动，加载该回路最近 24h 趋势 + 评分快照。' },
  { label: '整定仿真', text: '右侧详情卡顶栏「▶ 整定仿真」按钮 → 弹出整定工作台配置弹窗。' },
  { label: '仿真边界', text: '平台不直接修改 DCS 的 P/I/D 参数，仅输出建议、证据、风险与回退方案；参数由授权人员人工实施并留痕。' },
  { label: '灰行', text: '前置工单未闭合的回路标记为 blocked，整定入口禁用，等前置工单关闭后自动解锁。' },
];

/** U1 断言 ? 帮助弹窗说明项 */
const assertionHelpItems = [
  { label: '断言', text: '当前范围（装置/时间窗）内整定队列与适用性的核心问题一句话总结。' },
  { label: '适用性 L0~L4', text: 'L0 阻塞 / L1 待确认 / L2 待数据 / L3 待激励 / L4 就绪；显示整体最高非空级别与首要失败门禁。' },
  { label: '优先级', text: '评分 <65 高优（红）/ <73 中优（橙）/ ≥73 低优（灰），用于排序待整定队列。' },
];

/** U2 散点 ? 帮助弹窗说明项 */
const scatterHelpItems = [
  { label: '散点', text: '横轴 before 整定前评分，纵轴 after 整定后评分；对角线为无变化基线，上方=提升，下方=回退。' },
  { label: 'Δ≥5 有效', text: 'Δ = after - before ≥ 5 分记为有效提升，散点高亮；< 5 视为波动噪声。' },
  { label: '短注释', text: '标题右侧 3 枚：▲最大提升 / 中位提升 / ✕回退数（Δ<0）。' },
];

/**
 * 仿真确认弹窗（520px Modal.confirm + h() 渲染 4 段卡片）
 * P0 MVP：弹窗文案即"整定仿真"配置；P0.5 将升级为 920px 完整 4 锚点整定工作台弹窗
 */
function openSimConfirm(row: WorkbenchApi.TuneQueueItem): void {
  const loopLabel = row.loop_name ?? row.loop_id;
  const scoreTxt =
    row.score === null || row.score === undefined ? '—' : row.score.toFixed(1);
  const fitTxt =
    row.fitting_score === null || row.fitting_score === undefined
      ? '—'
      : row.fitting_score.toFixed(1);
  const priorityMap: Record<WorkbenchApi.TuneQueueItem['priority'], string> = {
    HIGH: '高',
    LOW: '低',
    MEDIUM: '中',
  };
  const batchNo = (row as unknown as { batch_no?: null | string }).batch_no;
  Modal.confirm({
    cancelText: '取消',
    okText: '开始仿真',
    okType: 'primary',
    title: `整定仿真 — ${loopLabel}`,
    width: 520,
    content: h('div', { style: 'font-size: 12px; line-height: 1.7' }, [
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '回路信息'),
      h(
        'div',
        {
          style:
            'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; margin-bottom: 14px; padding: 8px 10px; background: #FAFBFC; border: 1px solid #E4E7ED; border-radius: 2px;',
        },
        [
          h('span', { style: 'color: #8C8C8C' }, '位号'),
          h('span', { style: 'color: #262626; font-weight: 500' }, loopLabel),
          h('span', { style: 'color: #8C8C8C' }, '回路描述'),
          h('span', { style: 'color: #595959' }, row.loop_desc ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '归属单元'),
          h('span', { style: 'color: #595959' }, row.unit_name ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '建议来源'),
          h('span', { style: 'color: #595959' }, row.source),
        ],
      ),
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '整定建议'),
      h(
        'div',
        {
          style:
            'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; margin-bottom: 14px; padding: 8px 10px; background: #FAFBFC; border: 1px solid #E4E7ED; border-radius: 2px;',
        },
        [
          h('span', { style: 'color: #8C8C8C' }, '当前评分'),
          h('span', { style: 'color: #262626; font-weight: 600' }, scoreTxt),
          h('span', { style: 'color: #8C8C8C' }, '适配评分'),
          h('span', { style: 'color: #595959' }, fitTxt),
          h('span', { style: 'color: #8C8C8C' }, '建议策略'),
          h('span', { style: 'color: #595959' }, row.algorithm ?? '—'),
          h('span', { style: 'color: #8C8C8C' }, '优先级'),
          h('span', { style: 'color: #595959' }, priorityMap[row.priority]),
        ],
      ),
      h('div', { style: 'margin-bottom: 10px; font-weight: 600; color: #1F4E79' }, '仿真参数'),
      h(
        'div',
        {
          style:
            'display: grid; grid-template-columns: 80px 1fr; gap: 4px 12px; padding: 8px 10px; background: #F0F7FF; border: 1px solid #D6E8FF; border-radius: 2px;',
        },
        [
          h('span', { style: 'color: #8C8C8C' }, '仿真时长'),
          h('span', { style: 'color: #262626' }, '30 分钟（默认）'),
          h('span', { style: 'color: #8C8C8C' }, '步长'),
          h('span', { style: 'color: #262626' }, '1 秒（默认）'),
          h('span', { style: 'color: #8C8C8C' }, '初始值'),
          h('span', { style: 'color: #262626' }, '当前 PV / SP / OP / P / I / D'),
          h('span', { style: 'color: #8C8C8C' }, '关联批次'),
          h('span', { style: 'color: #595959' }, batchNo ?? '（独立仿真，不关联批次）'),
        ],
      ),
      h(
        'div',
        {
          style:
            'margin-top: 12px; padding: 6px 8px; font-size: 11px; color: #8C8C8C; background: #FFFBE6; border: 1px solid #FFE58F; border-radius: 2px;',
        },
        '⚠ 仿真仅输出建议与证据，参数由授权人员线下人工实施并留痕。仿真过程中 DCS 实时值不会被修改。',
      ),
    ]),
    onOk: () => {
      message.success(`仿真任务已提交：${loopLabel}`);
    },
  });
}

onMounted(() => {
  loadTuning();
});
watch(
  () => store.scopeParams,
  () => {
    loadTuning();
  },
  { deep: true },
);
</script>

<template>
  <!-- 严格 overflow-hidden 链：外壳不滚动，仅分区内滚动 -->
    <div class="flex h-full min-h-0 flex-col overflow-hidden p-2" style="gap: 4px">
      <!-- 加载/错误提示（flex-none，不参与 flex 分配） -->
      <div
        v-if="loading"
        class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
      >正在加载整定数据…</div>
      <div
        v-else-if="errorMsg"
        class="flex-none rounded border border-red-100 bg-red-50 px-3 py-1 text-[11px] text-red-600"
      >
        {{ errorMsg }}
        <button class="ml-2 underline" @click="loadTuning">重试</button>
      </div>

      <!-- ========== 上部：综合信息区（~45%） ========== -->
      <div class="flex min-h-0 flex-1 flex-col overflow-hidden" style="gap: 4px">

        <!-- U1 黄框断言 flex-none ~32px（压缩高度） -->
        <div
          v-if="assertion.pending > 0 || scatters.length > 0"
          class="flex flex-none items-center rounded-[2px] border border-[#FFE58F] bg-[#FFFBE6] px-2 py-[3px] text-[11px] leading-tight"
          style="min-height: 32px"
        >
          <div class="min-w-0 flex-1 text-[#593A00]">
            <b class="text-[#8C4A00] mr-1">⚠</b>
            <b class="text-[#FF4D4F]">{{ assertion.pending }} 条</b> 待整定
            （<b class="text-[#FF4D4F]">高 {{ assertion.hi }}</b> / 中 {{ assertion.mi }} / 低 {{ assertion.lo }}）
            <template v-if="assertion.fallbackTag">
              ，含 <b class="text-[#FF4D4F]">{{ assertion.fallbackTag }} 回退</b>
            </template>
            ；适用性 <b>{{ assertion.level }} {{ assertion.levelLabel }}</b>，主因
            <b class="text-[#FF4D4F]">{{ assertion.mainReason }}</b>
            <template v-if="assertion.effRate !== null">
              ；有效率 <b class="text-[#52C41A]">{{ assertion.effRate }}%</b>
            </template>
            <template v-if="assertion.avgDelta !== null">
              ，平均
              <b :class="assertion.avgDelta >= 0 ? 'text-[#52C41A]' : 'text-[#FF4D4F]'">
                {{ assertion.avgDelta >= 0 ? '+' : '' }}{{ assertion.avgDelta }} 分
              </b>
            </template>。
            <span class="ml-1 text-[9.5px] text-[#8C4A00] opacity-70">
              {{ (store.scopeParams as { plantName?: string }).plantName ?? '全厂' }} / {{ (store.scopeParams as { window?: string }).window ?? '30d' }}
            </span>
          </div>
          <HelpBubble :size="13" theme="blue" title="核心问题断言说明" :items="assertionHelpItems" class="ml-2 flex-none" />
        </div>
        <div
          v-else
          class="flex flex-none items-center rounded-[2px] border border-[#E4E7ED] bg-white px-2 py-1.5 text-[11px] text-[#8C8C8C]"
          style="min-height: 32px"
        >
          <span class="flex-1">ℹ 当前范围暂无整定数据，请扩大时间窗或切换至其他装置/单元。</span>
          <HelpBubble :size="13" theme="blue" title="核心问题断言说明" :items="assertionHelpItems" class="ml-2 flex-none" />
        </div>

        <!-- U2 散点(flex-[1.5]) + 右侧左右双图(flex-1) -->
        <div class="flex min-h-0 flex-1 overflow-hidden" style="gap: 4px">
          <!-- 左：散点 -->
          <div class="flex min-h-0 flex-[1.5] flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
            <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
              <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
              整定效果验证 · before×after（{{ scatters.length }} 回路 · Δ≥5 有效）
              <HelpBubble :size="12" theme="blue" title="整定效果散点说明" :items="scatterHelpItems" class="ml-1" />
              <span class="ml-auto flex items-center gap-2 font-normal text-[9.5px]">
                <span
                  v-if="scatterBadges.max !== null"
                  class="font-bold text-[#52C41A]"
                >▲最大 +{{ scatterBadges.max }}</span>
                <span
                  v-if="scatterBadges.median !== null"
                  class="font-bold text-[#1890FF]"
                >中 +{{ scatterBadges.median }}</span>
                <span
                  v-if="scatterBadges.regress > 0"
                  class="font-bold text-[#FF4D4F]"
                >✕回退 {{ scatterBadges.regress }}</span>
              </span>
            </div>
            <div class="min-h-0 flex-1 overflow-hidden p-1">
              <DeltaScatter :points="scatters" />
            </div>
          </div>

          <!-- 右：左右双图（适用性环形图 + 劣化饼图），各占 flex-1 等宽 -->
          <div class="flex min-h-0 flex-1 overflow-hidden" style="gap: 4px">
            <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
              <TuningFitnessCard :gates="fitnessGates" :queue="queue" />
            </div>
            <div class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[2px] border border-[#E4E7ED] bg-white">
              <TuningRootCauseDist :rows="queue" />
            </div>
          </div>
        </div>
      </div>

      <!-- ========== 下部：行动区（~55% · flex 1.25） ========== -->
      <div class="flex min-h-0 flex-[1.25] flex-col overflow-hidden rounded-[2px] border border-[#1F4E79] bg-white">
        <div class="flex h-[26px] flex-none items-center border-b border-[#1F4E79] bg-[#1F4E79] px-2 text-[11px] font-semibold text-white">
          <span class="mr-1.5 inline-block h-[12px] w-[4px] rounded-[2px] bg-[#52C41A]"></span>
          行动区 · 待整定清单 × 单回路趋势
          <HelpBubble :size="13" theme="white" title="行动区操作说明" :items="actionHelpItems" class="ml-1.5" />
          <span class="ml-auto text-[10px] font-normal opacity-90">
            {{ queue.length }} 条 ·
            高优 <b class="text-[#FF7875]">{{ assertion.hi }}</b> ·
            中 <b class="text-[#FFBB96]">{{ assertion.mi }}</b> ·
            低 <b class="text-[#BFBFBF]">{{ assertion.lo }}</b>
          </span>
        </div>
        <div class="flex min-h-0 flex-1 overflow-hidden">
          <!-- LOW 清单 flex:1（内部 overflow-auto 表格滚动；无操作列、无 Footer） -->
          <div
            class="flex min-h-0 flex-1 flex-col overflow-hidden border-r border-[#E4E7ED]"
          >
            <TuneQueueRow
              :rows="queue"
              :selected-id="selectedRow?.loop_id ?? null"
              @select="selectedRow = $event"
            />
          </div>
          <!-- ROW 详情 flex 1.4（趋势图吃满剩余高度，overflow-hidden 不滚动） -->
          <div class="flex min-h-0 flex-[1.4] flex-col overflow-hidden bg-[#F7F9FC] p-[5px_7px]">
            <TuningLoopDetail :row="selectedRow" @open-workbench="handleSim" />
          </div>
        </div>
      </div>
    </div>
</template>
