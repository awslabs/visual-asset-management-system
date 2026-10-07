# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The DynamoDB vector store: item shape, the lifecycle flips, deletes, continuation, scan and search.

The client is a hand-written fake in the house style so the tests run on any botocore; the wire-level
contract of every operation (parameter names, shapes) is pinned separately by
``test_vectorStore_contracts.py`` through ``botocore.stub.Stubber``. Query pages are served by the shared
``Pager`` so a loop that pages on the key's VALUE, or never threads the cursor, fails with a message.

Importing ``Pager`` places this file in the ``tests/_assertion_shapes`` bound/refusal family, which holds
it at zero pinned read expressions and pinned call counts: a recorded request is compared whole or by
containment here, and the exact wire literals live in the contract file. The per-item flips run on a
thread pool, so within one Query page the fake records updates in no fixed order and the assertions
over them are sets; across pages the order is fixed, because a page is processed before the next is
read.
"""

import importlib.util
import os
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.pagingStub import Pager

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "vectorsearch", "vectorStore.py"
)

TABLE = "vector-table"
INDEX = "vec-amazon-titan-embed-text-v2-0-2"
MODEL = "amazon.titan-embed-text-v2:0"
PK = "databaseId:assetId"
SK = "fileVersionKey"


@pytest.fixture
def vs():
    spec = importlib.util.spec_from_file_location("vectorStore_under_test", os.path.abspath(_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _client_error(code, message="x", operation="UpdateItem"):
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _cancelled_transaction(reason_codes):
    """The TransactionCanceledException DynamoDB raises; ``CancellationReasons`` carries one entry per
    action, ``None`` for an action that was not the cause."""
    return ClientError(
        {
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": f"Transaction cancelled, please refer cancellation reasons for specific reasons {reason_codes}",
            },
            "CancellationReasons": [{"Code": code, "Message": None if code == "None" else code} for code in reason_codes],
        },
        "TransactWriteItems",
    )


def _raiser(error):
    """A stand-in client method that raises ``error`` whatever it is called with."""

    def _raise(**kwargs):
        raise error

    return _raise


class FakeDynamo:
    """Records every call; query pages come from a Pager, update_item and delete_item can fail their
    condition per sort key, batch_write_item can echo the first request back as unprocessed once,
    transact_write_items can be cancelled once or always with the CancellationReasons codes of
    ``transact_cancel_reasons``."""

    def __init__(self):
        self.put_items = []
        self.updates = []
        self.deletes = []
        self.batch_writes = []
        self.transactions = []
        self.scans = []
        self.searches = []
        self.query_pager = Pager({"Items": []}, name="unused query")
        self.conditional_failures = set()
        self.unprocessed_once = False
        self.transact_cancel_once = False
        self.transact_cancel_always = False
        self.transact_cancel_reasons = ["None", "ConditionalCheckFailed"]
        self.scan_response = {"Items": []}
        self.search_response = {"SearchResults": []}
        self.search_error = None

    def query(self, **kwargs):
        return self.query_pager(**kwargs)

    def put_item(self, **kwargs):
        self.put_items.append(kwargs)
        return {}

    def transact_write_items(self, **kwargs):
        self.transactions.append(kwargs)
        if self.transact_cancel_always or (self.transact_cancel_once and len(self.transactions) == 1):
            raise _cancelled_transaction(self.transact_cancel_reasons)
        return {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if kwargs["Key"][SK]["S"] in self.conditional_failures:
            raise _client_error("ConditionalCheckFailedException")
        return {}

    def delete_item(self, **kwargs):
        self.deletes.append(kwargs)
        if kwargs["Key"][SK]["S"] in self.conditional_failures:
            raise _client_error("ConditionalCheckFailedException", operation="DeleteItem")
        return {}

    def batch_write_item(self, RequestItems):
        self.batch_writes.append(RequestItems)
        ((table, requests),) = RequestItems.items()
        if self.unprocessed_once and len(self.batch_writes) == 1:
            return {"UnprocessedItems": {table: requests[:1]}}
        return {"UnprocessedItems": {}}

    def scan(self, **kwargs):
        self.scans.append(kwargs)
        return self.scan_response

    def search_vectors(self, **kwargs):
        self.searches.append(kwargs)
        if self.search_error is not None:
            raise self.search_error
        return self.search_response


def _image(version, is_latest="true", is_archived="false", key_path="/m.glb", pk="db1:a1"):
    image = {PK: {"S": pk}, SK: {"S": f"{key_path}#{version}"}, "versionId": {"S": version}}
    if is_latest is not None:
        image["isLatest"] = {"S": is_latest}
    if is_archived is not None:
        image["isArchived"] = {"S": is_archived}
    return image


def _key(version, key_path="/m.glb", pk="db1:a1"):
    """A keys-only image, the shape the sibling Query's projection returns; ``version`` may carry a
    segment suffix (``"v1#t0000083456"``) to stand for an older version's segment item."""
    return {PK: {"S": pk}, SK: {"S": f"{key_path}#{version}"}}


def _segment_image(version, segment_key, pipeline_execution_id, key_path="/m.glb", pk="db1:a1"):
    """A keys-plus-run-id image, the shape the version-prefix Query's projection returns."""
    return {
        PK: {"S": pk},
        SK: {"S": f"{key_path}#{version}#{segment_key}"},
        "pipelineExecutionId": {"S": pipeline_execution_id},
    }


def _demotion(version, key_path="/m.glb", pk="db1:a1"):
    """The transaction action that demotes one sibling."""
    return {"Update": {
        "TableName": TABLE,
        "Key": _key(version, key_path, pk),
        "UpdateExpression": "SET isLatest = :target",
        "ConditionExpression": "isLatest = :current",
        "ExpressionAttributeValues": {":target": {"S": "false"}, ":current": {"S": "true"}},
    }}


def _put(vs, item):
    return {"Put": {"TableName": TABLE, "Item": vs.serialize_item(item)}}


def _batches(actions, size):
    """The transaction list ``actions`` splits into: consecutive slices of at most ``size``."""
    return [{"TransactItems": actions[start:start + size]} for start in range(0, len(actions), size)]


