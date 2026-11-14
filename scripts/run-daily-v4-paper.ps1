# Daily V4: refresh the live universe (monthly), wait for the open, run one
# cycle. The cadence state decides whether that cycle submits a rebalance.
param(
    [switch]$DryRun
)

$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "run-geld-runner.ps1") `
    -Command "run-daily" `
    -Config (Join-Path $projectRoot "configs\paper-daily-v4.toml") `
    -Output (Join-Path $projectRoot "artifacts\paper-daily-v4") `
    -DryRun:$DryRun
exit $LASTEXITCODE
