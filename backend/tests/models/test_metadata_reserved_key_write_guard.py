# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The reserved DynamoDB metadata namespace, enforced on the write models.

``common/dynamoDbMetadataKeys.py`` is the single source of truth for the metadata keys and
field-name prefixes VAMS reserves, and it separates the two halves:

* ``EXCLUDED_METADATA_RECORD_KEYS`` -- system marker records. The reindexer creates and then
  deletes ``REINDEX_METADATA_RECORD`` under the same DynamoDB primary key a user record of that
  name would occupy, so a caller who writes it loses the record on the next reindex touch with
  no error anywhere. ``MetadataItemModel`` refuses it, on every write model that embeds it.
* ``VAMS_`` / ``_`` field prefixes -- accepted. Such a key is stored and returned by every
  metadata GET; the prefix costs only search indexing (and, for a leading underscore, asset
  export output). The tests below pin that they are NOT refused, because refusing them is the
  plausible over-tightening of this guard.

The check is on write only, so a record an earlier release stored under a reserved name stays
readable and deletable -- also pinned below.
"""

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

RESERVED_RECORD_KEY = "REINDEX_METADATA_RECORD"

WRITE_MODELS = [
    "CreateAssetLinkMetadataRequestModel",
    "UpdateAssetLinkMetadataRequestModel",
    "CreateAssetMetadataRequestModel",
    "UpdateAssetMetadataRequestModel",
    "CreateFileMetadataRequestModel",
    "UpdateFileMetadataRequestModel",
    "CreateDatabaseMetadataRequestModel",
    "UpdateDatabaseMetadataRequestModel",
]


def _model(name):
    import models.metadata as m
    return getattr(m, name)


def _write_body(model_name, metadata_key):
    """Minimal valid body for each write model, carrying one metadata item."""
    body = {"metadata": [{"metadataKey": metadata_key, "metadataValue": "x"}]}
    if model_name.endswith("FileMetadataRequestModel"):
        body["filePath"] = "/folder/file.glb"
        body["type"] = "metadata"
    return body


def _delete_body(model_name):
    """Minimal valid body for each delete model, naming one metadata key."""
    body = {"metadataKeys": [RESERVED_RECORD_KEY]}
    if model_name.endswith("FileMetadataRequestModel"):
        body["filePath"] = "/folder/file.glb"
        body["type"] = "metadata"
    return body


@pytest.mark.unit
class TestReservedRecordKeyRefusedOnWrite:
    def test_rejects_the_reindex_marker_record_key(self):
        from models.metadata import MetadataItemModel
        with pytest.raises(ValidationError):
            MetadataItemModel(
                metadataKey=RESERVED_RECORD_KEY,
                metadataValue="x",
                metadataValueType="string",
            )

    @pytest.mark.parametrize("model_name", WRITE_MODELS)
    def test_rejects_the_marker_key_through_every_write_model(self, model_name):
        with pytest.raises(ValidationError) as raised:
            parse(_write_body(model_name, RESERVED_RECORD_KEY), model=_model(model_name))
        # Named, so a body this model refuses for an unrelated reason (a missing filePath, say)
        # cannot stand in for the guard. The bulk-body test below is the matching accept arm.
        assert "metadataKey is reserved" in str(raised.value), raised.value

    def test_the_refusal_names_the_field_without_echoing_the_key(self):
        """Rule 11: the message reaches the client verbatim via validation_error_message."""
        from models.metadata import MetadataItemModel
        with pytest.raises(ValidationError) as raised:
            MetadataItemModel(metadataKey=RESERVED_RECORD_KEY, metadataValue="x")
        message = str(raised.value)
        assert "metadataKey is reserved" in message, message
        # The pydantic error location names the field, and nothing in the error carries the
        # submitted value -- asserted over the WHOLE message, since that is what reaches the
        # client. Scoping it to the text before the msg would also hold for an error that
        # appended the value after it.
        assert RESERVED_RECORD_KEY not in message, message

    def test_the_guard_reads_the_shared_excluded_key_set(self, monkeypatch):
        """A future system key added to the module must be covered with no model edit.

        This is also the positive control on the detector: it proves the refusal comes from
        ``EXCLUDED_METADATA_RECORD_KEYS`` rather than from a literal spelled in the model, so a
        test that only submits ``REINDEX_METADATA_RECORD`` is not measuring a hardcoded string.
        """
        import common.dynamoDbMetadataKeys as reserved_keys
        from models.metadata import MetadataItemModel

        monkeypatch.setattr(
            reserved_keys,
            "EXCLUDED_METADATA_RECORD_KEYS",
            frozenset({"FUTURE_SYSTEM_RECORD"}),
        )
        with pytest.raises(ValidationError):
            MetadataItemModel(metadataKey="FUTURE_SYSTEM_RECORD", metadataValue="x")


@pytest.mark.unit
class TestWhatTheGuardMustNotRefuse:
    """Positive controls. Each of these is a legitimate write today and must stay one."""

    def test_accepts_an_ordinary_key(self):
        from models.metadata import MetadataItemModel
        item = MetadataItemModel(metadataKey="colour", metadataValue="red")
        assert item.metadataKey == "colour"

    @pytest.mark.parametrize("metadata_key", ["VAMS_owner", "_classification"])
    def test_accepts_the_reserved_field_prefixes(self, metadata_key):
        """The prefixes are accepted-but-non-indexable, not refused."""
        from models.metadata import MetadataItemModel
        item = MetadataItemModel(metadataKey=metadata_key, metadataValue="bob")
        assert item.metadataKey == metadata_key

    def test_a_differently_cased_marker_name_is_not_the_marker(self):
        """DynamoDB keys are case-sensitive, so a lowercase name collides with nothing."""
        from models.metadata import MetadataItemModel
        item = MetadataItemModel(metadataKey=RESERVED_RECORD_KEY.lower(), metadataValue="x")
        assert item.metadataKey == RESERVED_RECORD_KEY.lower()

    @pytest.mark.parametrize("model_name", WRITE_MODELS)
    def test_every_write_model_still_accepts_a_bulk_body(self, model_name):
        body = _write_body(model_name, "colour")
        body["metadata"].append({"metadataKey": "VAMS_owner", "metadataValue": "bob"})
        model = parse(body, model=_model(model_name))
        assert [item.metadataKey for item in model.metadata] == ["colour", "VAMS_owner"]

    @pytest.mark.parametrize(
        "model_name",
        [
            "DeleteAssetLinkMetadataRequestModel",
            "DeleteAssetMetadataRequestModel",
            "DeleteFileMetadataRequestModel",
            "DeleteDatabaseMetadataRequestModel",
        ],
    )
    def test_a_record_stored_under_the_marker_key_can_still_be_deleted(self, model_name):
        """Save-side only: a record an earlier release stored must remain removable.

        All FOUR delete surfaces, matching the four the guard refuses: leaving one out would let a
        later edit close it without anything going red.
        """
        model = parse(_delete_body(model_name), model=_model(model_name))
        assert model.metadataKeys == [RESERVED_RECORD_KEY]

    def test_a_record_stored_under_the_marker_key_still_reads_back(self):
        """Save-side only: the GET response model carries no reserved-name rule."""
        from models.metadata import AssetMetadataResponseModel
        item = AssetMetadataResponseModel(
            databaseId="db1",
            assetId="asset1",
            metadataKey=RESERVED_RECORD_KEY,
            metadataValue="x",
            metadataValueType="string",
        )
        assert item.metadataKey == RESERVED_RECORD_KEY
