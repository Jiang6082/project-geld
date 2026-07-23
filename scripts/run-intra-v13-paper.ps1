param(
    [switch]$DryRun,
    [int]$RetrySeconds = 30,
    [string]$ConfigPath = "",
    [string]$OutputPath = "",
    [string]$RunnerName = "Intra V13",
    [string]$RunnerKey = "intra_v13",
    [string]$MutexName = "Local\ProjectGeld-IntraV13-Paper"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$geld = Join-Path $projectRoot ".venv\Scripts\geld.exe"
$config = if ($ConfigPath) { $ConfigPath } else { Join-Path $projectRoot "configs\paper-intra-v13.toml" }
$output = if ($OutputPath) { $OutputPath } else { Join-Path $projectRoot "artifacts\paper-intra-v13" }
$heartbeat = Join-Path $output "runner-heartbeat.json"
$eastern = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")

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
        runner = $RunnerKey
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

$mutex = [System.Threading.Mutex]::new($false, $MutexName)
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
        Write-RunnerMessage "Another $RunnerName runner already owns the process lock; exiting."
        exit 0
    }

    Write-RunnerMessage "$RunnerName runner started (PID $PID, dry_run=$([bool]$DryRun))."
    Write-Heartbeat -Phase "starting"

    while ($true) {
        $now = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $eastern)
        if ($now.DayOfWeek -in @("Saturday", "Sunday")) {
            Write-RunnerMessage "Market weekend; runner exiting."
            break
        }

        $open = $now.Date.AddHours(9).AddMinutes(31)
        $stop = $now.Date.AddHours(15).AddMinutes(50)
        if ($now -lt $open) {
            Write-Heartbeat -Phase "waiting_for_open" -NextRun $open
            Start-Sleep -Seconds ([Math]::Min([Math]::Ceiling(($open - $now).TotalSeconds), 60))
            continue
        }
        if ($now -gt $stop) {
            Write-RunnerMessage "$RunnerName paper window complete; runner exiting."
            break
        }

        $arguments = @(
            "--config", $config,
            "intraday-paper-once",
            "--output", $output
        )
        if (-not $DryRun) {
            $arguments += "--submit"
        }

        Write-Heartbeat -Phase "running_cycle"
        Write-RunnerMessage "Starting scheduled $RunnerName cycle."
        $exitCode = Invoke-Geld -Arguments $arguments
        if ($exitCode -ne 0) {
            Write-RunnerMessage "Cycle failed with exit code $exitCode; retrying in $RetrySeconds seconds."
            Write-Heartbeat -Phase "cycle_error" -LastExitCode $exitCode
            Start-Sleep -Seconds ([Math]::Max(5, $RetrySeconds))
            continue
        }

        $afterRun = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $eastern)
        $minutesSinceOpen = [Math]::Max(0, ($afterRun - $open).TotalMinutes)
        $slotNumber = [Math]::Floor($minutesSinceOpen / 15) + 1
        $nextSlot = $open.AddMinutes($slotNumber * 15)
        if ($nextSlot -gt $stop) {
            Write-RunnerMessage "$RunnerName paper window complete; runner exiting."
            break
        }

        Write-Heartbeat -Phase "waiting_for_cycle" -NextRun $nextSlot -LastExitCode 0
        while ($true) {
            $nowLocal = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $eastern)
            $remaining = [Math]::Ceiling(($nextSlot - $nowLocal).TotalSeconds)
            if ($remaining -le 0) {
                break
            }
            Start-Sleep -Seconds ([Math]::Min($remaining, 60))
        }
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
