# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every committed EDH fixture is a sanitized request/response envelope.

A fixture that still carried a token header, an un-redacted credential key, or a real hostname
would be loaded by every downstream test that reads it, so each file is checked for the envelope
shape and re-run through ``sanitize_document``: a sanitized document is a fixed point, so any
difference means something slipped through. The first test refuses an empty directory so the
parametrized checks cannot pass by having nothing to check. A ``provenance`` block, when present,
names whether the exchange was captured live or derived from the EDH source tree; a source-derived
fixture is replaced by a live capture during the spike.
"""

import json
from pathlib import Path

import pytest

from backend.tests.handlers.addon.edh import fixtureSanitizer as fs

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PATHS = sorted(FIXTURES.glob("*.json"))
REQUIRED_REQUEST_KEYS = {"method", "path", "absolutePath", "query", "headers", "tokenHeaderName", "body"}
REQUIRED_RESPONSE_KEYS = {"status", "contentType", "elapsedSeconds", "json"}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_keys(doc):
    if isinstance(doc, dict):
        for key, value in doc.items():
            yield key, value
            yield from _walk_keys(value)
    elif isinstance(doc, list):
        for item in doc:
            yield from _walk_keys(item)


@pytest.mark.unit
def test_at_least_one_fixture_has_been_captured():
    assert FIXTURE_PATHS, f"no fixtures under {FIXTURES}; the spike has not captured anything yet"


@pytest.mark.unit
@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_fixture_is_an_envelope(path):
    doc = _load(path)

    assert doc["name"] == path.stem
    assert doc["capturedAt"].endswith("Z")
    assert isinstance(doc["edhVersion"], str)
    assert REQUIRED_REQUEST_KEYS <= set(doc["request"])
    assert REQUIRED_RESPONSE_KEYS <= set(doc["response"])
    assert isinstance(doc["response"]["status"], int)
    assert isinstance(doc["request"]["query"], dict)
    assert isinstance(doc["expect"], dict)
    assert doc["request"]["method"] == doc["request"]["method"].upper()
    assert doc["request"]["absolutePath"].startswith(("/", "ec2:", "iam:", "cloudformation:", "ssm:", "elbv2:", "ecr:"))

    provenance = doc.get("provenance")
    if provenance is not None:
        assert provenance["kind"] in {"live", "source-derived"}
        if provenance["kind"] == "source-derived":
            assert isinstance(provenance["edhCommit"], str) and provenance["edhCommit"]
            assert isinstance(provenance["sourceFiles"], list) and provenance["sourceFiles"]


@pytest.mark.unit
@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_fixture_carries_no_token_header_and_every_secret_key_is_redacted(path):
    doc = _load(path)

    assert "x-edh-token" not in {str(k).lower() for k in doc["request"]["headers"]}
    for key, value in _walk_keys(doc):
        if str(key).lower() in fs.SECRET_KEY_NAMES:
            assert value == fs.REDACTED, f"{path.name}: key {key!r} is not redacted"


@pytest.mark.unit
@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_fixture_is_a_fixed_point_of_the_sanitizer(path):
    doc = _load(path)

    assert fs.sanitize_document(doc) == doc
