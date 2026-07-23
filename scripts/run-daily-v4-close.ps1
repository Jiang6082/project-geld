param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$geld = Join-Path $projectRoot ".venv\Scripts\geld.exe"
$config = Join-Path $projectRoot "configs\paper-daily-v4.toml"
$output = Join-Path $projectRoot "artifacts\paper-daily-v4-close"
$heartbeat = Join-Path $output "runner-heartbeat.json"
$mutexName = "Local\ProjectGeld-DailyV4-Paper"

New-Item -ItemType Directory -Path $output -Force | Out-Null
$log = Join-Path $output ("runner-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-RunnerMessage {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"), $Message
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

function Write-Heartbeat {
    param([string]$Phase, [object]$LastExitCode = $null)
    $record = [ordered]@{
        runner = "daily_v4_close"
        process_id = $PID
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        phase = $Phase
        read_only = $true
        last_exit_code = $LastExitCode
    }
    $temporary = "$heartbeat.tmp"
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $heartbeat -Force
}

if (-not (Test-Path -LiteralPath $geld)) {
    throw "Virtual environment executable not found: $geld"
}

$mutex = [System.Threading.Mutex]::new($false, $mutexName)
$ownsMutex = $false
$runnerExitCode = 0
try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }
    if (-not $ownsMutex) {
        Write-RunnerMessage "Another Daily V4 process owns the lock; close check exiting."
        exit 0
    }
    Write-RunnerMessage "Daily V4 read-only close check started (PID $PID)."
    Write-Heartbeat -Phase "running"
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $geld --config $config daily-close-check --output $output 2>&1 |
            ForEach-Object {
                $line = $_.ToString()
                Write-Host $line
                Add-Content -LiteralPath $log -Value $line -Encoding UTF8
            }
        $runnerExitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($runnerExitCode -ne 0) {
        throw "Daily V4 close check failed with exit code $runnerExitCode."
    }
    Write-RunnerMessage "Daily V4 read-only close check completed successfully."
}
catch {
    $runnerExitCode = 1
    Write-RunnerMessage "Fatal close-check error: $($_.Exception.Message)"
    Write-Heartbeat -Phase "fatal_error" -LastExitCode 1
    exit 1
}
finally {
    if ($ownsMutex) {
        Write-Heartbeat -Phase "stopped" -LastExitCode $runnerExitCode
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
