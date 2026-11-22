#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Pipeline code reads the Region from AWS_REGION with no fallback.

A fallback to ``us-east-1`` names a commercial Region that does not exist in AWS GovCloud (US) or the
AWS European Sovereign Cloud, so a missing variable would send calls to the wrong partition instead of
failing where it is read. Lambda always sets AWS_REGION, and these pipelines' job definitions pass it
to their containers. The directories below are held to that; the older pipelines still carry copies,
which are removed separately.
"""

import os
import re

import pytest

PIPELINES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories (all .py below them) and single files held to no fallback.
CHECKED = [
    "genAi/cadStepAgent/lambda",
    "genAi/videoSopBom/lambda",
    "utility/zipArchive/lambda",
    "conversion/cogConvert/lambda",
    "conversion/cogConvert/container/cog_convert_pipeline",
    "conversion/3dBasic/lambda",
    "3dRecon/splatToolbox/container/__main__.py",
]

FALLBACK = re.compile(r"""AWS_REGION['"]\s*,\s*['"]us-east-1['"]""")


def _sources():
    for entry in CHECKED:
        path = os.path.join(PIPELINES_DIR, entry)
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


@pytest.mark.unit
def test_the_checked_pipelines_read_aws_region_without_a_fallback():
    offenders = []
    for path in _sources():
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if FALLBACK.search(line):
                    offenders.append(f"{os.path.relpath(path, PIPELINES_DIR)}:{number}")
    assert offenders == [], f"AWS_REGION read with a us-east-1 fallback: {offenders}"


@pytest.mark.unit
def test_every_checked_path_exists():
    for entry in CHECKED:
        assert os.path.exists(os.path.join(PIPELINES_DIR, entry)), entry


@pytest.mark.unit
@pytest.mark.parametrize("line", [
    "region_name=os.environ.get('AWS_REGION', 'us-east-1')",
    'os.getenv("AWS_REGION", "us-east-1")',
])
def test_control_the_pattern_finds_both_spellings(line):
    assert FALLBACK.search(line)
