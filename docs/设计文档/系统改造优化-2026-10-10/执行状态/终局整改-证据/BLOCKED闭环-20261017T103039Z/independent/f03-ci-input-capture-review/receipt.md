# F03 远端PG输入采证补丁非作者局部门

结论：最终五文件补丁局部门 PASS；真实远端采证、数值比对和F03整体仍须新版本CI原件，不由13项纯控扩签。本复验没有实际DB/网络/服务操作，没有读取远端actual KPI，没有改候选源码。

| 冻结对象 | SHA-256 |
|---|---|
| capture-critical-input-evidence.py | `879b46ff4736a433cf37e2ac40a85ed30491f5b302187dfc5e98c36cbe27b295` |
| test_critical_input_evidence.py | `71fa0cedb7f87b41570b05b0147db3696f0320472d8394c621049fdf975a247d` |
| .github/workflows/ci.yml | `674f03f52ad884e6ac686e7a551b512efd48f33ddd4e5f4fae4470c61dbc0167` |
| critical-input-policy.sql | `d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294` |
| critical-input-dataset-row.sql | `9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff` |

独立实际运行9项原unit及4项非作者控制，共 **13 passed / 0 failed / 0 error / 0 skipped，0.488s，rc0**。原测试中的数据库响应是明确fake，包检查使用自造真实NPZ/manifest及现有文件hash helper，不连接服务。非作者增加：dirty tracked tree在psql前拒绝；before/after实际配置内容变化后complete=false；PGHOSTADDR/PGSERVICE/PGSERVICEFILE/PGOPTIONS/PSQLRC不能从父环境绕过实际psql目标与只读选项；高精度numeric差异不丢失。runner全局禁止真实socket连接/子进程启动，只有测试明确的假command回调。

高精度边界以相同独立probe复验：原30f0把 `0.12345678901234567890` 与末位891合并成同一float，真实rc1；879b的objects使用Decimal保留两者不同，真实rc0。原源、原失败输出保留；与30f0相比仅增加Decimal解析及其导入，其余函数AST完全不变，未改产品计算、输入hash或四个业务断言。

源码与工作流审查见 `static-contract.json`：从最终YAML中移除恰好两个Critical环境键和两个新步骤后，与c6完整YAML相等；原Backend、Frontend、四case spec `d8aa48efd47f95ed1d7e1c8c895a3347b5e33f578f0f7a97eb2fca8c330b486c`、原gate `7bbd52348ef46c0d312aaf03190c1d93438292a98e71bbad2a0211c43f369fe3` 保持原字节。新增before和always-after保存真实原rc并非0退出；artifact仍always上传，critical/frontend门没有新增COE。**Backend原Mypy continue-on-error特许项原样保留**，未声称全仓COE全部移除。

采证边界：必须实际HEAD=GITHUB_SHA=fixtureSHA，固定CI run identity、专属loopback PG/Redis/TD及runner artifact目录；关键源必须tracked且全tracked diff clean。psql显式-X/无交互密码、固定库/用户/端口、最小子环境及只读PGOPTIONS，两份原SQL字节强校验，真实RR/RO/UTC/stdout/stderr/SQL/rc/时刻保存。after仅从calculation_dataset_snapshot的准确loop/window唯一KPI_GRID发现，再用原SQL按snapshot+loop读原行，零/多/错范围均拒绝；前后11节政策内容差异拒绝。没有查询KPI结果或result-record表，不将manifest参数冒充PG原行。

包检查只调用calc_snapshot的文件读取、inputHash和build摘要helper；它本身不是独立数学oracle。后续非作者数值复验仍必须从完整PG/原NPZ/manifest独立重算hash和期望，先封expected/readSet再开actual。旧39041829350缺少PG before/after/dataset原行的事实不被此补丁改写；新CI须实际生成这些原件并确认checkout/version，不能用本机c6通过替代。

作者128项封档清单 `8aa6a7b8b356b97be5b17daa3092dbdf13cc9a1e1b49f21c0681f55a69bdbd4e` 已独立逐项重算128/128、165597B，见 `author-manifest-check.json`。本回执、独立命令/原stdout/stderr/rc、两版precision反例、五文件完整副本和workflow最小diff归入本目录清单。本门完成后停止扩展，等待主协调正常提交/推送产生的真实CI原件；main未由本复验人合入，四项用户豁免不变。
