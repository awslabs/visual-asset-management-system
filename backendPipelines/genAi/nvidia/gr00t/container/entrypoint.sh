#!/bin/bash
set -e

cd /workspace

# Ensure PYTHONPATH includes workspace for gr00t imports
export PYTHONPATH=/workspace:${PYTHONPATH}

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
