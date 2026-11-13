#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""What the Isaac Lab container's ``entrypoint.sh`` prints about its job configuration.

The Batch job passes the whole job configuration as the script's first argument. The launch banner
reports its length, not its text.

The script runs as written, under bash. ``BASH_ENV`` names a file bash reads before the script, and that
file defines ``cd`` as a function that exits, so the run ends at ``cd /app``, after the banner and before
the Isaac Sim interpreter would start.
"""

import json
import shutil
import subprocess  # nosec B404 - runs the container's own entrypoint script
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "entrypoint.sh"

_CALLBACK_VALUE = "CALLBACK-VALUE-4a7e10"
_POLICY_VALUE = "s3://vams-assets-bucket/xASSET1/POLICY-VALUE-93cd/policy.pt"


def _job_config():
    """A job configuration shaped like the one ``executeBatchJob`` submits."""
    return {
        "name": "isaaclab-job",
        "trainingConfig": {"mode": "evaluate", "task": "Isaac-Cartpole-v0", "numEnvs": 16},
        "computeConfig": {"numNodes": 1},
        "policyS3Uri": _POLICY_VALUE,
        "outputS3AssetFilesPath": "s3://vams-assets-bucket/pipelines/isaaclab/JOB/output/E1/files/",
        "inputS3AssetFilePath": "s3://vams-assets-bucket/xASSET1/job.json",
        "externalSfnTaskToken": _CALLBACK_VALUE,
    }


@pytest.fixture
def run_entrypoint(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is not installed")
    stop_at_cd = tmp_path / "stop_at_cd.sh"
    stop_at_cd.write_text('cd() { echo "stopped before cd $*"; exit 0; }\n', encoding="utf-8")

    def run(argument):
        return subprocess.run(  # nosec B603 - fixed argv, no shell
            [bash, str(ENTRYPOINT), argument],
            capture_output=True, text=True, timeout=60, cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "BASH_ENV": str(stop_at_cd)},
        )

    return run


@pytest.mark.unit
def test_the_banner_reports_the_job_configuration_length_and_none_of_its_values(run_entrypoint):
    argument = json.dumps(_job_config())
    result = run_entrypoint(argument)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "stopped before cd /app" in result.stdout
    assert _CALLBACK_VALUE not in output
    assert "POLICY-VALUE-93cd" not in output
    assert f"Job Config: {len(argument)} characters\n" in result.stdout


@pytest.mark.unit
def test_a_missing_job_configuration_is_still_refused(run_entrypoint):
    result = run_entrypoint("")

    assert result.returncode == 1
    assert "ERROR: Job configuration required" in result.stdout
