# CLPM Prototype Responsive & Accessibility Spec

日期：2026-06-16
版本：v0.1
用途：定义原型的响应式和可访问性最低要求。

## Responsive rules

| 视口 | 断点建议 | 规则 |
|---|---:|---|
| Desktop | `>=1280px` | 工程首页与单回路证据链用三栏布局 |
| Tablet | `768px - 1279px` | 左侧清单独占上方，证据与动作区上下堆叠 |
| Mobile | `<768px` | 顺序重排：摘要 -> 列表 -> 证据 -> 动作，导航折叠 |

## Page-specific layout rules

| 页面 | Desktop | Tablet | Mobile |
|---|---|---|---|
| 工程首页 | 三栏工作台 | 清单上、证据中、动作下 | 卡片顺序化，但保留主次 |
| 管理首页 | 左右双栏 | 上下双层 | 摘要先、导出后 |
| 单回路证据链 | 趋势 + 规则/事件 + 动作侧栏 | 趋势在上，规则/动作在下 | 趋势摘要 + 可展开证据区 |
| 低效排行 | 大表格 + 侧栏 | 表格优先，侧栏抽屉化 | 列表化摘要 |
| 证据包 | 摘要 + manifest 双区 | 摘要在上 manifest 在下 | 摘要优先，manifest 折叠 |

## Accessibility rules

| 领域 | 规则 |
|---|---|
| Keyboard | 审核、实施、复评流必须可 Tab 完整操作 |
| Touch target | 可点击项最小 `44px` |
| Contrast | 正文对比度至少 `4.5:1`，状态标签不能只靠颜色区分 |
| Screen reader | 每个页面必须有主 landmark；导航、清单、证据、动作区有明确 section label |
| Tables | 表头清晰，可读行标题 |
| Charts | 每张图都有文字摘要，不依赖图形单独表达结论 |
| Focus | 焦点态必须清晰可见 |
| Error state | 错误信息可被朗读，且提示下一步动作 |