def _item(vs, **overrides):
    fields = dict(
        databaseId="db1", assetId="a1", filePath="/m.glb", versionId="v2", isLatest=True, isArchived=False,
        fileClass="mesh", fileExt="glb", embeddingModelId=MODEL, embeddingDimensions=2, embedding=[0.123456789012, 1e-12],
        sourceText="a mesh", sourceModalities=["renders", "file-attributes"], contentEtag="etag", bucketId="b1",
        fileSize=42, contentType="model/gltf-binary", analysisModelId="anthropic.claude-haiku-4-5", indexedAt="2026-09-08T00:00:00Z",
        pipelineExecutionId="pe1", workflowExecutionId="we1", previewFileKey=None,
    )
    fields.update(overrides)
    return vs.VectorItem(**fields)


def _segment_item(vs, **overrides):
    """A videoTime segment item of the same file version as ``_item``."""
    fields = dict(
        segmentKey="t0000083456", segmentKind="videoTime", segmentLabel="00:01:23.456-00:01:33.456",
        segmentStartMs=83456, segmentEndMs=93456, segmentCount=12,
    )
    fields.update(overrides)
    return _item(vs, **fields)


def _store(vs, fake, dimensions=2):
    return vs.DynamoDbVectorStore(TABLE, INDEX, MODEL, dimensions, fake)


@pytest.mark.unit
class TestVectorItemAndSerialization:
    def test_keys_are_derived(self, vs):
        item = _item(vs)
        assert item.pk == "db1:a1"
        assert item.fileVersionKey == "/m.glb#v2"

    def test_every_filter_attribute_is_a_string_and_present(self, vs):
        image = vs.serialize_item(_item(vs))
        for name in vs.INLINE_FILTER_ATTRIBUTES:
            assert "S" in image[name], name
        assert image["isLatest"] == {"S": "true"} and image["isArchived"] == {"S": "false"}
        assert vs.INLINE_FILTER_ATTRIBUTES == (
            "databaseId", "isLatest", "isArchived", "fileClass", "fileExt", "embeddingModelId", "segmentKind",
        )

    @pytest.mark.parametrize("flag", ["isLatest", "isArchived"])
    @pytest.mark.parametrize("value", ["true", "false", 1])
    def test_a_flag_that_is_not_a_bool_is_refused_at_construction(self, vs, flag, value):
        """The flags are serialized by truthiness, so a caller passing the stored strings would write
        ``"false"`` as ``"true"`` -- every item archived -- were the type not checked first; an int is
        refused for the same reason, the rule being one of type, not of truth value."""
        with pytest.raises(TypeError):
            _item(vs, **{flag: value})

    def test_real_bools_are_accepted_and_serialized_as_their_strings(self, vs):
        """Control for the type rule: each bool pair constructs and lands as its string."""
        for latest, archived in ((True, False), (False, True), (True, True), (False, False)):
            image = vs.serialize_item(_item(vs, isLatest=latest, isArchived=archived))
            assert image["isLatest"] == {"S": str(latest).lower()}
            assert image["isArchived"] == {"S": str(archived).lower()}

    def test_a_separator_in_the_file_path_is_encoded_in_the_sort_key_and_stored_plain(self, vs):
        item = _item(vs, filePath="/dir/a#b.glb")
        assert item.fileVersionKey == "/dir/a%23b.glb#v2"
        image = vs.serialize_item(item)
        assert image[SK] == {"S": "/dir/a%23b.glb#v2"} and image["filePath"] == {"S": "/dir/a#b.glb"}

    def test_a_sort_key_over_the_dynamodb_limit_is_refused_at_construction(self, vs):
        """A version id longer than the 64 bytes the key budget reserves can push the assembled sort key
        past 1024 bytes; the builder's ValueError surfaces here, before any request is built."""
        long_path = "/" + "d" * 1030  # a 926-byte key path
        _item(vs, filePath=long_path, versionId="V" * 97)  # control: a key of exactly 1024 bytes
        with pytest.raises(ValueError):
            _item(vs, filePath=long_path, versionId="V" * 98)

    def test_whole_file_item_carries_the_segment_defaults(self, vs):
        image = vs.serialize_item(_item(vs))
        assert image["segmentKey"] == {"S": ""} and image["segmentKind"] == {"S": "none"}
        assert image["segmentLabel"] == {"S": ""}
        assert image["segmentStartMs"] == {"NULL": True} and image["segmentEndMs"] == {"NULL": True}
        assert image["segmentCount"] == {"N": "0"}

    def test_segment_item_lands_under_the_segment_sort_key_with_its_fields_written(self, vs):
        item = _segment_item(vs)
        assert item.fileVersionKey == "/m.glb#v2#t0000083456"
        image = vs.serialize_item(item)
        assert image[SK] == {"S": "/m.glb#v2#t0000083456"}
        assert image["segmentKey"] == {"S": "t0000083456"} and image["segmentKind"] == {"S": "videoTime"}
        assert image["segmentLabel"] == {"S": "00:01:23.456-00:01:33.456"}
        assert image["segmentStartMs"] == {"N": "83456"} and image["segmentEndMs"] == {"N": "93456"}
        assert image["segmentCount"] == {"N": "12"}

    def test_text_chunk_item_has_no_time_window(self, vs):
        image = vs.serialize_item(_segment_item(vs, segmentKey="c000012", segmentKind="textChunk",
                                               segmentLabel="chunk 12/200", segmentStartMs=None, segmentEndMs=None))
        assert image[SK] == {"S": "/m.glb#v2#c000012"}
        assert image["segmentStartMs"] == {"NULL": True} and image["segmentEndMs"] == {"NULL": True}

    def test_unknown_segment_kind_is_refused(self, vs):
        with pytest.raises(ValueError):
            _segment_item(vs, segmentKind="frameTime")

    def test_over_long_or_separator_bearing_segment_key_is_refused(self, vs):
        _segment_item(vs, segmentKey="s" * 32)  # control: the cap itself is accepted
        with pytest.raises(ValueError):
            _segment_item(vs, segmentKey="s" * 33)
        with pytest.raises(ValueError):
            _segment_item(vs, segmentKey="t#1")

    def test_segment_key_and_kind_must_agree(self, vs):
        with pytest.raises(ValueError):
            _item(vs, segmentKind="videoTime")  # a kind with no key
        with pytest.raises(ValueError):
            _item(vs, segmentKey="c000001")  # a key with kind "none"

    def test_keys_and_numbers(self, vs):
        image = vs.serialize_item(_item(vs))
        assert image[PK] == {"S": "db1:a1"} and image[SK] == {"S": "/m.glb#v2"}
        assert image["embeddingDimensions"] == {"N": "2"} and image["fileSize"] == {"N": "42"}
        assert image["sourceModalities"] == {"L": [{"S": "renders"}, {"S": "file-attributes"}]}

    def test_embedding_is_a_list_of_numbers_rounded_to_nine_digits(self, vs):
        image = vs.serialize_item(_item(vs))
        assert image["embedding"] == {"L": [{"N": "0.123456789"}, {"N": "1E-12"}]}

    def test_number_string_edge_cases(self, vs):
        assert vs.number_string(0.0) == "0"
        assert vs.number_string(-0.0) == "0"
        assert vs.number_string(0.5) == "0.5"
        assert vs.number_string(-0.25) == "-0.25"
        assert vs.number_string(1.0) == "1"

    def test_absent_extension_and_version_take_their_sentinels(self, vs):
        item = _item(vs, fileExt="", versionId="")
        image = vs.serialize_item(item)
        assert image["fileExt"] == {"S": "none"}
        assert image["versionId"] == {"S": "null"}
        assert image[SK] == {"S": "/m.glb#null"}

    def test_preview_key_is_omitted_when_absent_and_written_when_present(self, vs):
        assert "previewFileKey" not in vs.serialize_item(_item(vs))
        assert vs.serialize_item(_item(vs, previewFileKey="a1/m.glb.previewFile.png"))["previewFileKey"] == {"S": "a1/m.glb.previewFile.png"}

    def test_no_bool_attribute_is_ever_written(self, vs):
        image = vs.serialize_item(_item(vs))
        assert not any("BOOL" in value for value in image.values())

    def test_deserialize_round_trip_gives_plain_python(self, vs):
        plain = vs.deserialize_item(vs.serialize_item(_item(vs)))
        assert plain["fileSize"] == 42 and isinstance(plain["fileSize"], int)
        assert plain["embedding"][0] == 0.123456789 and isinstance(plain["embedding"][0], float)
        assert plain["isLatest"] == "true"
        assert plain["sourceModalities"] == ["renders", "file-attributes"]
        assert plain["segmentStartMs"] is None and plain["segmentCount"] == 0
        assert vs.deserialize_item(vs.serialize_item(_segment_item(vs)))["segmentStartMs"] == 83456


