# 一键启动：书镜辨章（桌面版）
# PRD 9.i - 拉起本地 FastAPI 后端（默认 127.0.0.1:8000）与 Flutter Windows 前端。
#
# 用法：
#   .\start_app.ps1                  # 启动后端 + 前端（优先已编译的 exe，否则 flutter run）
#   .\start_app.ps1 -BackendOnly     # 仅启动后端
#   .\start_app.ps1 -FrontendOnly    # 仅启动前端
#   .\start_app.ps1 -Port 8010       # 自定义后端端口（前端会连接同一端口）
#   .\start_app.ps1 -RunFlutter      # 强制用 flutter run（开发模式）而非已编译的 exe
#
# 前端连接的后端地址在 frontend/lib/backend_base_url.dart，默认 127.0.0.1:8000。
# 若用自定义端口，请同步修改 backend_base_url.dart 或在首次运行后于"后端连接"页修改。
#
# 修复说明（fix-plan 2026-07-27 问题 13 / C1）：
#   PowerShell/cmd 对 GUI 子系统程序（frontend.exe）不等待，旧版 `& $ReleaseExe` 立即返回，
#   导致 finally 误把后端 job 杀掉、前端却还活着。改为 Start-Process -PassThru + WaitForExit()，
#   真正等到前端窗口关闭再停后端；后端启动改为轮询 /health 就绪（最多 60s）再拉起前端，
#   避免前端先于后端就绪而连不上；后端进程失败时把重定向的日志原样打印，不再吞错。
#   后端用 Start-Process（而非 Start-Job）拿真实进程对象，Stop-Process 能精确杀掉 python，
#   避免 8000 端口被残留进程占用；兼容 Windows PowerShell 5.1（打包用户默认环境）。

param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$RunFlutter
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$ReleaseExe = Join-Path $FrontendDir "build\windows\x64\runner\Release\frontend.exe"

function Write-Step([string]$msg) { Write-Host "[start_app] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[start_app] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[start_app] $msg" -ForegroundColor Yellow }
function Write-Err2([string]$msg) { Write-Host "[start_app] $msg" -ForegroundColor Red }

