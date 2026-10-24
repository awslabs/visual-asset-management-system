#!/bin/bash
# Script to run the VAMS v2.6 to v2.7 migration (orphaned trigger cleanup, vector index backfill,
# system-pipeline retirement report)
# Usage: ./run_migration.sh [config_file] [--execute] [--clear-vectors] [--async]
#                           [--steps STEP] [--limit N] [--profile NAME] [--region NAME]
#                           [--log-level LEVEL] [--confirm-account ID]
#
# Without --execute the migration is a DRY RUN. A real run that deletes rows or launches
# Bedrock-billed executions asks for the resolved AWS account id (or checks --confirm-account).

set -e

CONFIG_FILE="v2.6_to_v2.7_migration_config.json"
# An ARRAY, not a string: a value containing a space would otherwise be re-split when the command is
# expanded.
EXTRA_ARGS=()
EXECUTE=0

# A shift-based loop: a `for arg in "$@"` classifies every non-flag token as the config path, so
# `--steps vectorBackfill` would set CONFIG_FILE='vectorBackfill'.
while [ $# -gt 0 ]; do
    case "$1" in
        --steps|--limit|--profile|--region|--log-level|--confirm-account)
            if [ $# -lt 2 ]; then
                echo "Error: $1 requires a value."
                exit 1
            fi
            EXTRA_ARGS+=("$1" "$2")
            shift 2
            ;;
        --execute)
            EXECUTE=1
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --clear-vectors|--async)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --*=*)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --*)
            echo "Error: unknown flag '$1'. Supported: --execute --clear-vectors --async"
            echo "       --steps --limit --profile --region --log-level --confirm-account"
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
# immediately and the message below would be unreachable.
if ! $PYTHON_CMD -c "import boto3" 2>/dev/null; then
    echo "Error: boto3 is not installed. Please run: pip install boto3"
    exit 1
fi

LOGS_DIR="logs"
mkdir -p "$LOGS_DIR"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOGS_DIR/migration_$TIMESTAMP.log"

echo "Starting VAMS v2.6 to v2.7 migration..."
echo "Using config file: $CONFIG_FILE"
echo "Extra arguments: ${EXTRA_ARGS[*]}"
if [ "$EXECUTE" -eq 1 ]; then
    echo "Mode: EXECUTE (rows are deleted and Bedrock-billed executions launched; the account id is confirmed first)"
else
    echo "Mode: DRY RUN (no changes will be made; pass --execute for a real run)"
fi
echo "Logs will be saved to: $LOG_FILE"
echo ""

$PYTHON_CMD v2.6_to_v2.7_migration.py --config "$CONFIG_FILE" "${EXTRA_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
# The pipeline's own status is tee's, so read the migration's status from PIPESTATUS.
MIGRATION_STATUS=${PIPESTATUS[0]}

if [ "$MIGRATION_STATUS" -eq 0 ]; then
    echo ""
    echo "Migration completed successfully."
    echo ""
    echo "Next steps:"
    echo "  1. Read the system-pipeline retirement report above; abort what it lists as in flight and re-point"
    echo "     the workflows it lists as referencing a retired pipeline"
    echo "  2. Watch the vector backfill: vamscli execution list --workflow-database-id GLOBAL \\"
    echo "       --workflow-id system-genai-metadata --trigger-type System-Reindex --status RUNNING"
    echo "  3. Confirm the trigger cleanup took effect: re-run with --steps orphanedTriggers (without --execute)"
    echo "     and check the summary reads 'Rows that would be deleted' = 0 with 'Trigger rows examined' > 0"
    echo "     (the live workflows' triggers, e.g. the system workflow's own fileUpload trigger)"
else
    echo "Migration failed. Check the logs for details."
    echo "Log file: $LOG_FILE"
    exit "$MIGRATION_STATUS"
fi

echo ""
echo "Log file: $LOG_FILE"
