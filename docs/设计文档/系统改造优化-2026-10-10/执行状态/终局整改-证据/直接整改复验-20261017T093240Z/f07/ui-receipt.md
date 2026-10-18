# F07 真实隔离运行链独立复验回执

**PASS（本轮指定真实UI/API链）**。主协调未编写F07产品代码；实际候选前端/后端、私有PostgreSQL/Redis、CLPM_TEST_MODE=0，不mock业务响应，不启动Worker/Beat。

1. 原反例：ic关注1条，audit_peer数据库0条；同SPA退出/换号后peer监视星标仍“取消关注”。cli/34-account-isolation-confirm.json真实rc1，pre-fix-account-db.json保留。修owner/generation及认证生命周期后，40/42同SPA换号不串号。
2. 真实409：独立API将peer先关注rev1、取消rev2，UI未刷新仍expectedRevision0。旧码第43超时rc1，第47保全真实409 body。根因是RequestClient抛解包body，旧提取器只认Axios error.response。14例旧码5失败与修后63/63在implementation/409-real-shape保留。
3. 修后第51实际UI写请求409/currentRevision2→以2重试200/revision3，星标收敛。第52从我的关注进入360，跨页与刷新后仍已关注。
4. 第48/49真断网，PUT net::ERR_INTERNET_DISCONNECTED，明确失败提示且乐观星标回滚；第50网络恢复。未伪造服务器返回。
5. 最终第53：peer取消关注后保持同SPA，退出→ic登录→我的关注→360，旧账户星标“关注该回路”，新账户正确“取消关注该回路”；logout/login之间无硬刷新。真实API最终ic=1/peer=0/restricted=0。
6. real-account-security-final.json保全匿名401、越scope写403及各自关注集合；没有跨用户污染或成功假态。

六条实际UI链的原始路径/rc/结果在[ui-independent-results.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f07/ui-independent-results.json)；第51完整CAS请求、body和真实HTTP状态在[cli/51-fixed-real-409.json](/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f07/cli/51-fixed-real-409.json)。前端最终全量1021通过，完整分母另见根报告。

定位失误：第33查询按钮title不精确，由34真正反证替代；第38将未关注aria误写“关注”（实际“关注该回路”），第40纠正后通过。这些原rc1保留，不作为产品缺陷或独立负例。第43/47则是可复现产品409问题，已明确修复。

本结论不等于四岗位走查、完整IAR或已部署；四项用户豁免保留。自有UI/API/Redis/DB均已清理，真实关闭状态见phase2/scratch-cleanup.json；共享环境没有服务动作。
