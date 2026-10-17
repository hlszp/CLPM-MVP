# Slice N 任务书：P6-03/04-c PL 对账与四岗位走查（验证收口）

Owner：子代理N ｜ 工作树 `/Users/zhangping/DEV/clpm-p6-kcode-20261014`（只在此树）｜ 性质：**验证为主、只闭真实缺口**

## 必读（按节）
1. `执行状态/P6-01a-契约与文件Owner-20261014.md` **§8 PL-01/PL-02/PL-06 行 + A15/A16/A19/A20 行**
2. `03-阶段任务计划.md` §9 P6-04 验收 G6 段（四岗位走查口径：无合格岗位参与者只称代理走查）
3. 已交付消费链：02b use-drill 构造点、L 的 coverage strip/F 参考块、M 的模板维护页、G 的 my-work、wb360 双宿主
4. 五批验收报告 `P6五批-非作者验收报告.md`（已验点位不重复）

## 范围（独占）
- `F/__tests__/p6-pl-reconciliation.test.ts`（新建）+ 必要的**小修**（先报主协调确认缺口再改，改动限验证发现的真实缺口对应文件）
- **禁止**：为制造改动而改；改评分算子/后端；commit/push

## 工作项
1. **PL-01/02 对账（单测+浏览器）**：装置→单元→回路稳定 scope 链路（EvalRankTable 行→节点回路清单→wb360）；精确非整日/跨日窗+时区+指标+profile/证据往返（发送→消费→回显→单项清除→返回恢复）；等长比较口径
2. **PL-06 对账（单测）**：页面（pid-dashboard）/驾驶舱/报告导出（reports/performance）同范围同窗同版本数字一致性——构造同一 mock 数据集三端渲染断言
3. **四岗位代理走查（浏览器，如实称代理走查）**：ADMIN（管理+全导航可达）/IC 或 PE（我的工作→深链 wb360→返回恢复）/EXPERT（整定只读链）/SPONSOR（只读全可达无死链、无可执行入口、降级纯文本）；种子账号见主目录 README.md（admin/ic/pe/expert/sponsor 五个）；G5 后端环境分层如实记录（无 P6 API 点位记"环境缺省"）
4. **A19 容量前台口径核对**：1000 回路下我的工作任务列/装置排行的分页/筛选/计数完整性文案（不实测容量，核前端口径与后端分页契约一致）

## 输出
- 单测：三端对账/scope 链/往返恢复 vitest 全绿
- 走查证据：截图+路径记录存 `/tmp/p6-sliceN-evidence/`；报告写 `执行状态/P6-任务状态/SliceN-PL对账走查-报告.md`：PL-01/02/06、A15/16/19/20 逐项（证据/未验原因）、真实缺口清单（含修复建议与 Owner 建议）
- 发现真实缺口先报主协调裁决再动；零缺口如实报
- 完成后任务书末尾写「## 移交记录」标"待验收"。水位>50% 即 WIP 停手写断点。
## 移交记录

**状态：待验收**（2026-10-14 子代理N 完成，等主协调非作者验收）

- **交付**：`F/__tests__/p6-pl-reconciliation.test.ts`（新建，15 例：PL-01 链 4 + PL-02 往返/清除/等长 7 + PL-06 三端对账 4 含 G-N1 登记）；报告 `执行状态/P6-任务状态/SliceN-PL对账走查-报告.md`；证据 `/tmp/p6-sliceN-evidence/`（probe.mjs+probe2/3/8+probe4–7、probe*-report.json、api-diagnosis.txt、vitest-run.txt、eslint.txt、28 截图，可复跑）。
- **复跑**：vitest 15/15 exit 0；eslint 0；check:type 0；关联 6 套件 97/97 exit 0。
- **真实缺口 3 项（先报裁决未修，零代码改动除新测试）**：
  - **G-N2（严重建议优先）**：装置排行→节点回路清单 500——行 id=int source_node_id 与 ranking 端点 plantNodeId=plant_node.id UUID 空间不一致（02b 新链引入；API+浏览器双证）。修复：前端经 nodeTree 解析 UUID 下推（主）+ 后端非 UUID 输入 400（辅）。Owner：前端 02b + 后端 performance。
  - **G-N3（一般，预存后端）**：参评 N>M——workbench_assessment plant 行 loop_count（回路·小时累计）/total_loops（台账计数 CAL-06）口径矛盾（浏览器「1763/1030」「合计 10312/1030」实证）。Owner：后端 P1-02。
  - **G-N1（一般，预存 66419bbd）**：驾驶舱指标清单 pct() ×100 显示 '8100.00%'（率字段实 0–100）。修复：pct 不乘 100。Owner：前端驾驶舱/02b。
- **观察 3 项**：O-N1 /me/* 404 经全局拦截器 toast（环境缺省，P6 后端就绪复验）；O-N2 indicator-analysis 最差 100 条样本均值无 UI 口径提示（预存建议级）；O-N3 SPONSOR 预存模块页动作按钮未灰置（点击 POST 403 服务端零写红线有效，前端灰置预存欠账）。
- **环境分层如实**：15666=工作树全 P6 前端；17101=G5 树无 P6 后端——/me/work 404、page:my-work 缺码（IC/EXPERT 落地 /my-work 呈 404）、整定四身份字段「—」，均记环境缺省未伪造。
- **走查性质**：四岗位全部**代理走查**（admin/ic_engineer/expert/sponsor 种子账号），无合格岗位参与者，不称用户验收。
- **注**：本任务书正本在主目录（只读），本移交记录写于工作树镜像副本，请主协调收编时归并正本。
