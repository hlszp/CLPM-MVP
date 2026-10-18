from pathlib import Path
import datetime,json
E=Path(__file__).parent;S=E.parent.parent
assert S.name=='执行状态'
assert json.loads((E/'verification/backend-full-v3.json').read_text())['exit_code']==0
assert json.loads((E/'verification/frontend-full-rowkey-final.json').read_text())['exit_code']==0
assert json.loads((E/'f07/phase2/scratch-cleanup.json').read_text())['database_dropped']
def text(s):return s.replace('@E@',str(E)).replace('@S@',str(S))
(S/'终局整改-整改后复验报告.md').write_text(text('''# 终局整改 · 整改后复验报告

> **本轮候选代码与文档整改可在下列已验范围接收；执行包整体仍为 BLOCKED，不能宣称八项全量关闭。**
> F01/F04/F05/F06/F07/F08 在明确范围内 PASS；F02 候选正确性 PASS、真实容量 BLOCKED；F03 执行器/静态契约 PASS、真实业务与远端 CI BLOCKED。本轮没有提交、合入、推送或部署，四项用户豁免和 G6/G7=CONDITIONAL_PASS 保留。
> 日期：2026-10-17（Asia/Shanghai）。授权依据：用户“直接帮我根据这次复验情况，进行整改，整改后复验”。

## 1. 对象、角色与实际补丁

| 对象 | 固定身份 / 范围 |
|---|---|
| 原审计基线 | 86301e5e3083db0feb5f2d9db8fb9eacc8d3165d |
| 文档工作树 | main@b676ea59158aa4d1e106579017a85860add60293 加本轮文档/证据修改 |
| 整改代码 | /Users/zhangping/DEV/clpm-remediation-20261017，fix/final-audit-remediation@dbb1de314ae50e7373c6559ab746b690909bf1b0 加未提交补丁 |
| 实际补丁 SHA-256 | c5baf0fd18a4b109e221fded2c131fdb4be097a7671a87fb3695762b70843fc4 |
| 改动清单 | 36 文件：23 修改、13 新增；[source-manifest.json](@E@/candidate/source-manifest.json)逐文件大小/SHA、基点与patch身份；[candidate.patch](@E@/candidate/candidate.patch) |
| 可恢复性 | 从基点取原文件，在新scratch执行git apply --check/apply，36/36内容哈希匹配，临时目录已清理：[patch-restore-check.json](@E@/candidate/patch-restore-check.json) |
| 共享运行环境 | 后端运行树c9e01f5d、前端主树、schema p702perm01；不包含本轮候选改动 |

业务实现分别由 f02_rootcause、f03_gate、f04_restore 承担；F02/F03由F04作者交叉复验，F04由F02作者交叉复验。主协调只编写文档/证据工具并独立执行F01及真实浏览器验收，未编写业务实现。F05/F06/F08文档与归档由f03_gate非作者核验，终版另作快审；不能把作者自验升格为独立验收。

[原审计](@S@/终局对标检查报告.md)和[上一轮非作者回执](@S@/终局整改-非作者复验回执.md)保持原文；本报告是后续整改接续，不改写此前REWORK结论发生时的事实。

## 2. F01–F08 逐项结论

| 项 | 本轮处理与独立实测 | 判定与剩余边界 | 精确证据入口 |
|---|---|---|---|
| F01/P1 | 候选恢复D01预载/缓存及取消分类。重新执行19例；撤销_classify_node_results后指定用例rc4失败，恢复后3 passed；回装旧取消分类1 failed，原字节完全恢复。最终后端全量6909/0。 | **PASS（候选）**。原漏合入交付缺口仍开放，本轮未合入/部署。 | [19例/正反向真实rc](@E@/f01/f01-run-results.json)、[恢复哈希](@E@/f01/f01-mutation-restoration.json) |
| F02/P1 | 保留历史6 PASS+3 TERMINAL_MISMATCH；24/24不可变记录原错误为TD Query memory exhausted，撤回“仅数据覆盖、非逻辑问题”。修异常诚实传播、PG/TD有限重试、兄弟IO收束、少量资源错误仍阻断父SUCCESS。非作者383+5正向、三组2/2/6失败反向；恢复31通过。v3旧结构断言改行为测试，独立10例+7错误签名探针通过。 | **候选正确性PASS；整体BLOCKED**。共享TD仍可资源失败，真实候选standard-hourly完整容量屏障未执行。 | [v2非作者回执及原始命令](@E@/f02/non-author/receipt.md)、[历史核验/24记录/44日志片段](@E@/f02/non-author/historical-independent-check.json)、[v3非作者回执](@E@/f02/non-author-v3/receipt.md) |
| F03/P1 | 必需4个case ID按真实Playwright结构化结果校验，skip/fixme/only/预期失败/重试/flaky/错分母拒绿；补e2e独立依赖与CI三服务/合成输入前置。独立实际门禁8/8、另4类文件标记与11种JSON变体通过；环境不可达rc2、注入失败rc1、跳过rc3。E2E continue-on-error已移除。 | **执行器/静态契约PASS；整体BLOCKED**。真实业务AUTH/PERM/CALC/EVIDENCE四例、远端CI尚未运行；健康桩和契约4/4不能冒充业务通过。 | [非作者回执](@E@/f03/non-author/receipt.md)、[真实门禁命令](@E@/f03/non-author/contract.command.json)、[结构化变体](@E@/f03/non-author/structured-reports-results.json) |
| F04/P2 | 新增同事务真实快照/恢复，稳定UID、当前权限/scope、幂等ID、行锁、ABA及后改冲突保护，保留NULL/DCS/模板/pin。非作者73真实HTTP/49项API-PG判据通过，1000条×5批200精确恢复；选择旧groups反例红→绿。真实UI零关联模板、251条六页/200+51预览及执行、恢复NULL、换绑清pin/恢复pin、历史选择失效与同回路多快照冲突均成立。补稳定snapshotId:loopId行键。 | **PASS（候选+真实自有隔离环境）**。不是共享dev/生产部署验收；快照受既有审计保留期约束，不承诺永久恢复。 | [非作者API/PG回执](@E@/f04/non-author/receipt.md)、[1000原始结果](@E@/f04/non-author/api-pg-results.json)、[显式解除/follow](@E@/f04/non-author/explicit-template-api-results.json)、[独立UI回执](@E@/f04/ui-receipt.md) |
| F05/P2 | P7/看板/台账/遗留按六态接续；四豁免各行保留事实、Owner、输入和恢复标准；CP61/62/71/72接续、纠偏矩阵失效标记及原审计均保留。旧文档7份保全，不再作为当前总结宣称全绿/全部署。 | **PASS（文档一致性）**。历史错误原句在明确历史标记下保留，不改写审计。 | [非作者文档回执](@E@/docs-non-author/receipt.md)、[旧件备份](@E@/f05/before-documents-manifest.json)、[终版快审](@E@/docs-final-review/receipt.md) |
| F06/P2 | 分开审计/文档/候选/运行身份；实证1个Worker主进程+4池子，撤回无PPID支撑的“四独立Worker异常”。DB p702perm01；27条sys_config以内容+updatedAt哈希代替不存在的全局revision；非作者独立只读复核一致。 | **PASS（观测事实与声明）**。磁盘/cwd不能证明进程内存SHA；未查询远端，不声称已推送；未操作共享服务。 | [runtime-facts.json](@E@/f06/runtime-facts.json)、[运行台账](@S@/终局整改-运行来源台账.md)、[非作者回执](@E@/docs-non-author/receipt.md) |
| F07/P2 | 实际UI先复现换号缓存串号；补owner/generation与认证生命周期屏障。又复现真实RequestClient解包错误导致409无法收敛，补兼容真实错误体及14例边界。最终真实SPA换号、跨页/刷新、409→200、断网乐观回滚、未登录401/越scope403通过。 | **PASS（指定真实隔离运行链）**。未扩大为IAR全量/四岗位走查；未部署。 | [独立UI回执](@E@/f07/ui-receipt.md)、[真实409修复后请求](@E@/f07/cli/51-fixed-real-409.json)、[最终SPA换号](@E@/f07/cli/53-final-spa-account-boundary.json)、[安全边界](@E@/f07/real-account-security-final.json) |
| F08/P2 | 原manifest源24项内容/hash保留；三份大原件已树外持久归档，主协调与非作者均从tar完整恢复3/3且大小/SHA一致。Owner/位置/保留边界、遗留索引已登记。 | **PASS（本机持久保全）**。无异机/云副本，不宣称灾备；新证据总清单单独生成。 | [archive-manifest](@E@/f08/archive-manifest.json)、[非作者3/3恢复](@E@/docs-non-author/receipt.md)、[源manifest](@S@/终局整改-证据/evidence-manifest.json)、[本轮清单](@E@/evidence-manifest.json) |

## 3. 最终回归分母与门禁

| 检查 | 实测 | 原始命令/输出/退出码 |
|---|---|---|
| 后端collect-only | **7265/7431 collected，166 deselected，rc0** | [命令与SHA](@E@/verification/backend-collect-final-v3.json)、[stdout](@E@/verification/backend-collect-final-v3.stdout.log) |
| 后端全量 | **6909 passed / 0 failed / 340 skipped / 166 deselected / 12 xfailed / 4 xpassed；rc0** | [命令](@E@/verification/backend-full-v3.json)、[原始输出](@E@/verification/backend-full-v3.stdout.log)、[JUnit](@E@/verification/backend-full-v3.junit.xml) |
| 前端workspace全量 | **106文件、1021 passed / 0 failed / 0 skipped；rc0** | [命令/前后源码身份](@E@/verification/frontend-full-rowkey-final.json)、[JSON实际分母](@E@/verification/frontend-full-rowkey-final.results.json) |
| 后端ruff/format | 均rc0，920文件格式符合 | [check](@E@/verification/ruff-check-v3.json)、[format](@E@/verification/ruff-format-v3.json) |
| 前端eslint/type/build | 全部rc0；eslint保留16项既有warning | [eslint](@E@/verification/frontend-eslint-rowkey-final.json)、[type](@E@/verification/frontend-typecheck-rowkey-final.json)、[build](@E@/verification/frontend-build-rowkey-final.json) |
| 私有scratch schema check | alembic check rc0，No new upgrade operations detected | [记录](@E@/verification/alembic-check.json)、[stdout](@E@/verification/alembic-check.stdout.log) |

7431 = 6909+340+166+12+4，算术闭合。全量运行与collect使用同一后端源码身份，最终前端四项检查亦各自前后稳定。原审计的7362/历史6845，以及dbb上一轮7381/6859属于各自固定源码；不能混写为本轮实测。340 skipped、166 deselected及4个非strict xpass均明确保留，本轮定向PG测试不替代所有既有真实环境测试。原始数值告警/DLASCL输出不删改。

本轮首次全量的6908 passed/1 failed来自test_backfill_recovery_and_skip仍inspect已抽走的inline字符串；改为PG原始/包装/大小写及未知错误不误判的行为断言，经非作者10+7复核，再跑上述全量通过。初次失败原件[backend-full-final](@E@/verification/backend-full-final.json)保留，未改业务代码迎合旧字符串。1020→1021的最后增量为跨snapshot稳定行键测试，最终全量已在新增修复后重跑。

## 4. 新发现与反证闭环

- **F02并非仅数据覆盖问题**：24条原始不可变记录均带TD资源错误；数据仍可不足，但本批不能据此定性。当前串行96点查询仍有6次资源错误，不能无证据把并发定为唯一根因。batch4节点在报告采样后约0.352s到齐，不能倒改原TERMINAL_MISMATCH。
- **少量资源失败也不能父SUCCESS**：新门槛已独立以1/1000及standard/custom父链反向检出；一般G29业务错误阈值不变。
- **F04改选仍用旧快照**：同一个独立组件反例旧版1 failed，修后1 passed；真实UI第26步已证改选后明细清零、预览禁用。恢复后来编辑/ABA对象明确冲突，不反向覆盖。
- **F07跨账户缓存污染**：原数据库ic=1、peer=0而peer星标错误的反证保留；owner/generation修后同SPA换号实证通过。旧读写、409重试及finally/busy不能越账户污染。
- **真实409错误形状不符mock**：旧提取器只认Axios response，真实RequestClient抛解包body。14例中旧码5失败，修后通过；真实UI请求expectedRevision 0遇409/currentRevision2后，以2重试200/revision3。
- **F04结果行键依赖下标**：真实控制台触发AntD弃用警告，已改稳定snapshotId:loopId；同回路两快照真实预检2行（已恢复1、冲突1）无新增console error，最终前端1021通过。

各反向只在临时副本/临时插件执行；原始失败和恢复哈希留档。UI脚本的定位/字段名失误及开发期热更新打断另在UI回执解释，未把这些rc1删去，也未用它们冒充产品反例。

## 5. 环境、清理与证据完整性

F04/F07使用自有phase1/phase2 scratch DB、专属Redis、API17261/UI17266；CLPM_TEST_MODE=0保留真实DB鉴权和scope，lifespan包装只禁后台进程启动。未mock业务响应、未预写计算结果、未向共享队列投递。测试输入明确为合成夹具，不作为生产/现场校准证据。

自有浏览器已关闭；API/UI进程、Redis和scratch DB均清理。[phase2 cleanup](@E@/f07/phase2/scratch-cleanup.json)保留API超出15秒宽限后以SIGKILL结束的真实exit=-9，只有本轮自有API被处理；Worker/Beat未启停。清理及共享来源最终核对见[final-hygiene.json](@E@/final-hygiene.json)。运行版本、资源/配置与旧共享环境均未冒充新候选。

原审计SHA-256 `517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016`；上一轮回执SHA-256 `54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6`。两者开工前值由本轮交接记录提供，当前又独立重算一致；不声称仅凭当前文件能自证过去。

原24项源manifest只新增持久归档元数据，旧manifest副本保留；新归档SHA `241e671130b9aad1d3f0b1bb3b19befcac185411620f4c711ecf8e315fe2bbf1`。本轮新证据及子回执哈希另由[evidence-manifest.json](@E@/evidence-manifest.json)逐文件登记；不会覆盖原审计证据清单。浏览器原始快照/控制台文件已复制到[browser-artifacts](@E@/browser-artifacts/source-index.json)。

## 6. 保留的四项用户豁免

| 项 | 技术事实 / 裁决 | Owner、最小输入与恢复验收 |
|---|---|---|
| P6-07跨仓 | 未实施/未验；**用户豁免** | 用户+iCLPM Owner；冻结六要素契约→跨仓实现/CLPM适配→指定IAR权限、对象、证据、真实回链及降级 |
| IAR-A01–A28全量 | 已有子集以外未验、A18b后续范围保留；**用户豁免** | 验收Owner；固定候选和逐例前置→逐例矩阵→真实运行时全链/键盘焦点 |
| 真实岗位走查 | 代理不等于四岗位用户；**用户豁免** | 用户提供仪控/DCS/工艺/经理参与者和时间窗→Slice N真实回执 |
| P7-01现场校准 | 静态6/6已验、现场未验；**用户豁免** | 用户+现场工程师提供授权数据/标注→分层校准→独立验证→适用边界 |

G5既有900s有界条件放行、600s缺口、DEC-05、D03撤销和D04抽验另外保留，不把它们当成F02正确性或新四项技术验收。

## 7. 尚待执行的交付标准

| 未完成项 | Owner / 完成判据 |
|---|---|
| 本轮候选交付 | 主协调/Keeper在后续明确交付授权下提交/合入/部署；分别核main、分端实际运行来源和Worker加载事实。本轮没有执行这些动作。 |
| F02真实容量 | Keeper冻结候选运行SHA/Worker唯一归属、schema/config/数据manifest及资源预算，处理TD资源条件；原standard-hourly入口、新升序窗口、全1000分母及完整九条件屏障。不得改expectedStatus、剔失败批或缩分母；保留父子任务/不可变record/latest/节点和真实rc。 |
| F03真实业务/CI | CI Owner对冻结候选实际运行专属三服务job，4个必需ID一次passed、无skip/retry/flaky，保全task→snapshot→record→dataset/config血缘、UI附件、输入SHA和完整远端run。 |
| 既有发布/技术债 | [遗留索引](@S@/终局整改-遗留索引.md)保留发布SQL快照滞后、生产新装、有新数据的生产回退、大文件本机保全边界、stub/报表/死链、G-N3/导航、hourly重算与合成预警机制。 |

**最终判定：本轮已授权候选修复与隔离复验通过；运行交付和F02/F03剩余验收继续BLOCKED。四项豁免不撤销、不冒充已验，也不用于掩盖上述未完成项。**
'''))
(E/'f04/ui-receipt.md').write_text(text('''# F04 真实隔离浏览器独立复验回执

结论：**PASS（本轮候选的指定隔离UI链）**。主协调未编写F04产品代码。API是真实候选FastAPI/私有PostgreSQL，鉴权scope真实消费；测试生命周期不启动Worker/Beat，不向共享环境写业务数据。不是生产/共享dev部署验收。

| 检查 | 实测 / 原始记录 |
|---|---|
| DEF-1零关联 | 第05/06/07步从UI新建模板、发布v1，影响0；第08步仍可选择全部251条合成输入回路。 |
| 六页跨选/>200 | 第10与最终第13步选251条，预览按200+51分批；预览后只读PG全251条仍NULL。 |
| 确认应用 | 第13步实际请求4次=2预览+2确认，200/51完整分母，成功251、失败0；PG实际目标模板关联251。 |
| 恢复NULL | 第15预览2批仍关联251；第16确认2批，恢复251/0；PG六字段逐条与基线完全相同，第17刷新UI关联计数0。 |
| pin及换绑 | 第20 UI固定A@v1成功；第21实际UI换B follow，页面第22回显pin1→NULL；第24从真实服务端快照读取before=A pin1/after=B follow，预览及确认恢复A pin1。PG与第26 UI计数A=1、B=0一致。 |
| 历史入口/选择失效 | 第26刷新重新从本人历史进入，先读B、改选A，完整明细归零、预览禁用，必须重新读取。对应独立组件反例旧红1→新绿1。 |
| 后改/ABA/已恢复及稳定键 | 第27选择同一回路的两份历史快照，实际两行：已恢复/无变化1，失败/冲突1；未执行覆盖。新snapshotId:loopId键实际渲染，无新增console error。 |

精确raw：本目录cli/13、15、16、20、22、24、26、27开头JSON；汇总原始结果及路径为[ui-independent-results.json](@E@/f04/ui-independent-results.json)。PG基线/零写入/应用/恢复/换绑/原pin见本目录ui-fixture-baseline.json及ui-pg-*.json。第24完整快照/恢复响应另提取ui-rebind-restore-wire.json，raw CLI不覆盖。[实际页面](@E@/f04/final-ui-restore.png)已人工查看，显示两份同回路快照、完整分母2和已恢复1/冲突1。

机械失败保留：第02把admin默认落地页误写my-work（实际cockpit）；第11在F07源码更新触发开发热重载后找不到已关闭的预览，未发确认写请求，最终第13重做；第21断言误读data.items，真实协议为data.results，产品请求已成功，后以第22实际回显、只读PG和第24服务端快照确认；第23 GET监听路径写窄，只作打开UI证据，真正快照GET由第24补齐。不把这些失败伪装成全部CLI通过。原控制台rowKey弃用警告保留；作者补修后第27定向重验无新error，前端最终1021/0另有原始门禁。

私有测试输入及残留随整库清理；没有用测试输出充当现场校准或完整IAR。API1000实证与49判据由另一非作者独立签署，见non-author/receipt.md，不能用本UI251代替1000容量证明。
'''))
(E/'f07/ui-receipt.md').write_text(text('''# F07 真实隔离运行链独立复验回执

**PASS（本轮指定真实UI/API链）**。主协调未编写F07产品代码；实际候选前端/后端、私有PostgreSQL/Redis、CLPM_TEST_MODE=0，不mock业务响应，不启动Worker/Beat。

1. 原反例：ic关注1条，audit_peer数据库0条；同SPA退出/换号后peer监视星标仍“取消关注”。cli/34-account-isolation-confirm.json真实rc1，pre-fix-account-db.json保留。修owner/generation及认证生命周期后，40/42同SPA换号不串号。
2. 真实409：独立API将peer先关注rev1、取消rev2，UI未刷新仍expectedRevision0。旧码第43超时rc1，第47保全真实409 body。根因是RequestClient抛解包body，旧提取器只认Axios error.response。14例旧码5失败与修后63/63在implementation/409-real-shape保留。
3. 修后第51实际UI写请求409/currentRevision2→以2重试200/revision3，星标收敛。第52从我的关注进入360，跨页与刷新后仍已关注。
4. 第48/49真断网，PUT net::ERR_INTERNET_DISCONNECTED，明确失败提示且乐观星标回滚；第50网络恢复。未伪造服务器返回。
5. 最终第53：peer取消关注后保持同SPA，退出→ic登录→我的关注→360，旧账户星标“关注该回路”，新账户正确“取消关注该回路”；logout/login之间无硬刷新。真实API最终ic=1/peer=0/restricted=0。
6. real-account-security-final.json保全匿名401、越scope写403及各自关注集合；没有跨用户污染或成功假态。

六条实际UI链的原始路径/rc/结果在[ui-independent-results.json](@E@/f07/ui-independent-results.json)；第51完整CAS请求、body和真实HTTP状态在[cli/51-fixed-real-409.json](@E@/f07/cli/51-fixed-real-409.json)。前端最终全量1021通过，完整分母另见根报告。

定位失误：第33查询按钮title不精确，由34真正反证替代；第38将未关注aria误写“关注”（实际“关注该回路”），第40纠正后通过。这些原rc1保留，不作为产品缺陷或独立负例。第43/47则是可复现产品409问题，已明确修复。

本结论不等于四岗位走查、完整IAR或已部署；四项用户豁免保留。自有UI/API/Redis/DB均已清理，真实关闭状态见phase2/scratch-cleanup.json；共享环境没有服务动作。
'''))
# Update final explicit counts in current documents and checkpoint.
p=S/'任务看板.md';s=p.read_text().replace('前端最终分母见整改后复验报告。','前端106文件/1021 passed、0 failed、0 skipped，rc0；最终源码与门禁见整改后复验报告。');p.write_text(s)
p=S/'P7汇总与G7结论.md';s=p.read_text().replace('；F04/F07已通过真实隔离环境的指定定向复验；不扩为全量IAR、生产或运行部署','');p.write_text(s)
p=S/'P6-任务状态/STATE.md';s=p.read_text();s += '\n81. CP-20261017-1820（直接整改复验接续）：候选dbb1de31+36文件未提交补丁；后端7431收集/6909 passed、0 failed，前端1021 passed；F01反向及F02/F03/F04交叉非作者、F04/F07真实隔离UI、F05/F06/F08独立核验通过各自范围。F02真实容量、F03真实业务/远端CI与代码合入部署仍BLOCKED/未执行；四用户豁免、G6/G7=CONDITIONAL_PASS保留，不能宣称八项全量关闭。最终身份与证据见《终局整改-整改后复验报告.md》。\n';p.write_text(s)
p=S/'终局整改-直接整改执行.md';s=p.read_text().replace('执行中 |','已完成对应候选/证据范围，交付边界见终版报告 |').replace('待前置核对 |','真实隔离定向复验通过 |').replace('待实施完成 |','已完成对应非作者复验，未执行范围单列 |').replace('- [ ]','- [x]');s += '\n最终接续：本轮候选修复/隔离复验已完成，剩余运行与远端验收不销项。后端6909/0、前端1021/0；代码36文件未提交补丁SHA和真实退出码见终版报告。\n';p.write_text(s)
print('final report + two UI receipts + current document counts/checkpoint written')
