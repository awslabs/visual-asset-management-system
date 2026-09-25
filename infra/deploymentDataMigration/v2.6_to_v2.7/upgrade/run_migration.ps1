# PowerShell script to run the VAMS v2.6 to v2.7 migration (orphaned trigger cleanup, vector index
# backfill, system-pipeline retirement report)
# Usage: .\run_migration.ps1 [-ConfigFile <path>] [-Execute] [-ClearVectors] [-Async]
#                            [-Steps <step>] [-Limit <n>] [-Profile <name>] [-Region <name>]
#                            [-LogLevel <level>] [-ConfirmAccount <id>]
#
# Without -Execute the migration is a DRY RUN. A real run that deletes rows or launches
# Bedrock-billed executions asks for the resolved AWS account id (or checks -ConfirmAccount).
#
# The per-step, per-profile and per-region parameters let a Windows operator run a single step or
# target a specific deployment without bypassing this wrapper; bypassing it also loses the timestamped
# log file under logs\, which is the record of what a migration did.

param(
    [string]$ConfigFile = "v2.6_to_v2.7_migration_config.json",
    [switch]$Execute,
    [switch]$ClearVectors,
    [switch]$Async,
    [ValidateSet("all", "orphanedTriggers", "vectorBackfill", "systemPipelineRetirement")]
    [string]$Steps,
    [int]$Limit,
    [string]$Profile,
    [string]$Region,
    [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR")]
    [string]$LogLevel,
    [string]$ConfirmAccount
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
        Write-Error "Python is not installed or not in PATH. Install Python 3.9+ and try again."
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

Write-Host "Starting VAMS v2.6 to v2.7 migration..."
Write-Host "Using config file: $ConfigFile"
Write-Host "Logs will be saved to: $LogFile"
Write-Host ""

$ExtraArgs = @()
if ($Execute) {
    $ExtraArgs += "--execute"
    Write-Host "Mode: EXECUTE (rows are deleted and Bedrock-billed executions launched; the account id is confirmed first)" -ForegroundColor Yellow
} else {
    Write-Host "Mode: DRY RUN (no changes will be made; pass -Execute for a real run)" -ForegroundColor Yellow
}
if ($ClearVectors) { $ExtraArgs += "--clear-vectors"; Write-Host "Mode: CLEAR VECTORS (every stored vector is deleted before re-embedding)" -ForegroundColor Yellow }
if ($Async)        { $ExtraArgs += "--async";         Write-Host "Mode: ASYNCHRONOUS (reindexer results in CloudWatch Logs)" -ForegroundColor Yellow }
# Valued parameters. PSBoundParameters rather than a truthiness test, so -Limit 0 is passed through
# instead of being dropped as if it had not been given.
if ($PSBoundParameters.ContainsKey("Steps"))          { $ExtraArgs += @("--steps", $Steps) }
if ($PSBoundParameters.ContainsKey("Limit"))          { $ExtraArgs += @("--limit", $Limit) }
if ($PSBoundParameters.ContainsKey("Profile"))        { $ExtraArgs += @("--profile", $Profile) }
if ($PSBoundParameters.ContainsKey("Region"))         { $ExtraArgs += @("--region", $Region) }
if ($PSBoundParameters.ContainsKey("LogLevel"))       { $ExtraArgs += @("--log-level", $LogLevel) }
if ($PSBoundParameters.ContainsKey("ConfirmAccount")) { $ExtraArgs += @("--confirm-account", $ConfirmAccount) }
if ($ExtraArgs.Count -gt 0) { Write-Host "Extra arguments: $($ExtraArgs -join ' ')" }

try {
    & $PythonCmd v2.6_to_v2.7_migration.py --config $ConfigFile @ExtraArgs 2>&1 | Tee-Object -FilePath $LogFile

    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "Migration completed successfully." -ForegroundColor Green
        Write-Host ""
        Write-Host "Next steps:" -ForegroundColor Yellow
        Write-Host "  1. Read the system-pipeline retirement report above; abort what it lists as in flight and re-point"
        Write-Host "     the workflows it lists as referencing a retired pipeline"
        Write-Host "  2. Watch the vector backfill: vamscli execution list --workflow-database-id GLOBAL"
        Write-Host "       --workflow-id system-genai-metadata --trigger-type System-Reindex --status RUNNING"
        Write-Host "  3. Confirm the trigger cleanup took effect: re-run with -Steps orphanedTriggers (without -Execute)"
        Write-Host "     and check the summary reads 'Rows that would be deleted' = 0 with 'Trigger rows examined' > 0"
        Write-Host "     (the live workflows' triggers, e.g. the system workflow's own fileUpload trigger)"
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
