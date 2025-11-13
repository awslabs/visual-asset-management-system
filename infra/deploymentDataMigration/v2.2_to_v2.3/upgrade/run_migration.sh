#!/bin/bash
# Script to run the VAMS v2.2 to v2.3 data migration
# Usage: ./run_migration.sh [config_file]

set -e  # Exit on error

# Default config file
CONFIG_FILE="v2.2_to_v2.3_migration_config.json"

# Check if config file is provided as argument
if [ $# -eq 1 ]; then
    CONFIG_FILE=$1
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
LOG_FILE="$LOGS_DIR/migration_$TIMESTAMP.log"

echo "Starting VAMS v2.2 to v2.3 OpenSearch dual-index migration..."
echo "Using config file: $CONFIG_FILE"
echo "Logs will be saved to: $LOG_FILE"

# Run the migration with the config file
echo "Running migration script..."
python v2.2_to_v2.3_migration.py --config "$CONFIG_FILE" 2>&1 | tee -a "$LOG_FILE"

# Check if migration was successful
if [ $? -eq 0 ]; then
    echo "Migration completed successfully."
    
    # Ask if user wants to run verification
    read -p "Do you want to run verification? (y/n): " RUN_VERIFY
    if [[ $RUN_VERIFY == "y" || $RUN_VERIFY == "Y" ]]; then
        echo "Running verification script..."
        VERIFY_LOG_FILE="$LOGS_DIR/verification_$TIMESTAMP.log"
        python v2.2_to_v2.3_migration_verify.py --config "$CONFIG_FILE" 2>&1 | tee -a "$VERIFY_LOG_FILE"
        
        if [ $? -eq 0 ]; then
            echo "Verification completed successfully."
        else
            echo "Verification completed with errors. Check the logs for details."
        fi
    fi
else
    echo "Migration failed. Check the logs for details."
    exit 1
fi

echo "All operations completed."
echo "Log files:"
echo "  - Migration: $LOG_FILE"
if [[ $RUN_VERIFY == "y" || $RUN_VERIFY == "Y" ]]; then
    echo "  - Verification: $VERIFY_LOG_FILE"
fi