@pytest.mark.unit
class TestPutItem:
    def test_writes_the_serialized_image(self, vs):
        fake = FakeDynamo()
        item = _item(vs)
        _store(vs, fake).put_item(item)
        assert fake.put_items == [{"TableName": TABLE, "Item": vs.serialize_item(item)}]

    def test_other_model_is_refused(self, vs):
        fake = FakeDynamo()
        with pytest.raises(vs.VectorModelMismatch):
            _store(vs, fake).put_item(_item(vs, embeddingModelId="cohere.embed-english-v3"))
        assert fake.put_items == []

    def test_other_dimension_count_is_refused(self, vs):
        with pytest.raises(vs.VectorModelMismatch):
            _store(vs, FakeDynamo()).put_item(_item(vs, embeddingDimensions=1024))

    def test_vector_length_must_match_the_index(self, vs):
        with pytest.raises(vs.VectorModelMismatch):
            _store(vs, FakeDynamo()).put_item(_item(vs, embedding=[0.1, 0.2, 0.3]))


@pytest.mark.unit
class TestPutLatestItem:
    """The isLatest write path: one sibling Query, then a plain PutItem when it returns nothing, otherwise
    the Put and the conditional demotions in TransactWriteItems of at most DEMOTION_BATCH_ACTIONS (25)
    actions -- the Put and 24 demotions first, then 25 per transaction."""

    def test_one_query_then_one_transaction_holding_the_put_and_a_flip_per_sibling(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1"), _key("v0")]}, name="latest siblings")
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == 2
        assert fake.put_items == []
        assert fake.updates == []
        assert fake.transactions == [{"TransactItems": [_put(vs, item), _demotion("v1"), _demotion("v0")]}]

    def test_the_sibling_query_is_the_file_prefix_filtered_to_latest_items_of_other_versions(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).put_latest_item(_item(vs))
        (call,) = fake.query_pager.calls
        assert call == {
            "TableName": TABLE,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "FilterExpression": "isLatest = :latest AND versionId <> :v",
            "ExpressionAttributeNames": {"#pk": PK, "#sk": SK},
            "ExpressionAttributeValues": {
                ":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}, ":latest": {"S": "true"}, ":v": {"S": "v2"},
            },
            "ProjectionExpression": "#pk, #sk",
        }

    def test_the_sibling_query_prefix_is_the_encoded_key_path(self, vs):
        """A ``#`` in the file path is encoded in the prefix, so the Query cannot reach into a file whose
        name happens to start with this file's name and a separator."""
        fake = FakeDynamo()
        _store(vs, fake).put_latest_item(_item(vs, filePath="/dir/a#b.glb"))
        (call,) = fake.query_pager.calls
        assert call["ExpressionAttributeValues"][":prefix"] == {"S": "/dir/a%23b.glb#"}

    def test_a_segment_document_queries_the_file_prefix_and_excludes_its_own_version_whole(self, vs):
        """A segment document demotes the other versions' items, whole-file and segments alike, and never
        its own version's whole-file item or sibling segments -- the exclusion is by version, not by key."""
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1"), _key("v1#t0000083456")]}, name="latest siblings")
        item = _segment_item(vs)
        assert _store(vs, fake).put_latest_item(item) == 2
        (call,) = fake.query_pager.calls
        assert call["ExpressionAttributeValues"] == {
            ":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}, ":latest": {"S": "true"}, ":v": {"S": "v2"},
        }
        (transaction,) = fake.transactions
        assert transaction["TransactItems"] == [_put(vs, item), _demotion("v1"), _demotion("v1#t0000083456")]
        assert vs.serialize_item(item)[SK] == {"S": "/m.glb#v2#t0000083456"}

    def test_no_sibling_is_a_plain_put_item_and_no_transaction(self, vs):
        fake = FakeDynamo()
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == 0
        assert fake.put_items == [{"TableName": TABLE, "Item": vs.serialize_item(item)}]
        assert fake.transactions == [] and fake.updates == []

    def test_the_sibling_walk_pages_to_exhaustion_through_a_filtered_empty_page(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_key("v1")], "LastEvaluatedKey": {SK: {"S": "/m.glb#v1"}}},
            {"Items": [], "LastEvaluatedKey": {SK: {"S": "/m.glb#v1a"}}},
            {"Items": [_key("v0")]},
            name="latest siblings",
        )
        assert _store(vs, fake).put_latest_item(_item(vs)) == 2
        fake.query_pager.assert_paged_to_exhaustion()
        (transaction,) = fake.transactions
        assert {a["Update"]["Key"][SK]["S"] for a in transaction["TransactItems"] if "Update" in a} == {"/m.glb#v1", "/m.glb#v0"}

    def test_a_cancelled_transaction_is_retried_after_a_fresh_sibling_query_without_a_backoff(self, vs):
        """A lost condition (another writer demoted the sibling first) needs no waiting: the retry
        re-reads and re-runs at once."""
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1")]}, name="latest siblings")
        fake.transact_cancel_once = True
        with patch.object(vs.time, "sleep") as sleep:
            assert _store(vs, fake).put_latest_item(_item(vs)) == 1
        assert not sleep.called
        assert len(fake.query_pager.calls) >= 2
        assert len(fake.transactions) >= 2
        assert fake.transactions[-1] == fake.transactions[0]

    @pytest.mark.parametrize("code", ["ThrottlingError", "ProvisionedThroughputExceeded"])
    def test_a_throttled_cancellation_backs_off_before_the_fresh_sibling_query(self, vs, code):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1")]}, name="latest siblings")
        fake.transact_cancel_once = True
        fake.transact_cancel_reasons = [code, "None"]
        with patch.object(vs.time, "sleep") as sleep:
            assert _store(vs, fake).put_latest_item(_item(vs)) == 1
        sleep.assert_any_call(vs.TRANSACT_BACKOFF_BASE_SECONDS)
        assert len(fake.query_pager.calls) >= 2
        assert fake.transactions[-1] == fake.transactions[0]

    def test_the_backoff_doubles_and_the_last_attempt_does_not_sleep(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1")]}, name="latest siblings")
        fake.transact_cancel_always = True
        fake.transact_cancel_reasons = ["None", "ThrottlingError"]
        with patch.object(vs.time, "sleep") as sleep:
            with pytest.raises(vs.VectorStoreError):
                _store(vs, fake).put_latest_item(_item(vs))
        sleep.assert_any_call(vs.TRANSACT_BACKOFF_BASE_SECONDS)
        sleep.assert_any_call(vs.TRANSACT_BACKOFF_BASE_SECONDS * 2)
        assert sleep.call_count < vs.TRANSACT_MAX_ATTEMPTS

    def test_the_retry_is_bounded(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1")]}, name="latest siblings")
        fake.transact_cancel_always = True
        with pytest.raises(vs.VectorStoreError):
            _store(vs, fake).put_latest_item(_item(vs))
        assert 2 <= len(fake.transactions) <= vs.TRANSACT_MAX_ATTEMPTS

    def test_other_client_errors_propagate(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key("v1")]}, name="latest siblings")
        fake.transact_write_items = _raiser(_client_error("ProvisionedThroughputExceededException", operation="TransactWriteItems"))
        with pytest.raises(ClientError):
            _store(vs, fake).put_latest_item(_item(vs))

    def test_twenty_four_siblings_fill_one_transaction(self, vs):
        """The boundary below the spill: the Put and one demotion fewer than the batch size are a
        single transaction of exactly DEMOTION_BATCH_ACTIONS actions."""
        fake = FakeDynamo()
        siblings = vs.DEMOTION_BATCH_ACTIONS - 1
        fake.query_pager = Pager({"Items": [_key(f"v{i}") for i in range(siblings)]}, name="latest siblings")
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == siblings
        assert fake.transactions == [{"TransactItems": [_put(vs, item)] + [_demotion(f"v{i}") for i in range(siblings)]}]
        assert fake.put_items == []

    def test_the_twenty_fifth_sibling_spills_into_a_second_transaction(self, vs):
        """Control for the boundary above: one sibling more and the Put still commits with the first 24
        demotions, the last demotion following alone; nothing is refused and nothing bypasses the
        transactions."""
        fake = FakeDynamo()
        siblings = vs.DEMOTION_BATCH_ACTIONS
        fake.query_pager = Pager({"Items": [_key(f"v{i}") for i in range(siblings)]}, name="latest siblings")
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == siblings
        actions = [_put(vs, item)] + [_demotion(f"v{i}") for i in range(siblings)]
        assert fake.transactions == _batches(actions, vs.DEMOTION_BATCH_ACTIONS)
        assert [len(t["TransactItems"]) for t in fake.transactions] == [25, 1]
        assert fake.put_items == [] and fake.updates == []

    def test_ninety_nine_siblings_are_four_full_transactions(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key(f"v{i}") for i in range(99)]}, name="latest siblings")
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == 99
        actions = [_put(vs, item)] + [_demotion(f"v{i}") for i in range(99)]
        assert fake.transactions == _batches(actions, vs.DEMOTION_BATCH_ACTIONS)
        assert [len(t["TransactItems"]) for t in fake.transactions] == [25, 25, 25, 25]

    def test_hundreds_of_siblings_are_demoted_in_batches_after_the_put(self, vs):
        """An older version of a segmented file holds one latest-marked item per chunk until the S3
        ObjectCreated demotion runs: the Put and the first 24 demotions commit together, the rest follow
        in transactions of at most 25 in Query order, each sibling is demoted exactly once and the return
        value counts them all -- 1 Put + 250 demotions = 251 actions = ten transactions of 25 and one of 1."""
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_key(f"v1#c{i:06d}") for i in range(1, 251)]}, name="latest siblings")
        item = _item(vs)
        assert _store(vs, fake).put_latest_item(item) == 250
        actions = [_put(vs, item)] + [_demotion(f"v1#c{i:06d}") for i in range(1, 251)]
        assert fake.transactions == _batches(actions, vs.DEMOTION_BATCH_ACTIONS)
        assert [len(t["TransactItems"]) for t in fake.transactions] == [25] * 10 + [1]
        assert fake.put_items == [] and fake.updates == []

    def test_an_item_not_marked_latest_is_refused(self, vs):
        fake = FakeDynamo()
        with pytest.raises(ValueError):
            _store(vs, fake).put_latest_item(_item(vs, isLatest=False))
        assert fake.query_pager.calls == [] and fake.transactions == [] and fake.put_items == []

    def test_other_model_is_refused_before_the_query(self, vs):
        fake = FakeDynamo()
        with pytest.raises(vs.VectorModelMismatch):
            _store(vs, fake).put_latest_item(_item(vs, embeddingModelId="cohere.embed-english-v3"))
        assert fake.query_pager.calls == [] and fake.transactions == [] and fake.put_items == []


