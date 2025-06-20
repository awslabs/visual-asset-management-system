# PowerShell script to run the VAMS v2.2 to v2.3 data migration
# Usage: .\run_migration.ps1 [config_file]

# Exit on error
$ErrorActionPreference = "Stop"

# Default config file
$CONFIG_FILE = "v2.2_to_v2.3_migration_config.json"

# Check if config file is provided as argument
if ($args.Count -eq 1) {
    $CONFIG_FILE = $args[0]
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
$LOG_FILE = "$LOGS_DIR\migration_$TIMESTAMP.log"

Write-Host "Starting VAMS v2.2 to v2.3 data migration..."
Write-Host "Using config file: $CONFIG_FILE"
Write-Host "Logs will be saved to: $LOG_FILE"

# Run the migration with the config file
Write-Host "Running migration script..."
python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" | Tee-Object -FilePath $LOG_FILE

# Check if migration was successful
if ($LASTEXITCODE -eq 0) {
    Write-Host "Migration completed successfully."
    
    # Ask if user wants to run verification
    $RUN_VERIFY = Read-Host "Do you want to run verification? (y/n)"
    if ($RUN_VERIFY -eq "y" -or $RUN_VERIFY -eq "Y") {
        Write-Host "Running verification script..."
        $VERIFY_LOG_FILE = "$LOGS_DIR\verification_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration_verify.py --config "$CONFIG_FILE" | Tee-Object -FilePath $VERIFY_LOG_FILE
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Verification completed successfully."
        } else {
            Write-Host "Verification completed with errors. Check the logs for details."
        }
    }
} else {
    Write-Host "Migration failed. Check the logs for details."
    exit 1
}

Write-Host "All operations completed."
Write-Host "Log files:"
Write-Host "  - Migration: $LOG_FILE"
if ($RUN_VERIFY -eq "y" -or $RUN_VERIFY -eq "Y") {
    Write-Host "  - Verification: $VERIFY_LOG_FILE"
}
