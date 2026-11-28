# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The answer every file route gives a quarantined asset while the download block is on (D169).

The web tests render the viewers, previews and thumbnails with the body in
``web/src/visualizerPlugin/core/quarantineRefusalBody.testdata.json``. The handler tests compare
each route's real response with the same file, so the web is tested with the body the API sends and
the two cannot drift apart.
"""

import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
QUARANTINE_REFUSAL_TESTDATA = (
    REPO_ROOT / "web" / "src" / "visualizerPlugin" / "core" / "quarantineRefusalBody.testdata.json"
)


def quarantine_refusal():
    """``{"statusCode": 400, "body": {"message": ...}}`` as the web test data holds it."""
    return json.loads(QUARANTINE_REFUSAL_TESTDATA.read_text(encoding="utf-8"))


def assert_is_the_quarantine_refusal(response):
    """The handler's response is the documented refusal: its status and its whole body."""
    expected = quarantine_refusal()
    assert response["statusCode"] == expected["statusCode"]
    assert json.loads(response["body"]) == expected["body"]
