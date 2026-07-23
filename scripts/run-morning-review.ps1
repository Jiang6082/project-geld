# Read-only pre-market review runner. Generates the morning brief for both paper
# accounts from their logged performance. No API calls, no orders.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "artifacts\morning-review"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("runner-{0:yyyyMMdd}.log" -f (Get-Date))
& $python (Join-Path $projectRoot "scripts\morning_review.py") *>&1 | Tee-Object -FilePath $log -Append
