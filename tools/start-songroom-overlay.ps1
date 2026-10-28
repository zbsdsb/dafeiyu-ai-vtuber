$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& powershell.exe -NoProfile -File (Join-Path $PSScriptRoot 'start-caption-printer.ps1')
& powershell.exe -NoProfile -File (Join-Path $PSScriptRoot 'start-songroom-lipsync.ps1')
$out = Join-Path $root 'out\songroom'
[void][System.IO.Directory]::CreateDirectory($out)
$pidPath = Join-Path $out 'collector.pid'
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = 0
    if ([int]::TryParse([System.IO.File]::ReadAllText($pidPath).Trim(), [ref]$savedPid)) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -like '*songroom_overlay.py*') {
            Write-Output "COLLECTOR_ALREADY_RUNNING PID=$savedPid"
            exit 0
        }
    }
}
$python = Join-Path $root '.venv\Scripts\python.exe'
$process = Start-Process -FilePath $python -ArgumentList @('-u', 'tools/songroom_overlay.py') `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $out 'collector.stdout.txt') `
    -RedirectStandardError (Join-Path $out 'collector.stderr.txt')
Write-Output "COLLECTOR_LAUNCHED PID=$($process.Id)"
