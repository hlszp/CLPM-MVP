# Debug Session: playwright-local-token
- **Status**: [OPEN]
- **Issue**: Playwright 访问本地应用时被 `Access Token Required` 拦截，需确认是本地服务入口、代理层还是测试配置导致。
- **Debug Server**: Pending
- **Log File**: Pending

## Reproduction Steps
1. 在 `prototype` 目录运行 Playwright smoke 或最小复现用例。
2. 访问本地 `http://127.0.0.1:5173`。
3. 观察是否返回应用页面，还是出现 `Access Token Required` 拦截。

## Hypotheses & Verification
| ID | Hypothesis | Likelihood | Effort | Evidence |
|----|------------|------------|--------|----------|
| A | Playwright 命中了 IDE/代理暴露口而不是 Vite 本地服务，代理要求访问令牌。 | High | Low | Rejected：Playwright 文档请求直接命中 `http://127.0.0.1:5173/`，不是外部代理 URL。 |
| B | `baseURL`、`webServer.url` 或 host 组合不一致，导致请求落到错误地址。 | High | Low | Confirmed：配置固定写死 `5173`，而该端口已被其他 Vite 应用占用。 |
| C | 本地已有开发服务并非当前项目的 Vite 服务，`reuseExistingServer` 复用了受保护端口。 | Med | Low | Confirmed：`ps -p 33660` 显示 `5173` 上运行的是 `~/.understand-anything/...`，不是 `prototype`。 |
| D | 应用内存在鉴权态守卫，Playwright 未带必要存储态或请求头，页面渲染为未授权状态。 | Med | Med | Rejected：项目内未发现 `Access Token Required` 文案；应用自身未授权页文案为“当前角色不可访问”。 |
| E | 浏览器上下文、代理环境变量或公司网络配置给本地 HTTP 注入了额外鉴权链路。 | Low | Med | Rejected：环境中未设置 `HTTP_PROXY/HTTPS_PROXY/NO_PROXY`。 |

## Log Evidence
- `curl http://127.0.0.1:5173` 返回 HTML 标题为 `Understand Anything`，与 `prototype/index.html` 中的 `CLPM Prototype` 不一致。
- Playwright 网络日志显示主文档与脚本均来自 `http://127.0.0.1:5173/...`，但页面正文为 `Access Token Required`，并加载了 `src/components/TokenGate.tsx` 等非本项目脚本。
- `ps -p 33660 -o pid=,ppid=,command=` 显示 `5173` 的监听进程来自 `~/.understand-anything/repo/understand-anything...`。
- 切换到专用端口 `4173` 后，Playwright 访问首页得到标题 `CLPM Prototype`，正文为 CLPM 导航内容，不再出现令牌页。
- 修复后 `npm run test:smoke -- --project=desktop --grep "opens /$|opens /risk$|opens /tuning/sample$"` 3/3 通过。
- 修复后 `npm run test:smoke -- --project=desktop` 中 35 个 `opens ...` 路由测试全部通过，整套桌面 smoke 为 42 通过 / 8 失败。

## Verification Conclusion
- 最小修复：将 Playwright 独占本地端口从 `5173` 改为 `4173`，并在 `webServer.command` 中显式追加 `--port 4173 --strictPort`，避免 `reuseExistingServer` 误复用其他本地 Vite 服务。
- `Access Token Required` 拦截已消失，问题根因确认为“固定端口 `5173` 被其他项目占用，Playwright 命中了错误服务”。
- 仍有 8 个桌面 smoke 失败，但均为当前 CLPM 页面内容与测试断言不匹配，不再是访问令牌拦截问题。
