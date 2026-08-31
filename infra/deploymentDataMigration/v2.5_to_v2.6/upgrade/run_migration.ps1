# PowerShell script to run the VAMS v2.5 to v2.6 migration (OpenSearch reindex plus the data-model steps)
# Usage: .\run_migration.ps1 [-ConfigFile <path>] [-DryRun] [-ClearIndexes] [-Async]
#                            [-Steps <step>] [-Limit <n>] [-Profile <name>] [-Region <name>]
#                            [-Operation <op>] [-LogLevel <level>] [-ConfirmAccount <id>] [-Yes]
#
# The per-step, per-profile and per-region parameters exist so a Windows operator can run a single
# step or target a specific deployment without bypassing this wrapper — bypassing it also loses the
# timestamped log file under logs\, which is the record of what a migration did.

param(
    [string]$ConfigFile = "v2.5_to_v2.6_migration_config.json",
    [switch]$DryRun,
    [switch]$ClearIndexes,
    [switch]$Async,
    [ValidateSet("all", "reindex", "assetHistory", "workflowExecutions", "auxPreviewRelocation",
                 "pipelineWorkflowDefinitions", "globalListBackfill", "tagsNamespacing")]
    [string]$Steps,
    [int]$Limit,
    [string]$Profile,
    [string]$Region,
    [ValidateSet("both", "assets", "files")]
    [string]$Operation,
    [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR")]
    [string]$LogLevel,
    [string]$ConfirmAccount,
    [switch]$Yes
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
if ($Yes)          { $ExtraArgs += "--yes";           Write-Host "Mode: NO CONFIRMATION PROMPT (--yes)" -ForegroundColor Yellow }
# Valued parameters. PSBoundParameters rather than a truthiness test, so -Limit 0 is passed through
# instead of being dropped as if it had not been given.
if ($PSBoundParameters.ContainsKey("Steps"))          { $ExtraArgs += @("--steps", $Steps) }
if ($PSBoundParameters.ContainsKey("Limit"))          { $ExtraArgs += @("--limit", $Limit) }
if ($PSBoundParameters.ContainsKey("Profile"))        { $ExtraArgs += @("--profile", $Profile) }
if ($PSBoundParameters.ContainsKey("Region"))         { $ExtraArgs += @("--region", $Region) }
if ($PSBoundParameters.ContainsKey("Operation"))      { $ExtraArgs += @("--operation", $Operation) }
if ($PSBoundParameters.ContainsKey("LogLevel"))       { $ExtraArgs += @("--log-level", $LogLevel) }
if ($PSBoundParameters.ContainsKey("ConfirmAccount")) { $ExtraArgs += @("--confirm-account", $ConfirmAccount) }
if ($ExtraArgs.Count -gt 0) { Write-Host "Extra arguments: $($ExtraArgs -join ' ')" }

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