@pytest.mark.unit
class TestLatestFlips:
    def test_only_the_other_versions_are_flipped_and_the_walk_pages_to_exhaustion(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_image("v1"), _image("v2")], "LastEvaluatedKey": {SK: {"S": "/m.glb#v2"}}},
            {"Items": [_image("v3", is_latest="false")]},
            name="file versions",
        )
        changed, next_key = _store(vs, fake).set_not_latest_for_file_except("db1:a1", "/m.glb", "v2")
        assert (changed, next_key) == (1, None)
        fake.query_pager.assert_paged_to_exhaustion()
        assert [u["Key"][SK]["S"] for u in fake.updates] == ["/m.glb#v1"]
        assert fake.updates[0] == {
            "TableName": TABLE,
            "Key": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}},
            "UpdateExpression": "SET isLatest = :target",
            "ConditionExpression": "isLatest = :current",
            "ExpressionAttributeValues": {":target": {"S": "false"}, ":current": {"S": "true"}},
        }

    def test_query_groups_the_file_by_key_path_prefix(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).set_not_latest_for_file_except("db1:a1", "/m.glb", "v2")
        (call,) = fake.query_pager.calls
        assert call == {
            "TableName": TABLE,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "ExpressionAttributeNames": {"#pk": PK, "#sk": SK},
            "ExpressionAttributeValues": {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}},
            "ProjectionExpression": "#pk, #sk, versionId, isLatest, isArchived",
        }

    def test_a_lost_condition_is_not_counted(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1"), _image("v2")]}, name="file versions")
        fake.conditional_failures = {"/m.glb#v1"}
        assert _store(vs, fake).set_not_latest_for_file_except("db1:a1", "/m.glb", "v2") == (0, None)
        assert len(fake.updates) == 1

    def test_other_client_errors_propagate(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1")]}, name="file versions")
        fake.update_item = _raiser(_client_error("ProvisionedThroughputExceededException"))
        with pytest.raises(ClientError):
            _store(vs, fake).set_not_latest_for_file_except("db1:a1", "/m.glb", "v2")

    def test_the_store_has_no_second_name_for_the_sibling_flip(self, vs):
        """``set_not_latest_for_file_except`` is the one sibling-flip method and ``put_latest_item`` the
        one put-and-flip; a ``mark_others_not_latest`` alias would let a caller pick a name the store
        retired."""
        assert not hasattr(vs.VectorStore, "mark_others_not_latest")
        assert not hasattr(vs.DynamoDbVectorStore, "mark_others_not_latest")
        assert callable(vs.DynamoDbVectorStore.put_latest_item)


@pytest.mark.unit
class TestSetLatestForFileVersion:
    """The realignment a writer runs when its latest write raced a newer upload: the named version's
    items are flipped to latest, every other item of the file to not-latest, each conditionally."""

    def test_the_named_version_is_promoted_and_every_other_item_demoted(self, vs):
        # A v2 segment item carries versionId "v2" under its own sort key and is promoted with its file.
        v2_segment = {**_image("v2", is_latest="false"), SK: {"S": "/m.glb#v2#t0000083456"}}
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_image("v1", is_latest="true"), _image("v2", is_latest="false")],
             "LastEvaluatedKey": {SK: {"S": "/m.glb#v2"}}},
            {"Items": [v2_segment, _image("v3", is_latest="false")]},
            name="file versions",
        )
        changed = _store(vs, fake).set_latest_for_file_version("db1:a1", "/m.glb", "v2")
        assert changed == 3
        fake.query_pager.assert_paged_to_exhaustion()
        flips = {(u["Key"][SK]["S"], u["ExpressionAttributeValues"][":target"]["S"]) for u in fake.updates}
        assert flips == {("/m.glb#v1", "false"), ("/m.glb#v2", "true"), ("/m.glb#v2#t0000083456", "true")}
        assert all(u["ExpressionAttributeValues"][":current"]["S"] != u["ExpressionAttributeValues"][":target"]["S"]
                   for u in fake.updates)

    def test_items_already_aligned_are_not_written(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1", is_latest="false"), _image("v2", is_latest="true")]}, name="file")
        assert _store(vs, fake).set_latest_for_file_version("db1:a1", "/m.glb", "v2") == 0
        assert fake.updates == []

    def test_the_walk_reads_the_file_prefix_and_completes_whatever_the_page_count(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_image("v1")], "LastEvaluatedKey": {SK: {"S": "/m.glb#v1"}}},
            {"Items": [_image("v2", is_latest="false")]},
            name="file versions",
        )
        _store(vs, fake).set_latest_for_file_version("db1:a1", "/m.glb", "v2")
        fake.query_pager.assert_paged_to_exhaustion()
        first = fake.query_pager.calls[0]
        assert first["ExpressionAttributeValues"] == {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#"}}
        assert "begins_with" in first["KeyConditionExpression"]

    def test_a_lost_condition_is_not_counted(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1"), _image("v2", is_latest="false")]}, name="file")
        fake.conditional_failures = {"/m.glb#v1"}
        assert _store(vs, fake).set_latest_for_file_version("db1:a1", "/m.glb", "v2") == 1


@pytest.mark.unit
class TestArchivedFlips:
    def test_file_items_are_flipped_once_each_and_no_ops_are_skipped(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1"), _image("v2", is_archived="true")]}, name="file")
        assert _store(vs, fake).set_archived_for_file("db1:a1", "/m.glb", True) == (1, None)
        (update,) = fake.updates
        assert update["Key"][SK]["S"] == "/m.glb#v1"
        assert update["ExpressionAttributeValues"] == {":target": {"S": "true"}, ":current": {"S": "false"}}

    def test_asset_flip_queries_the_partition_without_a_prefix(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_image("v1", is_archived="true"), _image("v1", is_archived="true", key_path="/n.glb")]}, name="asset")
        assert _store(vs, fake).set_archived_for_asset("db1:a1", False) == (2, None)
        (call,) = fake.query_pager.calls
        assert "begins_with" not in call["KeyConditionExpression"]
        assert call["ExpressionAttributeValues"] == {":pk": {"S": "db1:a1"}}
        assert {u["ExpressionAttributeValues"][":target"]["S"] for u in fake.updates} == {"false"}
        assert {u["Key"][SK]["S"] for u in fake.updates} == {"/m.glb#v1", "/n.glb#v1"}

    def test_a_missing_flag_is_set_under_attribute_not_exists(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1", is_archived=None)]}, name="file")
        assert _store(vs, fake).set_archived_for_file("db1:a1", "/m.glb", True) == (1, None)
        (update,) = fake.updates
        assert update == {
            "TableName": TABLE,
            "Key": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}},
            "UpdateExpression": "SET isArchived = :target",
            "ConditionExpression": "attribute_not_exists(isArchived)",
            "ExpressionAttributeValues": {":target": {"S": "true"}},
        }


