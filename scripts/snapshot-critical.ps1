# snapshot-critical.ps1
# Copy critical source files to a timestamped .snapshots folder so the
# "I have a backup" recovery path (AGENTS.md Version Control Discipline #6)
# always exists even when the latest work is not yet in git.
#
# Usage (run manually or via a scheduled task / editor task on save):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\snapshot-critical.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot   # repo root
$stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd_HH-mm-ssZ')
$snapRoot = Join-Path $root '.snapshots'
$dest = Join-Path $snapRoot $stamp

# Files considered critical (add high-churn single files here).
$files = @(
    'backend\app\main.py',
    'backend\app\database.py',
    'backend\app\model_client.py',
    'frontend\lib\api_client.dart'
)

New-Item -ItemType Directory -Force -Path $dest | Out-Null
$copied = 0
foreach ($f in $files) {
    $src = Join-Path $root $f
    if (Test-Path -LiteralPath $src) {
        $dstFile = Join-Path $dest ($f -replace '[\\/]', '__')
        Copy-Item -LiteralPath $src -Destination $dstFile -Force
        Write-Output ("copied: {0} -> {1}" -f $f, $dstFile)
        $copied++
    } else {
        Write-Output ("skipped (missing): {0}" -f $f)
    }
}

# Keep only the most recent 50 snapshot folders; prune older ones.
Get-ChildItem -LiteralPath $snapRoot -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 50 |
    Remove-Item -Recurse -Force -LiteralPath { $_.FullName } -ErrorAction SilentlyContinue

Write-Output ("snapshot {0} done; {1} files copied." -f $stamp, $copied)