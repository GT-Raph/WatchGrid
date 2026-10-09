# Local Windows developer launcher. Starts both web server and monitoring scheduler.
# Production must continue using its separately deployed Celery worker and beat.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$env:WATCHGRID_LOCAL_NO_REDIS = "true"

Write-Host "Starting WatchGrid monitoring scheduler and Django server..."
$scheduler = Start-Process -FilePath $python -ArgumentList @("manage.py", "run_scheduler") -WorkingDirectory $projectRoot -PassThru -NoNewWindow
try {
    & $python manage.py runserver
} finally {
    if ($scheduler -and -not $scheduler.HasExited) {
        Stop-Process -Id $scheduler.Id -Force -ErrorAction SilentlyContinue
    }
}
