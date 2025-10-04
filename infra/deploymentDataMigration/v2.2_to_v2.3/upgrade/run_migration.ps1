# PowerShell script to run the VAMS v2.2 to v2.3 data migration
# Usage: .\run_migration.ps1 [config_file]

param(
    [string]$ConfigFile = "v2.2_to_v2.3_migration_config.json"
)

# Set error action preference
$ErrorActionPreference = "Stop"

# Check if config file exists
if (-not (Test-Path $ConfigFile)) {
    Write-Error "Config file '$ConfigFile' not found. Please provide a valid config file or create '$ConfigFile'."
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

Write-Host "Starting VAMS v2.2 to v2.3 OpenSearch dual-index migration..."
Write-Host "Using config file: $ConfigFile"
Write-Host "Logs will be saved to: $LogFile"

try {
    # Run the migration with the config file
    Write-Host "Running migration script..."
    python v2.2_to_v2.3_migration.py --config $ConfigFile 2>&1 | Tee-Object -FilePath $LogFile
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Migration completed successfully."
        
        # Ask if user wants to run verification
        $RunVerify = Read-Host "Do you want to run verification? (y/n)"
        if ($RunVerify -eq "y" -or $RunVerify -eq "Y") {
            Write-Host "Running verification script..."
            $VerifyLogFile = "$LogsDir\verification_$Timestamp.log"
            python v2.2_to_v2.3_migration_verify.py --config $ConfigFile 2>&1 | Tee-Object -FilePath $VerifyLogFile
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "Verification completed successfully."
            } else {
                Write-Host "Verification completed with errors. Check the logs for details."
            }
        }
    } else {
        Write-Error "Migration failed. Check the logs for details."
        exit 1
    }
} catch {
    Write-Error "Error running migration: $_"
    exit 1
}

Write-Host "All operations completed."
Write-Host "Log files:"
Write-Host "  - Migration: $LogFile"
if ($RunVerify -eq "y" -or $RunVerify -eq "Y") {
    Write-Host "  - Verification: $VerifyLogFile"
}
