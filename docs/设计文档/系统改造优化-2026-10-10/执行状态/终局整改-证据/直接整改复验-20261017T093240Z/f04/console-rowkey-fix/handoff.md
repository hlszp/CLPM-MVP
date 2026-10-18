# F04 真实控制台行键告警最小修复

主协调真实 UI 复验捕获 AntD `rowKey` 的 index 参数弃用/不保证稳定性告警；原证据由主协调保全，本目录不覆盖其1020例及旧门禁产物。

只改恢复弹窗的结果元数据与表键：结果行保留对应 `snapshotId`，详情和结果表共用单参数 `snapshotRowKey({snapshotId,loopId})`。去掉内联 any/index，跨快照出现同一回路时仍有稳定唯一键，不改变API请求、恢复状态、权限、选择或重试逻辑。

补一条组件边界：两份快照含相同loopId，结果键各自唯一、逆序后仍对应原snapshotId+loopId，并且键函数仅有一个参数。

- `components-final.*`：**28 passed/0，进程rc=0**。
- `eslint-final.*` / `format-final.*`：**rc=0**。初次eslint仅指出测试应使用toReversed，原失败日志保留；已修测试写法并复跑。
- `types.*` / 最终 `types-final.*`：具体真实rc和命令记录在相应 `.command.json`，无产品服务/数据库/Worker/Beat动作。
- `final-source.json` 为本轮最终11文件身份；父目录 `source-sha256.json`、`f04-source.diff` 已同步，v2另存备份。恢复组件SHA-256=`077648fcee21953223b1f9587a3619709f179fef63f9e9ed1fa172bef03425cc`。

源码已冻结。作者不签“真实控制台告警已消失”，由主协调后续浏览器回执确认。
