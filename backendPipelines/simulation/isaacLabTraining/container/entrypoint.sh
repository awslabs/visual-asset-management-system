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
# The host's NVIDIA driver is the AMI's. NVIDIA tests Isaac Sim 6.2 on 595.58.03 or newer; the
# pipeline runs on whatever the GPU AMI ships and records the version here (issue #1669).
echo "NVIDIA driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1 || true)"
echo "Isaac Lab image version: ${ISAAC_LAB_VERSION:-unknown}"
echo "=============================================="

# Validate input
if [ -z "$1" ]; then
    echo "ERROR: Job configuration required"
    echo "Usage: entrypoint.sh '<job_config_json>'"
    exit 1
fi

echo "Job Config: ${#1} characters"
echo "=============================================="

# Execute main training script using the Isaac Lab Python environment (Isaac Lab 3.0 runs in
# /opt/isaaclab-venv; the interpreter is not otherwise on PATH)
cd /app
/opt/isaaclab-venv/bin/python -u __main__.py "$@"

exit_code=$?
echo "=============================================="
echo "Training finished with exit code: $exit_code"
echo "=============================================="
exit $exit_code
