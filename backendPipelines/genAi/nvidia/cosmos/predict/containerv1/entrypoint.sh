#!/bin/bash
set -e

# Activate conda environment
# Use explicit empty args to prevent $@ from leaking into activate
source ~/miniconda3/bin/activate cosmos-predict1

# Change to code directory
cd /opt/ml/code

# Execute the command passed by Batch (e.g., python __main__.py {json})
exec "$@"
