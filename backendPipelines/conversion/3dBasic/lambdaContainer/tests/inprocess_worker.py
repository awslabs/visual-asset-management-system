#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A stand-in for ``lambda.run_worker`` that runs the conversion in this process with a stubbed
trimesh, so a test of the handler's flow (what is checked, fetched, uploaded and named) does not start
a child interpreter. The child process itself is covered by test_3dbasic_worker_process.py and the real
conversions."""

import os
import sys

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import conversion_worker  # noqa: E402


def in_process_worker(trimesh_stub):
    def run_worker(job, work_dir, timeout_seconds=None):
        conversion_worker.convert_model(job, trimesh_stub)
    return run_worker
