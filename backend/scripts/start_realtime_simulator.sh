#!/bin/bash
# CLPM 实时仿真器启动脚本（launchd wrapper）
# 设置必要的环境变量后启动 realtime_simulator.py

set -e

# 项目路径
PROJECT_DIR="/Users/zhangping/DEV/CLPM/backend"

# 设置 PATH（uv / homebrew / 系统）
export PATH="/Users/zhangping/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 设置 HOME（某些工具依赖）
export HOME="/Users/zhangping"

# 切换到项目目录
cd "$PROJECT_DIR"

# 启动实时仿真器
exec /Users/zhangping/.local/bin/uv run python scripts/realtime_simulator.py
