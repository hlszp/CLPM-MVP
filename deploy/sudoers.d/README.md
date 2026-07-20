# CLPM Tailscale 切换 sudoers 免密配置

本目录提供 CLPM 后端 `app/core/system.py` 调用 `tailscale up --accept-routes=...` 所需的 sudoers 免密配置文件。

## 用途

CLPM 链路配置页 `/loop/aas-sync` 切换"局域网/公网"时，后端通过 `subprocess.run(["sudo", "-n", "tailscale", "up", "--accept-routes=...", "--reset=false"])` 动态安装/移除 `192.168.100.0/24` 子网路由。为避免每次切换都需要密码，需配置 sudoers 免密。

## 文件清单

| 文件 | 适用平台 | 用户 | tailscale 路径 |
|---|---|---|---|
| `clpm-tailscale` | Linux 生产服务器 | `clpm` | `/usr/bin/tailscale` |
| `clpm-tailscale.macos` | macOS 开发机（Intel） | `zhangping` | `/usr/local/bin/tailscale` |
| `clpm-tailscale.macos-arm64` | macOS 开发机（Apple Silicon） | `zhangping` | `/opt/homebrew/bin/tailscale` |

> 安装前先用 `which tailscale` 确认实际路径，选择对应文件；路径仍不一致时手动修改后再安装。

## 安装步骤

### Linux 生产服务器（clpm 用户）

```bash
# 1. 拷贝到 /etc/sudoers.d/
sudo cp deploy/sudoers.d/clpm-tailscale /etc/sudoers.d/clpm-tailscale

# 2. 设置权限（sudoers 要求 440）
sudo chmod 440 /etc/sudoers.d/clpm-tailscale

# 3. 语法校验
sudo visudo -c

# 4. 验证免密（sudo -l 列出授权，不执行命令；注意 tailscale status 不在白名单）
sudo -nl | grep tailscale
```

### macOS 开发机（zhangping 用户）

```bash
# 1. 拷贝到 /etc/sudoers.d/（Apple Silicon 改用 clpm-tailscale.macos-arm64）
sudo cp deploy/sudoers.d/clpm-tailscale.macos /etc/sudoers.d/clpm-tailscale

# 2. 设置权限
sudo chmod 440 /etc/sudoers.d/clpm-tailscale

# 3. 语法校验
sudo visudo -c

# 4. 验证免密（注意：tailscale status 不在白名单，必然提示密码）
sudo -nl | grep tailscale
# 或用白名单命令做幂等验证（重复应用当前状态，无副作用）：
sudo -n /usr/local/bin/tailscale up --accept-routes=false --reset=false
```

## 路径查询

安装前请确认 tailscale 实际路径：

```bash
which tailscale
```

如果路径与 sudoers 文件中的不一致，请手动修改 sudoers 文件中的路径后再安装。

## 故障排查

### 现象 1：`sudo: a password is required`

**原因**：sudoers 文件未安装、路径不匹配，或 `sudo -n` 触发了未授权的命令变体。

**排查**：
1. 确认 `/etc/sudoers.d/clpm-tailscale` 存在且权限为 440
2. 确认 tailscale 路径与 `which tailscale` 一致
3. 确认命令参数完全匹配（包括 `--reset=false`）
4. `sudo -l` 查看当前用户授权的命令列表

### 现象 2：应用层切换返回 `failed`

**原因**：sudoers 未配置或配置错误。

**排查**：
1. 后端日志：`Tailscale 切换失败 (rc=1): ...`
2. 手动执行验证：`sudo -n tailscale up --accept-routes=false --reset=false`
3. 如果返回密码提示，回到现象 1 的排查步骤

### 现象 3：应用层切换返回 `skipped`

**原因**：tailscale 客户端不可用（容器环境或未安装），属正常行为。

**说明**：生产环境容器内通常不安装 tailscale，应用层会自动跳过切换，不阻断配置保存。

## 安全说明

- 本 sudoers 仅授权 `tailscale up --accept-routes=true/false --reset=false` 两条命令，无法执行其他 tailscale 子命令
- `--reset=false` 确保不重置 Tailscale 其他配置（如 exit node、SSH 等）
- 不会影响 Tailscale 登录状态或节点身份
- 用户在生产环境部署时，建议仅在该服务器实际需要局域网/公网切换时才安装此 sudoers；纯局域网生产环境无需安装
