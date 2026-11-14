# Daily V4 read-only close reconciliation (after the 20-minute SIP delay).
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot "run-geld-runner.ps1") `
    -Command "run-close-check" `
    -Config (Join-Path $projectRoot "configs\paper-daily-v4.toml") `
    -Output (Join-Path $projectRoot "artifacts\paper-daily-v4") `
    -CloseOutput (Join-Path $projectRoot "artifacts\paper-daily-v4-close")
exit $LASTEXITCODE
