# Shared Windows wrapper for the cross-platform geld runners.
# The runner itself handles the process lock, heartbeat, market calendar
# (holidays, early closes), universe refresh and retries; this script only
# resolves paths and appends timestamped output to the daily runner log.
param(
    [Parameter(Mandatory = $true)][ValidateSet("run-daily", "run-intraday", "run-close-check")][string]$Command,
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$CloseOutput = "",
    [string[]]$Extra = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$geld = Join-Path $projectRoot ".venv\Scripts\geld.exe"
if (-not (Test-Path -LiteralPath $geld)) {
    throw "Virtual environment executable not found: $geld"
}
$logDirectory = if ($CloseOutput) { $CloseOutput } else { $Output }
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$log = Join-Path $logDirectory ("runner-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

$arguments = @("--config", $Config, "--log-json", (Join-Path $logDirectory "runner.jsonl"), $Command, "--output", $Output)
if ($CloseOutput) {
    $arguments += @("--close-output", $CloseOutput)
}
if (-not $DryRun -and $Command -ne "run-close-check") {
    $arguments += "--submit"
}
$arguments += $Extra

$ErrorActionPreference = "Continue"
& $geld @arguments 2>&1 | ForEach-Object {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"), $_.ToString()
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
exit [int]$LASTEXITCODE
