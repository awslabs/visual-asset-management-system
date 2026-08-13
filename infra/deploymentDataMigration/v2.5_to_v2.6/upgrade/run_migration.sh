#!/bin/bash
# Script to run the VAMS v2.5 to v2.6 OpenSearch reindex migration (vams-*-v2 -> vams-*-v3)
# Usage: ./run_migration.sh [config_file] [--dry-run] [--clear-indexes] [--async]

set -e

CONFIG_FILE="v2.5_to_v2.6_migration_config.json"
EXTRA_ARGS=""

for arg in "$@"; do
    case $arg in
        --dry-run|--clear-indexes|--async)
            EXTRA_ARGS="$EXTRA_ARGS $arg"
            ;;
        --*)
            EXTRA_ARGS="$EXTRA_ARGS $arg"
            ;;
        *)
            CONFIG_FILE=$arg
            ;;
    esac
done

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file '$CONFIG_FILE' not found."
    exit 1
fi

if ! command -v python &> /dev/null; then
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    else
        echo "Error: Python is not installed or not in PATH."
        exit 1
    fi
else
    PYTHON_CMD="python"
fi

$PYTHON_CMD -c "import boto3" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: boto3 is not installed. Please run: pip install boto3"
    exit 1
fi

LOGS_DIR="logs"
mkdir -p $LOGS_DIR
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOGS_DIR/migration_$TIMESTAMP.log"

echo "Starting VAMS v2.5 to v2.6 OpenSearch reindex migration..."
echo "Using config file: $CONFIG_FILE"
echo "Extra arguments: $EXTRA_ARGS"
echo "Logs will be saved to: $LOG_FILE"
echo ""

$PYTHON_CMD v2.5_to_v2.6_migration.py --config "$CONFIG_FILE" $EXTRA_ARGS 2>&1 | tee -a "$LOG_FILE"
# The pipeline's own status is tee's, so read the migration's status from PIPESTATUS.
MIGRATION_STATUS=${PIPESTATUS[0]}

if [ "$MIGRATION_STATUS" -eq 0 ]; then
    echo ""
    echo "Reindex migration completed successfully."
    echo ""
    echo "Next steps:"
    echo "  1. Verify asset and file search returns expected results in the VAMS UI"
    echo "  2. Confirm the geospatial filter and map view work for assets/files with location metadata"
    echo "  3. Monitor CloudWatch logs for the reindexer function for any per-record failures"
else
    echo "Migration failed. Check the logs for details."
    echo "Log file: $LOG_FILE"
    exit "$MIGRATION_STATUS"
fi

echo ""
echo "Log file: $LOG_FILE"
