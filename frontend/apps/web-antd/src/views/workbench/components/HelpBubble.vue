<script setup lang="ts">
/**
 * 区域标题栏帮助图符 · V3.3 通用说明弹窗
 *
 * 用法：
 *   <HelpBubble title="行动区说明" :items="helpItems" />
 *   helpItems = [
 *     { text: '清单点击行 → 右侧趋势联动' },
 *     { label: '整定仿真', text: '点「整定仿真」按钮弹出整定工作台，参数由授权人员线下人工实施并留痕' },
 *     { label: '灰行', text: '前置工单未闭合的回路，整定入口禁用' },
 *   ]
 *
 * 触发：圆形 ? 图符，hover 蓝色高亮，click 弹 Modal.info 显示多段说明
 */
import { h } from 'vue';

import { Modal } from 'ant-design-vue';

interface HelpItem {
  /** 段落小标题（可选，粗体显示） */
  label?: string;
  /** 段落正文 */
  text: string;
}
interface Props {
  /** 弹窗标题 */
  title: string;
  /** 多段说明 */
  items: HelpItem[];
  /** 图符大小 px（默认 12） */
  size?: number;
  /** 图符颜色主题：white=深蓝底栏用 / blue=浅色卡头栏用 */
  theme?: 'blue' | 'white';
}
const props = withDefaults(defineProps<Props>(), {
  size: 12,
  theme: 'blue',
});

function openHelp() {
  Modal.info({
    title: props.title,
    width: 460,
    okText: '我知道了',
    content: h(
      'div',
      { style: 'font-size: 12px; line-height: 1.7; color: #595959; padding-top: 8px;' },
      props.items.map((item, idx) =>
        h('div', { key: idx, style: 'margin-bottom: 8px;' }, [
          item.label
            ? h(
                'span',
                { style: 'font-weight: 600; color: #1F4E79; margin-right: 6px;' },
                `■ ${item.label}`,
              )
            : null,
          h('span', { style: 'color: #595959;' }, item.text),
        ]),
      ),
    ),
  });
}
</script>

<template>
  <button
    type="button"
    class="flex flex-none items-center justify-center rounded-full border text-[10px] font-bold leading-none transition-colors"
    :class="theme === 'white'
      ? 'border-white/40 text-white/70 hover:border-white hover:text-white'
      : 'border-[#1F4E79]/40 text-[#1F4E79]/70 hover:border-[#1F4E79] hover:text-[#1F4E79] hover:bg-[#F0F7FF]'"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :title="`查看${title}`"
    @click.stop="openHelp"
  >?</button>
</template>
