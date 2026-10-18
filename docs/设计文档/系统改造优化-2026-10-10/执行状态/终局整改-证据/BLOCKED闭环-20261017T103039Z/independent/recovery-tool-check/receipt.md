# 独立gzip/清单恢复器准备

`recover_collector_artifacts.py`只读已关闭collector目录，先核完整manifest每文件SHA/size与闭合目录，再核每gzip引用compressed/raw SHA和行数；原始解压字节逐字写入新目录，不改JSON payload字符串/字段/record。禁止覆盖、源内输出、缺清单、重复/漏文件、孤儿对象、错误rawhash/行数和超限静默截断。rc0为恢复通过，rc1为反证，rc2为缺资料/显式限额；不产业务PASS。

独立合成10例全过，真实rc0，format/lint均0，原件`selftest.*`、`format.*`、`lint.*`，源码SHA见`source-identity.json`。未读取真实计算结果；与冻结四文件工具分开补充，不改原协议/旧SHA。

后续用既有backend venv执行：`python recover_collector_artifacts.py --source <已关闭collector绝对目录> --out <全新恢复目录>`；保留`recovery-index.json`，再做单独可审计字段映射。必须等collector停且清单关闭，不将运行中追加的目录当坏证据，也不能从产品gate摘要造屏障事实。
