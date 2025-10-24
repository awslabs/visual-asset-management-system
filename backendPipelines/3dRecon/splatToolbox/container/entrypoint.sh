#!/bin/bash
# Custom entrypoint script to properly handle JSON arguments

# Check if we have arguments
if [ $# -eq 0 ]; then
    echo "Error: No arguments provided"
    exit 1
fi

# Save the JSON argument to a temporary file
JSON_FILE="/tmp/pipeline_config.json"
echo "$1" > "$JSON_FILE"

# Execute the Python script with the JSON file path
cd /opt/ml/code
python __main__.py "$JSON_FILE"
