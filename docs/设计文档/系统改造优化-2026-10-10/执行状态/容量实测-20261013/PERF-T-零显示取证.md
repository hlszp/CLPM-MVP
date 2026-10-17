# PERF-T 回路墙"0 显示"问题取证登记（2026-10-13，γ 批）

> 结论先行：非产品缺陷，是**方案 α（合成未来窗数据集）的已知读侧代价**。机制两层：①历史形态（批 1 评分落库前）"1000 个 0/—"显示=当日无可用评分快照；②持续形态=「昨日基线」过滤器与合成窗结构性互斥 → PERF-T 全集 scoreDelta 恒 NULL（劣化排序/恶化徽标/恶化计数对合成集失效）。登记后不修（γ 批为测量批），真实回路不受影响。

## 一、现象

- 前任 18:5x 浏览器观察（浏览器三件.json.raw scopeNote）：回路墙 PERF-T 1000 回路 0 显示。
- 续作 19:0x 复核（API 直查 PERF-T-FACTORY scope，node=bcd11d75）：
  - `sortBy=score&sortOrder=desc`：PERF-T0071 score=65.73（ts_end=21:00）、PERF-T0610=64.39…… **批 1 的 850 个正分已正常上墙**；
  - 默认排序（升序）首页：PERF-T0950 等 score=0——是真实的 50 个零分回路（SUCCESS 且 score=0.00）排在前面，非显示错误；
  - 全集 1000：850 正分 + 50 零分（SUCCESS）+ 100 NULL（INCONCLUSIVE，显示"—/待评估"）。

## 二、机制（代码证据）

1. **wll 预计算本体正确**：`workbench_loop_latest` 实测 PERF-T 行 score=56.12/58.98/…（snapshot_ts=2026-10-13 20:00 UTC 窗），`_LATEST_SNAPSHOT_SQL` 取每回路最新快照**无日期过滤**（`app/services/workbench_loop_latest.py` L50-59）——评分面不受"今日"概念影响。
2. **「昨日基线」过滤器与合成窗结构性互斥**（L63-71）：
   ```sql
   -- _PREV_DAY_SNAPSHOT_SQL
   WHERE s.ts_end < date_trunc('day', now())
   ```
   PERF-T 全部快照 ts_end 均 ≥ 今日 0 点（04:00~09:00 残留窗与 20:00+ 合成窗全在"今日"内，合成窗相对墙钟甚至在未来）→ prev_score 恒 NULL → `compute_score_delta` 返回 None（L74-83）。同一过滤口径在 `app/services/monitor.py` L501/L1199（列表 scoreDelta）与 wll L69 双处一致。
3. **读侧消费面后果**：
   - `/loops/monitor` 列表项 scoreDelta 全 NULL（API 实测 delta=null）；
   - 劣化降序排序（sortBy=scoreDelta）对 PERF-T 集退化为无差别；
   - 单元/总览聚合 worsenedCount（scoreDelta ≤ -2 判 WORSENED）对 PERF-T 恒 0；
   - 前任观察到的"1000 个 0 显示"= 批 1 落库前（当时 PERF-T 当日仅有 04:00~09:00 窗 INCONCLUSIVE NULL 快照）列表 score 显示 0/— 的形态，评分落库后已消解为①所述正常分档。

## 三、定性

- **方案 α 读侧代价**（已知登记）：合成窗全在今日/未来 → "昨日基线"语义对合成集恒为空。真实回路（历史数据在今日 0 点前）不受影响。
- γ 批 SLO/屏障测量不受影响（屏障=九条件 DB 回查，不消费 scoreDelta）。
- 处置建议（不在 γ 批范围）：如需合成集参与劣化排序演示，需另造"今日 0 点前"的历史合成窗（额外的数据生成与窗位规划），或接受该读侧面在合成环境下的语义空缺——转 G5 汇总"方案 α 未验项/代价清单"一并呈报。

## 四、关联 UI 发现（同场登记）

- 回路墙树节点点击选中不稳定（点击"EO 工厂"文本未切换 node）——交互层问题，与 wll 无关，γ 后处置。
