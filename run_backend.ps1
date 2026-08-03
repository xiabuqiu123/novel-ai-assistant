param(
    [int]$Port = 8000,
    [string]$HostAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ProjectRoot "backend"

if (-not (Test-Path -LiteralPath $BackendDir)) {
    throw "Backend directory not found: $BackendDir"
}

Write-Host "Starting Long-Form Novel AI Analysis Assistant backend..."
Write-Host "Project root: $ProjectRoot"
Write-Host "Bind address: http://$HostAddress`:$Port"
Write-Host ""

$ipv4Addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        $_.PrefixOrigin -ne "WellKnown"
    } |
    Select-Object -ExpandProperty IPAddress -Unique

if ($ipv4Addresses) {
    Write-Host "Possible phone connection URLs:"
    foreach ($ip in $ipv4Addresses) {
        Write-Host "  http://$ip`:$Port"
    }
    Write-Host ""
}

Write-Host "Phone and PC must be on the same Wi-Fi/LAN. If the phone cannot connect, allow inbound TCP port $Port in Windows Firewall."
Write-Host "Press Ctrl+C to stop the backend."
Write-Host ""

Set-Location -LiteralPath $BackendDir
python -m uvicorn app.main:app --host $HostAddress --port $Port
