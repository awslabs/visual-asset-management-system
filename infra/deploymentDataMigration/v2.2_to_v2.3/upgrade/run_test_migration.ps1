# PowerShell script to run a test migration on a small subset of data
# Usage: .\run_test_migration.ps1 [config_file] [limit]

# Exit on error
$ErrorActionPreference = "Stop"

# Default values
$CONFIG_FILE = "v2.2_to_v2.3_migration_test_config.json"
$LIMIT = 10

# Check if config file is provided as argument
if ($args.Count -ge 1) {
    $CONFIG_FILE = $args[0]
}

# Check if limit is provided as argument
if ($args.Count -ge 2) {
    $LIMIT = $args[1]
}

# Check if config file exists
if (-not (Test-Path $CONFIG_FILE)) {
    Write-Error "Error: Config file '$CONFIG_FILE' not found."
    Write-Error "Please provide a valid config file or create '$CONFIG_FILE'."
    exit 1
}

# Create logs directory if it doesn't exist
$LOGS_DIR = "logs"
if (-not (Test-Path $LOGS_DIR)) {
    New-Item -ItemType Directory -Path $LOGS_DIR | Out-Null
}

# Generate timestamp for log files
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE = "$LOGS_DIR\test_migration_$TIMESTAMP.log"

Write-Host "Starting VAMS v2.2 to v2.3 test migration..."
Write-Host "Using config file: $CONFIG_FILE"
Write-Host "Processing limit: $LIMIT assets"
Write-Host "Logs will be saved to: $LOG_FILE"

# Run the migration with the config file and limit
Write-Host "Running migration script in dry run mode..."
python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" --limit $LIMIT --dry-run | Tee-Object -FilePath $LOG_FILE

# Check if migration was successful
if ($LASTEXITCODE -eq 0) {
    Write-Host "Test migration completed successfully."
    
    # Ask if user wants to run verification
    $RUN_VERIFY = Read-Host "Do you want to run verification? (y/n)"
    if ($RUN_VERIFY -eq "y" -or $RUN_VERIFY -eq "Y") {
        Write-Host "Running verification script..."
        $VERIFY_LOG_FILE = "$LOGS_DIR\test_verification_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration_verify.py --config "$CONFIG_FILE" --limit $LIMIT | Tee-Object -FilePath $VERIFY_LOG_FILE
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Verification completed successfully."
        } else {
            Write-Host "Verification completed with errors. Check the logs for details."
        }
    }
    
    # Ask if user wants to run the actual migration (without dry run)
    $RUN_ACTUAL = Read-Host "Do you want to run the actual migration on this subset of data? (y/n)"
    if ($RUN_ACTUAL -eq "y" -or $RUN_ACTUAL -eq "Y") {
        Write-Host "Running actual migration on subset of data..."
        $ACTUAL_LOG_FILE = "$LOGS_DIR\actual_migration_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" --limit $LIMIT | Tee-Object -FilePath $ACTUAL_LOG_FILE
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Actual migration completed successfully."
        } else {
            Write-Host "Actual migration failed. Check the logs for details."
        }
    }
} else {
    Write-Host "Test migration failed. Check the logs for details."
    exit 1
}

Write-Host "All operations completed."
Write-Host "Log files:"
Write-Host "  - Test Migration: $LOG_FILE"
if ($RUN_VERIFY -eq "y" -or $RUN_VERIFY -eq "Y") {
    Write-Host "  - Verification: $VERIFY_LOG_FILE"
}
if ($RUN_ACTUAL -eq "y" -or $RUN_ACTUAL -eq "Y") {
    Write-Host "  - Actual Migration: $ACTUAL_LOG_FILE"
}