function Test-Command([string]$name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# 打印后端重定向日志末尾，便于诊断启动失败。
function Show-BackendLogs {
    param([string]$OutLog, [string]$ErrLog)
    Write-Step "后端日志如下："
    foreach ($log in @($OutLog, $ErrLog)) {
        if ($log -and (Test-Path -LiteralPath $log)) {
            $txt = Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
            if ($txt) { Write-Host $txt } else { Write-Step "（$log 为空）" }
        }
    }
}

# 轮询后端 /health，最多等 TimeoutSeconds 秒；失败/退出/超时都打印日志后抛错。
function Wait-BackendReady {
    param([int]$Port, [int]$TimeoutSeconds = 60, $BackendProc, [string]$OutLog, [string]$ErrLog)
    $url = "http://127.0.0.1:$Port/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($BackendProc.HasExited) {
            Write-Err2 "后端进程已退出（exitCode=$($BackendProc.ExitCode)）。"
            Show-BackendLogs -OutLog $OutLog -ErrLog $ErrLog
            throw "后端启动失败（进程已退出，exitCode=$($BackendProc.ExitCode)）。"
        }
        try {
            $resp = Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 2 -ErrorAction Stop
            if ($resp) {
                Write-Ok "后端就绪：$url"
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Write-Err2 "后端健康检查超时（${TimeoutSeconds}s）：$url"
    Write-Step "请检查端口 $Port 是否被占用或后端日志。"
    Show-BackendLogs -OutLog $OutLog -ErrLog $ErrLog
    throw "后端在 ${TimeoutSeconds}s 内未就绪（$url）。"
}

function Start-Backend {
    if (-not (Test-Path -LiteralPath $BackendDir)) {
        throw "未找到后端目录：$BackendDir"
    }
    if (-not (Test-Command "python")) {
        throw "未检测到 python，请先安装 Python 3 并加入 PATH。"
    }
    Write-Step "启动后端 http://$HostAddress`:$Port ..."
    Push-Location -LiteralPath $BackendDir
    try {
        # 前台运行；Ctrl+C 时一并退出（由调用方管理进程生命周期）。
        python -m uvicorn app.main:app --host $HostAddress --port $Port
    }
    finally {
        Pop-Location
    }
}

function Find-Flutter {
    if (Test-Command "flutter") { return "flutter" }
    $bat = Join-Path $env:USERPROFILE "flutter\bin\flutter.bat"
    if (Test-Path -LiteralPath $bat) { return $bat }
    return $null
}

function Start-Frontend {
    if (-not (Test-Path -LiteralPath $FrontendDir)) {
        throw "未找到前端目录：$FrontendDir"
    }

    if (-not $RunFlutter -and (Test-Path -LiteralPath $ReleaseExe)) {
        Write-Step "使用已编译的前端：$ReleaseExe"
        Write-Ok "前端将连接 http://127.0.0.1:$Port （见 backend_base_url.dart）"
        # 关键修复：GUI 子系统程序不会阻塞父进程，必须显式 WaitForExit。
        $proc = Start-Process -FilePath $ReleaseExe -PassThru
        if ($null -eq $proc) { throw "无法启动前端进程：$ReleaseExe" }
        $proc.WaitForExit()
        return
    }

    $flutter = Find-Flutter
    if (-not $flutter) {
        Write-Warn2 "未找到 flutter，且无已编译的 $ReleaseExe"
        Write-Warn2 "请先运行：cd frontend; flutter build windows   （或在 PATH 中配置 flutter）"
        throw "无法启动前端：缺少 flutter 与已编译产物"
    }

    Write-Step "使用 flutter 启动前端（开发模式）..."
    Push-Location -LiteralPath $FrontendDir
    try {
        & $flutter run -d windows
    }
    finally {
        Pop-Location
    }
}

# ---- main ----
if (-not $BackendOnly -and -not $FrontendOnly) {
    Write-Step "一键启动：后端 + 前端"
    Write-Step "项目根目录：$ProjectRoot"
    Write-Host ""

    if (-not (Test-Command "python")) {
        throw "未检测到 python，请先安装 Python 3 并加入 PATH。"
    }

    # 端口已就绪则复用，避免重复双击拉起两个后端打架。
    $reuseExisting = $false
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method GET -TimeoutSec 1 -ErrorAction Stop
        Write-Ok "检测到端口 $Port 已有就绪后端，复用之，不再重复启动。"
        $reuseExisting = $true
    } catch { }

    $backendProc = $null
    $outLog = $null
    $errLog = $null
    if (-not $reuseExisting) {
        $stamp = (Get-Date -Format "yyyyMMddHHmmss")
        $outLog = Join-Path $env:TEMP "shujing_backend_$stamp.out.log"
        $errLog = Join-Path $env:TEMP "shujing_backend_$stamp.err.log"
        Write-Step "启动后端 http://$HostAddress`:$Port ..."
        Write-Step "（日志：$outLog / $errLog）"
        $backendProc = Start-Process -FilePath "python" `
            -ArgumentList "-m","uvicorn","app.main:app","--host",$HostAddress,"--port",$Port `
            -WorkingDirectory $BackendDir `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        if ($null -eq $backendProc) { throw "无法启动后端进程。" }
    }

    try {
        if ($backendProc) {
            Wait-BackendReady -Port $Port -TimeoutSeconds 60 -BackendProc $backendProc -OutLog $outLog -ErrLog $errLog
        }
        Start-Frontend
    }
    finally {
        Write-Step "前端已退出，停止后端..."
        if ($backendProc -and -not $backendProc.HasExited) {
            try { Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        Write-Ok "已退出书镜辨章。"
    }
}
elseif ($BackendOnly) {
    Start-Backend
}
elseif ($FrontendOnly) {
    Start-Frontend
}
else {
    throw "参数冲突：不能同时指定 -BackendOnly 与 -FrontendOnly"
}