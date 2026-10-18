# F02 配置冻结 after 原件归档增量独立复验

结论：PASS（仅采证工具）。新副本 `freeze_capacity_configuration_v2.py` SHA `2688732523d3f9300c9d11e2dbf9194248a7be195c18cbfa313f5bbf020e66e5`；旧07ee原工具不改，历史c452产物/既有协议/六正式ops/产品源码不改。

复验人自写 `probe_config_after_archive.py` SHA `8b146b3ef75504632e093d7fe049445ca888dd39ab9b3225172b526c9b3b06e6`，只借用作者1000loop/7000mapping/20node的fake PG/HTTP/git边界夹具，不连接实际服务、不导入产品。固定同一反例对旧源rc1、新源rc0：同/异MVCC分别覆盖稳定内容、内容变化、APIrevision变化共6组。稳定两组FROZEN/0，变化四组仍BLOCKED/2；六组均保存实取before/after各25张表完整原JSON、25canonical压缩件、identity及51个SHA/bytes引用。逐表与假DB返回原字符串逐字节比，解压恢复每值，包括NULL/0；不是用摘要代替after。

本复验人另从每组全部归档canonical独立重算前后内容digest，均匹配report；对应旧/新SQL完整transcript、原配置digest、FROZEN/BLOCKED结论完全相同。见 `independent-content-digest-check.json`。原先8项冻结门禁在进程内显式alias到新工具，连同7项新控独立执行15passed/rc0，原输出和命令在 `author-controls.*`。

只删除4个新增归档AST语句后整个源AST与07ee完全相同，插入位于实际after snapshot之后、APIrevision/内容稳定性拒绝之前。完整源比对与作者1424份原件（18,995,428字节）逐个SHA/size重算在 `source-and-manifest-independent.json`；作者原manifest SHA `48f165c22a171b71a654b1b85b1d748de1d4df426b235f3475f319f6d5fb5873`。

此门只证明采证工具增量可信及不放宽原拒绝语义。尚无本新副本的最终实际M配置冻结、20批测量或Worker配置内存证据，不签容量或整体F02 PASS。正式协议须引用本新路径/SHA，不能把c452旧freeze重贴最终身份。历史6+3、四项用户豁免与D04边界保持。