# The five file-wide and asset-wide walks, each as (name, caller) so one test drives them all through
# the same two-page script; the caller forwards the continuation keywords.
_WALKS = [
    ("set_archived_for_file", lambda store, **kw: store.set_archived_for_file("db1:a1", "/m.glb", True, **kw)),
    ("set_archived_for_asset", lambda store, **kw: store.set_archived_for_asset("db1:a1", True, **kw)),
    ("set_not_latest_for_file_except", lambda store, **kw: store.set_not_latest_for_file_except("db1:a1", "/m.glb", "v9", **kw)),
    ("delete_file", lambda store, **kw: store.delete_file("db1:a1", "/m.glb", **kw)),
    ("delete_asset", lambda store, **kw: store.delete_asset("db1:a1", **kw)),
]


def _touched(fake):
    """Every sort key the fake saw an update or a delete for, in the order they were issued."""
    updated = [u["Key"][SK]["S"] for u in fake.updates]
    deleted = [r["DeleteRequest"]["Key"][SK]["S"] for batch in fake.batch_writes for r in batch[TABLE]]
    return updated + deleted


@pytest.mark.unit
class TestContinuation:
    """The walks page under a time budget: a page is processed whole, then, when ``time_remaining_fn``
    reports fewer than ``min_remaining_ms`` milliseconds and a next page exists, its cursor is returned
    for the caller (the indexer) to re-enqueue; the resumed call reads from that cursor."""

    CURSOR = {SK: {"S": "/m.glb#v1"}}

    def _two_pages(self, name):
        return Pager({"Items": [_image("v1")], "LastEvaluatedKey": self.CURSOR}, {"Items": [_image("v2")]}, name=name)

    @pytest.mark.parametrize("name, walk", _WALKS, ids=[name for name, _ in _WALKS])
    def test_every_walk_stops_on_a_short_budget_and_resumes_from_the_cursor_it_returned(self, vs, name, walk):
        fake = FakeDynamo()
        fake.query_pager = self._two_pages(name)
        store = _store(vs, fake)
        assert walk(store, time_remaining_fn=lambda: 0) == (1, self.CURSOR)
        assert self.CURSOR not in fake.query_pager.resumed_from
        assert _touched(fake) == ["/m.glb#v1"]
        assert walk(store, start_key=self.CURSOR, time_remaining_fn=lambda: 0) == (1, None)
        fake.query_pager.assert_paged_to_exhaustion()
        assert fake.query_pager.calls[-1]["ExclusiveStartKey"] == self.CURSOR
        assert _touched(fake) == ["/m.glb#v1", "/m.glb#v2"]

    def test_an_ample_budget_walks_to_exhaustion_and_returns_no_cursor(self, vs):
        fake = FakeDynamo()
        fake.query_pager = self._two_pages("file")
        assert _store(vs, fake).set_archived_for_file("db1:a1", "/m.glb", True, time_remaining_fn=lambda: 300_000) == (2, None)
        fake.query_pager.assert_paged_to_exhaustion()

    def test_without_a_budget_the_walk_never_stops_early(self, vs):
        fake = FakeDynamo()
        fake.query_pager = self._two_pages("file")
        assert _store(vs, fake).delete_file("db1:a1", "/m.glb") == (2, None)
        fake.query_pager.assert_paged_to_exhaustion()

    def test_min_remaining_ms_is_the_threshold_and_defaults_to_sixty_seconds(self, vs):
        at_default = FakeDynamo()
        at_default.query_pager = self._two_pages("at the default")
        assert _store(vs, at_default).delete_file("db1:a1", "/m.glb", time_remaining_fn=lambda: 60_000) == (2, None)
        under_default = FakeDynamo()
        under_default.query_pager = self._two_pages("under the default")
        assert _store(vs, under_default).delete_file("db1:a1", "/m.glb", time_remaining_fn=lambda: 59_999) == (1, self.CURSOR)
        raised = FakeDynamo()
        raised.query_pager = self._two_pages("raised threshold")
        assert _store(vs, raised).delete_file(
            "db1:a1", "/m.glb", time_remaining_fn=lambda: 60_000, min_remaining_ms=120_000) == (1, self.CURSOR)

    def test_a_walk_that_completes_returns_no_cursor_whatever_the_budget(self, vs):
        """The budget is consulted only when a next page exists, so a finished walk is never asked to
        continue."""
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1"), _image("v2")]}, name="one page")
        assert _store(vs, fake).set_archived_for_asset("db1:a1", True, time_remaining_fn=lambda: 0) == (2, None)

    def test_per_item_updates_run_through_the_bounded_pool(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image(f"v{i}") for i in range(20)]}, name="file")
        seen = {}
        real_executor = vs.ThreadPoolExecutor

        def recording(*args, **kwargs):
            seen["max_workers"] = kwargs.get("max_workers", args[0] if args else None)
            return real_executor(*args, **kwargs)

        with patch.object(vs, "ThreadPoolExecutor", side_effect=recording):
            assert _store(vs, fake).set_archived_for_file("db1:a1", "/m.glb", True) == (20, None)
        assert seen["max_workers"] == vs.FLIP_WORKERS == 8
        assert {u["Key"][SK]["S"] for u in fake.updates} == {f"/m.glb#v{i}" for i in range(20)}


