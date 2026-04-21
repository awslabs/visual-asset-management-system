#!/bin/bash
# Script to run the VAMS v2.4 to v2.5 asset version databaseId backfill migration
# Usage: ./run_migration.sh [config_file] [--dry-run]

set -e  # Exit on error

# Default config file
CONFIG_FILE="v2.4_to_v2.5_migration_config.json"
EXTRA_ARGS=""

# Parse arguments
for arg in "$@"; do
    case $arg in
        --dry-run)
            EXTRA_ARGS="$EXTRA_ARGS --dry-run"
            ;;
        --*)
            EXTRA_ARGS="$EXTRA_ARGS $arg"
            ;;
        *)
            CONFIG_FILE=$arg
            ;;
    esac
done

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file '$CONFIG_FILE' not found."
    echo "Please provide a valid config file or create '$CONFIG_FILE'."
    exit 1
fi

# Check Python is available
if ! command -v python &> /dev/null; then
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    else
        echo "Error: Python is not installed or not in PATH."
        echo "Please install Python 3.6+ and try again."
        exit 1
    fi
else
    PYTHON_CMD="python"
fi

# Check boto3 is installed
$PYTHON_CMD -c "import boto3" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Error: boto3 is not installed."
    echo "Please run: pip install boto3"
    exit 1
fi

# Create logs directory if it doesn't exist
LOGS_DIR="logs"
mkdir -p $LOGS_DIR

# Generate timestamp for log files
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOGS_DIR/migration_$TIMESTAMP.log"

echo "Starting VAMS v2.4 to v2.5 Asset Version databaseId Backfill Migration..."
echo "Using config file: $CONFIG_FILE"
echo "Extra arguments: $EXTRA_ARGS"
echo "Logs will be saved to: $LOG_FILE"
echo ""

# Run the migration with the config file
echo "Running migration script..."
$PYTHON_CMD v2.4_to_v2.5_migration.py --config "$CONFIG_FILE" $EXTRA_ARGS 2>&1 | tee -a "$LOG_FILE"

# Check if migration was successful
if [ $? -eq 0 ]; then
    echo ""
    echo "Migration completed successfully."
    echo ""
    echo "Next Steps:"
    echo "1. Verify asset versions display correctly in VAMS UI"
    echo "2. Test asset version queries by database"
    echo "3. Monitor CloudWatch logs for any issues"
    echo ""
else
    echo "Migration failed. Check the logs for details."
    exit 1
fi

echo ""
echo "All operations completed."
echo "Log file: $LOG_FILE"
echo ""
