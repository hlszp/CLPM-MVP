<#
.SYNOPSIS
  CLPM 实时数据不落库 - Windows 一键证据采集（只读）

.DESCRIPTION
  自动判定部署形态（Docker Desktop / 原生 venv），按顺序采集：
    1 容器或进程状态
    2 五道闸门诊断脚本输出
    3 后端日志关键字（订阅 / Leader / PointHistoryWriter / 写入失败）
    4 celery worker + beat 日志尾部
    5 Redis 运行态镜像键 + Leader 锁 + 键总数
    6 TDengine 点表行数与最新时间
    7 容器内关键环境变量

  全程只读：不写库、不改配置、不重启服务。
  结果同时打印到屏幕并落盘为一个 txt，直接把这个 txt 发回即可。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\diag-realtime.ps1
  powershell -ExecutionPolicy Bypass -File scripts\diag-realtime.ps1 -ComposeFile docker-compose.prod.yml
#>
param(
  [string]$ComposeFile = "docker-compose.prod.yml",
  [string]$OutDir = "."
)

$ErrorActionPreference = "Continue"
# 中文日志经管道/重定向在 Windows 上默认按 OEM 代码页解码会乱码，显式统一 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$report = Join-Path $OutDir "diag-realtime-$stamp.txt"

function Say($m) {
  if ($null -eq $m) { $line = "" } else { $line = [string]$m }
  Write-Host $line
  Add-Content -Path $report -Value $line -Encoding UTF8
}

function RunSection($title, $block) {
  Say ""
  Say "=================================================================="
  Say $title
  Say "=================================================================="
  try { & $block } catch { Say ("[采集异常] " + $_.Exception.Message) }
}

Say "CLPM 实时链路诊断报告  生成时间=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  主机=$env:COMPUTERNAME"

# ---------- 1 判定部署形态 ----------
$dockerOk = $false
$hasBackend = $false
RunSection "1) 部署形态判定" {
  $v = docker version --format "{{.Server.Version}}" 2>&1
  if ($LASTEXITCODE -eq 0) {
    # 注意：& $block 会在新作用域执行，跨段变量必须写 $script: 作用域
    $script:dockerOk = $true
    Say "Docker 可用，Server 版本：$v"
    $names = docker ps --format "{{.Names}}" 2>&1
    Say "运行中的容器："
    $names | ForEach-Object { Say ("    " + $_) }
    if ($names -contains "clpm-backend") { $script:hasBackend = $true }
    Say ("clpm-backend 容器存在：" + $hasBackend)
  } else {
    Say "Docker 不可用（$v）→ 按原生 Windows 部署排查"
  }
}

