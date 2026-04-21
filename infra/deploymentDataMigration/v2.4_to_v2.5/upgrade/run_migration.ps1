# PowerShell script to run the VAMS v2.4 to v2.5 asset version databaseId backfill migration
# Usage: .\run_migration.ps1 [config_file] [-DryRun]

param(
    [string]$ConfigFile = "v2.4_to_v2.5_migration_config.json",
    [switch]$DryRun
)

# Set error action preference
$ErrorActionPreference = "Stop"

# Check if config file exists
if (-not (Test-Path $ConfigFile)) {
    Write-Error "Config file '$ConfigFile' not found. Please provide a valid config file or create '$ConfigFile'."
    exit 1
}

# Check Python is available
try {
    $null = & python --version 2>&1
    $PythonCmd = "python"
} catch {
    try {
        $null = & python3 --version 2>&1
        $PythonCmd = "python3"
    } catch {
        Write-Error "Python is not installed or not in PATH. Please install Python 3.6+ and try again."
        exit 1
    }
}

# Check boto3 is installed
try {
    & $PythonCmd -c "import boto3" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "boto3 not found"
    }
} catch {
    Write-Error "boto3 is not installed. Please run: pip install boto3"
    exit 1
}

# Create logs directory if it doesn't exist
$LogsDir = "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

# Generate timestamp for log files
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = "$LogsDir\migration_$Timestamp.log"

Write-Host "Starting VAMS v2.4 to v2.5 Asset Version databaseId Backfill Migration..."
Write-Host "Using config file: $ConfigFile"
Write-Host "Logs will be saved to: $LogFile"
Write-Host ""

# Build extra arguments
$ExtraArgs = @()
if ($DryRun) {
    $ExtraArgs += "--dry-run"
    Write-Host "Mode: DRY RUN (no changes will be made)" -ForegroundColor Yellow
    Write-Host ""
}

try {
    # Run the migration with the config file
    Write-Host "Running migration script..."
    & $PythonCmd v2.4_to_v2.5_migration.py --config $ConfigFile @ExtraArgs 2>&1 | Tee-Object -FilePath $LogFile

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Migration completed successfully." -ForegroundColor Green
        Write-Host ""
        Write-Host "Next Steps:" -ForegroundColor Yellow
        Write-Host "1. Verify asset versions display correctly in VAMS UI"
        Write-Host "2. Test asset version queries by database"
        Write-Host "3. Monitor CloudWatch logs for any issues"
        Write-Host ""
    } else {
        Write-Error "Migration failed. Check the logs for details."
        exit 1
    }
} catch {
    Write-Error "Error running migration: $_"
    exit 1
}

Write-Host ""
Write-Host "All operations completed."
Write-Host "Log file: $LogFile"
Write-Host ""
