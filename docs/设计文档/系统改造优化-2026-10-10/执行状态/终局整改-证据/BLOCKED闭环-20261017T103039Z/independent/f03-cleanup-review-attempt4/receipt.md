# 第四次本地失败运行的保全与实际清理复核

结论分列：原stop工具仍FAIL/rc1（AccessDenied(pid91036)退出观测竞态），原业务未全过；实际清理闭合PASS。此为本地测试scope归档/清理，只读复核不代替业务、数值、远端CI或容量20批验收。

本复验人用独立脚本验证61件原归档SHA/bytes，原manifest `7f957498ffd37f83901e7ebc6fc32148d23d2df402baf9cf11bd39045f7cef28` 全匹配。PG custom dump413947B/SHA79ae1b77…、Redis RDB47160B/SHAbf638282…只作不透明字节哈希，未解析、未恢复、未连接数据库或容器，不读取其KPI值。原PG按attempt4重命名且dump原命令rc0；原精确Redis容器77939…保存RDB后stop/rename，原空broker事实保留，没有purge或消费补绿。这里的“保全”不冒称已验证数据库恢复。绝对路径清单不修改；本目录的 `absolute-path-recovery-map.json` 提供原路径→归档相对路径/尺寸/SHA的可移植映射。

真实API91036只请求TERM，supervisor原rc=-15。独立从信号前offset11503后的原API日志定位Waiting→Application shutdown complete→Finished server process[91036]三原行，顺序、offset及SHA见verification.json与3份raw；无SIGINT fallback文件。原stop-outcome唯一错误AccessDenied保留，不将干净日志回写成工具rc0。两次后续原生观察psRc0/零所选产品进程，全部原7PID和frontend离场、17261/17266均关闭；前端原waitReturnCode为null，不伪填0。原私有broker default/deadletter无消息、unacked0、unackedIndex0；dbsize1不是“Redis全空”。

容量S01九张表before/after-root-stop各18份raw/canonical（两时点共36份）逐文件真实字节/哈希全等，各摘要与实际文件一致，mapping7000、6张计算目标表0行。F03轮换没有污染原20窗准备。

TD保全为实际SELECT*返回的**8个数据列**（ts/value/quality_raw/quality_class/quality_schema/received_at/source_kind/payload_hash）、7表完整7207行；逐行时间/float.hex/双QC与本轮7角色INPUT精确相等。原“9列”口头表述已被8列原column_meta反证并获Root接受，旧原件不改。point UUID由冻结fixture与精确p_<UUID>表名映射，**旧TD tag值未独立导出**，不把映射宣称为旧tag原始响应；实际DROP前已归档全部8数据列。这是恢复边界，不能补造第9列或旧tag。SELECT*原八列原件哈希完整在61清单，可恢复原JSON输入文件，不宣称实做TD还原。

原H9f356缺manifest/0dataset行的数值BLOCKED不变，原环境3c8c失效不变。四项用户豁免、F02历史6+3及正常20批/1000/600s条件不变。