@pytest.mark.unit
class TestDeletes:
    def test_delete_file_batches_by_twenty_five(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image(f"v{i}") for i in range(30)]}, name="file")
        assert _store(vs, fake).delete_file("db1:a1", "/m.glb") == (30, None)
        assert [len(w[TABLE]) for w in fake.batch_writes] == [25, 5]
        assert fake.batch_writes[0][TABLE][0] == {"DeleteRequest": {"Key": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v0"}}}}
        (call,) = fake.query_pager.calls
        assert {"#pk", "#sk"} <= set(call["ProjectionExpression"].split(", "))

    def test_delete_asset_uses_the_partition_query(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_image("v1"), _image("v1", key_path="/n.glb")]}, name="asset")
        assert _store(vs, fake).delete_asset("db1:a1") == (2, None)
        (call,) = fake.query_pager.calls
        assert "begins_with" not in call["KeyConditionExpression"]
        assert call["ExpressionAttributeValues"] == {":pk": {"S": "db1:a1"}}

    def test_unprocessed_deletes_are_retried_and_counted_once(self, vs):
        fake = FakeDynamo()
        fake.unprocessed_once = True
        with patch.object(vs.time, "sleep") as sleep:
            deleted = _store(vs, fake).delete_keys([_image("v1"), _image("v2")])
        assert deleted == 2
        assert len(fake.batch_writes) == 2
        assert len(fake.batch_writes[1][TABLE]) == 1
        assert 1 <= sleep.call_count < vs.BATCH_WRITE_MAX_ATTEMPTS

    def test_the_retry_is_bounded(self, vs):
        fake = FakeDynamo()
        fake.batch_write_item = lambda RequestItems: {"UnprocessedItems": dict(RequestItems)}
        with patch.object(vs.time, "sleep"):
            with pytest.raises(vs.VectorStoreError):
                _store(vs, fake).delete_keys([_image("v1")])

    def test_no_keys_means_no_write(self, vs):
        fake = FakeDynamo()
        assert _store(vs, fake).delete_keys([]) == 0
        assert fake.batch_writes == []


