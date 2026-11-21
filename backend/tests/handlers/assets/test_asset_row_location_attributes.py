# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every asset-row writer sets or keeps the location index attributes.

`locationBucket` and `locationKey` (common/assetLocations.py) are what AssetLocationGSI indexes. An
asset is created with them, and the writers that put a whole row back (archive, unarchive, preview
delete, the bucket-sync restore) must carry them over, or the asset drops out of the index, and give
them to a row that lacks them (`fill_location_attributes`), so a copy the backfill's last pass did not
see is indexed too. Two parts:

- an inventory of every write on the asset table handle under backend/backend, found by reading
  the source: a new writer fails it until it is listed here, and a new whole-row put also needs a
  preservation test below;
- behaviour tests per whole-row writer, driven with a stored row that carries the attributes and
  with one that lacks them.

The inventory finds writes made through an asset table handle (a module's `asset_table` or
`asset_storage_table`, a local `table = dynamodb.Table(asset_table_name)`, a handle kept on an object
such as `self.asset_table`) and low-level client writes that name the asset table (`TableName=`, a
transaction item's `TableName`, a `RequestItems` key). Today the only low-level calls on the asset
table are queries. A helper that receives the table as a parameter
(`common/assetProvenance.record_indexed_change`, an attribute-scoped UpdateItem) is outside it.
"""

import ast
import os
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.assets import assetFiles as _assetFiles
from backend.backend.handlers.assets import assetVersions as _assetVersions
from tests.handlers.assets.test_assetService_history import _load as _load_asset_service
from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create_asset
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load as _load_bucket_sync


BACKEND_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
WRITE_METHODS = {"put_item", "update_item", "delete_item", "batch_writer", "transact_write_items",
                 "batch_write_item"}

# (module, function, method) for every write on an asset table handle.
EXPECTED_ASSET_ROW_WRITES = {
    ("handlers/assets/createAsset.py", "save_asset_details", "put_item"),
    ("handlers/assets/assetService.py", "update_asset", "update_item"),
    ("handlers/assets/assetService.py", "archive_asset", "put_item"),
    ("handlers/assets/assetService.py", "archive_asset", "delete_item"),
    ("handlers/assets/assetService.py", "unarchive_asset", "put_item"),
    ("handlers/assets/assetService.py", "unarchive_asset", "delete_item"),
    ("handlers/assets/assetService.py", "delete_asset_permanent", "delete_item"),
    ("handlers/assets/assetVersions.py", "update_asset_current_version_reference", "update_item"),
    ("handlers/assets/assetFiles.py", "delete_asset_preview", "put_item"),
    ("handlers/assets/largeObjectCopy.py", "_after_landing", "update_item"),
    ("handlers/assets/sqsUploadFileLarge.py", "update_asset_attributes", "update_item"),
    ("handlers/assets/uploadFile.py", "update_asset_attributes", "update_item"),
    ("handlers/indexing/sqsBucketSync.py", "_update_asset_type_attribute", "update_item"),
    ("handlers/indexing/sqsBucketSync.py", "restore_archived_asset", "put_item"),
    ("handlers/indexing/sqsBucketSync.py", "restore_archived_asset", "delete_item"),
    ("handlers/subscription/subscriptionService.py", "add_sns_topic_in_asset", "update_item"),
    ("handlers/subscription/subscriptionService.py", "delete_sns_subscriptions", "update_item"),
    ("handlers/subscription/unsubscribeService.py", "delete_sns_subscriptions", "update_item"),
}

# The whole-row puts, each with a preservation test in this file (createAsset sets them instead).
WHOLE_ROW_PUTS_WITH_TESTS = {
    ("handlers/assets/createAsset.py", "save_asset_details"),
    ("handlers/assets/assetService.py", "archive_asset"),
    ("handlers/assets/assetService.py", "unarchive_asset"),
    ("handlers/assets/assetFiles.py", "delete_asset_preview"),
    ("handlers/indexing/sqsBucketSync.py", "restore_archived_asset"),
}

# The stored attribute names are part of the index contract (AssetLocationGSI keys on them).
INDEX_ATTRIBUTES = {"locationBucket": "vams-assets", "locationKey": "a1/"}


def _is_asset_table_name_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "get_table_name" and node.args
            and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "ASSET_STORAGE_TABLE")


def _assigned_names(tree, predicate):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and predicate(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _assigned_attributes(tree, predicate):
    """Attribute names (`self.<name> = ...`, `obj.<name> = ...`) assigned a value matching `predicate`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and predicate(node.value):
            names.update(t.attr for t in node.targets if isinstance(t, ast.Attribute))
    return names


def _refers_to(node, names, attributes):
    """`node` is a variable in `names` or an attribute access `<x>.<name>` with name in `attributes`."""
    return ((isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.Attribute) and node.attr in attributes))


