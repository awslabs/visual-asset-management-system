# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The corpus schema and the committed evaluation files: every case carries the three axes, every referenced
fixture and golden exists, and the bytes on disk are the bytes the manifests record. Pure Python."""

import hashlib
import os
import re
import sys

import pytest
import yaml

EVALUATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EVALUATION_DIR not in sys.path:
    sys.path.insert(0, EVALUATION_DIR)
import fetch_fixtures  # noqa: E402
import honesty  # noqa: E402

with open(os.path.join(EVALUATION_DIR, "corpus.yaml"), encoding="utf-8") as _fh:
    CORPUS = yaml.safe_load(_fh)
CASES = CORPUS["cases"]
FIXTURES_DIR = os.path.join(EVALUATION_DIR, CORPUS["globals"]["fixtures_dir"])
GOLDENS_DIR = os.path.join(EVALUATION_DIR, CORPUS["globals"]["golden_dir"])
MAX_COMMITTED_FILE_BYTES = 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def test_case_ids_are_unique_and_every_case_names_its_mode():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert all(c["mode"] in ("generate", "modify") for c in CASES)
    assert {"G1", "G9", "M1", "M7", "FM1", "FM9", "FG1", "FG9"} <= set(ids)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_every_case_carries_the_three_axes(case):
    assert isinstance(case.get("expect"), dict) and case["expect"], "expect"
    assert case.get("honesty_expect") in honesty.RULES, case.get("honesty_expect")
    rubric = case.get("intent_rubric")
    assert isinstance(rubric, list) and 2 <= len(rubric) <= 4, "intent_rubric needs two to four criteria"
    assert all(isinstance(c, str) and c.strip() for c in rubric)
    assert str(case.get("prompt", "")).strip()


@pytest.mark.parametrize("case", [c for c in CASES if c["mode"] == "modify"],
                         ids=[c["id"] for c in CASES if c["mode"] == "modify"])
def test_every_modify_input_is_committed_or_fetchable(case):
    spec = case["input"]
    if "fixture" in spec:
        name = spec["fixture"]
        assert os.path.isfile(os.path.join(FIXTURES_DIR, name)) or name in fetch_fixtures.FETCHED, name
    else:
        assert spec["case"] in {c["id"] for c in CASES}
        assert os.path.isfile(os.path.join(EVALUATION_DIR, spec["fallback"])), spec["fallback"]


@pytest.mark.parametrize("case", [c for c in CASES if c.get("golden")],
                         ids=[c["id"] for c in CASES if c.get("golden")])
def test_a_referenced_golden_exists(case):
    assert os.path.isfile(os.path.join(EVALUATION_DIR, case["golden"])), case["golden"]


def _sums(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                digest, name = line.split()
                out[name] = digest
    return out


def test_every_committed_fixture_matches_its_recorded_sha256():
    sums = _sums(os.path.join(FIXTURES_DIR, "SHA256SUMS.txt"))
    for name, digest in sums.items():
        path = os.path.join(FIXTURES_DIR, name)
        if name in fetch_fixtures.FETCHED and not os.path.isfile(path):
            continue
        assert _sha256(path) == digest, name
    step_files = [f for f in os.listdir(FIXTURES_DIR) if f.lower().endswith((".step", ".stp"))]
    assert set(step_files) <= set(sums), "a fixture without a recorded sha256"
    assert set(fetch_fixtures.FETCHED) <= set(sums)
    assert all(fetch_fixtures.FETCHED[name]["sha256"] == sums[name] for name in fetch_fixtures.FETCHED)


def test_every_golden_is_listed_in_goldens_md_with_its_sha256():
    with open(os.path.join(GOLDENS_DIR, "GOLDENS.md"), encoding="utf-8") as fh:
        table = fh.read()
    listed = dict(re.findall(r"`([\w.-]+\.(?:step|stp))`.*?`([0-9a-f]{64})`", table))
    goldens = [f for f in os.listdir(GOLDENS_DIR) if f.lower().endswith((".step", ".stp"))]
    assert goldens and set(goldens) == set(listed)
    for name in goldens:
        assert _sha256(os.path.join(GOLDENS_DIR, name)) == listed[name], name


def test_no_committed_file_is_over_one_megabyte():
    """The fetched fixtures are the only files allowed past the cap, and they are ignored by git."""
    for root, dirs, files in os.walk(EVALUATION_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
        for name in files:
            if name in fetch_fixtures.FETCHED:
                continue
            assert os.path.getsize(os.path.join(root, name)) <= MAX_COMMITTED_FILE_BYTES, name


def test_the_fetched_fixtures_are_ignored_by_git():
    with open(os.path.join(FIXTURES_DIR, ".gitignore"), encoding="utf-8") as fh:
        ignored = {line.strip() for line in fh if line.strip() and not line.startswith("#")}
    assert set(fetch_fixtures.FETCHED) <= ignored
