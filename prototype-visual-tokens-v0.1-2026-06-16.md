# CLPM Prototype Visual Tokens

日期：2026-06-16
版本：v0.1
用途：定义原型系统最小视觉基线。

## Typography

| Token | 建议值 | 用途 |
|---|---|---|
| `--font-sans` | `"Inter", "Noto Sans SC", sans-serif` | 通用界面文字 |
| `--font-mono` | `"JetBrains Mono", "SFMono-Regular", monospace` | ID、版本、代码型信息 |
| `--text-hero` | `32px / 700` | 顶层页标题 |
| `--text-h1` | `24px / 700` | 一级标题 |
| `--text-h2` | `18px / 600` | 分区标题 |
| `--text-body` | `14px / 400` | 正文 |
| `--text-small` | `12px / 400` | 辅助说明 |

## Color system

| Token | 建议值 | 用途 |
|---|---|---|
| `--bg-page` | `#F6F7F9` | 页面背景 |
| `--bg-panel` | `#FFFFFF` | 面板背景 |
| `--bg-muted` | `#EEF1F4` | 次级背景 |
| `--text-primary` | `#1F2937` | 主文本 |
| `--text-secondary` | `#4B5563` | 次文本 |
| `--border-default` | `#D5DAE1` | 默认边框 |
| `--accent-blue` | `#2563EB` | 主操作高亮 |
| `--status-ok` | `#0F766E` | 可评估 / 正常 |
| `--status-info` | `#0369A1` | 可诊断 / 信息 |
| `--status-warning` | `#B45309` | 需现场核实 / 部分可用 |
| `--status-danger` | `#B91C1C` | 数据不足 / 高风险 |
| `--status-neutral` | `#6B7280` | 不可判定 |

## Spacing

| Token | 值 |
|---|---:|
| `--space-1` | `4px` |
| `--space-2` | `8px` |
| `--space-3` | `12px` |
| `--space-4` | `16px` |
| `--space-5` | `24px` |
| `--space-6` | `32px` |

## Radius and shadow

| Token | 值 | 说明 |
|---|---|---|
| `--radius-sm` | `6px` | 小标签、按钮 |
| `--radius-md` | `10px` | 面板 |
| `--shadow-soft` | `0 1px 3px rgba(15,23,42,0.08)` | 轻阴影，不追求 SaaS 卡片感 |

## Chart rules

- 趋势图主线优先：PV、SP、OP 颜色稳定，不用彩虹色。
- MODE 带用浅色条，不抢趋势图主线。
- 雷达或分组图只表达状态，不做装饰性渐变。
- 所有图表旁都配文字摘要。
