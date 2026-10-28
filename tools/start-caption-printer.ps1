$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'out\songroom'
[void][IO.Directory]::CreateDirectory($out)
try {
    $health = Invoke-RestMethod 'http://127.0.0.1:5500/health' -TimeoutSec 2
    if ($health.service -eq 'local-caption-printer') { Write-Output 'CAPTION_PRINTER_ALREADY_RUNNING'; exit 0 }
    throw 'Port 5500 belongs to another service; refusing to replace it'
} catch {
    if (Get-NetTCPConnection -State Listen -LocalPort 5500 -ErrorAction SilentlyContinue) { throw 'Port 5500 is in use; no second service started' }
}
$process = Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') `
    -ArgumentList @('-u','tools/caption_printer.py') -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $out 'caption.stdout.txt') -RedirectStandardError (Join-Path $out 'caption.stderr.txt')
[IO.File]::WriteAllText((Join-Path $out 'caption.pid'), [string]$process.Id)
Write-Output "CAPTION_PRINTER_LAUNCHED PID=$($process.Id)"
