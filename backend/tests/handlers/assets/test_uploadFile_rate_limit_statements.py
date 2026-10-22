# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every stated upload-initialization rate limit matches MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE.

The limit is a hardcoded constant in uploadFile.py. The API specification, the quotas and known
limitations pages and the check_user_rate_limit docstring state it, so each statement is read
back and compared with the constant.
"""

import os
import pathlib
import re

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[4]
_DOCS = _REPOSITORY_ROOT / "documentation" / "docusaurus-site" / "docs"
_STATED_IN = [
    _REPOSITORY_ROOT / "documentation" / "VAMS_API.yaml",
    _DOCS / "additional" / "quotas.md",
    _DOCS / "troubleshooting" / "known-limitations.md",
]
# "20 upload initializations per user per minute", "**20 upload initializations ...",
# "20 initializations per user per minute", "| Upload initializations | 20 per user per minute"
_STATEMENT = re.compile(
    r"(\d+)\*{0,2} (?:upload )?initializations per user per minute"
    r"|Upload initializations\s*\|\s*(\d+) per user per minute")


def _stated_limits():
    found = []
    for path in _STATED_IN:
        for number in _STATEMENT.findall(path.read_text(encoding="utf-8")):
            found.append((path.name, int(number[0] or number[1])))
    return found


@pytest.mark.unit
class TestUploadRateLimitStatements:
    def test_every_document_states_the_limit(self):
        assert {name for name, _ in _stated_limits()} == {path.name for path in _STATED_IN}

    def test_every_stated_limit_matches_the_constant(self):
        from backend.backend.handlers.assets import uploadFile as uf

        limit = uf.MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE
        wrong = [(name, number) for name, number in _stated_limits() if number != limit]

        assert wrong == []

    def test_the_rate_limit_docstring_names_the_constant(self):
        from backend.backend.handlers.assets import uploadFile as uf

        docstring = uf.check_user_rate_limit.__doc__
        assert "MAX_ALLOWED_UPLOAD_PERUSER_PERMINUTE" in docstring
        assert not re.search(r"\b\d+ upload initializations", docstring)
