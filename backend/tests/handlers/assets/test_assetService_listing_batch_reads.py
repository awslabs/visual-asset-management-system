# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset listings enrich a page with batched reads, not one read per asset (#389, #395).

Both list branches of handle_get_request -- the per-database listing and the all-databases listing
-- used to issue one versions-table get_item and one buckets-table query PER ASSET on the page. The
page is now enriched by enrich_asset_listing_page: the current-version rows come back from
BatchGetItem through the shared common.dynamodb.batch_get_items (chunks of 100, UnprocessedKeys
re-requested with backoff, bounded), and bucket details are resolved once per distinct bucketId.

Four things are pinned here, each against the real assetService module loaded by file path:

- PARITY: the response body is byte-identical to what the per-item path produces for the same page
  (250 assets, two bucketIds, every version state an asset can be in, a NextToken).
- CALL COUNT: DynamoDB reads for the page are <= ceil(N/100) batch calls + one bucket query per
  distinct bucketId, with no per-asset get_item.
- RETRY: UnprocessedKeys are re-requested with exponential backoff; keys still unprocessed after
  the retry budget (or a failed batch call) fall back to the per-item read, so a throttled batch
  never blanks version info or fails the page.
- SHARED HELPER: the rows are read through the `batch_get_items` assetService resolved from
  common.dynamodb -- not a private copy of the chunk + retry loop -- and a helper that raises
  degrades the page to per-item reads instead of failing it.

