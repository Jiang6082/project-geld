# Intra V15 session loop: monthly universe refresh, one cycle per completed
# 15-minute bar, and a flatten-all before early closes.
param(
    [switch]$DryRun,
    [int]$RetrySeconds = 30
)

$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "run-geld-runner.ps1") `
    -Command "run-intraday" `
    -Config (Join-Path $projectRoot "configs\paper-intra-v15.toml") `
    -Output (Join-Path $projectRoot "artifacts\paper-intra-v15") `
    -Extra @("--retry-seconds", "$RetrySeconds") `
    -DryRun:$DryRun
exit $LASTEXITCODE