def _deleted_keys(fake):
    """Every sort key the fake saw a conditional DeleteItem for."""
    return {d["Key"][SK]["S"] for d in fake.deletes}


@pytest.mark.unit
class TestDeleteOtherRunSegments:
    """The whole-file document's cleanup: the same version's segments that another pipeline run
    published are deleted, each conditionally on still belonging to another run; the current run's own
    segments are left alone."""

    def test_deletes_the_segments_another_run_published_and_pages_to_exhaustion(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {
                "Items": [_segment_image("v2", "t0000000000", "pe1"), _segment_image("v2", "t0000010000", "pe0")],
                "LastEvaluatedKey": {SK: {"S": "/m.glb#v2#t0000010000"}},
            },
            {"Items": [_segment_image("v2", "c000001", "pe0")]},
            name="version segments",
        )
        assert _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1") == 2
        fake.query_pager.assert_paged_to_exhaustion()
        assert _deleted_keys(fake) == {"/m.glb#v2#t0000010000", "/m.glb#v2#c000001"}
        assert fake.batch_writes == []

    def test_each_delete_is_conditional_on_the_item_still_belonging_to_another_run(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_segment_image("v2", "t0000010000", "pe0")]}, name="version segments")
        _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1")
        (delete,) = fake.deletes
        assert delete == {
            "TableName": TABLE,
            "Key": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v2#t0000010000"}},
            "ConditionExpression": "pipelineExecutionId <> :run",
            "ExpressionAttributeValues": {":run": {"S": "pe1"}},
        }

    def test_a_key_the_current_run_rewrote_between_the_read_and_the_delete_survives(self, vs):
        # Segment keys are shared across runs: the current run's t0000010000 landed after the query read
        # the earlier run's item at that key, so the conditional delete loses and the item is kept.
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_segment_image("v2", "t0000010000", "pe0"), _segment_image("v2", "c000001", "pe0")]},
            name="version segments",
        )
        fake.conditional_failures = {"/m.glb#v2#t0000010000"}
        assert _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1") == 1
        assert _deleted_keys(fake) == {"/m.glb#v2#t0000010000", "/m.glb#v2#c000001"}

    def test_other_client_errors_propagate(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager({"Items": [_segment_image("v2", "c000001", "pe0")]}, name="version segments")
        fake.delete_item = _raiser(_client_error("ProvisionedThroughputExceededException", operation="DeleteItem"))
        with pytest.raises(ClientError):
            _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1")

    def test_queries_the_version_prefix_projecting_the_keys_and_the_run_id(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1")
        (call,) = fake.query_pager.calls
        assert call == {
            "TableName": TABLE,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :prefix)",
            "ExpressionAttributeNames": {"#pk": PK, "#sk": SK},
            "ExpressionAttributeValues": {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#v2#"}},
            "ProjectionExpression": "#pk, #sk, pipelineExecutionId",
        }

    def test_the_current_runs_own_segments_are_never_touched(self, vs):
        fake = FakeDynamo()
        fake.query_pager = Pager(
            {"Items": [_segment_image("v2", "t0000000000", "pe1"), _segment_image("v2", "c000001", "pe1")]},
            name="version segments",
        )
        assert _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1") == 0
        assert fake.deletes == [] and fake.batch_writes == []

    def test_a_stale_run_with_many_chunks_is_deleted_item_by_item_on_the_bounded_pool(self, vs):
        """Thirty stale chunks of an earlier run beside the current run's first chunk: every stale key is
        deleted, the current run's key is not, and the deletes run through the FLIP_WORKERS pool."""
        fake = FakeDynamo()
        stale = [_segment_image("v2", f"c{i:06d}", "pe0") for i in range(2, 32)]
        fake.query_pager = Pager({"Items": [_segment_image("v2", "c000001", "pe1"), *stale]}, name="version segments")
        seen = {}
        real_executor = vs.ThreadPoolExecutor

        def recording(*args, **kwargs):
            seen["max_workers"] = kwargs.get("max_workers", args[0] if args else None)
            return real_executor(*args, **kwargs)

        with patch.object(vs, "ThreadPoolExecutor", side_effect=recording):
            assert _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "v2", "pe1") == 30
        assert seen["max_workers"] == vs.FLIP_WORKERS
        assert _deleted_keys(fake) == {f"/m.glb#v2#c{i:06d}" for i in range(2, 32)}
        assert "/m.glb#v2#c000001" not in _deleted_keys(fake)

    def test_an_unversioned_file_is_addressed_under_the_null_version(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).delete_other_run_segments("db1:a1", "/m.glb", "", "pe1")
        (call,) = fake.query_pager.calls
        assert call["ExpressionAttributeValues"] == {":pk": {"S": "db1:a1"}, ":prefix": {"S": "/m.glb#null#"}}


@pytest.mark.unit
class TestScanKeys:
    def test_first_page(self, vs):
        fake = FakeDynamo()
        fake.scan_response = {"Items": [_image("v1")], "LastEvaluatedKey": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}}}
        items, last = _store(vs, fake).scan_keys(limit=1)
        assert items == [_image("v1")] and last == {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}}
        (call,) = fake.scans
        assert call == {"TableName": TABLE, "ProjectionExpression": "#pk, #sk", "ExpressionAttributeNames": {"#pk": PK, "#sk": SK}, "Limit": 1}

    def test_continuation_threads_the_cursor(self, vs):
        fake = FakeDynamo()
        cursor = {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}}
        items, last = _store(vs, fake).scan_keys(start_key=cursor)
        assert (items, last) == ([], None)
        assert fake.scans[0]["ExclusiveStartKey"] == cursor
        assert fake.scans[0]["Limit"] == 1000