Every listing run patches the SHIPPED helper (common/dynamodb.py loaded by path) into assetService's
globals -- the object enhance_assets_with_version_info's __globals__ resolves -- and the batch stubs sit
on the module's `dynamodb` resource, which the handler passes to it. Two stand-ins otherwise compete
for `common.dynamodb`: tests/conftest.py binds the real helper onto a MagicMock module at session
start, and backend/conftest.py re-installs the tests/mocks mirror (same loop, no backoff sleep)
before each test, so which one a lazily loaded assetService captured depends on when _load() first
ran. Patching the real function in makes every chunk / retry / backoff assertion exercise the code
that ships, in any test order.
"""

import importlib.util
import json
import os
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_assetService_history import _load

_REAL_DDB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py"
))
_spec = importlib.util.spec_from_file_location("real_common_dynamodb_for_asset_listing", _REAL_DDB_PATH)
REAL_DDB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REAL_DDB)

PAGE_SIZE = 250
BUCKETS = {
    "bucket-a": {"bucketId": "bucket-a", "bucketName": "vams-assets-a", "baseAssetsPrefix": "a/"},
    "bucket-b": {"bucketId": "bucket-b", "bucketName": "vams-assets-b", "baseAssetsPrefix": "/b"},
}
NEXT_TOKEN = "eyJkYXRhYmFzZUlkIjogeyJTIjogImRiMSJ9fQ=="
# The handler uses the helper's default budget; read it from the shipped module.
CHUNK_SIZE = REAL_DDB.BATCH_GET_CHUNK_SIZE
MAX_RETRIES = REAL_DDB.BATCH_GET_MAX_RETRIES
BACKOFF_SECONDS = REAL_DDB.BATCH_GET_RETRY_BACKOFF_SECONDS


def _version_key(asset):
    return (f"{asset['databaseId']}:{asset['assetId']}", asset["currentVersionId"])


def _build_page(n=PAGE_SIZE, database_id="db1"):
    """One listing page covering every version state, plus the versions-table rows behind it.

    Asset i cycles through: a current version whose row exists (with a Decimal-typed attribute,
    as the resource API returns), a currentVersionId whose row is missing, and no currentVersionId
    at all. The bucketId alternates so the page spans two buckets.
    """
    assets = []
    version_rows = {}
    for i in range(n):
        asset = {
            "databaseId": database_id,
            "assetId": f"asset-{i:03d}",
            "assetName": f"Asset {i}",
            "description": f"description {i}",
            "isDistributable": bool(i % 2),
            "tags": ["t1"] if i % 3 == 0 else [],
            "bucketId": "bucket-a" if i % 2 == 0 else "bucket-b",
            "assetLocation": {"Key": f"asset-{i:03d}/"},
            "object__type": "asset",
        }
        state = i % 3
        if state == 0:
            asset["currentVersionId"] = str(i // 3 + 1)
            version_rows[_version_key(asset)] = {
                "databaseId:assetId": f"{database_id}:{asset['assetId']}",
                "assetVersionId": asset["currentVersionId"],
                "dateCreated": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "comment": f"version {i}",
                "description": f"version description {i}",
                "createdBy": "user-1" if i % 2 else "SYSTEM_USER",
                "fileCount": Decimal(i),
            }
        elif state == 1:
            asset["currentVersionId"] = "9"  # no row behind it
        assets.append(asset)
    return assets, version_rows


class _VersionsTable:
    """Stands in for both the versions Table resource (get_item) and the service resource
    (batch_get_item) over one dict of rows. `unprocessed` scripts, per batch call index, which
    pending keys the call leaves in UnprocessedKeys; `fail_calls` makes those calls raise."""

    def __init__(self, module, rows, unprocessed=None, fail_calls=()):
        self.module = module
        self.rows = rows
        self.unprocessed = unprocessed or {}
        self.fail_calls = set(fail_calls)
        self.table = MagicMock(name="versions_table")
        self.table.get_item.side_effect = self._get_item
        self.resource = MagicMock(name="dynamodb")
        self.resource.batch_get_item.side_effect = self._batch_get_item
        self.batch_calls = []

    def _get_item(self, Key):
        if not Key["assetVersionId"]:
            # boto3 / DynamoDB reject a null or empty key attribute value
            raise ValueError("Invalid key attribute value (simulated ParamValidationError)")
        row = self.rows.get((Key["databaseId:assetId"], Key["assetVersionId"]))
        return {"Item": dict(row)} if row else {}

    def _batch_get_item(self, RequestItems):
        table_name = self.module.asset_versions_table_name
        assert list(RequestItems) == [table_name], RequestItems
        keys = RequestItems[table_name]["Keys"]
        assert 1 <= len(keys) <= CHUNK_SIZE, len(keys)
        call_index = len(self.batch_calls)
        self.batch_calls.append(list(keys))
        if call_index in self.fail_calls:
            raise RuntimeError("ProvisionedThroughputExceededException (simulated)")
        # DynamoDB rejects the WHOLE request -- not just the offending key -- in both of these cases
        if any(not k["databaseId:assetId"] or not k["assetVersionId"] for k in keys):
            raise ValueError("Key attribute cannot be null or empty (simulated ValidationException)")
        if len({(k["databaseId:assetId"], k["assetVersionId"]) for k in keys}) != len(keys):
            raise ValueError("Provided list of item keys contains duplicates (simulated ValidationException)")
        leave = self.unprocessed.get(call_index, lambda ks: [])(keys)
        leave_set = {(k["databaseId:assetId"], k["assetVersionId"]) for k in leave}
        served = [
            dict(self.rows[(k["databaseId:assetId"], k["assetVersionId"])])
            for k in keys
            if (k["databaseId:assetId"], k["assetVersionId"]) not in leave_set
            and (k["databaseId:assetId"], k["assetVersionId"]) in self.rows
        ]
        response = {"Responses": {table_name: served}}
        if leave:
            response["UnprocessedKeys"] = {table_name: {"Keys": list(leave)}}
        return response


def _buckets_table():
    table = MagicMock(name="buckets_table")

    def query(KeyConditionExpression, Limit):
        bucket_id = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [dict(BUCKETS[bucket_id])]} if bucket_id in BUCKETS else {"Items": []}

    table.query.side_effect = query
    return table


def _per_item_expected_body(m, items):
    """The response body the per-item path produces: enhance_asset_with_version_info +
    get_default_bucket_details per asset, then the same model conversion the handler applies."""
    formatted = []
    for item in items:
        if not item.get("bucketId"):
            continue
        enhanced = m.enhance_asset_with_version_info(item)
        enhanced["bucketName"] = m.get_default_bucket_details(enhanced["bucketId"])["bucketName"]
        try:
            formatted.append(m.AssetResponseModel(**enhanced).dict())
        except m.ValidationError:
            formatted.append(enhanced)
    return m.success(body={"Items": formatted, "NextToken": NEXT_TOKEN})["body"]


def _event(path_parameters):
    return {
        "requestContext": {"http": {"method": "GET", "path": "/assets"}},
        "pathParameters": path_parameters,
        "queryStringParameters": {"maxItems": str(PAGE_SIZE), "pageSize": str(PAGE_SIZE)},
    }


def _run_listing(m, versions, buckets, items, path_parameters, extra_patches=()):
    """Run handle_get_request over a scripted page with every table stub in place.

    Returns (response, per_item_expected_body, sleep). The expected body is computed through the
    per-item functions FIRST, against the same stubs, and the stubs' counters are reset before the
    handler runs, so the call-count assertions see only what the handler itself read.
    The shipped `batch_get_items` is patched into assetService's globals for the run (see the module
    docstring); `extra_patches` are entered after it, so a `patch.object(m, "batch_get_items", ...)`
    there replaces the helper with a scripted stand-in."""
    page = {"Items": [dict(i) for i in items], "NextToken": NEXT_TOKEN, "truncated": True}
    with ExitStack() as stack:
        stack.enter_context(patch.object(m, "versions_table", versions.table))
        stack.enter_context(patch.object(m, "dynamodb", versions.resource))
        stack.enter_context(patch.object(m, "buckets_table", buckets))
        stack.enter_context(patch.object(m, "get_assets", return_value=page))
        stack.enter_context(patch.object(m, "get_all_assets", return_value=page))
        stack.enter_context(patch.object(m, "batch_get_items", REAL_DDB.batch_get_items))
        sleep = stack.enter_context(patch("time.sleep"))
        m.claims_and_roles = {"tokens": ["u1"]}
        expected_body = _per_item_expected_body(m, items)
        versions.table.get_item.reset_mock()
        buckets.query.reset_mock()
        versions.batch_calls.clear()
        versions.resource.batch_get_item.reset_mock()
        for extra in extra_patches:
            stack.enter_context(extra)
        response = m.handle_get_request(_event(path_parameters))
    return response, expected_body, sleep


@pytest.mark.unit
class TestTheStubsInThisFileAreActuallyRead:
    def test_patch_object_reaches_the_handler_globals(self):
        m = _load()
        assert m.handle_get_request.__globals__ is m.__dict__
        assert m.enrich_asset_listing_page.__globals__ is m.__dict__

    def test_the_scripted_page_covers_every_version_state(self):
        assets, rows = _build_page()
        with_row = [a for a in assets if "currentVersionId" in a and _version_key(a) in rows]
        missing_row = [a for a in assets if "currentVersionId" in a and _version_key(a) not in rows]
        no_version = [a for a in assets if "currentVersionId" not in a]
        assert len(with_row) and len(missing_row) and len(no_version)
        assert len(with_row) + len(missing_row) + len(no_version) == PAGE_SIZE
        assert {a["bucketId"] for a in assets} == set(BUCKETS)


@pytest.mark.unit
class TestListingParity:
    @pytest.mark.parametrize("path_parameters", [{"databaseId": "db1"}, {}],
                             ids=["per-database", "all-databases"])
    def test_response_body_is_byte_identical_to_the_per_item_path(self, path_parameters):
        m = _load()
        assets, rows = _build_page()
        # One malformed record: the per-item path skipped it, the page path must too
        assets.insert(7, {"databaseId": "db1", "assetId": "no-bucket", "assetName": "x",
                          "description": "x", "isDistributable": False})
        versions = _VersionsTable(m, rows)

        response, expected_body, _ = _run_listing(m, versions, _buckets_table(), assets, path_parameters)

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        body = json.loads(response["body"])
        assert len(body["Items"]) == PAGE_SIZE
        assert body["NextToken"] == NEXT_TOKEN
        # The version states survive end to end, not just "something came back"
        assert body["Items"][0]["currentVersion"]["Version"] == "1"
        assert body["Items"][0]["currentVersion"]["Comment"] == "version 0"
        assert body["Items"][1]["currentVersion"] is None
        assert body["Items"][2]["currentVersion"] is None
        assert body["Items"][0]["bucketName"] == "vams-assets-a"
        assert body["Items"][1]["bucketName"] == "vams-assets-b"

    def test_an_empty_page_reads_nothing(self):
        m = _load()
        versions = _VersionsTable(m, {})
        buckets = _buckets_table()

        response, expected_body, _ = _run_listing(m, versions, buckets, [], {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        versions.resource.batch_get_item.assert_not_called()
        versions.table.get_item.assert_not_called()
        buckets.query.assert_not_called()


@pytest.mark.unit
class TestListingReadCount:
    @pytest.mark.parametrize("path_parameters", [{"databaseId": "db1"}, {}],
                             ids=["per-database", "all-databases"])
    def test_reads_are_bounded_by_chunks_plus_distinct_buckets(self, path_parameters):
        m = _load()
        assets, rows = _build_page()
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()

        response, _, sleep = _run_listing(m, versions, buckets, assets, path_parameters)

        assert response["statusCode"] == 200
        keyed_assets = [a for a in assets if "currentVersionId" in a]
        expected_chunks = -(-len(keyed_assets) // CHUNK_SIZE)  # ceil
        assert versions.resource.batch_get_item.call_count == expected_chunks
        assert versions.resource.batch_get_item.call_count <= -(-PAGE_SIZE // 100)
        # Every keyed asset was requested exactly once, in chunks no larger than the API limit
        requested = [k for call in versions.batch_calls for k in call]
        assert len(requested) == len(keyed_assets)
        assert all(len(call) <= CHUNK_SIZE for call in versions.batch_calls)
        assert {(k["databaseId:assetId"], k["assetVersionId"]) for k in requested} == \
            {_version_key(a) for a in keyed_assets}
        # No per-asset reads, one bucket query per distinct bucketId, no backoff needed
        versions.table.get_item.assert_not_called()
        assert buckets.query.call_count == len(BUCKETS)
        sleep.assert_not_called()

    def test_bucket_details_are_memoized_per_request_not_across_requests(self):
        m = _load()
        assets, rows = _build_page(n=4)
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()

        _run_listing(m, versions, buckets, assets, {})
        first = buckets.query.call_count
        _run_listing(m, versions, buckets, assets, {})

        # The memo is scoped to one request: the second request re-reads each bucket once
        assert first == len(BUCKETS)
        assert buckets.query.call_count == len(BUCKETS)

    def test_two_records_sharing_a_version_key_are_requested_once(self):
        """BatchGetItem rejects a request that names the same key twice, so a page carrying two
        records with the same databaseId/assetId/currentVersionId must send that key once and
        still enrich both records."""
        m = _load()
        assets, rows = _build_page()
        duplicate = dict(assets[0], assetName="Asset 0 (second record)")
        assets.insert(1, duplicate)
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()

        response, expected_body, _ = _run_listing(m, versions, buckets, assets, {"databaseId": "db1"})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        body = json.loads(response["body"])
        assert body["Items"][1]["assetName"] == "Asset 0 (second record)"
        assert body["Items"][1]["currentVersion"] == body["Items"][0]["currentVersion"]
        assert body["Items"][1]["currentVersion"]["Version"] == "1"
        requested = [(k["databaseId:assetId"], k["assetVersionId"])
                     for call in versions.batch_calls for k in call]
        assert requested
        assert len(requested) == len(set(requested))
        distinct_keys = {_version_key(a) for a in assets if "currentVersionId" in a}
        assert set(requested) == distinct_keys
        assert versions.resource.batch_get_item.call_count == -(-len(distinct_keys) // CHUNK_SIZE)
        versions.table.get_item.assert_not_called()

    @pytest.mark.parametrize("falsy_version_id", [None, ""], ids=["null", "empty"])
    def test_a_falsy_current_version_id_is_read_individually_not_batched(self, falsy_version_id):
        """A null or empty currentVersionId would make DynamoDB reject the whole 100-key chunk, so
        that record takes the per-item read on its own while the rest of the page still batches."""
        m = _load()
        assets, rows = _build_page()
        malformed = {
            "databaseId": "db1", "assetId": "asset-malformed", "assetName": "Malformed version id",
            "description": "d", "isDistributable": False, "tags": [], "bucketId": "bucket-a",
            "assetLocation": {"Key": "asset-malformed/"}, "object__type": "asset",
            "currentVersionId": falsy_version_id,
        }
        assets.insert(3, malformed)
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        body = json.loads(response["body"])
        assert body["Items"][3]["assetId"] == "asset-malformed"
        assert body["Items"][3]["currentVersion"] is None
        # The rest of the page still batches in ceil(N/100) calls, none of them carrying the bad key
        batched = [a for a in assets if a.get("currentVersionId")]
        assert versions.resource.batch_get_item.call_count == -(-len(batched) // CHUNK_SIZE)
        assert versions.batch_calls
        assert all(k["assetVersionId"] for call in versions.batch_calls for k in call)
        # Exactly the malformed record was read individually
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == {("db1:asset-malformed", falsy_version_id)}
        sleep.assert_not_called()


@pytest.mark.unit
class TestUnprocessedKeysRetry:
    def test_unprocessed_keys_are_re_requested_with_backoff(self):
        m = _load()
        assets, rows = _build_page()
        # Call 0 (chunk 1) leaves its second half unprocessed; call 1 is the re-request of that
        # half and completes; calls 2 and 3 are chunks 2 and 3, clean.
        versions = _VersionsTable(m, rows, unprocessed={0: lambda keys: keys[len(keys) // 2:]})
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        keyed = [a for a in assets if "currentVersionId" in a]
        expected_chunks = -(-len(keyed) // CHUNK_SIZE)
        assert versions.resource.batch_get_item.call_count == expected_chunks + 1
        # The re-request carries exactly the keys the first call left unprocessed
        first_half_left = versions.batch_calls[0][len(versions.batch_calls[0]) // 2:]
        assert versions.batch_calls[1] == first_half_left
        sleep.assert_called_once_with(BACKOFF_SECONDS)
        versions.table.get_item.assert_not_called()

    def test_backoff_is_exponential_and_bounded_then_falls_back_per_item(self):
        m = _load()
        assets, rows = _build_page(n=60)  # one chunk
        stuck = lambda keys: keys[:5]  # noqa: E731 -- these 5 keys never get processed
        versions = _VersionsTable(m, rows, unprocessed={i: stuck for i in range(1 + MAX_RETRIES)})
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        # 1 initial call + MAX_RETRIES re-requests, then stop
        assert versions.resource.batch_get_item.call_count == 1 + MAX_RETRIES
        assert sleep.call_args_list  # keys stayed unprocessed, so at least one backoff happened
        assert [c.args[0] for c in sleep.call_args_list] == [
            BACKOFF_SECONDS * (2 ** i) for i in range(MAX_RETRIES)
        ]
        # Exactly the stuck keys were read individually -- not the whole chunk, not none
        stuck_keys = {(k["databaseId:assetId"], k["assetVersionId"]) for k in versions.batch_calls[0][:5]}
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == stuck_keys

    def test_a_failed_batch_call_falls_back_to_per_item_reads_for_the_whole_page(self):
        """The shared helper lets a failed batch call propagate (its docstring: "the caller owns its
        fallback"), so the handler cannot tell which chunks did come back: every batched key takes
        the per-item read, the page still answers 200 with the same body, and no chunk after the
        failure is attempted."""
        m = _load()
        assets, rows = _build_page()
        versions = _VersionsTable(m, rows, fail_calls={0})  # chunk 1 raises
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        keyed = [a for a in assets if "currentVersionId" in a]
        assert -(-len(keyed) // CHUNK_SIZE) == 2  # chunk 2 exists and is never requested
        assert versions.resource.batch_get_item.call_count == 1
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == {_version_key(a) for a in keyed}
        sleep.assert_not_called()


@pytest.mark.unit
class TestVersionRowsAreReadThroughTheSharedHelper:
    """assetService resolves `batch_get_items` from common.dynamodb; patch it THERE (the object
    enhance_assets_with_version_info's __globals__ resolves) to pin the call path and the contract."""

    def test_the_handler_resolves_the_shared_helper_not_a_private_loop(self):
        m = _load()
        assert m.enhance_assets_with_version_info.__globals__ is m.__dict__
        # The bound name is a common.dynamodb `batch_get_items` -- whichever stand-in was live when
        # the module loaded (both carry the shared helper's signature), never a function defined in
        # assetService itself
        helper = m.batch_get_items
        assert helper.__name__ == "batch_get_items"
        assert helper.__globals__ is not m.__dict__
        assert helper.__globals__["__file__"].endswith(os.path.join("common", "dynamodb.py"))
        # The private copy and the constants only it used are gone (#395); the shared module owns them
        for name in ("_batch_get_version_rows", "BATCH_GET_CHUNK_SIZE", "BATCH_GET_MAX_RETRIES",
                     "BATCH_GET_RETRY_BACKOFF_SECONDS"):
            assert not hasattr(m, name), name
        assert not hasattr(m, "time")  # the backoff sleep left with the loop

    def test_the_page_is_read_with_one_helper_call_over_the_module_resource_and_table(self):
        m = _load()
        assets, rows = _build_page()
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()
        helper = MagicMock(name="batch_get_items", wraps=REAL_DDB.batch_get_items)

        response, expected_body, _ = _run_listing(
            m, versions, buckets, assets, {"databaseId": "db1"},
            extra_patches=[patch.object(m, "batch_get_items", helper)])

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        helper.assert_called_once()
        resource, table_name, keys = helper.call_args.args
        assert resource is versions.resource
        assert table_name == m.asset_versions_table_name
        assert helper.call_args.kwargs == {}  # the helper's default retry budget
        keyed = [a for a in assets if "currentVersionId" in a]
        assert [(k["databaseId:assetId"], k["assetVersionId"]) for k in keys] == \
            [_version_key(a) for a in keyed]
        # The wrapped real helper did the chunking: no per-item reads were needed
        assert versions.resource.batch_get_item.call_count == -(-len(keyed) // CHUNK_SIZE)
        versions.table.get_item.assert_not_called()

    def test_unresolved_keys_from_the_helper_take_the_per_item_read_and_missing_rows_do_not(self):
        """A key the helper hands back as unresolved is re-read individually; a key absent from both
        lists is a row that does not exist and is NOT re-read -- the distinction the helper exists
        to keep."""
        m = _load()
        assets, rows = _build_page(n=9)  # assets 0,3,6 have rows; 1,4,7 have an id with no row
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()
        keyed = [a for a in assets if "currentVersionId" in a]
        unresolved = [m._current_version_key(assets[0]), m._current_version_key(assets[7])]
        served = [dict(rows[_version_key(assets[3])]), dict(rows[_version_key(assets[6])])]
        helper = MagicMock(name="batch_get_items", return_value=(served, unresolved))

        response, expected_body, sleep = _run_listing(
            m, versions, buckets, assets, {},
            extra_patches=[patch.object(m, "batch_get_items", helper)])

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        body = json.loads(response["body"])
        assert body["Items"][0]["currentVersion"]["Version"] == "1"  # unresolved -> per-item read found it
        assert body["Items"][3]["currentVersion"]["Version"] == "2"  # served by the batch
        assert body["Items"][1]["currentVersion"] is None  # absent from both: no row, not re-read
        assert body["Items"][7]["currentVersion"] is None  # unresolved, per-item read found nothing
        helper.assert_called_once()
        versions.resource.batch_get_item.assert_not_called()  # the stubbed helper never reached it
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == {_version_key(assets[0]), _version_key(assets[7])}
        assert len(keyed) == 6
        sleep.assert_not_called()

    @pytest.mark.parametrize("error", [
        ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
                    "BatchGetItem"),
        RuntimeError("connection reset"),
    ], ids=["ClientError", "other"])
    def test_a_helper_that_raises_degrades_the_page_to_per_item_reads_never_a_500(self, error):
        m = _load()
        assets, rows = _build_page(n=12)
        versions = _VersionsTable(m, rows)
        buckets = _buckets_table()
        helper = MagicMock(name="batch_get_items", side_effect=error)

        response, expected_body, _ = _run_listing(
            m, versions, buckets, assets, {},
            extra_patches=[patch.object(m, "batch_get_items", helper)])

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        helper.assert_called_once()
        keyed = [a for a in assets if "currentVersionId" in a]
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == {_version_key(a) for a in keyed}


@pytest.mark.unit
class TestSingleAssetBranchIsUnchanged:
    def test_get_one_asset_still_reads_its_version_row_individually(self):
        """The single-asset branch keeps the per-item read; only the listings batch.

        Bucket resolution is stubbed here on purpose: test_assetService_history's _prepare
        assigns m.get_asset_bucket_details on the shared module and never restores it, so this
        test must not depend on which function is live at that attribute."""
        m = _load()
        assets, rows = _build_page(n=1)
        versions = _VersionsTable(m, rows)
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/database/db1/assets/asset-000"}},
            "pathParameters": {"databaseId": "db1", "assetId": "asset-000"},
            "queryStringParameters": {},
        }
        with patch.object(m, "versions_table", versions.table), \
                patch.object(m, "dynamodb", versions.resource), \
                patch.object(m, "get_asset_bucket_details", return_value={"bucketName": "vams-assets-a"}), \
                patch.object(m, "get_asset_details", return_value=dict(assets[0])), \
                patch.object(m, "authorize_single_asset", return_value=True):
            m.claims_and_roles = {"tokens": ["u1"]}
            response = m.handle_get_request(event)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["currentVersion"]["Version"] == "1"
        assert body["currentVersion"]["Comment"] == "version 0"
        versions.table.get_item.assert_called_once()
        versions.resource.batch_get_item.assert_not_called()
