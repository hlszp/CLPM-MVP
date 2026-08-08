#!/usr/bin/env node
/**
 * hex 硬编码白名单守护（整改 D3 / 审查 SYS-P2-02）
 *
 * 规则：
 * 1. WHITELIST（颜色定义型文件：token/色板/图表预设）内允许 hex；
 * 2. 其余文件按 GRANDFATHER 记录的基线计数棘轮管控——只减不增；
 * 3. 非白名单且不在 GRANDFATHER 中的文件出现 hex → 直接失败（新增债务拦截）。
 *
 * 用法：node scripts/check-hex-whitelist.mjs [--update-baseline]
 *   --update-baseline  将当前非白名单文件的 hex 计数写回 GRANDFATHER（收敛后更新棘轮）
 */
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = new URL('../frontend/apps/web-antd/src', import.meta.url).pathname;

/** 颜色定义型文件：hex 是它们的职责，不参与计数 */
const WHITELIST = new Set([
  'styles/industrial-light.css',
  'preferences.ts',
  'composables/use-clpm-theme.ts',
  'composables/use-loop-palettes.ts',
  'composables/use-echarts-preset.ts',
]);

/** 颜色定义型目录（constants 下全部为语义常量表） */
const WHITELIST_DIRS = ['constants/'];

/** 棘轮基线：非白名单文件允许的最大 hex 数（2026-08-09 首版，只减不增） */
const BASELINE_FILE = new URL('./hex-baseline.json', import.meta.url).pathname;

const HEX_RE = /#[0-9a-fA-F]{3,8}\b/g;
const EXT_RE = /\.(vue|ts|css)$/;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__') continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (EXT_RE.test(name)) out.push(p);
  }
  return out;
}

const isWhitelisted = (rel) =>
  WHITELIST.has(rel) || WHITELIST_DIRS.some((d) => rel.startsWith(d));

const counts = {};
for (const file of walk(SRC)) {
  const rel = relative(SRC, file);
  if (isWhitelisted(rel)) continue;
  const text = readFileSync(file, 'utf8');
  // 忽略注释行中的 hex（文档性引用不算样式债）
  const n = (text.match(HEX_RE) || []).length;
  if (n > 0) counts[rel] = n;
}

if (process.argv.includes('--update-baseline')) {
  writeFileSync(BASELINE_FILE, `${JSON.stringify(counts, null, 2)}\n`);
  console.log(`baseline updated: ${Object.keys(counts).length} files`);
  process.exit(0);
}

let baseline = {};
try {
  baseline = JSON.parse(readFileSync(BASELINE_FILE, 'utf8'));
} catch {
  console.error('hex-baseline.json 缺失，先运行 --update-baseline');
  process.exit(2);
}

let fail = 0;
for (const [file, n] of Object.entries(counts)) {
  const cap = baseline[file];
  if (cap === undefined) {
    console.error(`✗ 新增 hex 债务：${file}（${n} 处，不在白名单/基线中）`);
    fail++;
  } else if (n > cap) {
    console.error(`✗ hex 棘轮超限：${file}（${n} > 基线 ${cap}，只减不增）`);
    fail++;
  }
}
const total = Object.values(counts).reduce((s, n) => s + n, 0);
const baseTotal = Object.values(baseline).reduce((s, n) => s + n, 0);
console.log(
  `hex 债务现状：${total} 处 / ${Object.keys(counts).length} 文件（基线 ${baseTotal}，收敛 ${baseTotal - total}）`,
);
process.exit(fail > 0 ? 1 : 0);
