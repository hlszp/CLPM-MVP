<script setup lang="ts">
/**
 * 诊断：装置 / 单元 Top8 × 故障累积水平堆叠柱（方案 A：替换旧 FaultDeviceMatrix 热力矩阵）
 *
 * 解决齐总反馈：
 *  · FaultDeviceMatrix 矩阵稀疏（90% 单元 count≤1）且类别维度与 Pareto 重复、信息增量低
 *  · 水平堆叠柱更适合 10 字左右中文装置名，直观看出「哪个装置问题多、每装置受哪几类困扰」
 *
 * 单元归属（2026-08-26 改版）：
 *  · 优先用后端返回的工厂模型单元节点 unit_name（loop_ledger.unit_id → plant_node），
 *    不再按回路位号前缀/名称启发式拆解；unit_name 缺失时回退 extractUnit(loop_name)，
 *    仍无则归入「未关联单元」
 *
 * 设计：
 *  · 行 = Top8 单元（工厂模型节点）
 *  · 每单元一条水平堆叠柱：按「全局 Pareto Top5 类别」配色（全局一致色板，跨单元可肉眼对比）
 *    + 第 6 档 "其他" = 灰。
 *  · 每行右侧 = 合计 count 数字 + 严重度色点（若该单元任一单元格 severity≥2 则红，≥1 橙，其余蓝）
 *  · 右上角图例 = 全局 Pareto Top5 色块 + 其他（灰）
 *  · hover title = "单元（装置）· 总 N · A:3 B:5 … · Top3 回路"
 *  · 底部说明行：共多少单元 · 覆盖多少回路 · 合计异常 N 条
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  conclItems?: WorkbenchApi.DiagnosisConclItem[];
  openTags?: WorkbenchApi.DiagnosisOpenTag[];
  pareto?: WorkbenchApi.ParetoRow[];
}>();

// ---------- 单元名解析：工厂模型节点优先，启发式仅作兜底 ----------
/** 兜底：从 loop_name 末尾中文提取（仅当后端 unit_name 缺失时使用） */
function extractUnit(loopName: null | string | undefined): string {
  if (!loopName) return '未关联单元';
  const s = String(loopName).trim();
  const toks = s.split(/\s+/).filter(Boolean);
  if (toks.length >= 2) {
    const last = toks[toks.length - 1] ?? '';
    if (/[\u4E00-\u9FA5]/.test(last)) return last;
  }
  const parts = s.split(/_|-/);
  if (parts.length >= 2) {
    const last = parts[parts.length - 1] ?? '';
    if (/[\u4E00-\u9FA5]/.test(last)) return last;
  }
  const zh = s.match(
    /[\u4E00-\u9FA5][\u4E00-\u9FA5\w\d\-()（）]*\s*[\u4E00-\u9FA5]?/g,
  );
  if (zh && zh[0]) return zh[0];
  return s.slice(0, 3);
}

/** 单元归属：unit_name（工厂模型节点）> loop_name 启发式 > 未关联单元 */
function resolveUnit(it: {
  loop_name?: null | string;
  unit_name?: null | string;
}): string {
  return it.unit_name || extractUnit(it.loop_name);
}

function pickCategory(it: {
  category: null | string | undefined;
  symptom?: null | string;
  tag_code?: null | string;
}): string {
  return it.category || it.symptom || it.tag_code || '未分类';
}

