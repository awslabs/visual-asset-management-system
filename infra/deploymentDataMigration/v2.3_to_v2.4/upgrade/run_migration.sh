#!/bin/bash
# Script to run the VAMS v2.3 to v2.4 constraints table migration
# Usage: ./run_migration.sh [config_file]

set -e  # Exit on error

# Default config file
CONFIG_FILE="v2.3_to_v2.4_migration_config.json"

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

echo "Starting VAMS v2.3 to v2.4 Constraints Table Migration..."
echo "Using config file: $CONFIG_FILE"
echo "Logs will be saved to: $LOG_FILE"
echo ""

# Run the migration with the config file
echo "Running migration script..."
python v2.3_to_v2.4_migration.py --config "$CONFIG_FILE" 2>&1 | tee -a "$LOG_FILE"

# Check if migration was successful
if [ $? -eq 0 ]; then
    echo ""
    echo "Migration completed successfully."
    echo ""
    echo "Next Steps:"
    echo "1. Verify authorization works correctly in VAMS"
    echo "2. Test constraint management UI"
    echo "3. Monitor CloudWatch logs for authorization queries"
    echo "4. After verification, optionally run with --delete-old-data to cleanup"
    echo ""
    
    # Ask if user wants to delete old data
    read -p "Do you want to delete old constraint data from AuthEntitiesTable? (y/n): " DELETE_OLD
    if [[ $DELETE_OLD == "y" || $DELETE_OLD == "Y" ]]; then
        echo ""
        echo "WARNING: This will permanently delete constraints from the old table!"
        read -p "Are you sure? Type 'DELETE' to confirm: " CONFIRM
        
        if [ "$CONFIRM" == "DELETE" ]; then
            echo "Running deletion..."
            DELETE_LOG_FILE="$LOGS_DIR/deletion_$TIMESTAMP.log"
            python v2.3_to_v2.4_migration.py --config "$CONFIG_FILE" --delete-old-data 2>&1 | tee -a "$DELETE_LOG_FILE"
            
            if [ $? -eq 0 ]; then
                echo "Deletion completed successfully."
            else
                echo "Deletion completed with errors. Check the logs for details."
            fi
        else
            echo "Deletion cancelled. Old data remains in AuthEntitiesTable."
        fi
    else
        echo "Skipping deletion. Old data remains in AuthEntitiesTable for safety."
    fi
else
    echo "Migration failed. Check the logs for details."
    exit 1
fi

echo ""
echo "All operations completed."
echo "Log files:"
echo "  - Migration: $LOG_FILE"
if [[ $DELETE_OLD == "y" || $DELETE_OLD == "Y" ]]; then
    if [ "$CONFIRM" == "DELETE" ]; then
        echo "  - Deletion: $DELETE_LOG_FILE"
    fi
fi
echo ""
