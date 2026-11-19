# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Download as Zip of a single-file asset is refused before any job is recorded.

A single-file asset's location key names one object (no trailing ``/``). The archive job expands a
whole-asset or folder entry by listing the asset's key as an S3 prefix, which for a single-file
location also lists every longer key that starts with the same characters, so the create refuses such
an asset outright and points the caller at the plain file download. A folder asset is the control.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

MESSAGE = ("An asset that holds a single file cannot be downloaded as a zip. "
           "Download the file instead.")


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.assets.items[("db1", "asset1")]["assetLocation"] = {"Key": "xasset1/model.glb"}
    for key in ("xasset1/model.glb", "xasset1/model.glb.bak", "xasset1/model.glb/inner.txt"):
        e.s3.add(key, size=10)
    return e


def _create(e, selection):
    h.AUTH.user = "alice"
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path("db1", "asset1"), {"databaseId": "db1", "assetId": "asset1"},
        body={"selection": selection}), None)


@pytest.mark.parametrize("selection", [
    [{"type": "asset", "relativePath": "/"}],
    [{"type": "file", "relativePath": "/model.glb"}],
    [{"type": "folder", "relativePath": "/model.glb/"}],
], ids=["asset", "file", "folder"])
def test_a_single_file_asset_is_refused_and_no_job_is_recorded(env, selection):
    response = _create(env, selection)

    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == MESSAGE
    assert env.zip.items == {}


def test_control_a_folder_asset_still_creates_a_job(env):
    env.assets.items[("db1", "asset1")]["assetLocation"] = {"Key": "xasset1/"}

    response = _create(env, [{"type": "asset", "relativePath": "/"}])

    assert response["statusCode"] in (200, 201, 202)
    assert len(env.zip.items) == 1
