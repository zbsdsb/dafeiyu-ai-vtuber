$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'out\songroom'
[void][System.IO.Directory]::CreateDirectory($out)
$pidPath = Join-Path $out 'lipsync.pid'
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = 0
    if ([int]::TryParse([System.IO.File]::ReadAllText($pidPath).Trim(), [ref]$savedPid)) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -like '*songroom_lipsync.py*') {
            Write-Output "LIPSYNC_ALREADY_RUNNING PID=$savedPid"
            exit 0
        }
    }
}
$process = Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') `
    -ArgumentList @('-u', 'tools/songroom_lipsync.py') -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $out 'lipsync.stdout.txt') `
    -RedirectStandardError (Join-Path $out 'lipsync.stderr.txt')
Write-Output "LIPSYNC_LAUNCHED PID=$($process.Id)"