if ($hasBackend) {
  RunSection "2) 五道闸门诊断（容器内执行）" {
    # 说明：已部署的镜像里没有 scripts/（Dockerfile 原先只拷 app/alembic），
    # 先用 docker compose cp 送进 /tmp，未重建镜像也能跑；若镜像已含内置脚本则直接跑。
    $local = "backend\scripts\diag_realtime_pipeline.py"
    $cpOut = docker compose -f $ComposeFile cp $local backend:/tmp/diag_realtime_pipeline.py 2>&1
    if ($LASTEXITCODE -eq 0) {
      Say "已将诊断脚本复制到容器 /tmp/diag_realtime_pipeline.py"
      docker compose -f $ComposeFile exec -T backend python /tmp/diag_realtime_pipeline.py 2>&1 | ForEach-Object { Say $_ }
    } else {
      Say ("cp 失败：" + $cpOut)
      Say "改试镜像内置路径 scripts/diag_realtime_pipeline.py"
      docker compose -f $ComposeFile exec -T backend python scripts/diag_realtime_pipeline.py 2>&1 | ForEach-Object { Say $_ }
    }
  }
  RunSection "3) 后端日志关键字（最近 200 条命中）" {
    docker compose -f $ComposeFile logs --tail=3000 backend 2>&1 |
      Select-String -Pattern "订阅|Leader|PointHistoryWriter|点身份|批次写入失败|保持待命|落库" |
      Select-Object -Last 200 | ForEach-Object { Say $_.ToString() }
  }
  RunSection "4) Celery 容器日志尾部" {
    Say "--- celery-worker ---"
    docker compose -f $ComposeFile logs --tail=40 celery-worker 2>&1 | ForEach-Object { Say $_ }
    Say "--- celery-beat ---"
    docker compose -f $ComposeFile logs --tail=20 celery-beat 2>&1 | ForEach-Object { Say $_ }
  }
  RunSection "5) Redis 运行态键 / Leader 锁 / 键总数" {
    foreach ($k in @("datasource:rt:signalr_enabled", "datasource:rt:realtime_writeback_enabled", "realtime:subscriber:leader:lock")) {
      $val = docker compose -f $ComposeFile exec -T redis redis-cli GET $k 2>&1
      $ttl = docker compose -f $ComposeFile exec -T redis redis-cli TTL $k 2>&1
      Say ("    " + $k + " = " + $val + "   (ttl=" + $ttl + "s)")
    }
    $size = docker compose -f $ComposeFile exec -T redis redis-cli DBSIZE 2>&1
    Say ("    DBSIZE = " + $size)
    $rt = docker compose -f $ComposeFile exec -T redis redis-cli --scan --pattern "realtime:*" 2>&1 | Measure-Object -Line
    Say ("    realtime:* 键数量 ≈ " + $rt.Lines)
  }
  RunSection "6) TDengine 点表 / 宽表" {
    docker compose -f $ComposeFile exec -T tdengine taos -s "SELECT COUNT(*) FROM clpm_ts.st_point_data_v1;" 2>&1 | ForEach-Object { Say $_ }
    docker compose -f $ComposeFile exec -T tdengine taos -s "SELECT LAST(ts) FROM clpm_ts.st_point_data_v1;" 2>&1 | ForEach-Object { Say $_ }
    docker compose -f $ComposeFile exec -T tdengine taos -s "SELECT COUNT(*) FROM clpm_ts.st_loop_data;" 2>&1 | ForEach-Object { Say $_ }
  }
  RunSection "7) 容器内关键环境变量" {
    docker compose -f $ComposeFile exec -T backend sh -c 'echo ENV=$ENV; echo SIGNALR_ENABLED=$SIGNALR_ENABLED; echo SIGNALR_HUB_URL=$SIGNALR_HUB_URL; echo SIGNALR_SHARD_SIZE=$SIGNALR_SHARD_SIZE; echo REALTIME_WRITEBACK_ENABLED=$REALTIME_WRITEBACK_ENABLED; echo HISTORY_STORAGE_MODE=$HISTORY_STORAGE_MODE; echo TDENGINE_HOST=$TDENGINE_HOST; echo TDENGINE_PORT=$TDENGINE_PORT; echo TDENGINE_DB=$TDENGINE_DB; echo REDIS_HOST=$REDIS_HOST; echo REDIS_PORT=$REDIS_PORT' 2>&1 | ForEach-Object { Say $_ }
  }
  RunSection "8) sys_config 真相源" {
    docker compose -f $ComposeFile exec -T postgres psql -U clpm -d clpm -c "SELECT key,value FROM sys_config WHERE key LIKE 'history.%' OR key LIKE 'datasource.%' ORDER BY key;" 2>&1 | ForEach-Object { Say $_ }
  }
} else {
  RunSection "2) 五道闸门诊断（原生 venv 执行）" {
    $candidates = @(".\.venv\Scripts\python.exe", ".\venv\Scripts\python.exe")
    $py = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $py) {
      Say "未找到 venv python（尝试过：$($candidates -join ', ')）"
      Say "请手动执行：<你的python路径> scripts\diag_realtime_pipeline.py"
    } else {
      Say "使用解释器：$py"
      & $py "scripts\diag_realtime_pipeline.py" 2>&1 | ForEach-Object { Say $_ }
    }
  }
  RunSection "3) 相关进程" {
    Get-Process | Where-Object { $_.ProcessName -match "python|celery|uvicorn|nginx|node" } |
      Select-Object Id, ProcessName, StartTime, WorkingSet |
      Format-Table -AutoSize | Out-String | ForEach-Object { Say $_ }
  }
  RunSection "4) 监听端口" {
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -in @(5432, 6379, 6041, 7101, 7104, 7141, 17101, 15666, 17115) } |
      Select-Object LocalAddress, LocalPort, OwningProcess |
      Sort-Object LocalPort | Format-Table -AutoSize | Out-String | ForEach-Object { Say $_ }
  }
}

Say ""
Say "=================================================================="
Say "报告已保存：$report"
Say "请把该文件内容整体回贴（或上传）即可定位。"
Say "=================================================================="
