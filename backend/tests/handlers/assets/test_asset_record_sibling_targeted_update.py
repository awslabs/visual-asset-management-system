# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset-record writes on the two paths that share the upload-completion record with the asset API.

Three writers touch one asset row without any serialization between them: the asynchronous
large-file completion (``sqsUploadFileLarge``), which owns ``assetType`` or
``previewLocation``; and the asset edit API (``assetService.update_asset``), which owns the
caller-editable fields. Each reads the record and finishes later, so a full-record write
reverts whatever the other committed in between and returns 200 to both callers. Each writer
must therefore write only the attributes it owns, and only to a record that still exists --
an unconditional write recreates an asset archived mid-operation.
"""

import importlib.util
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

# Env vars the modules read at import time, set before importing them.
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")
os.environ.setdefault("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subs-table")

# Module-level import ensures the real backend.backend.handlers.assets package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.assets import sqsUploadFileLarge  # noqa: F401,E402

DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
BUCKET = "asset-bucket"


def _real_to_update_expr(record, op="SET"):
    """The real `common.dynamodb.to_update_expr`.

    Each handler binds `to_update_expr` at import time and `tests/conftest.py` re-registers
    `sys.modules['common.dynamodb']`, so the bound name can be a stand-in whose call yields
    nothing to unpack into three values. Patching the real logic in is what makes the
    expression the handler builds observable.
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


class FakeAssetTable:
    """In-memory asset table that applies a targeted SET update to the stored item.

    Records put_item separately so a full-record write is distinguishable from an attribute
    update, and honors an attribute_exists ConditionExpression so a write against a removed
    record fails the way DynamoDB fails it.
    """

    def __init__(self, item=None):
        self.items = {}
        if item is not None:
            self.items[(item['databaseId'], item['assetId'])] = dict(item)
        self.put_item_calls = []
        self.updated_attributes = []

    def stored(self):
        return self.items.get((DATABASE_ID, ASSET_ID))

    def put_item(self, Item, **kwargs):
        self.put_item_calls.append(Item)
        self.items[(Item['databaseId'], Item['assetId'])] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None, **kwargs):
        item = self.items.get((Key['databaseId'], Key['assetId']))
        if ConditionExpression and 'attribute_exists' in ConditionExpression and item is None:
            raise ClientError(
                {'Error': {'Code': 'ConditionalCheckFailedException',
                           'Message': 'The conditional request failed'}},
                'UpdateItem')
        assert UpdateExpression.startswith('SET '), UpdateExpression
        written = {}
        for assignment in UpdateExpression[len('SET '):].split(', '):
            name_ref, value_ref = [part.strip() for part in assignment.split(' = ')]
            written[ExpressionAttributeNames[name_ref]] = ExpressionAttributeValues[value_ref]
        self.updated_attributes.append(written)
        if item is not None:
            item.update(written)


def _asset(**overrides):
    item = {
        'databaseId': DATABASE_ID,
        'assetId': ASSET_ID,
        'bucketId': 'bucket-1',
        'assetLocation': {'Key': 'asset-1/'},
        'description': 'original',
        'tags': ['keep'],
        'assetType': 'none',
    }
    item.update(overrides)
    return item


#######################
# sqsUploadFileLarge -- the asynchronous large-file completion
#######################

def _run_file_processing(table, asset_read, asset_type='folder'):
    """Run update_asset_after_file_processing against the fake asset table.

    asset_read is the record the completion holds -- the snapshot taken before the
    concurrent edit that `table` already carries.
    """
    from backend.backend.handlers.assets import sqsUploadFileLarge as sq

    with patch.object(sq, 'get_asset_details', return_value=asset_read), \
            patch.object(sq, 'determine_asset_type', return_value=asset_type), \
            patch.object(sq, 'send_subscription_email', MagicMock()), \
            patch.object(sq, 'to_update_expr', _real_to_update_expr, create=True), \
            patch.object(sq, 'asset_table', table):
        sq.update_asset_after_file_processing(ASSET_ID, DATABASE_ID, BUCKET, 'asset-1/out/scan.laz')


def _run_preview_processing(table, asset_read, final_key='previews/asset-1/thumb.png'):
    from backend.backend.handlers.assets import sqsUploadFileLarge as sq

    with patch.object(sq, 'get_asset_details', return_value=asset_read), \
            patch.object(sq, 'to_update_expr', _real_to_update_expr, create=True), \
            patch.object(sq, 'asset_table', table):
        sq.update_asset_preview_location(ASSET_ID, DATABASE_ID, final_key)


@pytest.mark.unit
class TestLargeFileCompletionWrite:
    def test_concurrent_description_edit_survives_the_completion(self):
        table = FakeAssetTable(_asset(description='edited'))
        _run_file_processing(table, _asset(description='original'))

        assert table.stored()['description'] == 'edited'

    def test_completion_does_not_rewrite_the_whole_record(self):
        table = FakeAssetTable(_asset())
        _run_file_processing(table, _asset())

        assert table.put_item_calls == []
        assert table.updated_attributes == [{'assetType': 'folder'}]

    def test_completion_still_records_the_determined_asset_type(self):
        """POSITIVE CONTROL: the attribute the completion owns is still written."""
        table = FakeAssetTable(_asset())
        _run_file_processing(table, _asset())

        assert table.stored()['assetType'] == 'folder'

    def test_completion_keeps_an_existing_type_when_none_is_determined(self):
        """POSITIVE CONTROL for the fallback branch: an undetermined type does not blank
        a type the asset already carries."""
        table = FakeAssetTable(_asset(assetType='folder'))
        _run_file_processing(table, _asset(assetType='folder'), asset_type=None)

        assert table.stored()['assetType'] == 'folder'
        assert table.put_item_calls == []

    def test_asset_removed_during_processing_is_not_recreated(self):
        table = FakeAssetTable()  # the asset was archived/deleted mid-processing

        _run_file_processing(table, _asset())

        assert table.items == {}
        assert table.put_item_calls == []


