/**
 * 工作台“不得造数”守护（整改 G40）。
 *
 * 背景：views/workbench 的图表曾用主序列做算术派生，伪造出“上一周期”
 * “催化裂化”等序列，图例还硬编码过具体数值（催化裂化（82.1））。渲染点是
 * **默认落地页的第一个 Tab**，而用户会据此决定是否介入——对以“可信数据”
 * 为护城河的产品，这是不可接受的。
 *
 * 说明：本文件是**源码级**守护（扫描禁用模式），不是渲染级断言。它可以防止
 * 同一反模式回归，但无法证明渲染结果正确；渲染级守护需挂载组件断言实际
 * path 数量，属后续补强项（已登记整改方案）。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

const WORKBENCH_DIR = join(__dirname, '../views/workbench');

/** 已知的造数表达式片段（主序列算术派生）。 */
const FORBIDDEN = [
  'p.v - 1.2',
  'p.v - 2.1',
  '催化裂化（82.1）',
];

function collectVueFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectVueFiles(full));
    else if (entry.name.endsWith('.vue')) out.push(full);
  }
  return out;
}

describe('工作台不得造数（G40）', () => {
  it('不得存在主序列算术派生或硬编码图例数值', () => {
    const offenders: string[] = [];
    for (const file of collectVueFiles(WORKBENCH_DIR)) {
      const src = readFileSync(file, 'utf8');
      for (const bad of FORBIDDEN) {
        if (src.includes(bad)) offenders.push(`${file}: ${bad}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
