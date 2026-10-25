# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch-failure codes are one vocabulary in two languages: the session-row module stores them
and the web renders their copy. A code added on one side without the other is a badge with no
sentence (web) or a sentence nobody ever sees (backend); this guard fails on either."""

import os
import re

import pytest

from backend.backend.handlers.addon.edh.common.edhSessionState import LAST_ERROR_CODES

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
_BADGE = os.path.join(_REPO_ROOT, "web", "src", "addons", "edh", "components", "WorkstationStatusBadge.tsx")
_TYPES = os.path.join(_REPO_ROOT, "web", "src", "addons", "edh", "types.ts")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _copy_map_keys(source):
    body = re.search(r"export const LAST_ERROR_COPY[^{]*\{(.*?)\n\};", source, re.S)
    assert body, "LAST_ERROR_COPY is not declared where the guard reads it"
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*):", body.group(1), re.M))


def _union_members(source):
    body = re.search(r"export type WorkstationLastError =(.*?);", source, re.S)
    assert body, "WorkstationLastError is not declared where the guard reads it"
    return set(re.findall(r'"([A-Z][A-Z0-9_]*)"', body.group(1)))


@pytest.mark.unit
class TestLastErrorVocabularyMirror:
    def test_the_web_copy_map_names_exactly_the_stored_codes(self):
        keys = _copy_map_keys(_read(_BADGE))
        assert keys, "the copy map is empty"
        assert keys == set(LAST_ERROR_CODES)

    def test_the_web_type_union_names_exactly_the_stored_codes(self):
        members = _union_members(_read(_TYPES))
        assert members, "the union is empty"
        assert members == set(LAST_ERROR_CODES)

    def test_the_guard_reads_the_landed_files(self):
        # Control: a moved file must fail here, not pass by reading nothing.
        assert os.path.exists(_BADGE) and os.path.exists(_TYPES)
