$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot 'dsh-companion'
$python = Join-Path $root '.venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8765/'
$logs = Join-Path $root 'var'
function Test-Companion {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -eq 200 -and $response.Content -match 'DeepSeek Companion')
    } catch { return $false }
}
if (Test-Companion) { Write-Host "Companion is already running: $url"; exit 0 }
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    Write-Error 'Port 8765 is already occupied by another service. No process was stopped.'
    exit 1
}
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Python environment not found: $python. See dsh-companion README for installation."
    exit 1
}
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$stdout = Join-Path $logs 'startup.stdout.log'
$stderr = Join-Path $logs 'startup.stderr.log'
$process = Start-Process -FilePath $python -ArgumentList '-m', 'dsh_companion.cli', 'run' -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Companion) { Write-Host "Companion is running: $url (PID $($process.Id))"; exit 0 }
    if ($process.HasExited) { break }
    Start-Sleep -Seconds 1
    $process.Refresh()
}
Write-Error "Companion did not become ready. Check $stderr and $stdout."
exit 1
