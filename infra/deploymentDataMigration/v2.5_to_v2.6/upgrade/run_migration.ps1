# PowerShell script to run the VAMS v2.5 to v2.6 OpenSearch reindex migration (vams-*-v2 -> vams-*-v3)
# Usage: .\run_migration.ps1 [-ConfigFile <path>] [-DryRun] [-ClearIndexes] [-Async]

param(
    [string]$ConfigFile = "v2.5_to_v2.6_migration_config.json",
    [switch]$DryRun,
    [switch]$ClearIndexes,
    [switch]$Async
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ConfigFile)) {
    Write-Error "Config file '$ConfigFile' not found."
    exit 1
}

try {
    $null = & python --version 2>&1
    $PythonCmd = "python"
} catch {
    try {
        $null = & python3 --version 2>&1
        $PythonCmd = "python3"
    } catch {
        Write-Error "Python is not installed or not in PATH. Install Python 3.6+ and try again."
        exit 1
    }
}

try {
    & $PythonCmd -c "import boto3" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "boto3 not found" }
} catch {
    Write-Error "boto3 is not installed. Please run: pip install boto3"
    exit 1
}

$LogsDir = "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = "$LogsDir\migration_$Timestamp.log"

Write-Host "Starting VAMS v2.5 to v2.6 OpenSearch reindex migration..."
Write-Host "Using config file: $ConfigFile"
Write-Host "Logs will be saved to: $LogFile"
Write-Host ""

$ExtraArgs = @()
if ($DryRun)       { $ExtraArgs += "--dry-run";       Write-Host "Mode: DRY RUN (no changes will be made)" -ForegroundColor Yellow }
if ($ClearIndexes) { $ExtraArgs += "--clear-indexes"; Write-Host "Mode: CLEAR INDEXES (existing v3 documents will be deleted first)" -ForegroundColor Yellow }
if ($Async)        { $ExtraArgs += "--async";         Write-Host "Mode: ASYNCHRONOUS (results in CloudWatch Logs)" -ForegroundColor Yellow }

try {
    & $PythonCmd v2.5_to_v2.6_migration.py --config $ConfigFile @ExtraArgs 2>&1 | Tee-Object -FilePath $LogFile

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Reindex migration completed successfully." -ForegroundColor Green
        Write-Host ""
        Write-Host "Next steps:" -ForegroundColor Yellow
        Write-Host "  1. Verify asset and file search returns expected results in the VAMS UI"
        Write-Host "  2. Confirm the geospatial filter and map view work for entities with location metadata"
        Write-Host "  3. Monitor CloudWatch logs for the reindexer Lambda for any per-record failures"
    } else {
        Write-Error "Migration failed. Check the logs for details."
        exit 1
    }
} catch {
    Write-Error "Error running migration: $_"
    exit 1
}

Write-Host ""
Write-Host "Log file: $LogFile"
