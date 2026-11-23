#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The GR00T image build's check of Isaac-GR00T's dependency lists against the recorded copy.

The Dockerfile runs ``check_upstream_requirements.py`` after the clone. It must pass on matching input and stop
the build, with a one-line message and no traceback, on a difference, a missing or unparsable file, or a record
without its lists. The script runs with the image's system Python 3.10 and the standard library only.
"""

import json
import os
import subprocess
import sys

import pytest

_CONTAINER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "genAi", "nvidia", "gr00t", "container"
)
_SCRIPT = os.path.join(_CONTAINER, "check_upstream_requirements.py")
_RECORD = os.path.join(_CONTAINER, "upstream-requirements.json")

UPSTREAM = """[project]
name = "gr00t"
dependencies = [
    "a==1.0",  # a comment
    "b>=2,<3",
]

[project.optional-dependencies]
dev = [
    "ruff",
]
base = [
    "c==3.0; platform_system != 'Darwin'",
]
"""
RECORD = {"commit": "0" * 40, "dependencies": ["a==1.0", "b>=2,<3"], "base": ["c==3.0; platform_system != 'Darwin'"]}


def run(tmp_path, upstream=UPSTREAM, record=RECORD, record_text=None):
    pyproject = tmp_path / "pyproject.toml"
    if upstream is not None:
        pyproject.write_text(upstream)
    recorded = tmp_path / "upstream-requirements.json"
    if record_text is not None:
        recorded.write_text(record_text)
    elif record is not None:
        recorded.write_text(json.dumps(record))
    return subprocess.run(
        [sys.executable, _SCRIPT, str(pyproject), str(recorded)], capture_output=True, text=True
    )


def test_matching_lists_pass(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "match the locked copy" in result.stdout


def test_the_committed_record_matches_its_own_pyproject_lists():
    """The record is what the image build compares upstream with, so it must hold the lists pyproject.toml copies."""
    with open(_RECORD, encoding="utf-8") as handle:
        record = json.load(handle)
    with open(os.path.join(_CONTAINER, "pyproject.toml"), encoding="utf-8") as handle:
        text = handle.read()
    for entry in record["dependencies"] + record["base"]:
        assert f'    "{entry}",' in text, f"{entry} is in the record but not in pyproject.toml"


@pytest.mark.parametrize(
    "upstream",
    [
        UPSTREAM.replace("a==1.0", "a==1.1"),
        UPSTREAM.replace('    "b>=2,<3",\n', ""),
        UPSTREAM.replace('    "b>=2,<3",\n', '    "b>=2,<3",\n    "extra",\n'),
        UPSTREAM.replace('    "a==1.0",  # a comment\n    "b>=2,<3",\n', '    "b>=2,<3",\n    "a==1.0",\n'),
        UPSTREAM.replace("c==3.0", "c==3.1"),
    ],
    ids=["changed pin", "removed entry", "added entry", "reordered", "changed base pin"],
)
def test_a_difference_stops_the_build_with_the_entries_named(tmp_path, upstream):
    result = run(tmp_path, upstream=upstream)
    assert result.returncode == 1
    assert "differ from the copy" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "case",
    ["no upstream file", "no record file", "record not JSON", "record is a list", "record without base", "record without commit", "record list of numbers", "upstream without base", "upstream without project table", "upstream line not a quoted requirement"],
)
def test_a_missing_or_unreadable_input_fails_with_one_line_and_no_traceback(tmp_path, case):
    kwargs = {}
    if case == "no upstream file":
        kwargs["upstream"] = None
    elif case == "no record file":
        kwargs["record"] = None
    elif case == "record not JSON":
        kwargs["record_text"] = "{not json"
    elif case == "record is a list":
        kwargs["record_text"] = "[]"
    elif case == "record without base":
        kwargs["record"] = {k: v for k, v in RECORD.items() if k != "base"}
    elif case == "record without commit":
        kwargs["record"] = {k: v for k, v in RECORD.items() if k != "commit"}
    elif case == "record list of numbers":
        kwargs["record"] = {**RECORD, "dependencies": [1, 2]}
    elif case == "upstream without base":
        kwargs["upstream"] = UPSTREAM.split("base = [")[0]
    elif case == "upstream without project table":
        kwargs["upstream"] = "[tool.x]\ny = 1\n"
    elif case == "upstream line not a quoted requirement":
        kwargs["upstream"] = UPSTREAM.replace('    "b>=2,<3",', "    b>=2,")
    result = run(tmp_path, **kwargs)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr, result.stderr
    lines = [l for l in result.stderr.splitlines() if l.strip()]
    assert len(lines) == 1, result.stderr