@pytest.mark.unit
class TestSearch:
    def test_unfiltered_request_omits_every_expression_key(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).search([0.5, 0.25], top_k=5, filters={})
        (call,) = fake.searches
        assert call == {"TableName": TABLE, "IndexName": INDEX, "SearchVector": [{"N": "0.5"}, {"N": "0.25"}], "TopK": 5}

    def test_filters_become_aliased_equalities(self, vs):
        fake = FakeDynamo()
        _store(vs, fake).search([0.5, 0.25], top_k=10, filters={"isLatest": "true", "embeddingModelId": MODEL, "databaseId": "db1"})
        (call,) = fake.searches
        assert call["SearchConditionExpression"] == "#isLatest = :isLatest AND #embeddingModelId = :embeddingModelId AND #databaseId = :databaseId"
        assert call["ExpressionAttributeNames"] == {"#isLatest": "isLatest", "#embeddingModelId": "embeddingModelId", "#databaseId": "databaseId"}
        assert call["ExpressionAttributeValues"] == {":isLatest": {"S": "true"}, ":embeddingModelId": {"S": MODEL}, ":databaseId": {"S": "db1"}}

    def test_hits_carry_plain_items_and_the_distance(self, vs):
        fake = FakeDynamo()
        fake.search_response = {"SearchResults": [
            {"Item": {PK: {"S": "db1:a1"}, SK: {"S": "/m.glb#v1"}, "fileClass": {"S": "mesh"}, "fileSize": {"N": "42"}}, "Score": 0.25},
            {"Item": {PK: {"S": "db1:a2"}, SK: {"S": "/n.glb#v1"}}, "Score": 0.75},
        ]}
        hits = _store(vs, fake).search([0.5, 0.25], top_k=2, filters={})
        assert [h.distance for h in hits] == [0.25, 0.75]
        assert hits[0].item == {PK: "db1:a1", SK: "/m.glb#v1", "fileClass": "mesh", "fileSize": 42}

    @pytest.mark.parametrize("top_k", [0, 101])
    def test_top_k_outside_the_service_range_is_refused_client_side(self, vs, top_k):
        with pytest.raises(ValueError):
            _store(vs, FakeDynamo()).search([0.5, 0.25], top_k=top_k, filters={})

    def test_top_k_bounds_are_inclusive(self, vs):
        fake = FakeDynamo()
        store = _store(vs, fake)
        store.search([0.5, 0.25], top_k=1, filters={})
        store.search([0.5, 0.25], top_k=100, filters={})
        assert [c["TopK"] for c in fake.searches] == [1, 100]

    def test_vector_length_must_match(self, vs):
        with pytest.raises(ValueError):
            _store(vs, FakeDynamo()).search([0.5], top_k=5, filters={})

    def test_unknown_filter_attribute_is_refused(self, vs):
        with pytest.raises(ValueError):
            _store(vs, FakeDynamo()).search([0.5, 0.25], top_k=5, filters={"assetId": "a1"})

    def test_backfilling_validation_exception_is_index_not_ready(self, vs):
        fake = FakeDynamo()
        fake.search_error = _client_error("ValidationException", "The table does not have the specified index: vec-x", "SearchVectors")
        with pytest.raises(vs.VectorIndexNotReady):
            _store(vs, fake).search([0.5, 0.25], top_k=5, filters={})

    def test_other_validation_exceptions_propagate(self, vs):
        """Control: a request bug is not disguised as an index that is still building."""
        fake = FakeDynamo()
        fake.search_error = _client_error("ValidationException", "Invalid comparator used in SearchConditionExpression", "SearchVectors")
        with pytest.raises(ClientError):
            _store(vs, fake).search([0.5, 0.25], top_k=5, filters={})

    def test_throttling_propagates(self, vs):
        fake = FakeDynamo()
        fake.search_error = _client_error("ThrottlingException", "slow down", "SearchVectors")
        with pytest.raises(ClientError):
            _store(vs, fake).search([0.5, 0.25], top_k=5, filters={})

    def test_is_index_not_ready_markers(self, vs):
        assert vs.is_index_not_ready(_client_error("ValidationException", "Index is backfilling")) is True
        assert vs.is_index_not_ready(_client_error("ValidationException", "Index status is not ACTIVE")) is True
        assert vs.is_index_not_ready(_client_error("ResourceNotFoundException", "does not have the specified index")) is False
