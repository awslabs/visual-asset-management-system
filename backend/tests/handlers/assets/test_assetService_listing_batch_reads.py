# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset listings enrich a page with batched reads, not one read per asset (#389).

Both list branches of handle_get_request -- the per-database listing and the all-databases listing
-- used to issue one versions-table get_item and one buckets-table query PER ASSET on the page. The
page is now enriched by enrich_asset_listing_page: the current-version rows come back from
BatchGetItem in chunks of 100 (UnprocessedKeys re-requested with backoff, bounded), and bucket
details are resolved once per distinct bucketId.

Three things are pinned here, each against the real assetService module loaded by file path:

- PARITY: the response body is byte-identical to what the per-item path produces for the same page
  (250 assets, two bucketIds, every version state an asset can be in, a NextToken).
- CALL COUNT: DynamoDB reads for the page are <= ceil(N/100) batch calls + one bucket query per
  distinct bucketId, with no per-asset get_item.
- RETRY: UnprocessedKeys are re-requested with exponential backoff; keys still unprocessed after
  the retry budget (or a failed batch call) fall back to the per-item read, so a throttled batch
  never blanks version info or fails the page.
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.assets.test_assetService_history import _load

PAGE_SIZE = 250
BUCKETS = {
    "bucket-a": {"bucketId": "bucket-a", "bucketName": "vams-assets-a", "baseAssetsPrefix": "a/"},
    "bucket-b": {"bucketId": "bucket-b", "bucketName": "vams-assets-b", "baseAssetsPrefix": "/b"},
}
NEXT_TOKEN = "eyJkYXRhYmFzZUlkIjogeyJTIjogImRiMSJ9fQ=="


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
        row = self.rows.get((Key["databaseId:assetId"], Key["assetVersionId"]))
        return {"Item": dict(row)} if row else {}

    def _batch_get_item(self, RequestItems):
        table_name = self.module.asset_versions_table_name
        assert list(RequestItems) == [table_name], RequestItems
        keys = RequestItems[table_name]["Keys"]
        assert 1 <= len(keys) <= self.module.BATCH_GET_CHUNK_SIZE, len(keys)
        assert len({(k["databaseId:assetId"], k["assetVersionId"]) for k in keys}) == len(keys), \
            "BatchGetItem rejects duplicate keys"
        call_index = len(self.batch_calls)
        self.batch_calls.append(list(keys))
        if call_index in self.fail_calls:
            raise RuntimeError("ProvisionedThroughputExceededException (simulated)")
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


def _run_listing(m, versions, buckets, items, path_parameters):
    """Run handle_get_request over a scripted page with every table stub in place.

    Returns (response, per_item_expected_body). The expected body is computed through the per-item
    functions FIRST, against the same stubs, and the stubs' counters are reset before the handler
    runs, so the call-count assertions see only what the handler itself read."""
    page = {"Items": [dict(i) for i in items], "NextToken": NEXT_TOKEN, "truncated": True}
    with patch.object(m, "versions_table", versions.table), \
            patch.object(m, "dynamodb", versions.resource), \
            patch.object(m, "buckets_table", buckets), \
            patch.object(m, "get_assets", return_value=page), \
            patch.object(m, "get_all_assets", return_value=page), \
            patch("time.sleep") as sleep:
        m.claims_and_roles = {"tokens": ["u1"]}
        expected_body = _per_item_expected_body(m, items)
        versions.table.get_item.reset_mock()
        buckets.query.reset_mock()
        versions.batch_calls.clear()
        versions.resource.batch_get_item.reset_mock()
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
        expected_chunks = -(-len(keyed_assets) // m.BATCH_GET_CHUNK_SIZE)  # ceil
        assert versions.resource.batch_get_item.call_count == expected_chunks
        assert versions.resource.batch_get_item.call_count <= -(-PAGE_SIZE // 100)
        # Every keyed asset was requested exactly once, in chunks no larger than the API limit
        requested = [k for call in versions.batch_calls for k in call]
        assert len(requested) == len(keyed_assets)
        assert all(len(call) <= m.BATCH_GET_CHUNK_SIZE for call in versions.batch_calls)
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
        expected_chunks = -(-len(keyed) // m.BATCH_GET_CHUNK_SIZE)
        assert versions.resource.batch_get_item.call_count == expected_chunks + 1
        # The re-request carries exactly the keys the first call left unprocessed
        first_half_left = versions.batch_calls[0][len(versions.batch_calls[0]) // 2:]
        assert versions.batch_calls[1] == first_half_left
        sleep.assert_called_once_with(m.BATCH_GET_RETRY_BACKOFF_SECONDS)
        versions.table.get_item.assert_not_called()

    def test_backoff_is_exponential_and_bounded_then_falls_back_per_item(self):
        m = _load()
        assets, rows = _build_page(n=60)  # one chunk
        stuck = lambda keys: keys[:5]  # noqa: E731 -- these 5 keys never get processed
        versions = _VersionsTable(m, rows, unprocessed={i: stuck for i in range(1 + m.BATCH_GET_MAX_RETRIES)})
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        # 1 initial call + BATCH_GET_MAX_RETRIES re-requests, then stop
        assert versions.resource.batch_get_item.call_count == 1 + m.BATCH_GET_MAX_RETRIES
        assert sleep.call_args_list  # keys stayed unprocessed, so at least one backoff happened
        assert [c.args[0] for c in sleep.call_args_list] == [
            m.BATCH_GET_RETRY_BACKOFF_SECONDS * (2 ** i) for i in range(m.BATCH_GET_MAX_RETRIES)
        ]
        # Exactly the stuck keys were read individually -- not the whole chunk, not none
        stuck_keys = {(k["databaseId:assetId"], k["assetVersionId"]) for k in versions.batch_calls[0][:5]}
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == stuck_keys

    def test_a_failed_batch_call_falls_back_to_per_item_reads_for_that_chunk(self):
        m = _load()
        assets, rows = _build_page()
        versions = _VersionsTable(m, rows, fail_calls={1})  # chunk 2 raises
        buckets = _buckets_table()

        response, expected_body, sleep = _run_listing(m, versions, buckets, assets, {})

        assert response["statusCode"] == 200
        assert response["body"] == expected_body
        keyed = [a for a in assets if "currentVersionId" in a]
        expected_chunks = -(-len(keyed) // m.BATCH_GET_CHUNK_SIZE)
        assert versions.resource.batch_get_item.call_count == expected_chunks
        failed_chunk_keys = {(k["databaseId:assetId"], k["assetVersionId"]) for k in versions.batch_calls[1]}
        read_individually = {
            (c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["assetVersionId"])
            for c in versions.table.get_item.call_args_list
        }
        assert read_individually == failed_chunk_keys
        sleep.assert_not_called()


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
