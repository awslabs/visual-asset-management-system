#!/bin/bash
# Script to run a test migration on a small subset of data
# Usage: ./run_test_migration.sh [config_file] [limit]

set -e  # Exit on error

# Default values
CONFIG_FILE="v2.2_to_v2.3_migration_test_config.json"
LIMIT=10

# Check if config file is provided as argument
if [ $# -ge 1 ]; then
    CONFIG_FILE=$1
fi

# Check if limit is provided as argument
if [ $# -ge 2 ]; then
    LIMIT=$2
fi

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file '$CONFIG_FILE' not found."
    echo "Please provide a valid config file or create '$CONFIG_FILE'."
    exit 1
fi

# Create logs directory if it doesn't exist
LOGS_DIR="logs"
mkdir -p $LOGS_DIR

# Generate timestamp for log files
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOGS_DIR/test_migration_$TIMESTAMP.log"

echo "Starting VAMS v2.2 to v2.3 test migration..."
echo "Using config file: $CONFIG_FILE"
echo "Processing limit: $LIMIT assets"
echo "Logs will be saved to: $LOG_FILE"

# Run the migration with the config file and limit
echo "Running migration script in dry run mode..."
python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" --limit $LIMIT --dry-run 2>&1 | tee -a "$LOG_FILE"

# Check if migration was successful
if [ $? -eq 0 ]; then
    echo "Test migration completed successfully."
    
    # Ask if user wants to run verification
    read -p "Do you want to run verification? (y/n): " RUN_VERIFY
    if [[ $RUN_VERIFY == "y" || $RUN_VERIFY == "Y" ]]; then
        echo "Running verification script..."
        VERIFY_LOG_FILE="$LOGS_DIR/test_verification_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration_verify.py --config "$CONFIG_FILE" --limit $LIMIT 2>&1 | tee -a "$VERIFY_LOG_FILE"
        
        if [ $? -eq 0 ]; then
            echo "Verification completed successfully."
        else
            echo "Verification completed with errors. Check the logs for details."
        fi
    fi
    
    # Ask if user wants to run the actual migration (without dry run)
    read -p "Do you want to run the actual migration on this subset of data? (y/n): " RUN_ACTUAL
    if [[ $RUN_ACTUAL == "y" || $RUN_ACTUAL == "Y" ]]; then
        echo "Running actual migration on subset of data..."
        ACTUAL_LOG_FILE="$LOGS_DIR/actual_migration_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" --limit $LIMIT 2>&1 | tee -a "$ACTUAL_LOG_FILE"
        
        if [ $? -eq 0 ]; then
            echo "Actual migration completed successfully."
        else
            echo "Actual migration failed. Check the logs for details."
        fi
    fi
else
    echo "Test migration failed. Check the logs for details."
    exit 1
fi

echo "All operations completed."
echo "Log files:"
echo "  - Test Migration: $LOG_FILE"
if [[ $RUN_VERIFY == "y" || $RUN_VERIFY == "Y" ]]; then
    echo "  - Verification: $VERIFY_LOG_FILE"
fi
if [[ $RUN_ACTUAL == "y" || $RUN_ACTUAL == "Y" ]]; then
    echo "  - Actual Migration: $ACTUAL_LOG_FILE"
fi
