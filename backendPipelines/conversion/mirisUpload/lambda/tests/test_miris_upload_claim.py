#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The gate and every release site derive the claim key here, so they cannot drift apart."""
import io
from unittest.mock import MagicMock

import pytest

import mirisClaim
from miris_upload_fakes import FakeS3, claim_held_by

pytestmark = pytest.mark.unit


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


# The workflow supplies `auxBucket + pipelines/{pipelineName}/{executionId}/`.
EXEC_A = "s3://vams-aux-bucket/pipelines/miris-upload/exec-1111/"
EXEC_B = "s3://vams-aux-bucket/pipelines/miris-upload/exec-2222/"
FP = "f" * 64
USD_EXTENSIONS = [".usd", ".usda", ".usdc", ".usdz"]


@pytest.mark.parametrize("aux", [EXEC_A, EXEC_B, "s3://vams-aux-bucket"])
def test_key_is_bucket_root_scoped_and_execution_independent(aux):
    assert mirisClaim.claim_location(aux, "db1", "a1", FP) == (
        "vams-aux-bucket", f"locks/miris-upload/db1/a1/{FP}.claim")
    assert mirisClaim.claim_location(aux, "db2", "a1", FP) != mirisClaim.claim_location(aux, "db1", "a1", FP)


@pytest.mark.parametrize("aux,database,asset,fingerprint", [
    ("", "db1", "a1", FP), (EXEC_A, "", "a1", FP), (EXEC_A, "db1", "", FP), (EXEC_A, "db1", "a1", "")])
def test_missing_inputs_yield_no_location(aux, database, asset, fingerprint):
    assert mirisClaim.claim_location(aux, database, asset, fingerprint) == (None, None)


# --- content fingerprint ----------------------------------------------------------------------


def _asset(**files):
    """An asset bucket holding `files` ({relative path: etag}) under the root `a1/`."""
    s3 = FakeS3()
    for rel, etag in files.items():
        s3.put_file("assets", f"a1/{rel}", etag)
    return s3


def test_the_extension_match_is_exact_and_case_insensitive():
    s3 = _asset(**{"Scene.USDA": "e1", "scene.usda.bak": "e2", "x.us": "e3", "folder/": "e4"})
    files = mirisClaim.list_trigger_files(s3, "assets", "a1/", USD_EXTENSIONS)
    assert files == [("Scene.USDA", '"e1"')]


def test_the_fingerprint_ignores_listing_order_and_is_empty_without_files():
    pairs = [("b.usd", '"2"'), ("a.usd", '"1"')]
    assert mirisClaim.content_fingerprint(pairs) == mirisClaim.content_fingerprint(pairs[::-1])
    assert mirisClaim.content_fingerprint([]) == ""


@pytest.mark.parametrize("root,expected", [
    ("a1/", "a1/"), ("a1", "a1/"), ("/vams/data/a1", "vams/data/a1/"), ("", ""), (None, "")])
def test_asset_root_prefix_normalizes_to_a_folder(root, expected):
    assert mirisClaim.asset_root_prefix(root) == expected


# --- release ----------------------------------------------------------------------------------


def _held_by(token):
    return claim_held_by(MagicMock(), token)


def test_release_deletes_the_claim_this_execution_holds():
    s3 = _held_by("tok-a")
    assert mirisClaim.release_claim(
        s3, _Logger(), EXEC_A, "db1", "a1", FP, mirisClaim.owner_id("tok-a")) is True
    s3.delete_object.assert_called_once_with(
        Bucket="vams-aux-bucket", Key=f"locks/miris-upload/db1/a1/{FP}.claim")


@pytest.mark.parametrize("aux, fingerprint", [("", FP), (EXEC_A, "")], ids=["no-location", "manual-run"])
def test_release_without_a_claim_location_is_a_no_op(aux, fingerprint):
    s3 = MagicMock()
    assert mirisClaim.release_claim(s3, _Logger(), aux, "db1", "a1", fingerprint, "owner") is False
    s3.get_object.assert_not_called()
    s3.delete_object.assert_not_called()


def test_release_swallows_delete_errors():
    s3 = _held_by("tok-a")
    s3.delete_object.side_effect = Exception("AccessDenied")
    assert mirisClaim.release_claim(
        s3, _Logger(), EXEC_A, "db1", "a1", FP, mirisClaim.owner_id("tok-a")) is False


def test_release_leaves_a_claim_held_by_another_execution():
    s3 = _held_by("tok-b")
    assert mirisClaim.release_claim(
        s3, _Logger(), EXEC_A, "db1", "a1", FP, mirisClaim.owner_id("tok-a")) is False
    s3.delete_object.assert_not_called()


@pytest.mark.parametrize("body", [b"not json", b"[]"])
def test_release_leaves_an_unreadable_claim(body):
    s3 = MagicMock()
    s3.get_object.side_effect = lambda **_: {"Body": io.BytesIO(body)}
    assert mirisClaim.release_claim(s3, _Logger(), EXEC_A, "db1", "a1", FP, "") is False
    s3.delete_object.assert_not_called()


def test_owner_id_is_a_digest_and_empty_without_a_token():
    assert mirisClaim.owner_id("") == ""
    assert len(mirisClaim.owner_id("tok-a")) == 64
    assert "tok-a" not in mirisClaim.owner_id("tok-a")
