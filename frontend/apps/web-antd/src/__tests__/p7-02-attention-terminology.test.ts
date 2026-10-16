/**
 * P7-02 D-3：系统建议页（/monitor/attention）页内术语同源测试
 *
 * P6-H 更名「关注队列」→「系统建议」只覆盖菜单/页签/路由 title，页内
 * 残留旧称（E2E 报告 §6 D-3：h1 工具栏标题/导出文件名/导出成功文案/
 * 帮助弹窗标题）。本测试锁定修复后口径：
 *
 * - 可见文案（h1/导出文件名/导出成功与加载失败 message/帮助标题）均为
 *   「系统建议」；
 * - 文件内旧称「关注队列」仅允许出现在注释的更名溯源记录中（一行），
 *   其余零命中。
 */
import { describe, expect, it } from 'vitest';

import src from '../views/monitor/attention.vue?raw';

describe('P7-02 D-3 系统建议页术语同源', () => {
  it('h1 工具栏标题为「系统建议」', () => {
    expect(src).toContain('<ClpmPageToolbar title="系统建议"');
  });

  it('导出文件名与成功/失败文案使用新称', () => {
    expect(src).toContain('`系统建议_${stamp}.csv`');
    expect(src).toContain('导出全量系统建议');
    expect(src).toContain('加载系统建议失败');
  });

  it('帮助弹窗标题为「系统建议 帮助」', () => {
    expect(src).toContain("title: '系统建议 帮助'");
  });

  it('旧称「关注队列」仅存于更名溯源注释（可见文案零命中）', () => {
    const hits = src.split('\n').filter((line) => line.includes('关注队列'));
    expect(hits).toHaveLength(1);
    expect(hits[0]).toMatch(/^\s*\*\s*系统建议（列表页标杆/);
  });
});
