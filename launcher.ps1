# Shujing-Bianzhang packaged launcher (launcher.ps1)
# fix-plan 2026-07-27 C1/C3: same logic as start_app.ps1, for the no-install zip package.
#
# Flat package layout (all files in one directory):
#   <AppDir>/+- frontend.exe (+ flutter_windows.dll + data/)
#            +- backend.exe
#            +- launcher bat (double-click entry point)
#            +- launcher.ps1
#            +- README txt
#
# Flow: start backend.exe -> poll /health until ready (up to 60s; onefile first unpack
#       is slow) -> start frontend -> wait for the frontend window to exit (GUI subsystem
#       apps are not waited on by cmd, so WaitForExit is required) -> stop the backend.
# On failure the backend stderr is printed as-is; errors are never swallowed.
#
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads BOM-less .ps1 files
# with the system ANSI codepage (GBK on zh-CN), which mangles any non-ASCII text and
# can break string quoting.

param(
    [int]$Port = 8000,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step([string]$msg) { Write-Host "[launcher] $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "[launcher] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[launcher] $msg" -ForegroundColor Yellow }
function Write-Error2([string]$msg){ Write-Host "[launcher] $msg" -ForegroundColor Red }

# Locate the backend exe; tolerate several naming layouts.
function Resolve-BackendExe {
    $primary = Join-Path $Here "backend.exe"
    if (Test-Path -LiteralPath $primary) { return $primary }
    $nested = Join-Path $Here "backend\novel-backend\novel-backend.exe"
    if (Test-Path -LiteralPath $nested) { return $nested }
    $flat = Join-Path $Here "novel-backend.exe"
    if (Test-Path -LiteralPath $flat) { return $flat }
    return $null
}

# Locate the frontend exe; same directory in the package, also compatible with a
# source-tree Release build.
function Resolve-FrontendExe {
    $primary = Join-Path $Here "frontend.exe"
    if (Test-Path -LiteralPath $primary) { return $primary }
    $release = Join-Path $Here "frontend\build\windows\x64\runner\Release\frontend.exe"
    if (Test-Path -LiteralPath $release) { return $release }
    return $null
}

function Wait-BackendReady {
    param([int]$Port, [int]$TimeoutSeconds, $BackendProc)
    $url = "http://127.0.0.1:$Port/health"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($BackendProc.HasExited) {
            Write-Step "Backend process exited early (exitCode=$($BackendProc.ExitCode))."
            throw "Backend process exited unexpectedly (exitCode=$($BackendProc.ExitCode))."
        }
        try {
            $resp = Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 2 -ErrorAction Stop
            if ($resp) {
                Write-Ok "Backend is ready: $url"
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Write-Error2 "Backend health check timed out after ${TimeoutSeconds}s: $url"
    Write-Step "Check whether port $Port is occupied, or backend.exe was blocked by antivirus."
    throw "Backend did not become ready within ${TimeoutSeconds}s ($url)."
}

# Kill exactly the process tree rooted at $RootPid (descendants first, then the
# root). Descendants are resolved through Win32_Process parent linkage, never by
# image name, so unrelated same-named processes are not touched. If CIM fails,
# it degrades to killing only the root PID.
function Stop-ProcessTree {
    param([int]$RootPid)
    if ($RootPid -le 0) { return }
    $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $toKill = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $toKill.Add($RootPid)
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($p in $procs) {
            if ($null -ne $p.ParentProcessId -and [int]$p.ParentProcessId -eq $parent) {
                $childPid = [int]$p.ProcessId
                if (-not $toKill.Contains($childPid)) {
                    $toKill.Add($childPid)
                    $queue.Enqueue($childPid)
                }
            }
        }
    }
    for ($i = $toKill.Count - 1; $i -ge 0; $i--) {
        try { Stop-Process -Id $toKill[$i] -Force -ErrorAction SilentlyContinue } catch { }
    }
}

# ---- main ----
Write-Step "Shujing-Bianzhang launcher"
Write-Step "Script directory: $Here"
Write-Host ""

$backendExe = Resolve-BackendExe
if (-not $backendExe) {
    Write-Error2 "Backend executable not found (backend.exe / novel-backend.exe)."
    throw "Backend executable not found."
}
$frontendExe = Resolve-FrontendExe
if (-not $frontendExe) {
    Write-Error2 "Frontend executable not found (frontend.exe)."
    throw "Frontend executable not found."
}

Write-Step "Backend : $backendExe"
Write-Step "Frontend: $frontendExe"
Write-Host ""

# If the port already serves a healthy backend (e.g. the launcher was double-clicked
# twice), reuse it instead of starting a duplicate.
$reuseExisting = $false
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Method GET -TimeoutSec 1 -ErrorAction Stop
    Write-Ok "Port $Port already has a healthy backend; reusing it, not starting another."
    $reuseExisting = $true
} catch {
    # Port is free; proceed with a normal start.
}

$backendProc = $null
if (-not $reuseExisting) {
    Write-Step "Starting backend at http://127.0.0.1:$Port ..."
    # -NoNewWindow keeps output in this console for diagnosis.
    $backendProc = Start-Process -FilePath $backendExe `
        -ArgumentList "--host","127.0.0.1","--port",$Port `
        -NoNewWindow -PassThru
    if ($null -eq $backendProc) { throw "Failed to start backend process: $backendExe" }
}

try {
    if ($backendProc) {
        Wait-BackendReady -Port $Port -TimeoutSeconds $HealthTimeoutSeconds -BackendProc $backendProc
    }
    Write-Step "Starting frontend..."
    $frontendProc = Start-Process -FilePath $frontendExe -PassThru
    if ($null -eq $frontendProc) { throw "Failed to start frontend process: $frontendExe" }
    Write-Ok "Frontend started; waiting for the window to close..."
    # GUI subsystem apps are not waited on by cmd; WaitForExit is required.
    $frontendProc.WaitForExit()
}
finally {
    Write-Step "Frontend exited; stopping backend..."
    # PyInstaller onefile: the bootloader spawns a child process (also named
    # backend.exe) that actually runs python. Stop-Process -Id only kills the
    # bootloader; the child would stay orphaned holding the sqlite handle and
    # the port. Kill exactly the process tree we started (bootloader PID plus
    # descendants resolved via CIM parent linkage); never taskkill /F /IM,
    # which matches by image name and would kill unrelated backend.exe instances.
    if ($backendProc) {
        Stop-ProcessTree -RootPid $backendProc.Id
    }
    Write-Ok "Shujing-Bianzhang has exited."
}
