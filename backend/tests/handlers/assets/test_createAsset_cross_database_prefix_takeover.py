# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S25-SEC-001 (CRITICAL): asset creation by assetId must consult the asset records,
not only the S3 listing, before binding a new asset onto a derived S3 prefix.

The reported chain: the by-assetId branch derives ``s3_key = baseAssetsPrefix + assetId + '/'``,
which carries no databaseId, so two databases sharing a bucket collide on assetId alone (and the
shared-bucket topology is the default). The pre-persist existence check is
``get_item(Key={'databaseId': <caller's db>, 'assetId': ...})``, a different partition key from a
victim in another database — or in that database's ``#deleted`` partition — so the victim is
invisible to it. The only remaining uniqueness guard was ``check_s3_prefix_exists``, which uses
``list_objects_v2``; archiving an asset issues ``delete_object`` with no VersionId against every
object under the prefix (including the 0-byte folder marker), so the versions are retained but
nothing live remains and the listing reports the prefix as empty. The result was a create in DB-A
that bound onto DB-B's retained data, reachable afterwards through ordinary file APIs.

## The two layers, and which failure each one owns

An assetId-keyed index cannot express the invariant that actually matters, which is about the KEY: no
new asset may occupy a key equal to, a parent of, or a child of an existing asset's key in the same
physical location. ``bucketExistingKey`` onboarding decouples the two — an asset created that way gets
``assetId = x<uuid>`` and ``assetLocation.Key = assets/projects/building-a/`` — so a victim onboarded
that way is invisible to any lookup keyed on the attacker's chosen assetId. The guards are therefore
split, and the tests are grouped by which one they pin:

* ``check_s3_prefix_exists`` reads through ``list_object_versions``, which returns retained versions AND
  delete markers. Occupancy is a property of the prefix, so this catches an overlapping victim whatever
  its assetId or bucket record — including the archived ``bucketExistingKey`` victim above.
  ``TestArchiveAwarePrefixCheck`` and ``TestNonDerivedVictimKeyIsRejected``.
* ``assert_derived_asset_key_not_owned`` reads the asset records, and covers the one case S3 cannot: a
  record that still owns the key while its S3 data has been permanently expunged.
  ``TestArchivedPrefixTakeoverDenied``.

## Why these tests assert arguments, not only the verdict

The module loader stubs ``handlers.authz`` with a bare ``MagicMock``, so any enforcer call returns a
truthy Mock — a test written against a 403/allow verdict alone can pass for the wrong reason. Each
denial test here therefore also asserts *where* the denial came from: which layer refused, and that the
other one was not silently taking the credit. Same for the permit cases, which assert the folder marker
was actually written.

The query stub resolves a ``KeyConditionExpression`` **semantically** rather than by object equality —
see ``_condition_terms``. Matching one exact spelling made a strictly broader, safer implementation
fail three denial tests, which trains the next author back toward the narrow query.

``_load(fresh=True)`` is required: sibling test modules stub module globals in place on the cached
instance, and these tests exercise the real ownership functions.
"""

from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.conditions import Attr, ConditionBase, Key

from tests.handlers.assets.test_createAsset_conditional_put import _load

_OWN_DB = "database-a"
_OTHER_DB = "database-b"
_BUCKET_NAME = "shared-asset-bucket"
_BASE_PREFIX = "assets/"
_OWN_BUCKET_ID = "bucket-record-a"
_SIBLING_BUCKET_ID = "bucket-record-b"
_VICTIM_ASSET_ID = "victim-asset"
_VICTIM_KEY = _BASE_PREFIX + _VICTIM_ASSET_ID + "/"

# An asset onboarded through bucketExistingKey: server-generated assetId, caller-chosen key. The two
# share no segment, which is exactly why an assetId-keyed lookup cannot find it.
_ONBOARDED_ASSET_ID = "xdd1d0e4c-6f4a-4a0e-9a54-2f0c1b7e5a11"


def _condition_terms(condition):
    """{attributeName: (operator, operands)} for a DynamoDB key condition.

    Reads the condition for what it SELECTS instead of comparing it to one expected
    object. The stub used to require exact object equality, so widening the
    implementation's query to a broader — and strictly safer — shape returned no
    rows from the stub and made three DENIAL tests fail: the handler still refused,
    but the stub had hidden the record it was refusing over. A suite that punishes a
    safer implementation pushes the next author back toward the narrow one.
    """
    terms = {}

    def walk(node):
        if not isinstance(node, ConditionBase):
            raise AssertionError(f"not a DynamoDB condition: {node!r}")
        expression = node.get_expression()
        operator = expression["operator"]
        values = expression["values"]
        if operator in ("AND", "OR"):
            for value in values:
                walk(value)
            return
        attribute = values[0]
        if not isinstance(attribute, (Key, Attr)):
            raise AssertionError(f"unsupported key-condition shape: {expression}")
        terms[attribute.name] = (operator, tuple(values[1:]))

    walk(condition)
    return terms


def _term_selects(term, value):
    """Whether one key-condition term selects a row holding this attribute value."""
    operator, operands = term
    if operator == "=":
        return value == operands[0]
    if operator == "begins_with":
        return str(value).startswith(str(operands[0]))
    if operator == "BETWEEN":
        return operands[0] <= value <= operands[1]
    # Silently selecting nothing would make a denial test pass over a query that
    # returned no rows, which is the failure mode this whole helper exists to avoid.
    raise AssertionError(f"the stub does not model key-condition operator {operator!r}")


def _stub_s3_versions(m, entries, truncated=False):
    """Stub s3_client.list_object_versions the way S3 answers a prefix query.

    entries: (key, kind) pairs where kind is "version" or "deleteMarker". A prefix
    query returns every entry whose Key starts with the prefix, which is the whole
    reason a parent prefix sees a child asset's archived objects; and S3 caps the
    COMBINED page at MaxKeys, so an existence-only check has to read both lists.
    """
    def _list(Bucket, Prefix, MaxKeys=None, **kwargs):
        matched = [(k, kind) for k, kind in entries if k.startswith(Prefix)]
        if MaxKeys is not None:
            matched = matched[:MaxKeys]
        response = {}
        versions = [{"Key": k, "VersionId": f"v{i}"}
                    for i, (k, kind) in enumerate(matched) if kind == "version"]
        markers = [{"Key": k, "VersionId": f"d{i}"}
                   for i, (k, kind) in enumerate(matched) if kind == "deleteMarker"]
        if versions:
            response["Versions"] = versions
        if markers:
            response["DeleteMarkers"] = markers
        if truncated:
            # S3 sets this when more entries match than the page returned. A page of
            # siblings with the conflicting key still outstanding looks exactly like this.
            response["IsTruncated"] = True
        return response

    m.s3_client = MagicMock()
    m.s3_client.list_object_versions.side_effect = _list


def _request_model(m, asset_id=_VICTIM_ASSET_ID, database_id=_OWN_DB, bucket_existing_key=None):
    return m.CreateAssetRequestModel(
        databaseId=database_id,
        assetId=asset_id,
        assetName="attacker asset",
        description="cross-database prefix takeover test",
        isDistributable=True,
        tags=[],
        bucketExistingKey=bucket_existing_key,
    )


def _asset_record(database_id, asset_id, key, bucket_id=_OWN_BUCKET_ID, archived=False):
    record = {
        "databaseId": database_id,
        "assetId": asset_id,
        "bucketId": bucket_id,
        "assetLocation": {"Key": key},
    }
    if archived:
        record["status"] = "archived"
    return record


def _wire(m, bucket_records=None, owned_records=(), prefix_exists=False, key_exists=True,
          s3_entries=None, base_prefix=_BASE_PREFIX):
    """Stub everything create_asset touches, leaving the ownership checks real.

    bucket_records: the bucket-registry rows the bucketNameGSI returns for the
        physical bucket. Defaults to the caller's own record only.
    owned_records: existing asset records the BucketIdGSI should return, selected by
        whatever the implementation's key condition actually constrains.
    prefix_exists: stubs check_s3_prefix_exists with a fixed verdict, for tests
        about what create_asset does with the verdict.
    s3_entries: when given, leaves check_s3_prefix_exists REAL and stubs the S3
        client with these (key, kind) entries instead — for tests about how the
        verdict is reached. Mutually exclusive with prefix_exists.
    """
    if bucket_records is None:
        bucket_records = [{
            "bucketId": _OWN_BUCKET_ID,
            "bucketName": _BUCKET_NAME,
            "baseAssetsPrefix": base_prefix,
        }]

    m.database_table = MagicMock()
    m.database_table.get_item.return_value = {"Item": {"databaseId": _OWN_DB}}

    m.buckets_table = MagicMock()
    m.buckets_table.query.return_value = {"Items": list(bucket_records)}

    m.asset_table = MagicMock()
    m.asset_table.get_item.return_value = {}  # caller's own partition: nothing there

    def _query(**kwargs):
        terms = _condition_terms(kwargs.get("KeyConditionExpression"))
        bucket_term = terms.get("bucketId")
        if bucket_term is None:
            raise AssertionError(
                "an ownership query must constrain bucketId; an unkeyed read of the "
                f"index would be a table scan: {terms}")
        asset_term = terms.get("assetId")
        matches = []
        for record in owned_records:
            if not _term_selects(bucket_term, record["bucketId"]):
                continue
            if asset_term is not None and not _term_selects(asset_term, record["assetId"]):
                continue
            matches.append(record)
        return {"Items": matches}

    m.asset_table.query.side_effect = _query

    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": _OWN_BUCKET_ID,
        "bucketName": _BUCKET_NAME,
        "baseAssetsPrefix": base_prefix,
    })
    if s3_entries is None:
        m.check_s3_prefix_exists = MagicMock(return_value=prefix_exists)
    else:
        _stub_s3_versions(m, s3_entries)
    m.check_s3_key_exists = MagicMock(return_value=key_exists)
    m.create_prefix_folder = MagicMock(return_value=True)
    m.create_initial_version_record = MagicMock(return_value="v0")
    m.create_sns_topic_for_asset = MagicMock(return_value="arn:sns")
    m.save_asset_details = MagicMock()
    m.update_asset_count = MagicMock()
    m.write_asset_history_record = MagicMock()
    m.validate_tags_exist = MagicMock(return_value=True)
    m.verify_all_required_tags_satisfied = MagicMock(return_value=True)
    return m


def _ownership_lookup_calls(m, bucket_ids=(_OWN_BUCKET_ID, _SIBLING_BUCKET_ID)):
    """Every BucketIdGSI query that constrained bucketId to one of these records.

    Deliberately indifferent to the rest of the condition: "the ownership check
    consulted the index for the right bucket records" is the property worth pinning
    in a denial test, and the refusal itself is asserted separately. The cost shape
    — that the derived branch narrows to a single assetId — is pinned in
    TestOwnershipCheckCostShape, where it is the actual subject.
    """
    calls = []
    for call in m.asset_table.query.call_args_list:
        if call.kwargs.get("IndexName") != "BucketIdGSI":
            continue
        term = _condition_terms(call.kwargs.get("KeyConditionExpression")).get("bucketId")
        if term and any(_term_selects(term, bucket_id) for bucket_id in bucket_ids):
            calls.append(call)
    return calls


def _assert_nothing_persisted(m):
    m.save_asset_details.assert_not_called()
    m.create_prefix_folder.assert_not_called()
    m.create_initial_version_record.assert_not_called()
    m.create_sns_topic_for_asset.assert_not_called()


@pytest.mark.unit
class TestArchivedPrefixTakeoverDenied:
    """The reported attack, and the cross-database variant that shares its root cause."""

    def test_archived_victim_in_other_database_is_rejected(self):
        m = _load(fresh=True)
        # The victim was archived: its record moved to the "#deleted" partition and
        # every object under its prefix carries a delete marker, so the S3 listing
        # reports the prefix as empty.
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _VICTIM_ASSET_ID, _VICTIM_KEY, archived=True)],
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)

        # The denial came from the asset records: the S3 layer was never consulted,
        # so this pins the record layer specifically. That layer is what covers a
        # record whose S3 data has been permanently expunged, where nothing remains
        # under the prefix for the S3 layer to see.
        m.check_s3_prefix_exists.assert_not_called()

        # The pre-persist existence check really is blind to the victim's partition,
        # which is why the ownership query is the only thing that can catch this.
        m.asset_table.get_item.assert_called_once_with(
            Key={"databaseId": _OWN_DB, "assetId": _VICTIM_ASSET_ID}
        )

        lookups = _ownership_lookup_calls(m)
        assert len(lookups) == 1
        assert lookups[0].kwargs["IndexName"] == "BucketIdGSI"

    def test_active_victim_in_other_database_is_rejected(self):
        m = _load(fresh=True)
        # Same hole, different trigger: an ACTIVE asset in another database whose
        # prefix happens not to list (a folder marker that failed to write, a
        # lifecycle-expired prefix). The listing is stubbed False so only the
        # ownership check can deny.
        _wire(
            m,
            owned_records=[_asset_record(_OTHER_DB, _VICTIM_ASSET_ID, _VICTIM_KEY)],
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        m.check_s3_prefix_exists.assert_not_called()
        assert len(_ownership_lookup_calls(m)) == 1

    def test_takeover_of_own_archived_asset_is_rejected(self):
        m = _load(fresh=True)
        # The caller's own database's "#deleted" partition is also a different
        # partition key from the pre-persist get_item, so re-claiming one's own
        # archived asset's prefix has to be denied by the same check.
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OWN_DB}#deleted", _VICTIM_ASSET_ID, _VICTIM_KEY, archived=True)],
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["owner"]})

        _assert_nothing_persisted(m)

    def test_error_message_does_not_disclose_the_other_database(self):
        m = _load(fresh=True)
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _VICTIM_ASSET_ID, _VICTIM_KEY, archived=True)],
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m), {"tokens": ["attacker"]})

        message = str(caught.value)
        assert _OTHER_DB not in message
        # Indistinguishable from the ordinary live-collision rejection, so the
        # response does not confirm that an archived asset exists.
        assert message.endswith(
            "Asset identifier is not unique for the given S3 bucket location")


@pytest.mark.unit
class TestRejectionMessageScope:
    """How specific the refusal may be depends on whose record it collided with.

    Rule 11 forbids naming another database, and the generic message honours that. But
    the caller's OWN archived asset is already visible to them through
    listAssets?includeArchived=true, so refusing that case with the same opaque text
    withholds nothing and names no remedy. These two must not drift apart: a fix that
    makes the own-database message specific by making the cross-database one specific
    too would reopen the disclosure.
    """

    def _collide(self, m, owner_database_id):
        _wire(
            m,
            owned_records=[_asset_record(owner_database_id, _VICTIM_ASSET_ID, _VICTIM_KEY)],
            prefix_exists=False,
        )
        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m), {"tokens": ["owner"]})
        return str(caught.value)

    def test_own_archived_asset_refusal_names_the_remedy(self):
        message = self._collide(_load(fresh=True), f"{_OWN_DB}#deleted")

        assert "archived asset in this database" in message
        # Both remedies, because which one the caller wants depends on whether they
        # still need the data.
        assert "Unarchive" in message
        assert "permanently delete" in message

    def test_own_live_asset_refusal_is_specific_but_claims_no_archive(self):
        message = self._collide(_load(fresh=True), _OWN_DB)

        assert "asset in this database" in message
        assert "archived" not in message.lower()

    def test_cross_database_refusal_stays_generic(self):
        message = self._collide(_load(fresh=True), f"{_OTHER_DB}#deleted")

        assert message.endswith(
            "Asset identifier is not unique for the given S3 bucket location")
        assert _OTHER_DB not in message
        # None of the own-database vocabulary may leak into the cross-database case.
        assert "Unarchive" not in message
        assert "this database" not in message

    def test_a_database_whose_name_prefixes_the_owner_is_not_treated_as_its_own(self):
        # "database-a" vs "database-a-extra": a substring comparison rather than an
        # equality one after stripping the archive suffix would call these the same
        # database and hand the caller a specific message about another tenant's asset.
        message = self._collide(_load(fresh=True), f"{_OWN_DB}-extra#deleted")

        assert message.endswith(
            "Asset identifier is not unique for the given S3 bucket location")


@pytest.mark.unit
class TestExistingProtectionsRetained:
    """A refactor must not lose the S3-listing guard while the new check takes credit."""

    def test_occupied_prefix_with_no_asset_record_is_still_rejected(self):
        m = _load(fresh=True)
        # No asset record owns the location, so the ownership check passes and the
        # rejection has to come from the listing — the pre-existing protection.
        _wire(m, owned_records=[], prefix_exists=True)

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        m.check_s3_prefix_exists.assert_called_once_with(_BUCKET_NAME, _VICTIM_KEY)
        _assert_nothing_persisted(m)

    def test_s3_external_generation_still_binds_onto_an_occupied_prefix(self):
        m = _load(fresh=True)
        # Bucket-sync ingestion: the prefix existing is the trigger for creation.
        _wire(m, owned_records=[], prefix_exists=True)

        response = m.create_asset(_request_model(m), {"tokens": ["SYSTEM_USER"]}, True)

        assert response.assetId == _VICTIM_ASSET_ID
        m.create_prefix_folder.assert_not_called()
        m.save_asset_details.assert_called_once()

    def test_s3_external_generation_cannot_bind_onto_an_archived_asset(self):
        m = _load(fresh=True)
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _VICTIM_ASSET_ID, _VICTIM_KEY, archived=True)],
            prefix_exists=True,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["SYSTEM_USER"]}, True)

        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestOrdinaryCreatesStillSucceed:
    """Positive controls. Without these, a fix that rejects everything would pass."""

    def test_unused_asset_id_creates_and_writes_the_folder_marker(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        response = m.create_asset(
            _request_model(m, asset_id="brand-new-asset"), {"tokens": ["user1"]})

        assert response.assetId == "brand-new-asset"
        m.create_prefix_folder.assert_called_once_with(
            _BUCKET_NAME, _BASE_PREFIX + "brand-new-asset/")
        m.save_asset_details.assert_called_once()
        saved = m.save_asset_details.call_args.args[0]
        assert saved["bucketId"] == _OWN_BUCKET_ID
        assert saved["assetLocation"]["Key"] == _BASE_PREFIX + "brand-new-asset/"

    def test_generated_asset_id_creates_successfully(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        response = m.create_asset(
            m.CreateAssetRequestModel(
                databaseId=_OWN_DB,
                assetName="generated id asset",
                description="no assetId supplied",
                isDistributable=True,
                tags=[],
            ),
            {"tokens": ["user1"]},
        )

        assert response.assetId.startswith("x")
        m.create_prefix_folder.assert_called_once()

    def test_same_asset_id_in_a_bucket_at_a_different_prefix_is_permitted(self):
        m = _load(fresh=True)
        # A sibling bucket record on the same physical bucket but a different base
        # prefix is NOT colocated, so an asset holding the same assetId there does
        # not block this create.
        _wire(
            m,
            bucket_records=[
                {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX},
                {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": "other-root/"},
            ],
            owned_records=[_asset_record(
                _OTHER_DB, _VICTIM_ASSET_ID, "other-root/" + _VICTIM_ASSET_ID + "/",
                bucket_id=_SIBLING_BUCKET_ID)],
            prefix_exists=False,
        )

        response = m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert response.assetId == _VICTIM_ASSET_ID
        m.create_prefix_folder.assert_called_once_with(_BUCKET_NAME, _VICTIM_KEY)

    def test_same_asset_id_at_a_non_overlapping_location_is_permitted(self):
        m = _load(fresh=True)
        # assetIds are unique per database, not globally. An existing asset that
        # shares the assetId but was bound (via bucketExistingKey) to an unrelated
        # location owns no part of the derived prefix, so it must not block.
        _wire(
            m,
            owned_records=[_asset_record(
                _OTHER_DB, _VICTIM_ASSET_ID, _BASE_PREFIX + "somewhere-else/")],
            prefix_exists=False,
        )

        response = m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert response.assetId == _VICTIM_ASSET_ID
        m.create_prefix_folder.assert_called_once_with(_BUCKET_NAME, _VICTIM_KEY)


@pytest.mark.unit
class TestPhysicalPrefixKeying:
    """Two bucket records can point at one physical bucket + prefix."""

    def test_asset_under_a_colocated_sibling_bucket_record_is_found(self):
        m = _load(fresh=True)
        _wire(
            m,
            bucket_records=[
                {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX},
                # Same physical location, spelled differently in the registry.
                {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": "/assets"},
            ],
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _VICTIM_ASSET_ID, _VICTIM_KEY,
                bucket_id=_SIBLING_BUCKET_ID, archived=True)],
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        # One ownership lookup per colocated bucket record.
        assert len(_ownership_lookup_calls(m)) == 2

    def test_registry_read_is_a_keyed_query_not_a_scan(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        m.create_asset(_request_model(m, asset_id="brand-new-asset"), {"tokens": ["user1"]})

        m.buckets_table.scan.assert_not_called()
        kwargs = m.buckets_table.query.call_args.kwargs
        assert kwargs["IndexName"] == "bucketNameGSI"
        assert kwargs["KeyConditionExpression"] == Key("bucketName").eq(_BUCKET_NAME)

    def test_resolve_colocated_bucket_ids_spans_overlapping_prefix_roots(self):
        m = _load(fresh=True)
        m.buckets_table = MagicMock()
        m.buckets_table.query.return_value = {"Items": [
            {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": _BASE_PREFIX},
            # Same physical prefix, three spellings.
            {"bucketId": "sibling-slashless", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": "assets"},
            {"bucketId": "sibling-leading-slash", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": "/assets/"},
            # NESTED inside this record's prefix. Equality misses it, and that miss is
            # a bypass: a DB-A create of assetId "team1" resolves to assets/team1/,
            # the parent of every asset held under this record.
            {"bucketId": "nested-child", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": "assets/team1/"},
            # Nesting the other way: the bucket root, which contains this prefix.
            {"bucketId": "nested-parent", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": ""},
            # A sibling segment that merely shares a character run with the target.
            # "assetsX/" is not under "assets/", and the trailing-slash normalization
            # is what keeps the comparison on segment boundaries; without it a raw
            # string-prefix test would treat these unrelated roots as overlapping.
            {"bucketId": "lookalike-root", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": "assetsX/"},
            # Different prefix root in the same bucket.
            {"bucketId": "unrelated-root", "bucketName": _BUCKET_NAME,
             "baseAssetsPrefix": "archive/"},
        ]}

        result = m.resolve_colocated_bucket_ids(_OWN_BUCKET_ID, _BUCKET_NAME, _BASE_PREFIX)

        assert result[0] == _OWN_BUCKET_ID
        # "nested-parent" carries no prefix, which is the bucket root and therefore
        # contains every prefix in the bucket. Including it only widens the record set
        # the ownership checks read; keys_conflict still decides the verdict on full
        # keys, so an unrelated asset under it cannot cause a false rejection.
        assert set(result) == {
            _OWN_BUCKET_ID, "sibling-slashless", "sibling-leading-slash",
            "nested-child", "nested-parent",
        }
        assert "unrelated-root" not in result
        assert "lookalike-root" not in result

    def test_resolve_colocated_bucket_ids_pages_to_exhaustion(self):
        m = _load(fresh=True)
        m.buckets_table = MagicMock()
        m.buckets_table.query.side_effect = [
            {"Items": [{"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                        "baseAssetsPrefix": _BASE_PREFIX}],
             "LastEvaluatedKey": {"bucketId": _OWN_BUCKET_ID}},
            {"Items": [{"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                        "baseAssetsPrefix": _BASE_PREFIX}]},
        ]

        result = m.resolve_colocated_bucket_ids(_OWN_BUCKET_ID, _BUCKET_NAME, _BASE_PREFIX)

        assert set(result) == {_OWN_BUCKET_ID, _SIBLING_BUCKET_ID}
        assert m.buckets_table.query.call_count == 2
        assert m.buckets_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
            "bucketId": _OWN_BUCKET_ID
        }

    def test_registry_read_failure_fails_closed(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.buckets_table.query.side_effect = RuntimeError("registry unavailable")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestLayerAttribution:
    """Which layer covers which case, asserted once and only here.

    This is the counterpart to the cost-shape tests: the exact-assetId lookup is what
    makes a create O(1), and the price of that narrowness is that the record layer
    cannot see a victim whose key does not derive from its assetId. Both facts belong
    together, and both are properties of the chosen SHAPE rather than of the outcome —
    so a broader, safer record query is entitled to fail these, and is not entitled to
    fail a denial test.
    """

    _VICTIM_KEY_NON_DERIVED = _BASE_PREFIX + "legacy/model1/"

    def _record_layer_verdict(self, m):
        """(raised, exception) from running the record layer alone over a non-derived victim."""
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _ONBOARDED_ASSET_ID, self._VICTIM_KEY_NON_DERIVED,
                archived=True)],
            prefix_exists=False,
        )
        try:
            m.assert_derived_asset_key_not_owned(
                [_OWN_BUCKET_ID], "legacy", _BASE_PREFIX + "legacy/", _OWN_DB)
        except m.VAMSGeneralErrorResponse as exc:
            return True, exc
        return False, None

    def test_the_record_layer_cannot_see_a_non_derived_victim(self):
        m = _load(fresh=True)
        raised, _ = self._record_layer_verdict(m)

        assert not raised, (
            "the record layer caught a victim whose assetId differs from its key, which "
            "means the lookup is no longer the exact one the cost-shape tests require")

    def test_the_s3_layer_does_see_that_victim(self):
        m = _load(fresh=True)
        # Same victim, same target prefix, S3 answering honestly: occupied. This pairs
        # with the test above — together they say the S3 layer is not decoration.
        _stub_s3_versions(m, [(self._VICTIM_KEY_NON_DERIVED + "model.glb", "deleteMarker")])

        assert m.check_s3_prefix_exists(_BUCKET_NAME, _BASE_PREFIX + "legacy/") is True


@pytest.mark.unit
class TestOwnershipCheckCostShape:
    """A functional test cannot tell an O(1) lookup from an O(assets-in-bucket) walk."""

    def test_derived_key_check_uses_an_exact_key_condition(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)

        m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert m.asset_table.query.call_count == 1
        call = m.asset_table.query.call_args
        terms = _condition_terms(call.kwargs["KeyConditionExpression"])
        # Both key attributes constrained by equality is what makes this O(1); the
        # partition-only form is what makes a create O(assets in the bucket).
        assert terms.get("bucketId") == ("=", (_OWN_BUCKET_ID,))
        assert terms.get("assetId") == ("=", (_VICTIM_ASSET_ID,))
        assert "ExclusiveStartKey" not in call.kwargs

    def test_derived_key_check_does_not_follow_a_pagination_token(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        # An exact key-condition lookup returns at most one row, so a paged
        # response can only mean the query degenerated into a partition walk.
        m.asset_table.query.side_effect = None
        m.asset_table.query.return_value = {
            "Items": [], "LastEvaluatedKey": {"bucketId": _OWN_BUCKET_ID, "assetId": "z"}
        }

        m.create_asset(_request_model(m), {"tokens": ["user1"]})

        assert m.asset_table.query.call_count == 1

    def test_bucket_existing_key_check_still_walks_the_partition(self):
        m = _load(fresh=True)
        m.asset_table = MagicMock()
        m.asset_table.query.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"bucketId": _OWN_BUCKET_ID, "assetId": "m"}},
            {"Items": []},
        ]

        m.assert_existing_key_not_owned([_OWN_BUCKET_ID], _BASE_PREFIX + "supplied/key.txt")

        # An arbitrary caller-supplied key needs parent/child comparison against
        # every record, so this one pages to exhaustion by design.
        assert m.asset_table.query.call_count == 2
        first = m.asset_table.query.call_args_list[0].kwargs
        assert first["KeyConditionExpression"] == Key("bucketId").eq(_OWN_BUCKET_ID)
        assert m.asset_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
            "bucketId": _OWN_BUCKET_ID, "assetId": "m"
        }


@pytest.mark.unit
class TestBucketExistingKeyBranchUnchanged:
    """The parent/child scan on the caller-supplied-key branch keeps its behavior."""

    @pytest.mark.parametrize("existing_key,label", [
        (_BASE_PREFIX + "supplied/", "equal"),
        (_BASE_PREFIX, "existing is a parent"),
        (_BASE_PREFIX + "supplied/deeper/", "existing is a child"),
    ])
    def test_collisions_are_rejected(self, existing_key, label):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(_OTHER_DB, "other-asset", existing_key)])

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(
                _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["attacker"]})

        assert "bucketExistingKey is already in use" in str(caught.value)
        _assert_nothing_persisted(m)

    def test_archived_owner_of_a_supplied_key_is_rejected(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(
            f"{_OTHER_DB}#deleted", "other-asset", _BASE_PREFIX + "supplied/", archived=True)])

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(
                _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)

    def test_non_overlapping_key_is_permitted(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(
            _OTHER_DB, "other-asset", _BASE_PREFIX + "unrelated/")])

        response = m.create_asset(
            _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["user1"]})

        assert response.assetId == _VICTIM_ASSET_ID
        saved = m.save_asset_details.call_args.args[0]
        assert saved["assetLocation"]["Key"] == _BASE_PREFIX + "supplied/"
        # This branch binds onto data that already exists; no marker is written.
        m.create_prefix_folder.assert_not_called()

    def test_supplied_key_always_resolves_under_the_database_base_prefix(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[])

        m.create_asset(
            _request_model(m, bucket_existing_key="other-root/file.txt"),
            {"tokens": ["user1"]},
        )

        saved = m.save_asset_details.call_args.args[0]
        assert saved["assetLocation"]["Key"] == _BASE_PREFIX + "other-root/file.txt"
        # And the ownership check saw the resolved key, not the raw input.
        assert m.check_s3_key_exists.call_args.args == (
            _BUCKET_NAME, _BASE_PREFIX + "other-root/file.txt")

    def test_missing_key_in_s3_is_rejected(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], key_exists=False)

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(
                _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["attacker"]})

        assert "does not exist" in str(caught.value)
        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestFolderMarkerFailureIsFatal:
    """A create that silently wrote no folder marker leaves an empty-listing prefix."""

    def test_create_prefix_folder_raises_on_put_failure(self):
        m = _load(fresh=True)
        m.s3_client = MagicMock()
        m.s3_client.put_object.side_effect = RuntimeError("AccessDenied")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_prefix_folder(_BUCKET_NAME, _VICTIM_KEY)

    def test_create_is_abandoned_when_the_folder_marker_fails(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.create_prefix_folder = MagicMock(
            side_effect=m.VAMSGeneralErrorResponse("Error creating the asset S3 location"))

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        # Nothing persisted, so no active asset is left holding an empty prefix.
        m.save_asset_details.assert_not_called()
        m.create_initial_version_record.assert_not_called()


@pytest.mark.unit
class TestArchiveAwarePrefixCheck:
    """check_s3_prefix_exists must see archived data, which is the root of the finding.

    Archiving issues delete_object with no VersionId over every object under the
    asset's prefix, including the folder marker. list_objects_v2 returns only current
    objects, so it reports such a prefix as EMPTY -- which is what made the whole
    takeover possible. list_object_versions returns the retained versions and the
    delete markers, and a check that reads only one of those two lists is still blind
    to the case that matters most.
    """

    def _verdict(self, m, entries, truncated=False):
        _stub_s3_versions(m, entries, truncated=truncated)
        return m.check_s3_prefix_exists(_BUCKET_NAME, _VICTIM_KEY)

    def test_delete_markers_alone_mean_the_prefix_is_occupied(self):
        m = _load(fresh=True)
        # A fully archived asset: every object delete-markered, nothing current. This
        # is the exact state list_objects_v2 reported as empty.
        assert self._verdict(m, [(_VICTIM_KEY, "deleteMarker"),
                                 (_VICTIM_KEY + "model.glb", "deleteMarker")]) is True

    def test_retained_versions_alone_mean_the_prefix_is_occupied(self):
        m = _load(fresh=True)
        assert self._verdict(m, [(_VICTIM_KEY + "model.glb", "version")]) is True

    def test_both_lists_populated_means_occupied(self):
        m = _load(fresh=True)
        assert self._verdict(m, [(_VICTIM_KEY + "a.glb", "version"),
                                 (_VICTIM_KEY + "b.glb", "deleteMarker")]) is True

    def test_a_genuinely_empty_prefix_is_free(self):
        m = _load(fresh=True)
        assert self._verdict(m, [("somewhere-else/a.glb", "version")]) is False

    def test_the_archive_blind_listing_is_not_used(self):
        m = _load(fresh=True)
        _stub_s3_versions(m, [(_VICTIM_KEY, "deleteMarker")])

        m.check_s3_prefix_exists(_BUCKET_NAME, _VICTIM_KEY)

        # list_objects_v2 cannot answer this question, so reaching for it at all is
        # the defect. The stub would happily return a truthy MagicMock for it.
        m.s3_client.list_objects_v2.assert_not_called()
        kwargs = m.s3_client.list_object_versions.call_args.kwargs
        assert kwargs["Bucket"] == _BUCKET_NAME
        # Assert the probe COVERS the location, not that it spells it one particular way.
        # A correct implementation may query a broader prefix and filter the results —
        # it has to, because a key equal to the location without its trailing slash is a
        # conflict that the slash-bearing prefix does not match. Pinning the exact string
        # here would fail a strictly safer implementation, which is how a test ends up
        # pushing the next author toward the narrower one.
        assert _VICTIM_KEY.startswith(kwargs["Prefix"])
        # Bounded: this runs on every asset create, so it must not page the bucket.
        assert kwargs["MaxKeys"] <= 100

    def test_a_key_equal_to_the_location_without_its_slash_is_occupied(self):
        """The round-2 reviewer's bypass, executed end to end against the real handler.

        `bucketExistingKey` accepts `legacy`, which `normalize_s3_path` resolves to
        `<base>legacy` with NO trailing slash, so a DB-B asset can own that key while its
        assetId is a uuid. A DB-A create of assetId `legacy` derives `<base>legacy/`, and
        `<base>legacy` sorts one byte OUTSIDE that prefix — so a probe of the slash-bearing
        form alone reports the location free. Both ownership layers then miss it: the exact
        lookup keys on assetId, and the occupancy probe never saw the object.

        It is a real conflict, not a technicality: `keys_conflict()` returns True for the
        pair, and `resolve_asset_file_path` appends the slash when reading files, so the
        victim's data lives inside the prefix the attacker would own.
        """
        m = _load(fresh=True)
        bare = _VICTIM_KEY.rstrip("/")
        assert self._verdict(m, [(bare, "version")]) is True

    def test_a_sibling_sharing_the_bare_prefix_is_not_occupancy(self):
        """Control for the widened probe: it must not reject an unrelated neighbour.

        Dropping the trailing slash to catch the case above also matches
        `<base>legacy-other/`, which is a different asset entirely. Without this, the fix
        for the bypass above would refuse legitimate creates whose id merely shares a
        leading substring with an existing one — a far more common shape than the attack.
        """
        m = _load(fresh=True)
        sibling = _VICTIM_KEY.rstrip("/") + "-other/model.glb"
        assert self._verdict(m, [(sibling, "version")]) is False

    def test_a_truncated_page_of_siblings_only_is_treated_as_occupied(self):
        """Fail-closed on an inconclusive page.

        The widened probe can fill a page with siblings while the conflicting key is still
        outstanding. This check is the ONLY occupancy guard on the derived branch, so an
        inconclusive answer must read as occupied: refusing a create that could have been
        allowed is recoverable, admitting a cross-database takeover is not.
        """
        m = _load(fresh=True)
        sibling = _VICTIM_KEY.rstrip("/") + "-other/model.glb"
        assert self._verdict(m, [(sibling, "version")], truncated=True) is True

    def test_an_s3_failure_fails_closed(self):
        m = _load(fresh=True)
        m.s3_client = MagicMock()
        m.s3_client.list_object_versions.side_effect = RuntimeError("AccessDenied")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.check_s3_prefix_exists(_BUCKET_NAME, _VICTIM_KEY)

    def test_a_create_is_abandoned_when_the_s3_check_throws(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], s3_entries=[])
        m.s3_client.list_object_versions.side_effect = RuntimeError("throttled")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        # A revoked s3:ListBucketVersions grant or a throttle must not silently
        # reinstate the takeover by letting the create through.
        _assert_nothing_persisted(m)


@pytest.mark.unit
class TestNonDerivedVictimKeyIsRejected:
    """The blind spot an assetId-keyed index cannot express.

    bucketExistingKey onboarding is the DOCUMENTED external-S3 flow and it permanently
    decouples the two identifiers: the asset gets assetId "x<uuid>" and
    assetLocation.Key "assets/projects/building-a/". A victim onboarded that way, then
    archived, is invisible to an exact lookup keyed on the ATTACKER's chosen assetId --
    keys_conflict() would say the pair conflicts, but the lookup never retrieves the
    record to compare. S3 is what closes this, because the victim's retained versions
    and delete markers sit under the derived prefix whatever the owning record says.

    The suite already had the non-overlapping twin
    (test_same_asset_id_at_a_non_overlapping_location_is_permitted, correctly a
    PERMIT). Its missing overlapping counterpart is why the bypass shipped.
    """

    @pytest.mark.parametrize("victim_key,relationship", [
        # The derived key assets/legacy/ and the victim's key are the same place.
        (_BASE_PREFIX + "legacy/", "equal"),
        # The victim sits INSIDE the derived key: a folder-style onboarding, then a
        # deeper file. The derived key is the parent of both.
        (_BASE_PREFIX + "legacy/model1/", "victim is a child"),
        (_BASE_PREFIX + "legacy/model1/deeper/", "victim is a deeper child"),
    ])
    def test_archived_non_derived_victim_blocks_the_derived_key(self, victim_key, relationship):
        m = _load(fresh=True)
        # The victim record is present and reachable through the index, but under an
        # assetId that shares nothing with its key -- so the record layer cannot find
        # it, and the create must be refused by the S3 layer instead.
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _ONBOARDED_ASSET_ID, victim_key, archived=True)],
            s3_entries=[(victim_key, "deleteMarker"),
                        (victim_key + "model.glb", "deleteMarker")],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m, asset_id="legacy"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        # Rule 11: still generic, because the owner is another database.
        assert str(caught.value).endswith(
            "Asset identifier is not unique for the given S3 bucket location")

        # The record layer was consulted for the right bucket record, so the refusal is
        # not standing in for a check that never ran. WHICH layer refused is
        # deliberately NOT asserted here: pinning that would make a broader, safer
        # record query fail a denial test. Attribution lives in TestLayerAttribution.
        assert _ownership_lookup_calls(m), "the record layer was not consulted at all"

    def test_a_live_non_derived_victim_is_also_blocked(self):
        m = _load(fresh=True)
        # Same topology without the archive step, so the retained data is current
        # objects rather than delete markers.
        victim_key = _BASE_PREFIX + "legacy/model1/"
        _wire(
            m,
            owned_records=[_asset_record(_OTHER_DB, _ONBOARDED_ASSET_ID, victim_key)],
            s3_entries=[(victim_key + "model.glb", "version")],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m, asset_id="legacy"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)

    def test_an_unrelated_sibling_prefix_still_creates(self):
        m = _load(fresh=True)
        # Positive control for the denials above: the victim's archived data at
        # assets/legacy/ must not block an unrelated assetId. Without this, a check
        # that answered "occupied" for every prefix would pass the denials perfectly.
        _wire(
            m,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _ONBOARDED_ASSET_ID, _BASE_PREFIX + "legacy/",
                archived=True)],
            s3_entries=[(_BASE_PREFIX + "legacy/model.glb", "deleteMarker")],
        )

        response = m.create_asset(
            _request_model(m, asset_id="unrelated"), {"tokens": ["user1"]})

        assert response.assetId == "unrelated"
        m.create_prefix_folder.assert_called_once_with(
            _BUCKET_NAME, _BASE_PREFIX + "unrelated/")

    def test_a_lookalike_sibling_prefix_still_creates(self):
        m = _load(fresh=True)
        # assets/legacyX/ shares a character run with assets/legacy/ but is a
        # different folder. S3's Prefix match is a raw string comparison, so the
        # derived key's trailing slash is what keeps them apart -- and a create of
        # assetId "legacy" must not be blocked by data under "legacyX".
        _wire(
            m,
            owned_records=[],
            s3_entries=[(_BASE_PREFIX + "legacyX/model.glb", "deleteMarker")],
        )

        response = m.create_asset(
            _request_model(m, asset_id="legacy"), {"tokens": ["user1"]})

        assert response.assetId == "legacy"
        m.create_prefix_folder.assert_called_once_with(
            _BUCKET_NAME, _BASE_PREFIX + "legacy/")

    @pytest.mark.xfail(
        strict=True,
        reason="S25-SEC-002 (residual, PRE-EXISTING and unchanged by this round): when the "
               "victim's key is a proper ANCESTOR of the derived key -- reachable only by "
               "onboarding an asset at the bucket record's baseAssetsPrefix root itself -- "
               "neither layer sees it. The record layer misses it because the assetIds "
               "differ; the S3 layer misses it because the ancestor's objects do not extend "
               "under the derived child prefix. Closing it needs either a key-indexed lookup "
               "or a rule forbidding an asset from owning the prefix root, both out of scope "
               "here. The exposure also runs the OTHER way -- the ancestor's owner gains "
               "visibility of the new asset's files through its own listFiles -- so it is "
               "not the takeover S25-SEC-001 describes.")
    def test_a_victim_owning_the_prefix_root_blocks_a_derived_child(self):
        m = _load(fresh=True)
        _wire(
            m,
            owned_records=[_asset_record(_OTHER_DB, _ONBOARDED_ASSET_ID, _BASE_PREFIX)],
            # The ancestor's own objects sit at assets/ and assets/model.glb, so
            # nothing at all lists under assets/legacy/.
            s3_entries=[(_BASE_PREFIX, "version"), (_BASE_PREFIX + "model.glb", "version")],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m, asset_id="legacy"), {"tokens": ["attacker"]})


@pytest.mark.unit
class TestNestedRegistryPrefixes:
    """Two bucket records whose prefix roots NEST, which equality-based colocation missed.

    Rows {bucketName: B, baseAssetsPrefix: "assets/"} and
    {bucketName: B, baseAssetsPrefix: "assets/team1/"} describe overlapping regions of
    one bucket, so an asset held under the second shares S3 keys with the first. While
    colocation compared prefixes for EQUALITY the two were never cross-checked, and a
    DB-A create of assetId "team1" took the parent of every asset under the other row.
    """

    _NESTED_RECORDS = [
        {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
         "baseAssetsPrefix": _BASE_PREFIX},
        {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
         "baseAssetsPrefix": _BASE_PREFIX + "team1/"},
    ]

    def test_a_nested_registry_row_is_colocated(self):
        m = _load(fresh=True)
        _wire(m, bucket_records=self._NESTED_RECORDS, owned_records=[], s3_entries=[])

        m.create_asset(_request_model(m, asset_id="team1"), {"tokens": ["user1"]})

        # Both records consulted, so the nested row is inside the ownership scope.
        assert len(_ownership_lookup_calls(m)) == 2

    def test_a_derived_key_taking_the_nested_root_is_rejected(self):
        m = _load(fresh=True)
        # The victim lives under the nested row at assets/team1/<uuid>/, so the
        # attacker's derived key assets/team1/ is its parent.
        victim_key = _BASE_PREFIX + "team1/" + _ONBOARDED_ASSET_ID + "/"
        _wire(
            m,
            bucket_records=self._NESTED_RECORDS,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _ONBOARDED_ASSET_ID, victim_key,
                bucket_id=_SIBLING_BUCKET_ID, archived=True)],
            s3_entries=[(victim_key + "model.glb", "deleteMarker")],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m, asset_id="team1"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)

    def test_a_bucket_existing_key_under_the_nested_root_is_rejected(self):
        m = _load(fresh=True)
        # Mutation M9: reverting createAsset's colocation argument to [s3_bucket_id]
        # left 33 tests passing. This is the branch where the caller controls the key
        # most directly, and it has NO S3 layer -- bucketExistingKey requires the key
        # to exist already -- so the colocated record set is the whole defence.
        victim_key = _BASE_PREFIX + "team1/" + _ONBOARDED_ASSET_ID + "/"
        _wire(
            m,
            bucket_records=self._NESTED_RECORDS,
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _ONBOARDED_ASSET_ID, victim_key,
                bucket_id=_SIBLING_BUCKET_ID, archived=True)],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(
                _request_model(m, bucket_existing_key="team1/" + _ONBOARDED_ASSET_ID + "/"),
                {"tokens": ["attacker"]})

        assert "bucketExistingKey is already in use" in str(caught.value)
        _assert_nothing_persisted(m)
        # The sibling record's partition really was read; a check keyed on the
        # caller's own bucketId alone would never have looked there.
        assert _ownership_lookup_calls(m, bucket_ids=(_SIBLING_BUCKET_ID,))

    def test_a_bucket_existing_key_under_an_equal_sibling_root_is_rejected(self):
        m = _load(fresh=True)
        # The same M9 gap with the sibling record at the SAME prefix rather than a
        # nested one, so the branch stays pinned even if nesting support regresses.
        _wire(
            m,
            bucket_records=[
                {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX},
                {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": "/assets"},
            ],
            owned_records=[_asset_record(
                _OTHER_DB, _ONBOARDED_ASSET_ID, _BASE_PREFIX + "supplied/",
                bucket_id=_SIBLING_BUCKET_ID)],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(
                _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        assert _ownership_lookup_calls(m, bucket_ids=(_SIBLING_BUCKET_ID,))

    def test_a_bucket_existing_key_colliding_with_an_asset_under_an_ANCESTOR_root(self):
        m = _load(fresh=True)
        # Nesting the other way round, and it needs the OTHER containment direction.
        # The caller's own record is the nested one (assets/team1/); the victim is held
        # under the wider assets/ record but was onboarded, via bucketExistingKey, to a
        # key inside assets/team1/. Only "target root is contained by candidate root"
        # brings that record into scope.
        victim_key = _BASE_PREFIX + "team1/shared/"
        _wire(
            m,
            base_prefix=_BASE_PREFIX + "team1/",
            bucket_records=[
                {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX + "team1/"},
                {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX},
            ],
            owned_records=[_asset_record(
                _OTHER_DB, _ONBOARDED_ASSET_ID, victim_key, bucket_id=_SIBLING_BUCKET_ID)],
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(
                _request_model(m, bucket_existing_key="shared/"), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        assert _ownership_lookup_calls(m, bucket_ids=(_SIBLING_BUCKET_ID,))

    def test_a_derived_key_colliding_with_an_asset_under_an_ANCESTOR_root(self):
        m = _load(fresh=True)
        # The same direction on the derived branch, where it is the record layer that
        # has to reach the wider record: same assetId, key held under the assets/ row.
        victim_key = _BASE_PREFIX + "team1/" + _VICTIM_ASSET_ID + "/"
        _wire(
            m,
            base_prefix=_BASE_PREFIX + "team1/",
            bucket_records=[
                {"bucketId": _OWN_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX + "team1/"},
                {"bucketId": _SIBLING_BUCKET_ID, "bucketName": _BUCKET_NAME,
                 "baseAssetsPrefix": _BASE_PREFIX},
            ],
            owned_records=[_asset_record(
                f"{_OTHER_DB}#deleted", _VICTIM_ASSET_ID, victim_key,
                bucket_id=_SIBLING_BUCKET_ID, archived=True)],
            # S3 answers "empty", so only the record layer can refuse -- which is what
            # makes this a test of the colocated record set rather than of S3.
            prefix_exists=False,
        )

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["attacker"]})

        _assert_nothing_persisted(m)
        m.check_s3_prefix_exists.assert_not_called()

    def test_an_unrelated_key_under_a_nested_root_still_creates(self):
        m = _load(fresh=True)
        # Positive control: widening the colocated set must not reject a create whose
        # key merely happens to sit in an overlapping record's partition.
        _wire(
            m,
            bucket_records=self._NESTED_RECORDS,
            owned_records=[_asset_record(
                _OTHER_DB, _ONBOARDED_ASSET_ID, _BASE_PREFIX + "team1/other/",
                bucket_id=_SIBLING_BUCKET_ID)],
        )

        response = m.create_asset(
            _request_model(m, bucket_existing_key="team2/mine/"), {"tokens": ["user1"]})

        assert response.assetId == _VICTIM_ASSET_ID
        saved = m.save_asset_details.call_args.args[0]
        assert saved["assetLocation"]["Key"] == _BASE_PREFIX + "team2/mine/"


@pytest.mark.unit
class TestOwnershipQueryFailsClosed:
    """Mutation M7: turning the ownership query's `except Exception` from log-and-raise
    into log-and-continue gave "28 passed, 0 failed". In production that means a
    DynamoDB throttle, a revoked GSI grant or a projection change silently reinstates
    the whole takeover -- the loudest possible failure becomes the quietest.
    """

    def test_derived_key_check_raises_when_the_query_throws(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.asset_table.query.side_effect = RuntimeError("ProvisionedThroughputExceeded")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.assert_derived_asset_key_not_owned(
                [_OWN_BUCKET_ID], _VICTIM_ASSET_ID, _VICTIM_KEY, _OWN_DB)

    def test_a_create_is_abandoned_when_the_derived_key_query_throws(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.asset_table.query.side_effect = RuntimeError("AccessDeniedException")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)
        # And the S3 layer did not quietly wave it through on the record layer's
        # behalf: the create stopped at the failure.
        m.check_s3_prefix_exists.assert_not_called()

    def test_a_create_is_abandoned_when_the_supplied_key_query_throws(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[])
        m.asset_table.query.side_effect = RuntimeError("AccessDeniedException")

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.create_asset(
                _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["user1"]})

        _assert_nothing_persisted(m)

    def test_a_query_failure_is_not_reported_as_a_uniqueness_conflict(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[], prefix_exists=False)
        m.asset_table.query.side_effect = RuntimeError("ProvisionedThroughputExceeded")

        with pytest.raises(m.VAMSGeneralErrorResponse) as caught:
            m.create_asset(_request_model(m), {"tokens": ["user1"]})

        # An infrastructure failure reported as "identifier is not unique" would send
        # the caller off renaming their asset forever.
        assert "Error validating S3 location" in str(caught.value)


@pytest.mark.unit
class TestMalformedOwnershipRecords:
    """A record the key comparison cannot evaluate is skipped, and that skip is logged.

    keys_conflict('', target) is False, so a record with an absent or empty
    assetLocation.Key is silently treated as owning nothing -- and a record with no
    bucketId is not on BucketIdGSI at all, so it cannot even be read here. No reachable
    create path produces either, so this is a fail-open DEFAULT rather than a live
    hole. The choice made is to LOG and continue rather than reject: rejecting would
    turn one malformed row into a create-path outage for every database sharing the
    bucket, while the log line makes the skip visible instead of invisible.
    """

    @pytest.mark.parametrize("record_patch", [
        {"assetLocation": {"Key": ""}},
        {"assetLocation": {}},
        {"assetLocation": {"Key": None}},
    ])
    def test_a_record_with_no_usable_key_is_logged_and_skipped(self, record_patch):
        m = _load(fresh=True)
        record = _asset_record(_OTHER_DB, _VICTIM_ASSET_ID, _VICTIM_KEY)
        record.update(record_patch)
        _wire(m, owned_records=[record], prefix_exists=False)
        m.logger = MagicMock()

        response = m.create_asset(_request_model(m), {"tokens": ["user1"]})

        # Skipped, so the create proceeds -- and the skip is on the record.
        assert response.assetId == _VICTIM_ASSET_ID
        warnings = " ".join(str(c) for c in m.logger.warning.call_args_list)
        assert "no usable assetLocation.Key" in warnings
        assert _VICTIM_ASSET_ID in warnings

    def test_the_supplied_key_branch_logs_the_same_skip(self):
        m = _load(fresh=True)
        _wire(m, owned_records=[_asset_record(_OTHER_DB, "other-asset", "")])
        m.logger = MagicMock()

        m.create_asset(
            _request_model(m, bucket_existing_key="supplied/"), {"tokens": ["user1"]})

        warnings = " ".join(str(c) for c in m.logger.warning.call_args_list)
        assert "no usable assetLocation.Key" in warnings

    def test_a_well_formed_record_logs_no_skip(self):
        m = _load(fresh=True)
        # Positive control: the warning must mark a real anomaly, not fire on every
        # non-conflicting record it walks past.
        _wire(
            m,
            owned_records=[_asset_record(
                _OTHER_DB, _VICTIM_ASSET_ID, _BASE_PREFIX + "somewhere-else/")],
            prefix_exists=False,
        )
        m.logger = MagicMock()

        m.create_asset(_request_model(m), {"tokens": ["user1"]})

        warnings = " ".join(str(c) for c in m.logger.warning.call_args_list)
        assert "no usable assetLocation.Key" not in warnings