// ---------- 全局 Pareto Top5 类别色板（跨装置一致）----------
const CATEGORIES = computed<string[]>(() => {
  const arr = (props.pareto ?? [])
    .map((p) => p.root_cause)
    .filter(Boolean) as string[];
  if (arr.length === 0) {
    // Pareto 空时退化为数据里聚合 Top5（保证示例数据/边界场景仍有颜色）
    const set = new Map<string, number>();
    for (const t of props.openTags ?? []) {
      const c = pickCategory({ category: t.category, symptom: t.symptom });
      set.set(c, (set.get(c) ?? 0) + 1);
    }
    for (const it of props.conclItems ?? []) {
      const c = pickCategory({ category: it.category, tag_code: it.tag_code });
      set.set(c, (set.get(c) ?? 0) + 1);
    }
    return [...set.entries()]
      .toSorted((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([x]) => x);
  }
  return arr.slice(0, 5);
});

const PALETTE = [
  '#1F4E79', // 深蓝（和 Pareto 柱同主色，视觉统一）
  '#1890FF', // 蓝
  '#52C41A', // 绿
  '#FA8C16', // 橙
  '#FF4D4F', // 红
  '#BFBFBF', // 其他（灰）
];

const OTHERS = '其他';

function catColor(cat: string): string {
  const arr: string[] = [...CATEGORIES.value];
  const idx = arr.indexOf(cat);
  const fallback = PALETTE[PALETTE.length - 1] ?? '#BFBFBF';
  return idx === -1 ? fallback : (PALETTE[idx] ?? fallback);
}

// ---------- 聚合：unit × category ----------
type UnitCell = {
  count: number;
  loops: string[]; // Top3
  severity: number; // 0~3
};
type UnitRow = {
  cells: Map<string, UnitCell>; // category -> cell
  count: number; // 合计
  factory: null | string; // 所属装置（工厂模型父节点）
  name: string; // 单元（工厂模型节点）
  severity: number;
};

const unitsMap = computed<Map<string, UnitRow>>(() => {
  const m = new Map<string, UnitRow>();
  function bump(
    unit: string,
    cat: string,
    loopName: null | string | undefined,
    sev: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN' | null | undefined,
    factory: null | string | undefined,
  ) {
    let sevRank = 0;
    switch (sev) {
    case 'CRITICAL': {
    sevRank = 3;
    break;
    }
    case 'ERROR': {
    sevRank = 2;
    break;
    }
    case 'WARN': { {
    sevRank = 1;
    // No default
    }
    break;
    }
    }
    // 映射到 全局 Top5 或 其他
    const c = CATEGORIES.value.includes(cat) ? cat : OTHERS;
    let row = m.get(unit);
    if (!row) {
      row = { cells: new Map(), count: 0, factory: factory ?? null, name: unit, severity: 0 };
      m.set(unit, row);
    } else if (!row.factory && factory) {
      row.factory = factory;
    }
    let cell = row.cells.get(c);
    if (!cell) {
      cell = { count: 0, loops: [], severity: 0 };
      row.cells.set(c, cell);
    }
    cell.count += 1;
    cell.severity = Math.max(cell.severity, sevRank);
    row.count += 1;
    row.severity = Math.max(row.severity, sevRank);
    if (cell.loops.length < 3 && loopName && !cell.loops.includes(loopName)) {
      cell.loops.push(loopName);
    }
  }
  for (const t of props.openTags ?? []) {
    bump(
      resolveUnit(t),
      pickCategory({ category: t.category, symptom: t.symptom }),
      t.loop_name,
      t.severity,
      t.factory_name,
    );
  }
  for (const it of props.conclItems ?? []) {
    bump(
      resolveUnit(it),
      pickCategory({ category: it.category, tag_code: it.tag_code }),
      it.loop_name,
      it.severity,
      it.factory_name,
    );
  }
  return m;
});

const units = computed<UnitRow[]>(() =>
  [...unitsMap.value.values()]
    .toSorted((a, b) => b.count - a.count)
    .slice(0, 8),
);

const totalBad = computed(() => units.value.reduce((s, u) => s + u.count, 0));

const coveredLoops = computed(() => {
  const set = new Set<string>();
  for (const t of props.openTags ?? []) {
    if (t.loop_id) set.add(t.loop_id);
  }
  for (const it of props.conclItems ?? []) {
    if (it.loop_id) set.add(it.loop_id);
  }
  return set.size;
});

function severityColor(s: number): string {
  if (s >= 3) return '#FF4D4F';
  if (s >= 2) return '#FA8C16';
  if (s >= 1) return '#FAAD14';
  return '#1890FF';
}

// ---------- 堆叠段宽度（百分比，相对单装置总）----------
function segWidth(u: UnitRow, cat: string): string {
  const c = u.cells.get(cat)?.count ?? 0;
  if (c <= 0) return '0%';
  const pct = (c / Math.max(1, u.count)) * 100;
  return `${pct}%`;
}

function segCount(u: UnitRow, cat: string): number {
  return u.cells.get(cat)?.count ?? 0;
}

// hover title 工具
function rowTitle(u: UnitRow): string {
  const head = u.factory ? `${u.name}（${u.factory}）` : u.name;
  const parts: string[] = [ `${head} · 共 ${u.count} 条`];
  const segs: string[] = [];
  // 按 Pareto Top5 顺序 + 其他
  const catsInOrder = [...CATEGORIES.value, OTHERS];
  for (const c of catsInOrder) {
    const cell = u.cells.get(c);
    if (cell && cell.count > 0) segs.push(`${c}:${cell.count}`);
  }
  if (segs.length > 0) parts.push(segs.join(' · '));
  const loopList = [...u.cells.values()]
    .flatMap((x) => x.loops)
    .filter((x, idx, arr) => arr.indexOf(x) === idx)
    .slice(0, 3);
  if (loopList.length > 0) parts.push(`典型回路：${loopList.join('、')}`);
  return parts.join('\n');
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        故障装置 / 单元 Top 累积
        <span class="text-[10px] font-normal text-gray-400">
          单元 {{ units.length }} · 覆盖 {{ coveredLoops }} 回路 · 累计 {{ totalBad }} 条
        </span>
      </span>
      <div class="flex flex-wrap items-center gap-2 text-[10px] text-gray-500">
        <span
          v-for="(c, i) in CATEGORIES"
          :key="`lg-${c}-${i}`"
          class="inline-flex items-center gap-1"
        >
          <i
            class="inline-block h-2.5 w-2.5 rounded-sm"
            :style="{ backgroundColor: PALETTE[i] }"
          ></i>
          {{ c.length > 6 ? `${c.slice(0, 6)}…` : c }}
        </span>
        <span class="inline-flex items-center gap-1">
          <i
            class="inline-block h-2.5 w-2.5 rounded-sm"
            :style="{ backgroundColor: PALETTE[PALETTE.length - 1] }"
          ></i>
          其他
        </span>
      </div>
    </div>

    <!-- 主体列表：8 行 flex-col justify-between 填满高度 -->
    <div
      class="flex flex-1 min-h-0 flex-col justify-between overflow-hidden px-3 pb-2 pt-1 text-[11px]"
    >
      <!-- 空态 -->
      <div
        v-if="units.length === 0"
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        近窗口暂无装置/单元异常分布
      </div>

      <template v-else>
        <div
          v-for="u in units"
          :key="`u-${u.name}`"
          class="flex min-h-0 flex-1 items-center gap-2 py-0.5"
          :title="rowTitle(u)"
        >
          <!-- 严重度色点 -->
          <span
            class="inline-block h-2 w-2 flex-none rounded-full"
            :style="{ backgroundColor: severityColor(u.severity) }"
          ></span>
          <!-- 单元名（截断 10 字；title 含所属装置） -->
          <div
            class="w-[92px] flex-none min-w-0 truncate text-gray-700"
            :title="u.factory ? `${u.name}（${u.factory}）` : u.name"
          >
            {{ u.name.length > 10 ? `${u.name.slice(0, 10)}…` : u.name }}
          </div>
          <!-- 水平堆叠柱条：flex 总宽 100%，每段 flex:segWidth，min-width 2px 防 0 塌陷 -->
          <div
            class="relative flex h-4 flex-1 min-w-0 items-stretch overflow-hidden rounded-sm border border-[#EBEDEF]"
          >
            <template
              v-for="c in [...CATEGORIES, OTHERS]"
              :key="`seg-${u.name}-${c}`"
            >
              <div
                v-if="segCount(u, c) > 0"
                class="h-full"
                :style="{
                  width: segWidth(u, c),
                  backgroundColor: catColor(c),
                  minWidth: '2px',
                }"
                :title="`${c}: ${segCount(u, c)}`"
              ></div>
            </template>
          </div>
          <!-- 合计 count 数字；≥5 红粗，≥2 橙，其余灰 -->
          <div
            class="w-12 flex-none text-right tabular-nums"
            :class="
              u.count >= 5
                ? 'font-semibold text-[#FF4D4F]'
                : u.count >= 2
                  ? 'text-[#FA8C16]'
                  : 'text-gray-500'
            "
          >
            {{ u.count }}
          </div>
        </div>
      </template>
    </div>

    <!-- 底部说明行：Top 装置与 Pareto 治理主战场联动 -->
    <div
      class="flex-none border-t border-dashed border-[#E4E7ED] px-3 py-1.5 text-[10.5px] text-gray-500"
    >
      <template v-if="units[0]">
        头号风险单元：
        <span class="font-semibold text-[#FF4D4F]">{{ units[0].name }}</span>
        <template v-if="units[0].factory">（{{ units[0].factory }}）</template>
        &nbsp;共 {{ units[0].count }} 条 · 建议优先按
        <span class="text-[#1F4E79] font-medium">{{ CATEGORIES[0] ?? '—' }}</span>
        类别集中治理
      </template>
      <template v-else> 近窗口无装置异常数据 </template>
    </div>
  </div>
</template>
