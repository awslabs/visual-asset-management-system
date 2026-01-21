#!/bin/bash
# IsaacLab Training Container Entrypoint

set -e

echo "=============================================="
echo "IsaacLab Training Container"
echo "=============================================="
echo "Date: $(date)"
echo "Hostname: $(hostname)"
echo "GPU Info:"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo "No GPU detected"
echo "=============================================="

# Validate input
if [ -z "$1" ]; then
    echo "ERROR: Job configuration required"
    echo "Usage: entrypoint.sh '<job_config_json>'"
    exit 1
fi

echo "Job Config: $1"
echo "=============================================="

# Execute main training script using Isaac Sim Python
cd /app
/isaac-sim/python.sh -u __main__.py "$@"

exit_code=$?
echo "=============================================="
echo "Training finished with exit code: $exit_code"
echo "=============================================="
exit $exit_code
