#!/bin/bash
# Script to run the VAMS v2.5 to v2.6 migration (OpenSearch reindex plus the data-model steps)
# Usage: ./run_migration.sh [config_file] [--dry-run] [--clear-indexes] [--async]
#                           [--steps STEP] [--limit N] [--profile NAME] [--region NAME]
#                           [--operation OP] [--log-level LEVEL] [--confirm-account ID] [--yes]

set -e

CONFIG_FILE="v2.5_to_v2.6_migration_config.json"
# An ARRAY, not a string: a value containing a space (never true for these flags today, but the
# string form also mangled quoting) would otherwise be re-split when the command is expanded.
EXTRA_ARGS=()

# A shift-based loop, because the previous `for arg in "$@"` classified every non-flag token as the
# config path. `--steps workflowExecutions` therefore set EXTRA_ARGS='--steps' and
# CONFIG_FILE='workflowExecutions', and the run died on "Config file 'workflowExecutions' not found."
# The `--flag=value` form happened to work, which is how the split went unnoticed.
while [ $# -gt 0 ]; do
    case "$1" in
        --steps|--limit|--profile|--region|--operation|--log-level|--confirm-account)
            if [ $# -lt 2 ]; then
                echo "Error: $1 requires a value."
                exit 1
            fi
            EXTRA_ARGS+=("$1" "$2")
            shift 2
            ;;
        --dry-run|--clear-indexes|--async|--yes)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --*=*)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --*)
            echo "Error: unknown flag '$1'. Supported: --dry-run --clear-indexes --async --yes"
            echo "       --steps --limit --profile --region --operation --log-level --confirm-account"
            exit 1
            ;;
        *)
            CONFIG_FILE="$1"
            shift
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

# Tested inside the `if`, not after it: under `set -e` a bare failing command exits the script
# immediately, so the helpful message below was unreachable and the operator saw only a bare
# ModuleNotFoundError-free silent exit.
if ! $PYTHON_CMD -c "import boto3" 2>/dev/null; then
    echo "Error: boto3 is not installed. Please run: pip install boto3"
    exit 1
fi

LOGS_DIR="logs"
mkdir -p $LOGS_DIR
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOGS_DIR/migration_$TIMESTAMP.log"

echo "Starting VAMS v2.5 to v2.6 OpenSearch reindex migration..."
echo "Using config file: $CONFIG_FILE"
echo "Extra arguments: ${EXTRA_ARGS[*]}"
echo "Logs will be saved to: $LOG_FILE"
echo ""

$PYTHON_CMD v2.5_to_v2.6_migration.py --config "$CONFIG_FILE" "${EXTRA_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
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
