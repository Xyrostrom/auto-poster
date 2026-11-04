# Runs ONE auto-post on this PC (the same thing the GitHub job does), then
# pushes state/posted.json so GitHub and this PC agree on what was posted.
# Used by Windows Task Scheduler when GitHub Actions is not available.
# Logs go to the "logs" folder next to this script.

$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ('run-{0:yyyy-MM-dd_HHmm}.log' -f (Get-Date))

if (-not (Test-Path -LiteralPath '.env')) {
    "No .env file next to run-local.ps1 - copy .env.example to .env and fill it in." | Out-File $logFile -Encoding utf8
    exit 1
}

# Load .env into this process only (never printed).
foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim().Trim('"'), 'Process')
    }
}
$env:PYTHONIOENCODING = 'utf-8'
$platforms = if ($env:PLATFORMS) { $env:PLATFORMS } else { 'youtube' }

# Get the latest code and posted-list first, so a video GitHub already posted is skipped.
cmd /c "git pull --quiet --rebase origin main >> `"$logFile`" 2>&1"

cmd /c "python scripts\publish.py --platforms $platforms >> `"$logFile`" 2>&1"
$code = $LASTEXITCODE

# Save the record of what was posted (also on failure: it counts attempts).
git add state/posted.json
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    cmd /c "git commit --quiet -m `"chore: record post run [skip ci]`" >> `"$logFile`" 2>&1"
    cmd /c "git push --quiet origin HEAD:main >> `"$logFile`" 2>&1"
}
exit $code
