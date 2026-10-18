# F03 作者工具移交

仅本地工具增量已完成并冻结；真实静态并发、真实四业务、独立数值及远端 workflow 等待 Root/F04 原件，不签通过。

- product HEAD edfa831afd6069963e7f6f73c2721637d6a199b5，最后 git status 干净；没有改产品/四业务源码、没有提交/推送/启停服务或写业务数据。
- driver de1a8981f8beb491265cbda24c3619a83b63c4cb3a80c5795b0c9f592df26801。完整四工具与大小见 source-manifest.json，SHA f41a86db17edd6eeebe77678eec7648fcba90c4d3a9a1e106e2c6a148410e1ee。v2旧SHA225372ed...保持；v2-v3.patch仅serve_site/supervisor_api/stop，函数外逐字节不变。
- final-controls.command.json / stderr.log：真实纯fake控制8例rc0，覆盖正确屏障顺序、旧记录/错误logger拒绝、缺屏障不CONT且frontend仍清、真实API-2失败、pending3失败、Beat身份/忙队列/错误frontend pgid拒绝；不是进程/队列实际验收。
- final-lint/final-format-check/final-node-syntax.command.json 与各stdout/stderr：均rc0，原stdout/stderr SHA在命令元数据中。
- Root执行单-v3.md：17267纯静态old/new探针逐字入口；之后由Root指定新business run-id/out，继续复用固定真实CI bootstrap/seed和四例门禁。
- 保留首轮真实失败、资源归档、API-2、pending3和原v2源/证据。旧版并发探针允许实际PASS，不制造旧必红；新版全字节/请求/冷浏览器必须真实通过。缺关键原件或任何非0均不扩签。
