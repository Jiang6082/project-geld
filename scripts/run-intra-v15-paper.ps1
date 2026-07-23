param(
    [switch]$DryRun,
    [int]$RetrySeconds = 30
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$arguments = @{
    RetrySeconds = $RetrySeconds
    ConfigPath = Join-Path $projectRoot "configs\paper-intra-v15.toml"
    OutputPath = Join-Path $projectRoot "artifacts\paper-intra-v15"
    RunnerName = "Intra V15"
    RunnerKey = "intra_v15"
    MutexName = "Local\ProjectGeld-IntraV15-Paper"
}
if ($DryRun) {
    $arguments["DryRun"] = $true
}
& (Join-Path $PSScriptRoot "run-intra-v13-paper.ps1") @arguments
exit $LASTEXITCODE
