<script setup lang="ts">
/**
 * 故障装置 / 单元 × 异常类别 矩阵热力图（方案 A，替换原 ConclTimeline）
 *
 * 输入：open_tags（近 N 小时未处置异常标签）+ concl_items（近 N 小时诊断结论）
 * 聚合逻辑：
 *   - 行 = 装置 / 单元：从 loop_name 提取首段「XIC-401 精馏塔」=> 取后半段中文装置名；
 *     无中文时退化为 loop_name 前缀分组（LIC/FIC/… × 前缀前 4 字符，避免离散过多）
 *   - 列 = 异常类别（open_tags.category / concl_items.category）缺省分类为 symptom/tag_code
 *   - 单元格 = (unit, category) 聚合 count；颜色映射 4 级严重度：0=浅灰 <1 黄 <3 橙 ≥3 红（透明度百分比叠加）
 *   - 点击单元 → 父级可下发 drill-down；显示悬浮提示「装置 X，类别 Y，异常 N 条，Top 回路 A B C」
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  conclItems?: WorkbenchApi.DiagnosisConclItem[];
  openTags?: WorkbenchApi.DiagnosisOpenTag[];
}>();

// 从 loop_name 提取「装置/单元」名：取后半段中文部分（或中文括号前）
// 例："41FC4015A_PIDA 脱甲烷塔" → "脱甲烷塔"；"TIC-408 主分馏塔中段回流" → "主分馏塔中段回流"
function extractUnit(loopName: null | string | undefined): string {
  if (!loopName) return '未分组';
  const s = String(loopName).trim();
  // 先按空格分开，取末段（若为中文）
  const toks = s.split(/\s+/).filter(Boolean);
  if (toks.length >= 2) {
    const last = toks[toks.length - 1] ?? '';
    if (/[\u4E00-\u9FA5]/.test(last)) return last;
  }
  // 再按 _ 分割取末段
  const parts = s.split(/_|-/);
  if (parts.length >= 2) {
    const last = parts[parts.length - 1] ?? '';
    if (/[\u4E00-\u9FA5]/.test(last)) return last;
  }
  // 取全部中文
  const zh = s.match(/[\u4E00-\u9FA5][\u4E00-\u9FA5\w\d\-()（）]*\s*[\u4E00-\u9FA5]?/g);
  if (zh && zh[0]) return zh[0];
  // 最后退化：首 3 字符
  return s.slice(0, 3);
}

function pickCategory(it: {
  category: null | string | undefined;
  symptom?: null | string;
  tag_code?: null | string;
}): string {
  return it.category || it.symptom || it.tag_code || '未分类';
}

type Cell = {
  cat: string;
  count: number;
  severity: number;
  topLoops: string[];
  unit: string;
};

const allCells = computed<Cell[]>(() => {
  const map = new Map<string, Cell>();
  function bump(
    unit: string,
    cat: string,
    loopName: null | string | undefined,
    sev: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN' | null | undefined,
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
    const key = `${unit}\u0001${cat}`;
    let c = map.get(key);
    if (!c) {
      c = { cat, count: 0, severity: 0, topLoops: [], unit };
      map.set(key, c);
    }
    c.count += 1;
    c.severity = Math.max(c.severity, sevRank);
    if (c.topLoops.length < 3 && loopName && !c.topLoops.includes(loopName)) {
      c.topLoops.push(loopName);
    }
  }
  for (const t of props.openTags ?? []) {
    bump(
      extractUnit(t.loop_name),
      pickCategory({ category: t.category, symptom: t.symptom }),
      t.loop_name,
      t.severity,
    );
  }
  for (const it of props.conclItems ?? []) {
    bump(
      extractUnit(it.loop_name),
      pickCategory({ category: it.category, tag_code: it.tag_code }),
      it.loop_name,
      it.severity,
    );
  }
  return [...map.values()];
});

const units = computed<string[]>(() => {
  const set = new Map<string, number>();
  for (const c of allCells.value) {
    set.set(c.unit, (set.get(c.unit) ?? 0) + c.count);
  }
  return [...set.entries()]
    .toSorted((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([u]) => u);
});

const categories = computed<string[]>(() => {
  const set = new Map<string, number>();
  for (const c of allCells.value) {
    set.set(c.cat, (set.get(c.cat) ?? 0) + c.count);
  }
  return [...set.entries()]
    .toSorted((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([k]) => k);
});

const cellMap = computed(() => {
  const m = new Map<string, Cell>();
  for (const c of allCells.value) m.set(`${c.unit}\u0001${c.cat}`, c);
  return m;
});

const totalBad = computed(() =>
  allCells.value.reduce((s, c) => s + c.count, 0),
);

// 单元格颜色：count 越大越红，叠加严重度
function cellColor(c: Cell | undefined) {
  if (!c) return '#FAFBFC';
  const n = c.count;
  const s = c.severity; // 0~3
  // 4 级调色：#F7FAFC / #FFF7E6 / #FFE7BA / #FFCCC7 / #FFA39E （工业配色一致）
  if (n === 0) return '#FAFBFC';
  if (n === 1) return s >= 2 ? '#FFE7BA' : '#F2F8FF';
  if (n === 2) return s >= 2 ? '#FFCCC7' : '#FFF7E6';
  if (n === 3) return s >= 2 ? '#FFA39E' : '#FFD591';
  if (n <= 5) return '#FFA39E';
  return '#FF7875';
}

function unitCount(u: string) {
  return allCells.value
    .filter((c) => c.unit === u)
    .reduce((s, c) => s + c.count, 0);
}
function catCount(cat: string) {
  return allCells.value
    .filter((c) => c.cat === cat)
    .reduce((s, c) => s + c.count, 0);
}

const UNIT_SEP = '\u0001';
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div
      class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5"
    >
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span
          class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"
        ></span>
        故障装置 / 单元分布
        <span class="text-[10px] font-normal text-gray-400">
          累计 {{ totalBad }} 条 · 装置 Top {{ units.length }} × 类别 Top
          {{ categories.length }}
        </span>
      </span>
      <div class="flex items-center gap-2 text-[10px] text-gray-400">
        <span class="inline-flex items-center gap-1">
          <i
            class="inline-block h-3 w-3 rounded-sm border border-gray-200"
            :style="{
              backgroundColor: cellColor({
                cat: '',
                count: 1,
                severity: 0,
                topLoops: [],
                unit: '',
              }),
            }"
          ></i
          >1
        </span>
        <span class="inline-flex items-center gap-1">
          <i
            class="inline-block h-3 w-3 rounded-sm border border-gray-200"
            :style="{
              backgroundColor: cellColor({
                cat: '',
                count: 2,
                severity: 1,
                topLoops: [],
                unit: '',
              }),
            }"
          ></i
          >2
        </span>
        <span class="inline-flex items-center gap-1">
          <i
            class="inline-block h-3 w-3 rounded-sm border border-gray-200"
            :style="{
              backgroundColor: cellColor({
                cat: '',
                count: 4,
                severity: 2,
                topLoops: [],
                unit: '',
              }),
            }"
          ></i
          >3–5
        </span>
        <span class="inline-flex items-center gap-1">
          <i
            class="inline-block h-3 w-3 rounded-sm border border-gray-200"
            :style="{
              backgroundColor: cellColor({
                cat: '',
                count: 99,
                severity: 3,
                topLoops: [],
                unit: '',
              }),
            }"
          ></i
          >≥6
        </span>
      </div>
    </div>

    <!-- 矩阵主体：grid 单元格按行数均分布满高度 -->
    <div
      v-if="units.length > 0 && categories.length > 0"
      class="flex flex-1 min-h-0 flex-col overflow-hidden px-3 pb-2 pt-1 text-[10.5px]"
    >
      <!-- 顶部列头（类别名）：空+类别列 + count 列 -->
      <div
        class="grid flex-none min-h-0 items-center gap-1 pb-1 text-[10.5px] font-medium text-gray-500"
        :style="{
          gridTemplateColumns: `100px repeat(${categories.length}, minmax(0, 1fr)) 44px`,
        }"
      >
        <div class="truncate">装置 / 单元 ↓</div>
        <div
          v-for="cat in categories"
          :key="`cat-${cat}`"
          class="truncate text-center"
          :title="cat"
        >
          {{ cat.length > 6 ? cat.slice(0, 6) : cat }}
        </div>
        <div class="truncate pr-1 text-right">合计</div>
      </div>

      <!-- 矩阵行：flex-col 每行按 (总高 - 列头 - 合计行底) / N 均分 -->
      <div
        class="grid flex-1 min-h-0 auto-rows-fr gap-1"
        :style="{
          gridTemplateColumns: `100px repeat(${categories.length}, minmax(0, 1fr)) 44px`,
          gridTemplateRows: `repeat(${units.length}, minmax(0, 1fr))`,
        }"
      >
        <template v-for="u in units" :key="`u-${u}`">
          <div
            class="flex items-center gap-1 truncate text-gray-700"
            :title="u"
          >
            <span
              class="inline-block h-1.5 w-1.5 flex-none rounded-full"
              :style="{
                backgroundColor:
                  unitCount(u) >= 5
                    ? '#FF4D4F'
                    : unitCount(u) >= 2
                      ? '#FA8C16'
                      : '#1890FF',
              }"
            ></span>
            <span class="truncate">
              {{ u.length > 10 ? u.slice(0, 10) : u }}
            </span>
          </div>
          <template v-for="cat in categories" :key="`${u}-${cat}`">
            <div
              class="group/cell relative flex h-full min-h-0 w-full cursor-pointer items-center justify-center rounded-sm border border-[#EBEDEF] transition-colors hover:ring-1 hover:ring-[#1890FF]"
              :style="{
                backgroundColor: cellColor(
                  cellMap.get(`${u}${UNIT_SEP}${cat}`),
                ),
              }"
              :title="
                cellMap.get(`${u}${UNIT_SEP}${cat}`)
                  ? `${u} · ${cat}\n异常 ${cellMap.get(
                      `${u}${UNIT_SEP}${cat}`,
                    )!.count} 条\n典型回路：${cellMap.get(
                      `${u}${UNIT_SEP}${cat}`,
                    )!.topLoops.join('、')}`
                  : ''
              "
            >
              <span
                class="tabular-nums"
                :class="
                  (cellMap.get(`${u}${UNIT_SEP}${cat}`)?.count ?? 0) >= 4
                    ? 'text-white font-semibold'
                    : (cellMap.get(`${u}${UNIT_SEP}${cat}`)?.count ?? 0) >= 2
                      ? 'text-gray-800'
                      : 'text-gray-400'
                "
              >
                {{ cellMap.get(`${u}${UNIT_SEP}${cat}`)?.count ?? 0 }}
              </span>
            </div>
          </template>
          <div class="flex items-center justify-end pr-2 text-right tabular-nums text-gray-600">
            <span
              :class="
                unitCount(u) >= 5
                  ? 'text-[#FF4D4F] font-semibold'
                  : unitCount(u) >= 2
                    ? 'text-[#FA8C16]'
                    : 'text-gray-500'
              "
            >
              {{ unitCount(u) }}
            </span>
          </div>
        </template>
      </div>

      <!-- 合计底行 -->
      <div
        class="grid flex-none min-h-0 items-center gap-1 border-t border-dashed border-[#E4E7ED] pt-1 text-[10.5px] font-medium text-gray-500"
        :style="{
          gridTemplateColumns: `100px repeat(${categories.length}, minmax(0, 1fr)) 44px`,
        }"
      >
        <div class="truncate">合计 ←</div>
        <div
          v-for="cat in categories"
          :key="`cc-${cat}`"
          class="tabular-nums text-center"
        >
          <span
            :class="
              catCount(cat) >= 10
                ? 'text-[#FF4D4F]'
                : catCount(cat) >= 4
                  ? 'text-[#FA8C16]'
                  : 'text-gray-500'
            "
          >
            {{ catCount(cat) }}
          </span>
        </div>
        <div class="pr-1 text-right tabular-nums text-[#1F4E79]">
          {{ totalBad }}
        </div>
      </div>
    </div>

    <!-- 空态 -->
    <div
      v-else
      class="flex flex-1 items-center justify-center text-xs text-gray-300"
    >
      近窗口暂无装置/单元异常分布
    </div>
  </div>
</template>
