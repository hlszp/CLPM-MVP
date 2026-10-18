# 远端 run39031454818 原件交接

本轮真实终态为 **workflow FAILURE**。PR head `8a00175339228a7dd710751d59d626ee97fe2233`，三个 job 原始 checkout 日志均为 PR merge `c452ea7fa913a7fffc721de81470ef0874b6bc46`；未将两种 SHA 混用。完整元数据、三个 job 原始日志、整轮 logs zip、artifact zip 和91项逐文件哈希均在本目录。

Backend SUCCESS：7448 collected / 166 deselected / 7282 selected = 6930 passed + 336 skipped + 12 xfailed + 4 xpassed，0 failed，coverage 75.61%，491.63s。原 annotated 文件7个测试全部通过；原30s/coverage60/22样本与16/18门槛未放宽，ARX性能修复的真实Linux红→绿证据成立。既有Mypy非阻塞例外仍存在，本交接不将其冒称全后端类型检查0。

Frontend SUCCESS：106文件/1021单测、lint/type/build、原2条烟测均通过。Critical FAILURE：AUTH真实单次PASS；PERM API403正确，但测试误期待403页面文案，而现行页面码路由过滤落确切404；PERM失败后CALC/EVIDENCE串行未执行，NPZ=0。因此不能据本轮签四业务或独立数值通过。具体错误、附件和trace原件在artifact12100418238；`critical-failure-summary.json`仅是便读索引，原件不改。

后续由Root授权的局部修复见相邻 `../permission-local-remediation/`（真实wire与生产路由合同原件保全）。没有取消、rerun、修改GitHub状态或触发新CI。所有收集仅GET，实际退出码与SHA各自保全。作者不签独立数值结论。