@pytest.mark.unit
class TestLargeFilePreviewCompletionWrite:
    def test_concurrent_tag_edit_survives_the_preview_completion(self):
        table = FakeAssetTable(_asset(tags=['edited']))
        _run_preview_processing(table, _asset(tags=['keep']))

        assert table.stored()['tags'] == ['edited']

    def test_preview_completion_writes_only_the_preview_location(self):
        table = FakeAssetTable(_asset())
        _run_preview_processing(table, _asset())

        assert table.put_item_calls == []
        assert table.updated_attributes == [
            {'previewLocation': {'Key': 'previews/asset-1/thumb.png'}}]

    def test_preview_completion_still_sets_the_preview_location(self):
        """POSITIVE CONTROL: the attribute the preview completion owns is still written."""
        table = FakeAssetTable(_asset())
        _run_preview_processing(table, _asset())

        assert table.stored()['previewLocation'] == {'Key': 'previews/asset-1/thumb.png'}

    def test_asset_removed_during_preview_processing_is_not_recreated(self):
        table = FakeAssetTable()

        _run_preview_processing(table, _asset())

        assert table.items == {}
        assert table.put_item_calls == []


#######################
# assetService.update_asset -- the asset edit API
#######################

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)

_cached_asset_service = None


def _load_asset_service():
    """Load the real assetService module by file path with boto3 stubbed.

    The root conftest replaces `handlers.assets` with a non-package mock, so assetService
    cannot be imported through the package path. Loading it from its own absolute path gives
    a module whose functions resolve their globals in the returned module object, which is
    what makes assigning `m.asset_table` effective.
    """
    global _cached_asset_service
    if _cached_asset_service is not None:
        return _cached_asset_service

    stub_names = (
        "handlers.assets.assetCount", "handlers.assets.assetFiles",
        "handlers.authz", "handlers.auth",
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    count_stub = types.ModuleType("handlers.assets.assetCount")
    count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = count_stub

    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.aux_bucket_asset_file_base = (
        lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    )
    sys.modules["handlers.assets.assetFiles"] = files_stub

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    # The mock common.dynamodb module lacks the helpers assetService imports; add them
    # for the load.
    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    for helper in ("validate_pagination_info", "to_update_expr"):
        if dynamodb_mod is not None and not hasattr(dynamodb_mod, helper):
            setattr(dynamodb_mod, helper, MagicMock())
            added_attrs.append(helper)

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetService_sibling_write_under_test", os.path.abspath(_ASSET_SERVICE_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        for attr in added_attrs:
            if dynamodb_mod is not None:
                delattr(dynamodb_mod, attr)

    _cached_asset_service = module
    return module


def _run_update_asset(table, asset_read, update_data):
    svc = _load_asset_service()
    # Harness check: the function under test must resolve its globals in this very module
    # object, otherwise the assignments below patch a different copy and assert nothing.
    assert svc.update_asset.__globals__ is svc.__dict__

    with patch.object(svc, 'get_asset_details', return_value=asset_read), \
            patch.object(svc, 'authorize_single_asset', return_value=True), \
            patch.object(svc, 'write_asset_history_record', MagicMock()), \
            patch.object(svc, 'build_asset_snapshot', MagicMock(return_value={})), \
            patch.object(svc, 'send_subscription_email', MagicMock()), \
            patch.object(svc, 'to_update_expr', _real_to_update_expr, create=True), \
            patch.object(svc, 'asset_table', table):
        return svc.update_asset(DATABASE_ID, ASSET_ID, update_data,
                                {"tokens": ["alice@corp"], "roles": []})


@pytest.mark.unit
class TestAssetEditWrite:
    def test_concurrent_preview_location_survives_a_description_edit(self):
        """The upload completion's previewLocation, committed after the edit read the record,
        is not reverted by the edit's write."""
        table = FakeAssetTable(_asset(previewLocation={'Key': 'previews/asset-1/thumb.png'}))
        response = _run_update_asset(table, _asset(), {'description': 'new'})

        assert response.success is True
        assert table.stored()['previewLocation'] == {'Key': 'previews/asset-1/thumb.png'}

    def test_concurrent_asset_type_survives_a_description_edit(self):
        table = FakeAssetTable(_asset(assetType='folder'))
        _run_update_asset(table, _asset(assetType='none'), {'description': 'new'})

        assert table.stored()['assetType'] == 'folder'

    def test_edit_writes_only_the_edited_attributes(self):
        table = FakeAssetTable(_asset())
        _run_update_asset(table, _asset(), {'description': 'new', 'isDistributable': True})

        assert table.put_item_calls == []
        assert table.updated_attributes == [{'description': 'new', 'isDistributable': True}]

    def test_the_edit_still_lands(self):
        """POSITIVE CONTROL: the edited fields are actually persisted."""
        table = FakeAssetTable(_asset())
        _run_update_asset(table, _asset(),
                          {'description': 'new', 'assetName': 'renamed'})

        assert table.stored()['description'] == 'new'
        assert table.stored()['assetName'] == 'renamed'

    def test_asset_archived_mid_edit_is_not_recreated(self):
        svc = _load_asset_service()
        table = FakeAssetTable()  # archive moved the row to the #deleted partition

        with pytest.raises(svc.VAMSGeneralErrorResponse):
            _run_update_asset(table, _asset(), {'description': 'new'})

        assert table.items == {}
        assert table.put_item_calls == []