def _asset_table_writes(path, relpath):
    """(relpath, function, method) for every write on the asset table in one source file.

    A write counts when it goes through an asset table handle (a module variable, a function's local
    or an attribute on an object such as `self.asset_table`, assigned `<resource>.Table(<asset table
    name>)`), or through a low-level client call that names the asset table: `TableName=<asset table
    name>` as a keyword or inside a dict (`transact_write_items` items), or the table name as a
    `RequestItems` key (`batch_write_item`)."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    name_vars = _assigned_names(tree, _is_asset_table_name_call)
    name_attrs = _assigned_attributes(tree, _is_asset_table_name_call)
    if not name_vars and not name_attrs:
        return set()

    def names_the_table(node):
        return _refers_to(node, name_vars, name_attrs) or _is_asset_table_name_call(node)

    def is_table_handle(value):
        return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                and value.func.attr == "Table" and value.args and names_the_table(value.args[0]))

    def names_the_table_in_arguments(call):
        for keyword in call.keywords:
            if keyword.arg == "TableName" and names_the_table(keyword.value):
                return True
        for node in ast.walk(call):
            if node is call or not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "TableName" and names_the_table(value):
                    return True
                if key is not None and names_the_table(key):
                    return True
        return False

    module_handles = set()
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Assign) and is_table_handle(node.value):
            module_handles.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Try, ast.If)):
            pending.extend(node.body + node.orelse + getattr(node, "finalbody", [])
                           + [s for h in getattr(node, "handlers", []) for s in h.body])
    attribute_handles = _assigned_attributes(tree, is_table_handle)
    writes = set()
    for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        handles = module_handles | _assigned_names(function, is_table_handle)
        for call in ast.walk(function):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in WRITE_METHODS):
                continue
            if (_refers_to(call.func.value, handles, attribute_handles)
                    or names_the_table_in_arguments(call)):
                writes.add((relpath, function.name, call.func.attr))
    return writes


def _all_asset_table_writes():
    writes = set()
    for dirpath, _, filenames in os.walk(BACKEND_ROOT):
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                relpath = os.path.relpath(path, BACKEND_ROOT).replace(os.sep, "/")
                writes |= _asset_table_writes(path, relpath)
    return writes


@pytest.mark.unit
class TestAssetRowWriterInventory:
    def test_every_asset_row_write_is_listed(self):
        found = _all_asset_table_writes()
        assert found == EXPECTED_ASSET_ROW_WRITES, (
            "An asset-row write was added or removed. A new writer must set or keep "
            f"{sorted(INDEX_ATTRIBUTES)} (a whole-row put needs a preservation test here). "
            f"Added: {sorted(found - EXPECTED_ASSET_ROW_WRITES)}; "
            f"removed: {sorted(EXPECTED_ASSET_ROW_WRITES - found)}")

    def test_every_whole_row_put_has_a_preservation_test(self):
        puts = {(module, function) for module, function, method in EXPECTED_ASSET_ROW_WRITES
                if method == "put_item"}
        assert puts == WHOLE_ROW_PUTS_WITH_TESTS

    def test_inventory_detects_a_write_through_a_local_handle(self, tmp_path):
        # Control: the reader finds the local `table = dynamodb.Table(asset_table_name)` form.
        source = tmp_path / "writer.py"
        source.write_text(
            "asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)\n"
            "def rewrite(row):\n"
            "    table = dynamodb.Table(asset_table_name)\n"
            "    table.put_item(Item=row)\n"
            "def other(row):\n"
            "    table = dynamodb.Table(other_table_name)\n"
            "    table.put_item(Item=row)\n")
        assert _asset_table_writes(str(source), "writer.py") == {("writer.py", "rewrite", "put_item")}

    def test_inventory_detects_low_level_client_writes(self, tmp_path):
        # Control: a client call that names the asset table counts, as a keyword, inside a
        # transaction item and as a batch RequestItems key; a read and another table do not.
        source = tmp_path / "writer.py"
        source.write_text(
            "asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)\n"
            "other_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)\n"
            "def put(item):\n"
            "    dynamodb_client.put_item(TableName=asset_table_name, Item=item)\n"
            "def move(item, key):\n"
            "    dynamodb_client.transact_write_items(TransactItems=[\n"
            "        {'Put': {'TableName': asset_table_name, 'Item': item}},\n"
            "        {'Delete': {'TableName': asset_table_name, 'Key': key}}])\n"
            "def batch(requests):\n"
            "    dynamodb_client.batch_write_item(RequestItems={asset_table_name: requests})\n"
            "def direct(item):\n"
            "    client.update_item(TableName=get_table_name(ResourceKeys.ASSET_STORAGE_TABLE), Key=item)\n"
            "def read(key):\n"
            "    return dynamodb_client.query(TableName=asset_table_name, KeyConditionExpression=key)\n"
            "def other(item):\n"
            "    dynamodb_client.put_item(TableName=other_table_name, Item=item)\n")
        assert _asset_table_writes(str(source), "writer.py") == {
            ("writer.py", "put", "put_item"),
            ("writer.py", "move", "transact_write_items"),
            ("writer.py", "batch", "batch_write_item"),
            ("writer.py", "direct", "update_item"),
        }

    def test_inventory_detects_a_handle_kept_on_an_object(self, tmp_path):
        # Control: a table handle (or table name) kept on `self` counts in every method that uses it.
        source = tmp_path / "writer.py"
        source.write_text(
            "class Store:\n"
            "    def __init__(self, resource, client):\n"
            "        self.table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)\n"
            "        self.asset_table = resource.Table(self.table_name)\n"
            "        self.other = resource.Table(other_table_name)\n"
            "        self.client = client\n"
            "    def save(self, row):\n"
            "        self.asset_table.put_item(Item=row)\n"
            "    def remove(self, key):\n"
            "        self.client.delete_item(TableName=self.table_name, Key=key)\n"
            "    def other_write(self, row):\n"
            "        self.other.put_item(Item=row)\n")
        assert _asset_table_writes(str(source), "writer.py") == {
            ("writer.py", "save", "put_item"),
            ("writer.py", "remove", "delete_item"),
        }

    def test_only_the_location_module_names_the_index_attributes(self):
        # Writers set the attributes through common.assetLocations, so an expression that drops them
        # cannot be written without naming them here.
        offenders = []
        for dirpath, _, filenames in os.walk(BACKEND_ROOT):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                relpath = os.path.relpath(path, BACKEND_ROOT).replace(os.sep, "/")
                if relpath == "common/assetLocations.py":
                    continue
                text = open(path, encoding="utf-8").read()
                if "locationBucket" in text or "'locationKey'" in text or '"locationKey"' in text:
                    offenders.append(relpath)
        assert offenders == []


def _stored_row(database_id="db1", asset_id="a1", **extra):
    row = {
        "databaseId": database_id, "assetId": asset_id, "assetName": "N1", "description": "d1",
        "isDistributable": True, "tags": [], "bucketId": "b1", "assetLocation": {"Key": f"{asset_id}/"},
        **INDEX_ATTRIBUTES,
    }
    row.update(extra)
    return row


def _put_item(table):
    return table.put_item.call_args.kwargs["Item"]


@pytest.mark.unit
class TestCreateAssetSetsTheAttributes:
    def _wire(self, m, base_prefix):
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {}
        m.database_table = MagicMock()
        m.database_table.get_item.return_value = {"Item": {"databaseId": "testdb1"}}
        m.get_default_bucket_details = MagicMock(return_value={
            "bucketId": "b1", "bucketName": "vams-assets", "baseAssetsPrefix": base_prefix})
        m.assert_existing_key_not_owned = MagicMock()
        m.assert_derived_asset_key_not_owned = MagicMock()
        m.resolve_colocated_bucket_ids = MagicMock(return_value=["b1"])
        m.check_s3_prefix_exists = MagicMock(return_value=False)
        m.check_s3_key_exists = MagicMock(return_value=True)
        m.create_prefix_folder = MagicMock()
        m.create_initial_version_record = MagicMock(return_value="0")
        m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
        m.update_asset_count = MagicMock()
        m.write_asset_history_record = MagicMock()
        m.validate_tags_exist = MagicMock(return_value=True)
        m.verify_all_required_tags_satisfied = MagicMock(return_value=True)

    @staticmethod
    def _request(m, **extra):
        return m.CreateAssetRequestModel(databaseId="testdb1", assetId="asset-1", assetName="asset-1",
                                         description="location attributes", isDistributable=True, tags=[], **extra)

    @pytest.mark.parametrize("base_prefix,expected_key", [("", "asset-1/"), ("assets/", "assets/asset-1/")])
    def test_derived_location(self, base_prefix, expected_key):
        m = _load_create_asset(fresh=True)
        self._wire(m, base_prefix)
        m.create_asset(self._request(m), {"tokens": ["user1"]})
        row = _put_item(m.asset_table)
        assert row["assetLocation"] == {"Key": expected_key}
        assert row["locationBucket"] == "vams-assets"
        assert row["locationKey"] == expected_key
        # The derived key is what the create path composed before the shared helper existed.
        assert expected_key == base_prefix + "asset-1/"

    @pytest.mark.parametrize("existing_key,expected_key", [
        ("projects/alpha/", "projects/alpha/"),
        ("/projects/alpha/", "projects/alpha/"),
        ("projects/model.glb", "projects/model.glb"),
    ])
    def test_existing_key_location(self, existing_key, expected_key):
        m = _load_create_asset(fresh=True)
        self._wire(m, "")
        m.create_asset(self._request(m, bucketExistingKey=existing_key), {"tokens": ["user1"]})
        row = _put_item(m.asset_table)
        assert row["assetLocation"] == {"Key": expected_key}
        assert row["locationBucket"] == "vams-assets"
        assert row["locationKey"] == expected_key

    def test_bucket_sync_auto_create_sets_them(self):
        m = _load_create_asset(fresh=True)
        self._wire(m, "")
        m.check_s3_prefix_exists = MagicMock(return_value=True)
        m.create_asset(self._request(m), {"tokens": ["SYSTEM_USER"]}, True)
        row = _put_item(m.asset_table)
        assert (row["locationBucket"], row["locationKey"]) == ("vams-assets", "asset-1/")


@pytest.fixture
def asset_service(monkeypatch):
    """assetService with one stored asset row (`stored["row"]`) and the registration's bucket name
    `vams-assets`."""
    m = _load_asset_service()
    stored = {"row": _stored_row()}
    asset_table = MagicMock()
    asset_table.get_item.side_effect = lambda Key, **_: (
        {"Item": dict(stored["row"])} if Key["databaseId"] == stored["row"]["databaseId"] else {})
    db_table = MagicMock()
    db_table.get_item.return_value = {"Item": {"databaseId": "db1"}}
    for name, value in {
        "asset_table": asset_table, "db_table": db_table,
        "write_asset_history_record": MagicMock(), "send_subscription_email": MagicMock(),
        "get_asset_bucket_details": MagicMock(return_value={"bucketName": "vams-assets"}),
        "archive_multi_assetFiles": MagicMock(), "archive_file_preview": MagicMock(),
        "unarchive_multi_assetFiles": MagicMock(return_value=0), "unarchive_file_preview": MagicMock(),
        "update_asset_count": MagicMock(),
    }.items():
        monkeypatch.setattr(m, name, value)
    return m, stored, asset_table


def _without_index_attributes(row):
    return {k: v for k, v in row.items() if k not in INDEX_ATTRIBUTES}


def _wire_preview_delete(monkeypatch, row):
    asset_table = MagicMock()
    monkeypatch.setattr(_assetFiles, "asset_table", asset_table)
    monkeypatch.setattr(_assetFiles, "get_asset_with_permissions", MagicMock(return_value=row))
    monkeypatch.setattr(_assetFiles, "get_default_bucket_details",
                        MagicMock(return_value={"bucketName": "vams-assets"}))
    monkeypatch.setattr(_assetFiles, "delete_s3_object_all_versions", MagicMock(return_value=True))
    monkeypatch.setattr(_assetFiles, "send_subscription_email", MagicMock())
    return asset_table


def _wire_bucket_sync_restore(monkeypatch, stored_archived_row):
    m = _load_bucket_sync()
    table = MagicMock()
    table.get_item.side_effect = lambda Key, **_: (
        {"Item": dict(stored_archived_row)} if Key.get("databaseId") == "db1#deleted"
        else {"Item": {"databaseId": "db1"}})
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    monkeypatch.setattr(m, "dynamodb", dynamodb)
    monkeypatch.setattr(m, "update_asset_count", MagicMock())
    monkeypatch.setattr(m, "write_asset_history_record", MagicMock())
    monkeypatch.setattr(m, "asset_cache", MagicMock())
    return m, table


@pytest.mark.unit
class TestWholeRowWritersKeepTheAttributes:
    def test_archive(self, asset_service):
        m, _, asset_table = asset_service
        request = MagicMock(reason="cleanup")
        m.archive_asset("db1", "a1", request, {"tokens": ["u1"]})
        row = _put_item(asset_table)
        assert row["databaseId"] == "db1#deleted"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_unarchive(self, asset_service):
        m, stored, asset_table = asset_service
        stored["row"] = _stored_row(database_id="db1#deleted", status="archived")
        request = MagicMock(reason="restore", unarchiveFiles=False)
        m.unarchive_asset("db1", "a1", request, {"tokens": ["u1"]})
        row = _put_item(asset_table)
        assert row["databaseId"] == "db1"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_current_version_reference(self, monkeypatch):
        # A targeted update of the version attributes: it neither sets nor removes an index attribute.
        asset_table = MagicMock()
        monkeypatch.setattr(_assetVersions, "asset_table", asset_table)
        assert _assetVersions.update_asset_current_version_reference(_stored_row(), "3") is True
        asset_table.put_item.assert_not_called()
        update = asset_table.update_item.call_args.kwargs
        assert "currentVersionId" in update["ExpressionAttributeNames"].values()
        assert not set(INDEX_ATTRIBUTES) & set(update["ExpressionAttributeNames"].values())
        assert "locationBucket" not in update["UpdateExpression"] and "locationKey" not in update["UpdateExpression"]

    def test_preview_delete(self, monkeypatch):
        asset_table = _wire_preview_delete(monkeypatch, _stored_row(previewLocation={"Key": "previews/a1/p.png"}))
        _assetFiles.delete_asset_preview("db1", "a1", {"tokens": ["u1"]})
        row = _put_item(asset_table)
        assert "previewLocation" not in row
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_bucket_sync_restore(self, monkeypatch):
        archived = _stored_row(database_id="db1#deleted", status="archived")
        m, table = _wire_bucket_sync_restore(monkeypatch, archived)
        # The caller's image predates the attributes; the row as stored carries them.
        assert m.restore_archived_asset("b1", "a1", _without_index_attributes(archived)) == "db1"
        row = _put_item(table)
        assert row["databaseId"] == "db1"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_stored_attributes_are_kept_as_stored(self, asset_service):
        # A row that carries both attributes is written back with them, not recomputed from the
        # registration (here a different bucket name).
        m, _, asset_table = asset_service
        m.get_asset_bucket_details = MagicMock(return_value={"bucketName": "another-name"})
        m.archive_asset("db1", "a1", MagicMock(reason="cleanup"), {"tokens": ["u1"]})
        assert {k: _put_item(asset_table)[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES


@pytest.mark.unit
class TestWholeRowWritersFillMissingAttributes:
    """A row the backfill has not reached is written back WITH the attributes (L1): the backfill's
    consistent verification scan is not a snapshot, so a copy written behind its cursor would
    otherwise stay out of the index while the marker says complete."""

    def test_archive(self, asset_service):
        m, stored, asset_table = asset_service
        stored["row"] = _without_index_attributes(_stored_row())
        m.archive_asset("db1", "a1", MagicMock(reason="cleanup"), {"tokens": ["u1"]})
        row = _put_item(asset_table)
        assert row["databaseId"] == "db1#deleted"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_unarchive(self, asset_service):
        m, stored, asset_table = asset_service
        stored["row"] = _without_index_attributes(_stored_row(database_id="db1#deleted", status="archived"))
        m.unarchive_asset("db1", "a1", MagicMock(reason="restore", unarchiveFiles=False), {"tokens": ["u1"]})
        row = _put_item(asset_table)
        assert row["databaseId"] == "db1"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_preview_delete(self, monkeypatch):
        row = _without_index_attributes(_stored_row(previewLocation={"Key": "previews/a1/p.png"}))
        asset_table = _wire_preview_delete(monkeypatch, row)
        _assetFiles.delete_asset_preview("db1", "a1", {"tokens": ["u1"]})
        assert {k: _put_item(asset_table)[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES

    def test_bucket_sync_restore(self, monkeypatch):
        # The registration's bucket name is the event's bucket: no extra registration read.
        archived = _without_index_attributes(_stored_row(database_id="db1#deleted", status="archived"))
        m, table = _wire_bucket_sync_restore(monkeypatch, archived)
        assert m.restore_archived_asset("b1", "a1", archived, bucket_name="vams-assets") == "db1"
        row = _put_item(table)
        assert row["databaseId"] == "db1"
        assert {k: row[k] for k in INDEX_ATTRIBUTES} == INDEX_ATTRIBUTES
        table.query.assert_not_called()

    def test_bucket_sync_restore_without_a_bucket_name_invents_nothing(self, monkeypatch):
        archived = _without_index_attributes(_stored_row(database_id="db1#deleted", status="archived"))
        m, table = _wire_bucket_sync_restore(monkeypatch, archived)
        assert m.restore_archived_asset("b1", "a1", archived) == "db1"
        assert not set(INDEX_ATTRIBUTES) & set(_put_item(table))
