#!/bin/bash
set -e

cd /opt/cosmos-predict2.5

# Ensure deps are synced (fast if already done during build)
uv sync --locked --extra=${CUDA_NAME:-cu128} 2>/dev/null || true

cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
