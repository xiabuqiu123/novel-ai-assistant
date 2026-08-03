# 把后端打包成单个 backend.exe（PyInstaller onefile）。
# 用法：.\build_backend_exe.ps1
# 产物：backend\dist\backend.exe（单文件，onefile 首次解包需 5–15s）。
# 数据库与 system prompt 不会打进 exe：DB 运行时落在 exe 同级 data\，prompt 由 --add-data 内嵌（ sys._MEIPASS 读取）。
#
# 注意：
#   - 首次运行会 pip 安装 pyinstaller（需联网）。
#   - 打包前请确保 backend\app\main.py / model_client.py 已是 frozen-aware 版本（fix-plan C2）。
#   - 构建环境是 Anaconda 全家桶，会顺着可选 hook 拖进 PyQt/matplotlib/IPython 等 GUI/科学库，
#     导致 PyInstaller 检测到 PyQt5 与 PyQt6 双 Q 绑定而中止。后端只依赖
#     fastapi/uvicorn/httpx/pydantic/multipart，下面用 --exclude-module 把这些无关库剔除。

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "backend"
$AppName = "backend"
$PromptSrc = Join-Path $ProjectRoot "outputs\novel_ai_system_prompt.md"

if (-not (Test-Path -LiteralPath $PromptSrc)) {
    throw "未找到系统 prompt：$PromptSrc"
}

# 后端实际不依赖的 GUI/科学/开发库（Anaconda 环境会顺 hook 拖入，需显式排除）。
$excludeModules = @(
    "PyQt5","PyQt6","PySide2","PySide6",
    "matplotlib","tkinter","_tkinter","PIL","pillow",
    "IPython","jupyter","jupyter_core","jupyter_client","jupyter_server","notebook","nbformat","nbconvert",
    "zmq","pyzmq",
    "black","yapf","pygments","blib2to3",
    "pytest","sympy","scipy","pandas","numpy",
    "cryptography","bcrypt","cffi","pycparser"
)

Push-Location -LiteralPath $BackendDir
try {
    Write-Host "[build_backend_exc] 检查 pyinstaller..."
    & python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[build_backend_exe] 安装 pyinstaller..."
        python -m pip install --upgrade pyinstaller
    }

    $pyiArgs = @(
        "PyInstaller",
        "--noconfirm","--clean","--onefile",
        "--name",$AppName,
        "--collect-all","uvicorn",
        "--collect-all","fastapi",
        "--collect-all","pydantic",
        "--collect-all","starlette",
        "--collect-all","anyio",
        "--collect-all","h11",
        "--hidden-import","multipart",
        "--hidden-import","anyio._backends._asyncio",
        "--hidden-import","click"
    )
    foreach ($mod in $excludeModules) {
        $pyiArgs += @("--exclude-module",$mod)
    }
    $pyiArgs += @("--add-data","$PromptSrc;.","run_server.py")

    Write-Host "[build_backend_exe] 开始打包 $AppName (onefile，已排除 GUI/科学库) ..."
    & python -m @pyiArgs

    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

    $out = Join-Path $BackendDir "dist\$AppName.exe"
    if (-not (Test-Path -LiteralPath $out)) { throw "未生成预期产物：$out" }

    Write-Host "[build_backend_exe] 完成。产物：$out" -ForegroundColor Green
    Write-Host "[build_backend_exe] 运行：.\dist\$AppName.exe --host 127.0.0.1 --port 8000" -ForegroundColor Green
}
finally {
    Pop-Location
}