param(
    [string]$DailyTaskName = "ProjectGeld-DailyV4-Paper",
    [string]$DailyCloseTaskName = "ProjectGeld-DailyV4-Close",
    [string]$IntraTaskName = "ProjectGeld-IntraV15-Paper",
    [string]$ReviewTaskName = "ProjectGeld-MorningReview",
    [switch]$CloseOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$reviewAt = [DateTime]::Today.AddHours(9).AddMinutes(0)
$morningAt = [DateTime]::Today.AddHours(9).AddMinutes(25)
$closeAt = [DateTime]::Today.AddHours(16).AddMinutes(25)
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function Register-PaperTask {
    param(
        [string]$TaskName,
        [string]$ScriptName,
        [string]$Description,
        [DateTime]$StartTime
    )
    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        throw "Runner script not found: $scriptPath"
    }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`"" `
        -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $days -At $StartTime
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -WakeToRun `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 8)
    $principal = New-ScheduledTaskPrincipal `
        -UserId $identity `
        -LogonType Interactive `
        -RunLevel Limited
    $task = New-ScheduledTask `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $Description
    Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
}

if (-not $CloseOnly) {
    Register-PaperTask `
        -TaskName $DailyTaskName `
        -ScriptName "run-daily-v4-paper.ps1" `
        -Description "Project Geld Daily V4 Alpaca paper runner." `
        -StartTime $morningAt
}
Register-PaperTask `
    -TaskName $DailyCloseTaskName `
    -ScriptName "run-daily-v4-close.ps1" `
    -Description "Project Geld Daily V4 read-only close reconciliation and staging." `
    -StartTime $closeAt
if (-not $CloseOnly) {
    Register-PaperTask `
        -TaskName $IntraTaskName `
        -ScriptName "run-intra-v15-paper.ps1" `
        -Description "Project Geld Intra V15 Alpaca paper runner." `
        -StartTime $morningAt
    Register-PaperTask `
        -TaskName $ReviewTaskName `
        -ScriptName "run-morning-review.ps1" `
        -Description "Project Geld read-only pre-market review brief (no orders)." `
        -StartTime $reviewAt
}

$legacyTaskName = "ProjectGeld-IntraV13-Paper"
if (-not $CloseOnly -and $legacyTaskName -ne $IntraTaskName) {
    $legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
    if ($null -ne $legacy) {
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    }
}

$taskNames = if ($CloseOnly) {
    @($DailyCloseTaskName)
} else {
    @($DailyTaskName, $DailyCloseTaskName, $IntraTaskName, $ReviewTaskName)
}
Get-ScheduledTask -TaskName $taskNames |
    Get-ScheduledTaskInfo |
    Select-Object TaskName, LastRunTime, LastTaskResult, NextRunTime
