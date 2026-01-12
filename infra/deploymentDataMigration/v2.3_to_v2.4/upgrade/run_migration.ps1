# PowerShell script to run the VAMS v2.3 to v2.4 constraints table migration
# Usage: .\run_migration.ps1 [config_file]

param(
    [string]$ConfigFile = "v2.3_to_v2.4_migration_config.json"
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

Write-Host "Starting VAMS v2.3 to v2.4 Constraints Table Migration..."
Write-Host "Using config file: $ConfigFile"
Write-Host "Logs will be saved to: $LogFile"
Write-Host ""

try {
    # Run the migration with the config file
    Write-Host "Running migration script..."
    python v2.3_to_v2.4_migration.py --config $ConfigFile 2>&1 | Tee-Object -FilePath $LogFile
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Migration completed successfully." -ForegroundColor Green
        Write-Host ""
        Write-Host "Next Steps:" -ForegroundColor Yellow
        Write-Host "1. Verify authorization works correctly in VAMS"
        Write-Host "2. Test constraint management UI"
        Write-Host "3. Monitor CloudWatch logs for authorization queries"
        Write-Host "4. After verification, optionally run with --delete-old-data to cleanup"
        Write-Host ""
        
        # Ask if user wants to delete old data
        $DeleteOld = Read-Host "Do you want to delete old constraint data from AuthEntitiesTable? (y/n)"
        if ($DeleteOld -eq "y" -or $DeleteOld -eq "Y") {
            Write-Host ""
            Write-Host "WARNING: This will permanently delete constraints from the old table!" -ForegroundColor Red
            $Confirm = Read-Host "Are you sure? Type 'DELETE' to confirm"
            
            if ($Confirm -eq "DELETE") {
                Write-Host "Running deletion..."
                $DeleteLogFile = "$LogsDir\deletion_$Timestamp.log"
                python v2.3_to_v2.4_migration.py --config $ConfigFile --delete-old-data 2>&1 | Tee-Object -FilePath $DeleteLogFile
                
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Deletion completed successfully." -ForegroundColor Green
                } else {
                    Write-Host "Deletion completed with errors. Check the logs for details." -ForegroundColor Yellow
                }
            } else {
                Write-Host "Deletion cancelled. Old data remains in AuthEntitiesTable."
            }
        } else {
            Write-Host "Skipping deletion. Old data remains in AuthEntitiesTable for safety."
        }
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
Write-Host "Log files:"
Write-Host "  - Migration: $LogFile"
if ($DeleteOld -eq "y" -or $DeleteOld -eq "Y") {
    if ($Confirm -eq "DELETE") {
        Write-Host "  - Deletion: $DeleteLogFile"
    }
}
Write-Host ""
