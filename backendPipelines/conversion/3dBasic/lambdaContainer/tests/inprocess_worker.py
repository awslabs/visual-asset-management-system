#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A stand-in for ``lambda.run_worker`` that runs the conversion in this process with a stubbed
trimesh, so a test of the handler's flow (what is checked, fetched, uploaded and named) does not start
a child interpreter. The child process itself is covered by test_3dbasic_worker_process.py and the real
conversions.

The format-specific loading of ``model_formats`` (the XML and archive checks, the USD reader) is
replaced by the plain trimesh load the stub answers, since a stubbed input is not a real file of its
format; ``test_3dbasic_new_formats.py`` covers that code with real files."""

import os
import sys
from unittest.mock import patch

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import conversion_worker  # noqa: E402


def _stub_load(job, trimesh):
    import referenced_files
    resolver = referenced_files.ConfinedResolver(
        job["input_dir"], job["input_relative_path"], job["by_reference"])
    return trimesh.load(job["input_file"], resolver=resolver), None, None


def in_process_worker(trimesh_stub):
    def run_worker(job, work_dir, timeout_seconds=None):
        import model_formats
        with patch.object(model_formats, "load_model", _stub_load):
            conversion_worker.convert_model(job, trimesh_stub)
    return run_worker
