# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ASCII-only rule on a caller-supplied assetId at create time.

The rule is deliberately narrower than ASSET_ID and lives at ONE place —
`create_asset()`, the single funnel both `POST /assets` and the two
`POST /ingest-asset` stages reach. `filename_pattern` / ASSET_ID stay
Unicode-tolerant because they are validated on read paths too, so an id an
earlier release stored has to keep matching them to stay addressable. That
split is what these tests pin: the create gate fires, and nothing that made a
legitimate id legitimate changed.
"""

import os

import pytest

from tests.handlers.assets.test_createAsset_conditional_put import _load

# Cyrillic 'у' and 'р' inside an otherwise Latin word: filename-legal (every
# character is `\w` or `-`), so it reaches the gate rather than being stopped by
# the shared rule first.
NON_ASCII_ASSET_ID = "pуmр-smoke"

# Uppercase, a space and a dot: exactly what ASSET_ID allows and the narrow ID
# rule (`^[-_a-zA-Z0-9]{3,63}$`) would reject.
LEGITIMATE_ASSET_ID = "Smoke Pump.v2"


def _request_model(m, asset_id, asset_name="asset-1"):
    return m.CreateAssetRequestModel(
        databaseId="testdb1",
        assetId=asset_id,
        assetName=asset_name,
        description="ascii assetId gate test",
        isDistributable=True,
        tags=[],
    )


def _wire_create(m):
    """Stub everything create_asset touches after the gate, recording the writes.

    The writers are recorded rather than merely replaced: a MagicMock standing in
    for a writer returns success either way, so "the asset was not created" has
    to be asserted from what save_asset_details was actually handed.
    """
    m.saved = []
    m.asset_table = _Recorder()
    m.database_table = _Recorder({"Item": {"databaseId": "testdb1"}})
    m.get_default_bucket_details = lambda *a, **k: {
        "bucketId": "b1", "bucketName": "bucket", "baseAssetsPrefix": ""
    }
    m.resolve_colocated_bucket_ids = lambda *a, **k: ["b1"]
    m.assert_existing_key_not_owned = lambda *a, **k: None
    m.assert_derived_asset_key_not_owned = lambda *a, **k: None
    m.check_s3_prefix_exists = lambda *a, **k: False
    m.create_prefix_folder = lambda *a, **k: None
    m.create_initial_version_record = lambda *a, **k: "v0"
    m.create_sns_topic_for_asset = lambda *a, **k: "arn:sns"
    m.save_asset_details = lambda item, *a, **k: m.saved.append(item)
    m.update_asset_count = lambda *a, **k: None
    m.validate_tags_exist = lambda *a, **k: True
    m.verify_all_required_tags_satisfied = lambda *a, **k: True
    m.write_asset_history_record = lambda *a, **k: None
    m.build_asset_snapshot = lambda *a, **k: {}


class _Recorder:
    """A DynamoDB table stand-in that records its reads instead of answering blind."""

    def __init__(self, get_item_result=None):
        self.get_item_calls = []
        self.put_item_calls = []
        self._get_item_result = get_item_result if get_item_result is not None else {}

    def get_item(self, **kwargs):
        self.get_item_calls.append(kwargs)
        return self._get_item_result

    def put_item(self, **kwargs):
        self.put_item_calls.append(kwargs)
        return {}


@pytest.mark.unit
class TestTheGateIsWiredToTheModuleUnderTest:
    """Module identity, asserted in band — a by-path load can silently resolve
    elsewhere, and every assertion below would then be about another file."""

    def test_the_loaded_module_is_the_repository_source(self):
        m = _load(fresh=True)
        assert os.path.normcase(os.path.abspath(m.__file__)) == os.path.normcase(
            os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__), "..", "..", "..",
                    "backend", "handlers", "assets", "createAsset.py",
                )
            )
        )

    def test_the_gate_helper_is_the_real_function_not_a_stub(self):
        m = _load(fresh=True)
        assert callable(m.validate_ascii_asset_id)
        assert m.validate_ascii_asset_id.__module__.endswith("assetsV3")


@pytest.mark.unit
class TestANonAsciiAssetIdIsRefusedAtCreate:
    def test_it_raises_a_value_error_naming_the_ascii_rule(self):
        m = _load(fresh=True)
        _wire_create(m)
        with pytest.raises(ValueError) as excinfo:
            m.create_asset(_request_model(m, NON_ASCII_ASSET_ID), {"tokens": ["user1"]})
        # The handler maps ValueError to a 400 carrying str(v), so the message is
        # what the caller is told; it must name the rule, not merely fail.
        assert "ASCII" in str(excinfo.value)

    def test_nothing_is_written(self):
        m = _load(fresh=True)
        _wire_create(m)
        with pytest.raises(ValueError):
            m.create_asset(_request_model(m, NON_ASCII_ASSET_ID), {"tokens": ["user1"]})
        assert m.saved == []
        assert m.asset_table.put_item_calls == []

    def test_the_gate_runs_before_the_existence_read(self):
        """The gate is the first statement, so a refused id costs no DynamoDB read.
        This also distinguishes the gate from the pre-existing duplicate-id check,
        which raises VAMSGeneralErrorResponse from the same function."""
        m = _load(fresh=True)
        _wire_create(m)
        with pytest.raises(ValueError):
            m.create_asset(_request_model(m, NON_ASCII_ASSET_ID), {"tokens": ["user1"]})
        assert m.asset_table.get_item_calls == []
        assert m.database_table.get_item_calls == []


@pytest.mark.unit
class TestLegitimateAssetIdsStillCreate:
    """POSITIVE CONTROLS. Without these the refusal above passes equally on a gate
    that rejects everything, or on an assetId narrowed toward the ID rule."""

    def test_an_uppercase_spaced_dotted_id_is_accepted(self):
        m = _load(fresh=True)
        _wire_create(m)
        response = m.create_asset(
            _request_model(m, LEGITIMATE_ASSET_ID), {"tokens": ["user1"]}
        )
        assert response.assetId == LEGITIMATE_ASSET_ID
        assert [item["assetId"] for item in m.saved] == [LEGITIMATE_ASSET_ID]

    def test_an_omitted_asset_id_still_auto_generates(self):
        m = _load(fresh=True)
        _wire_create(m)
        response = m.create_asset(_request_model(m, None), {"tokens": ["user1"]})
        assert response.assetId.startswith("x")
        assert len(m.saved) == 1


@pytest.mark.unit
class TestTheS3DerivedPathIsExempt:
    def test_the_gate_does_not_fire_when_s3_external_generated(self):
        """Branch coverage for the exemption, and nothing more.

        It proves the `not s3ExternalGenerated` condition is present and that a
        non-ASCII id reaches the writer through that branch. It does NOT prove
        bucket-sync auto-creation of a non-ASCII S3 folder works — see the
        companion test below, which measures why it cannot.
        """
        m = _load(fresh=True)
        _wire_create(m)
        response = m.create_asset(
            _request_model(m, NON_ASCII_ASSET_ID), {"tokens": ["SYSTEM_USER"]}, True
        )
        assert response.assetId == NON_ASCII_ASSET_ID
        assert [item["assetId"] for item in m.saved] == [NON_ASCII_ASSET_ID]

    def test_the_real_bucket_sync_shape_is_refused_by_the_assetName_rule(self):
        """Recorded, not changed, and pre-existing.

        `create_new_asset` in sqsBucketSync passes the S3 folder name as BOTH
        assetId and assetName, and assetName is validated as OBJECT_NAME
        (`^[a-zA-Z0-9\\-._\\s]{1,256}$`), which is ASCII-only and always has
        been. So a non-ASCII S3 folder is already rejected at the request model,
        before create_asset is reached — the exemption above is not what makes
        bucket-sync ingestion work, and removing it would change nothing for the
        shape bucket sync actually produces.
        """
        m = _load(fresh=True)
        with pytest.raises(m.ValidationError):
            _request_model(m, NON_ASCII_ASSET_ID, asset_name=NON_ASCII_ASSET_ID)
