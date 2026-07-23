param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$geld = Join-Path $projectRoot ".venv\Scripts\geld.exe"
$config = Join-Path $projectRoot "configs\paper-daily-v4.toml"
$output = Join-Path $projectRoot "artifacts\paper-daily-v4"
$heartbeat = Join-Path $output "runner-heartbeat.json"
$eastern = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
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
    param(
        [string]$Phase,
        [object]$NextRun = $null,
        [object]$LastExitCode = $null
    )
    $record = [ordered]@{
        runner = "daily_v4"
        process_id = $PID
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
        phase = $Phase
        dry_run = [bool]$DryRun
        next_run = if ($null -eq $NextRun) { $null } else { ([DateTime]$NextRun).ToUniversalTime().ToString("o") }
        last_exit_code = $LastExitCode
    }
    $temporary = "$heartbeat.tmp"
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $heartbeat -Force
}

function Invoke-Geld {
    param([string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $geld @Arguments 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            Add-Content -LiteralPath $log -Value $line -Encoding UTF8
        }
        return [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
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
        Write-RunnerMessage "Another Daily V4 runner already owns the process lock; exiting."
        exit 0
    }

    Write-RunnerMessage "Daily V4 runner started (PID $PID, dry_run=$([bool]$DryRun))."
    Write-Heartbeat -Phase "starting"

    while ($true) {
        $now = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $eastern)
        if ($now.DayOfWeek -in @("Saturday", "Sunday")) {
            Write-RunnerMessage "Market weekend; runner exiting."
            break
        }

        $runAt = $now.Date.AddHours(9).AddMinutes(31)
        if ($now -lt $runAt) {
            Write-Heartbeat -Phase "waiting_for_open" -NextRun $runAt
            Start-Sleep -Seconds ([Math]::Min([Math]::Ceiling(($runAt - $now).TotalSeconds), 60))
            continue
        }
        if ($now -gt $now.Date.AddHours(16)) {
            Write-RunnerMessage "Regular session is over; runner exiting."
            break
        }

        $arguments = @(
            "--config", $config,
            "paper-once",
            "--output", $output
        )
        if (-not $DryRun) {
            $arguments += "--submit"
        }

        Write-Heartbeat -Phase "running_cycle"
        Write-RunnerMessage "Starting scheduled Daily V4 cycle."
        $exitCode = Invoke-Geld -Arguments $arguments
        if ($exitCode -ne 0) {
            $runnerExitCode = $exitCode
            Write-RunnerMessage "Daily V4 cycle failed with exit code $exitCode."
            Write-Heartbeat -Phase "cycle_error" -LastExitCode $exitCode
            exit $exitCode
        }
        Write-RunnerMessage "Daily V4 cycle completed successfully."
        break
    }
}
catch {
    $runnerExitCode = 1
    Write-RunnerMessage "Fatal runner error: $($_.Exception.Message)"
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
