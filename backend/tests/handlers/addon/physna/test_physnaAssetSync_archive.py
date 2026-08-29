# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-013 / FIX-045: archive must not destroy Physna state, and the re-sync
must not cost a round trip per file.

FIX-013 -- VAMS archive and unarchive are both implemented as a rewrite of the
asset row across the ``databaseId#deleted`` partition-key suffix
(``assetService.archive_asset`` / ``unarchive_asset``), so each operation emits
two ``assetStorageTable`` stream records for the same asset: a write under the
destination key and a REMOVE under the source key. Every record that looked like
a delete deleted every Physna asset under the asset's folder, and the
``is_delete=False`` branch only iterated the *Physna* listing -- so with Physna
already empty it was a no-op and nothing uploaded the files back.

The re-sync cannot rebuild what archive destroys: ``archive_multi_assetFiles``
places an S3 delete marker on every file and unarchive restores them only when
the caller opts in (``unarchiveFiles``), so after a default archive/unarchive
cycle there are no readable bytes to upload. The fix therefore has to be "do not
delete while the asset still exists in VAMS", which
``_sync_asset_metadata_to_physna`` resolves against the asset row itself; the
VAMS-driven upload of files Physna is missing is the drift-repair path on top of
that, not the archive answer.

The asset-row stream is only one of the two paths archive drives. The same
operation writes an S3 delete marker per file, and those ``ObjectRemoved`` events
reach ``physnaFileSync`` through ``sqsBucketSync.lambda_handler_deleted`` and
``fileIndexerSnsTopic``, where they deleted the Physna copy outright. Both
handlers therefore run over one Physna tenant double here, because a test that
drives only the asset-level handler cannot fail on a file-level delete.

Two further properties are pinned because both turn an error into permanent loss
of indexed geometry:

* The repair upload must read the replacement bytes before it retires the copy
  it is replacing, so an S3 read failure or a delete-marked object leaves the
  existing copy in place.
* Neither inventory the asset sync reads is complete --
  ``list_physna_assets_under`` narrows its scan to the asset folder, and the VAMS
  metadata index holds no row for a file carrying neither metadata nor
  attributes -- so a delete needs the object's S3 version state to show every
  version purged rather than an absence from a list.

Every one of these has a paired positive control, because "stop deleting" is
also satisfied by an implementation that stopped writing.

FIX-045 -- one asset-level edit re-synced every file with two DynamoDB queries
per file (``get_file_metadata`` reads the metadata table and the file-attribute
table). The count assertions below pin the read cost as independent of the file
count; without a count assertion a performance fix cannot be shown to work or
protected from regressing.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
ARCHIVED_DB = f"{DB}#deleted"
PREFIX = f"{DB}/{ASSET}/"


# ---------------------------------------------------------------------------
# Doubles for the four boundaries: DynamoDB, the Physna HTTP client, the asset
# row, and the file upload. Everything between them runs for real.
# ---------------------------------------------------------------------------


def _meta_row(relative, key, value="v", value_type="string"):
    return {
        "metadataKey": key,
        "databaseId:assetId": f"{DB}:{ASSET}",
        "databaseId:assetId:filePath": f"{DB}:{ASSET}:{relative}",
        "metadataValue": value,
        "metadataValueType": value_type,
    }


def _attr_row(relative, key, value="v", value_type="string"):
    return {
        "attributeKey": key,
        "databaseId:assetId": f"{DB}:{ASSET}",
        "databaseId:assetId:filePath": f"{DB}:{ASSET}:{relative}",
        "attributeValue": value,
        "attributeValueType": value_type,
    }


def _condition_value(condition):
    """Pull the compared value out of a boto3 ``Key(...).eq(...)`` condition."""
    return condition.get_expression()["values"][1]


class _FakeTable:
    """Answers both GSI shapes over an in-memory row list, counting queries.

    ``DatabaseIdAssetIdIndex`` (whole asset) is what ``_list_vams_file_paths``
    and the FIX-045 prefetch use; ``DatabaseIdAssetIdFilePathIndex`` (one file,
    or the asset-level ``:/`` row) is what ``get_asset_metadata`` and the
    per-file ``get_file_metadata`` use. Serving both from one row list is what
    lets a single fixture compare the two read strategies.
    """

    def __init__(self, rows):
        self.rows = list(rows)
        self.query = MagicMock(side_effect=self._query)

    def _query(self, **kwargs):
        index = kwargs.get("IndexName")
        wanted = _condition_value(kwargs["KeyConditionExpression"])
        if index == "DatabaseIdAssetIdIndex":
            attr = "databaseId:assetId"
        elif index == "DatabaseIdAssetIdFilePathIndex":
            attr = "databaseId:assetId:filePath"
        else:
            raise AssertionError(f"unexpected IndexName {index!r}")
        return {"Items": [r for r in self.rows if r.get(attr) == wanted]}


class _FakeResponse:
    def __init__(self, status=204, data=b""):
        self.status = status
        self.data = data


class _FakePhysnaTenant:
    """The Physna side of one tenant: which paths exist, and their metadata.

    Records every verb the sync issues, so a test can assert on the external
    round-trip count as well as on the surviving state. Asset deletes and
    metadata-field deletes are recorded separately -- both are HTTP DELETEs, and
    conflating them makes "nothing was destroyed" unassertable.
    """

    def __init__(self, paths_metadata=None):
        self.assets = {
            path: dict(metadata)
            for path, metadata in (paths_metadata or {}).items()
        }
        self.uuid_by_path = {p: f"uuid-{i}" for i, p in enumerate(sorted(self.assets))}
        self.calls = []

    # -- Physna client seam -------------------------------------------------
    def client(self):
        client = MagicMock()
        client.request = MagicMock(side_effect=self._request)
        return client

    def _request(self, method, url, **kwargs):
        if method == "DELETE" and url.endswith("/metadata"):
            self.calls.append(("DELETE_METADATA", url))
            return _FakeResponse()
        if method == "POST" and url.endswith("/assets"):
            # The real multipart upload physnaFileSync issues. Recorded under
            # the Physna path so a POST and an UPLOAD seam call read alike.
            path = kwargs["fields"]["path"]
            self.calls.append(("POST", path))
            uuid = f"uuid-post-{len(self.uuid_by_path)}"
            self.assets[path] = {}
            self.uuid_by_path[path] = uuid
            return _FakeResponse(
                status=201, data=json.dumps({"id": uuid}).encode("utf-8")
            )
        self.calls.append((method, url))
        uuid = url.rsplit("/", 1)[-1]
        path = next(
            (p for p, known in self.uuid_by_path.items() if known == uuid), None
        )
        if method == "DELETE" and path is not None:
            self.assets.pop(path, None)
            self.uuid_by_path.pop(path, None)
        if method == "PATCH" and path is not None:
            body = json.loads(kwargs["body"].decode("utf-8"))
            self.assets[path] = dict(body["metadata"])
        return _FakeResponse()

    # -- listing seam -------------------------------------------------------
    def listing(self, *_args, **_kwargs):
        return iter(
            [
                {
                    "id": self.uuid_by_path[path],
                    "path": path,
                    "metadata": dict(metadata),
                }
                for path, metadata in sorted(self.assets.items())
            ]
        )

    # -- single-asset read seam ---------------------------------------------
    def get_asset(self, _client, _tenant_id, uuid):
        path = next(
            (p for p, known in self.uuid_by_path.items() if known == uuid), None
        )
        if path is None:
            return None
        return {"id": uuid, "path": path, "metadata": dict(self.assets[path])}

    def uuid_for(self, _client, _tenant_id, path):
        return self.uuid_by_path.get(path)

    # -- upload seam --------------------------------------------------------
    def upload(self, database_id, asset_id, relative, _bucket, _key, client=None):
        path = f"{database_id}/{asset_id}{relative}"
        self.calls.append(("UPLOAD", path))
        self.assets.setdefault(path, {})
        self.uuid_by_path.setdefault(path, f"uuid-up-{len(self.uuid_by_path)}")
        return True

    @property
    def paths(self):
        return set(self.assets)

    def verb_count(self, verb):
        return len([c for c in self.calls if c[0] == verb])

    def paths_for(self, verb):
        return sorted(target for v, target in self.calls if v == verb)


class _FakeS3:
    """The S3 side of one asset's files: live versions and delete markers.

    Only the three calls the Physna handlers make are modelled -- the version
    listing that decides whether a removal is permanent, the head_object that
    yields the current VersionId, and the download that supplies the bytes.
    Expressing archive / unarchive / permanent delete as the version-state
    transitions ``assetService`` performs is what makes "archive must not
    delete" assertable: archive writes a delete marker over live versions
    (``mark_file_as_archived``), unarchive removes that marker
    (``unarchive_multi_assetFiles``), and only a permanent delete purges every
    version (``delete_s3_object_all_versions``).
    """

    def __init__(self, keys):
        self.objects = {
            key: {"versions": ["ver-1"], "marker": None} for key in keys
        }
        # Set to a ClientError to make every download fail with it.
        self.download_error = None

    # -- VAMS lifecycle transitions -----------------------------------------
    def archive(self):
        for state in self.objects.values():
            state["marker"] = "dm-1"

    def unarchive(self):
        for state in self.objects.values():
            state["marker"] = None

    def purge(self, key=None):
        if key is None:
            self.objects.clear()
        else:
            self.objects.pop(key, None)

    # -- boto3 seam ---------------------------------------------------------
    def list_object_versions(self, Bucket, Prefix, MaxKeys=None, **_kwargs):
        versions = []
        markers = []
        for key, state in sorted(self.objects.items()):
            if not key.startswith(Prefix):
                continue
            versions.extend(
                {"Key": key, "VersionId": v} for v in state["versions"]
            )
            if state["marker"]:
                markers.append({"Key": key, "VersionId": state["marker"]})
        return {"Versions": versions, "DeleteMarkers": markers}

    def head_object(self, Bucket, Key, **_kwargs):
        state = self.objects.get(Key)
        if state is None or state["marker"]:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )
        return {"VersionId": state["versions"][-1]}

    def download_file(self, Bucket, Key, Filename, **_kwargs):
        if self.download_error is not None:
            raise self.download_error
        state = self.objects.get(Key)
        if state is None or state["marker"]:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "gone"}}, "GetObject"
            )
        with open(Filename, "wb") as handle:
            handle.write(b"bytes")


class _VamsSide:
    """The VAMS state one asset has: its row, under either key, and its files.

    ``assetService`` moves the row across the '#deleted' suffix and rewrites the
    S3 version state in the same operation, so the two are driven together here.
    """

    def __init__(self, s3_keys=(), live=True, archived=False):
        self.asset = {
            "assetName": "My Asset",
            "bucketId": "b-1",
            "assetLocation": {"Key": "prefix/asset-1/"},
        }
        self.live = live
        self.archived = archived
        self.s3 = _FakeS3(s3_keys)

    def get_asset_details(self, database_id, asset_id):
        if database_id == DB and self.live:
            return dict(self.asset)
        if database_id == ARCHIVED_DB and self.archived:
            return dict(self.asset, status="archived")
        return None

    # The VAMS lifecycle transitions, as assetService performs them.
    def archive(self):
        self.archived = True
        self.live = False
        self.s3.archive()

    def unarchive(self, restore_files=False):
        # ``unarchive_asset`` restores the S3 objects only when the caller opts
        # in (``unarchiveFiles``); the default moves the row alone and leaves
        # every file delete-marked.
        self.live = True
        self.archived = False
        if restore_files:
            self.s3.unarchive()

    def permanently_delete(self):
        self.live = False
        self.archived = False
        self.s3.purge()


def _asset_record(event_name, database_id):
    return {
        "eventSource": "aws:dynamodb",
        "eventName": event_name,
        "dynamodb": {
            "Keys": {
                "databaseId": {"S": database_id},
                "assetId": {"S": ASSET},
            },
            "NewImage": {
                "databaseId": {"S": database_id},
                "assetId": {"S": ASSET},
            },
        },
    }


def _stream_records(*records):
    """Wrap DynamoDB stream records as the SNS-over-SQS batch the handler gets."""
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps(
                    {"Type": "Notification", "Message": json.dumps(record)}
                ),
            }
            for record in records
        ]
    }


# The two stream records VAMS emits per lifecycle operation. Their order through
# SNS -> SQS is not guaranteed, which is why the tests feed both orders.
ARCHIVE_RECORDS = (_asset_record("INSERT", ARCHIVED_DB), _asset_record("REMOVE", DB))
UNARCHIVE_RECORDS = (_asset_record("INSERT", DB), _asset_record("REMOVE", ARCHIVED_DB))


def _physna_state(vams_files, extra=None, asset_name="My Asset"):
    """A Physna tenant holding exactly ``vams_files``, already VAMS-tagged."""
    state = {}
    for relative in vams_files:
        metadata = {
            "partFamily": "widgets",
            "__VAMS__FileVersion": f"ver-{relative}",
        }
        if asset_name is not None:
            metadata["__VAMS__AssetName"] = asset_name
        metadata.update(extra or {})
        state[f"{PREFIX}{relative.lstrip('/')}"] = metadata
    return state


ASSET_BASE_KEY = "prefix/asset-1/"
BUCKET = "bucket-1"


def _s3_key(relative):
    return ASSET_BASE_KEY + relative.lstrip("/")


@pytest.fixture
def harness(monkeypatch):
    """Wire physnaAssetSync to the in-memory VAMS + Physna doubles."""
    from backend.backend.handlers.addon.physna import (
        physnaAssetSync,
        physnaCommon,
        physnaFileSync,
    )

    def _build(
        vams_files,
        physna_paths_metadata,
        metadata_rows=None,
        attribute_rows=None,
        bucket=True,
        s3_files=None,
    ):
        # S3 holds the asset's files unless a test says otherwise. The prune
        # decision reads this, so a fixture that left S3 empty would make every
        # file look permanently deleted.
        s3_relatives = vams_files if s3_files is None else s3_files
        vams = _VamsSide([_s3_key(relative) for relative in s3_relatives])
        monkeypatch.setattr(physnaFileSync, "_s3", vams.s3)
        tenant = _FakePhysnaTenant(physna_paths_metadata)

        # One metadata row per VAMS file is what puts that file in
        # _list_vams_file_paths; a test can supply its own row set instead.
        if metadata_rows is None:
            metadata_rows = [
                _meta_row(relative, "partFamily", "widgets")
                for relative in vams_files
            ]
        metadata_table = _FakeTable(metadata_rows)
        attribute_table = _FakeTable(attribute_rows or [])

        monkeypatch.setattr(physnaCommon, "asset_file_metadata_table", metadata_table)
        monkeypatch.setattr(physnaCommon, "file_attribute_table", attribute_table)
        monkeypatch.setattr(
            physnaAssetSync, "PhysnaClient", MagicMock(return_value=tenant.client())
        )
        monkeypatch.setattr(physnaAssetSync, "list_physna_assets_under", tenant.listing)
        monkeypatch.setattr(
            physnaAssetSync, "get_asset_details", vams.get_asset_details
        )
        monkeypatch.setattr(
            physnaAssetSync,
            "get_bucket_details",
            lambda _bucket_id: (
                {"bucketName": BUCKET, "baseAssetsPrefix": "prefix/"}
                if bucket
                else None
            ),
        )
        monkeypatch.setattr(physnaAssetSync, "delete_folder_if_empty", MagicMock())
        monkeypatch.setattr(
            physnaAssetSync, "ensure_metadata_fields_registered", MagicMock()
        )
        monkeypatch.setattr(physnaAssetSync, "_record_asset_sync", MagicMock())
        monkeypatch.setattr(
            physnaAssetSync.physnaFileSync,
            "_upload_file_to_physna",
            MagicMock(side_effect=tenant.upload),
        )
        return physnaAssetSync, tenant, vams, metadata_table, attribute_table

    return _build


@pytest.fixture
def file_sync(monkeypatch):
    """Wire physnaFileSync's S3-event path onto the same doubles.

    FIX-013's end-to-end assertion needs both handlers over one Physna tenant:
    the asset row moving across the '#deleted' suffix drives physnaAssetSync,
    and the S3 delete markers the same operation writes drive physnaFileSync.
    A test that exercises only the asset-level handler cannot fail on a delete
    issued by the file-level one.
    """
    from backend.backend.handlers.addon.physna import physnaFileSync

    def _wire(tenant, vams):
        monkeypatch.setattr(physnaFileSync, "_s3", vams.s3)
        monkeypatch.setattr(
            physnaFileSync, "PhysnaClient", MagicMock(return_value=tenant.client())
        )
        monkeypatch.setattr(
            physnaFileSync, "lookup_physna_asset_id", tenant.uuid_for
        )
        monkeypatch.setattr(physnaFileSync, "delete_folder_if_empty", MagicMock())
        monkeypatch.setattr(physnaFileSync, "_record_file_sync", MagicMock())
        # The metadata-free resolver has its own tests; what matters here is
        # that it hands back the LIVE databaseId, which is the case that makes
        # the Physna path match and the copy destroyable.
        monkeypatch.setattr(
            physnaFileSync,
            "_resolve_asset_from_s3_key_without_metadata",
            lambda bucket, key: {
                "databaseId": DB,
                "assetId": ASSET,
                "relativePath": "/" + key[len(ASSET_BASE_KEY):],
                "bucketName": bucket,
                "s3Key": key,
            },
        )
        return physnaFileSync

    return _wire


def _s3_event(keys, event_name):
    """The SQS -> SNS -> S3 batch shape sqsBucketSync forwards to the indexers."""
    records = [
        {
            "eventSource": "aws:s3",
            "eventName": event_name,
            "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}},
        }
        for key in keys
    ]
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps(
                    {
                        "Type": "Notification",
                        "Message": json.dumps({"Records": records}),
                    }
                ),
            }
        ]
    }


# ---------------------------------------------------------------------------
# FIX-013
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestArchiveUnarchivePreservesPhysnaState:
    """FIX-013 -- the lifecycle assertion, and the one that fails today."""

    @pytest.mark.parametrize("reverse_archive", [False, True])
    @pytest.mark.parametrize("reverse_unarchive", [False, True])
    def test_archive_then_unarchive_leaves_the_physna_state_intact(
        self, harness, reverse_archive, reverse_unarchive
    ):
        """FIX-013: an archive/unarchive cycle must not lose the Physna copies.

        Both record orderings are exercised in both halves of the cycle: a
        single-order test leaves the SNS -> SQS race unproven.
        """
        vams_files = ["/a.step", "/b.stl"]
        expected = _physna_state(vams_files)
        sync, tenant, vams, _, _ = harness(vams_files, expected)

        archive = list(ARCHIVE_RECORDS)
        unarchive = list(UNARCHIVE_RECORDS)
        if reverse_archive:
            archive.reverse()
        if reverse_unarchive:
            unarchive.reverse()

        vams.archive()
        sync.lambda_handler(_stream_records(*archive), MagicMock())
        vams.unarchive()
        sync.lambda_handler(_stream_records(*unarchive), MagicMock())

        assert tenant.paths == set(expected), (
            f"Physna copies lost across the archive/unarchive cycle; "
            f"remaining={sorted(tenant.paths)}"
        )
        assert tenant.verb_count("DELETE") == 0
        assert tenant.assets == expected

    def test_archive_alone_does_not_destroy_what_unarchive_needs(self, harness):
        """FIX-013: archive must leave the Physna copies and their tags in place.

        Unarchive cannot rebuild them -- archive delete-marks every S3 object and
        a default unarchive does not restore them, so there are no bytes to
        upload back. Whatever archive destroys is destroyed permanently.
        """
        vams_files = ["/a.step", "/b.stl"]
        expected = _physna_state(vams_files)
        sync, tenant, vams, _, _ = harness(vams_files, expected)

        vams.archive()
        sync.lambda_handler(_stream_records(*ARCHIVE_RECORDS), MagicMock())

        assert tenant.paths == set(expected)
        assert tenant.assets == expected, "metadata (incl. __VAMS__ tags) changed"
        assert tenant.verb_count("DELETE") == 0
        assert tenant.verb_count("DELETE_METADATA") == 0
        assert tenant.verb_count("UPLOAD") == 0

    @pytest.mark.parametrize(
        "records",
        [(_asset_record("REMOVE", DB),), (_asset_record("REMOVE", ARCHIVED_DB),)],
        ids=["remove-on-live-key", "remove-on-archived-key"],
    )
    def test_permanent_delete_still_removes_every_physna_copy(self, harness, records):
        """NEGATIVE CONTROL: the delete path must still delete.

        Without this, "archive keeps the copies" is equally satisfied by a
        handler that never deletes anything, and a permanently deleted asset
        would stay in the customer's Physna tenant forever.
        """
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, vams, _, _ = harness(vams_files, _physna_state(vams_files))

        vams.permanently_delete()
        sync.lambda_handler(_stream_records(*records), MagicMock())

        assert tenant.paths == set()
        assert tenant.verb_count("DELETE") == 2

    def test_resync_uploads_only_the_files_physna_is_missing(self, harness):
        """FIX-013: the re-sync repairs drift toward Physna holding less than VAMS.

        NEGATIVE CONTROL in the same test: a path Physna holds that VAMS does not
        must still be pruned, so the upload direction was added without losing
        the delete direction. The orphan has no S3 object either, which is the
        evidence the prune now requires.
        """
        vams_files = ["/a.step", "/b.stl", "/c.obj"]
        state = _physna_state(["/a.step"])
        orphan = f"{PREFIX}gone.step"
        state[orphan] = {"__VAMS__FileVersion": "ver-gone"}
        sync, tenant, _, _, _ = harness(vams_files, state)

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.paths_for("UPLOAD") == [
            f"{PREFIX}b.stl",
            f"{PREFIX}c.obj",
        ]
        assert orphan not in tenant.paths, "orphan pruning was lost"
        assert tenant.verb_count("DELETE") == 1

    def test_unsupported_extensions_are_never_uploaded(self, harness):
        """A VAMS file Physna does not accept must not be retried on every sync.

        physnaFileSync never uploads these extensions, so Physna is permanently
        missing them; attempting them would spend an S3 download plus a rejected
        upload per file on every asset-level edit.
        """
        vams_files = ["/model.ifc", "/cloud.ply", "/real.step"]
        sync, tenant, _, _, _ = harness(vams_files, {})

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.paths_for("UPLOAD") == [f"{PREFIX}real.step"]

    def test_upload_is_skipped_when_the_bucket_cannot_be_resolved(self, harness):
        """No bucket means no S3 key to read, so the repair must not be attempted."""
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, _, _, _ = harness(vams_files, {}, bucket=False)

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.verb_count("UPLOAD") == 0

    def test_archived_asset_row_still_supplies_the_asset_name(self, harness):
        """The asset-name tag must survive a sync driven by an archive event.

        The asset row moves under the '#deleted' key while archived. Reading only
        the live key yields no assetName, and the full-replace prune then strips
        __VAMS__AssetName from every file in the tenant.
        """
        vams_files = ["/a.step", "/b.stl"]
        # A stale asset name on Physna's side forces the PATCH, so the payload is
        # actually asserted rather than skipped as already-equal.
        sync, tenant, vams, _, _ = harness(
            vams_files, _physna_state(vams_files, asset_name="Old Name")
        )
        vams.archive()

        sync.lambda_handler(_stream_records(*ARCHIVE_RECORDS), MagicMock())

        assert tenant.paths == set(_physna_state(vams_files))
        for path, metadata in tenant.assets.items():
            assert metadata.get("__VAMS__AssetName") == "My Asset", (
                f"asset-name tag not preserved on {path}: {metadata}"
            )


@pytest.mark.unit
class TestFileSyncObjectRemovedIsNotAnArchiveDelete:
    """FIX-013 -- the other half of archive: the S3 delete events it produces.

    ``archive_multi_assetFiles`` calls ``s3.delete_object`` on every file of the
    asset, so archive emits ``ObjectRemoved:DeleteMarkerCreated`` per file; an
    opt-in unarchive then removes those markers with a versioned delete, which
    emits ``ObjectRemoved:Delete``. Both shapes travel
    S3 -> per-bucket removed topic -> bucketSyncDeleted ->
    ``sqsBucketSync.lambda_handler_deleted`` -> fileIndexerSnsTopic -> the Physna
    file-sync queue, and physnaFileSync deleted the Physna copy for either one.
    Hardening the asset-level handler alone leaves that path intact, so these
    tests drive physnaFileSync over the same tenant double.
    """

    VAMS_FILES = ["/a.step", "/b.stl"]

    def _setup(self, harness, file_sync):
        expected = _physna_state(self.VAMS_FILES)
        sync, tenant, vams, _, _ = harness(self.VAMS_FILES, expected)
        return sync, file_sync(tenant, vams), tenant, vams, expected

    def _keys(self):
        return [_s3_key(relative) for relative in self.VAMS_FILES]

    def test_archive_delete_markers_leave_the_physna_copies_in_place(
        self, harness, file_sync
    ):
        """An archived object still has its versions; the copy must survive."""
        _sync, fs, tenant, vams, expected = self._setup(harness, file_sync)

        vams.archive()
        response = fs.lambda_handler(
            _s3_event(self._keys(), "ObjectRemoved:DeleteMarkerCreated"), MagicMock()
        )

        assert response["statusCode"] == 200
        assert tenant.paths == set(expected), (
            f"archive deleted Physna copies through the file-sync path; "
            f"remaining={sorted(tenant.paths)}"
        )
        assert tenant.verb_count("DELETE") == 0

    def test_unarchive_marker_removal_leaves_the_restored_copies_in_place(
        self, harness, file_sync
    ):
        """The deterministic loss: unarchive removes the marker, then VAMS moves
        the row back, so the resolver returns the live databaseId and the Physna
        path matches. Deleting here destroys the copy of a file VAMS has just
        restored."""
        _sync, fs, tenant, vams, expected = self._setup(harness, file_sync)

        vams.archive()
        vams.unarchive(restore_files=True)
        fs.lambda_handler(
            _s3_event(self._keys(), "ObjectRemoved:Delete"), MagicMock()
        )

        assert tenant.paths == set(expected)
        assert tenant.verb_count("DELETE") == 0

    def test_permanent_file_delete_still_removes_the_physna_copy(
        self, harness, file_sync
    ):
        """NEGATIVE CONTROL: once every version is purged the copy is an orphan.

        Without this, "archive keeps the copies" is equally satisfied by a
        handler that never deletes anything.
        """
        _sync, fs, tenant, vams, _ = self._setup(harness, file_sync)

        purged = _s3_key("/a.step")
        vams.s3.purge(purged)
        fs.lambda_handler(_s3_event([purged], "ObjectRemoved:Delete"), MagicMock())

        assert f"{PREFIX}a.step" not in tenant.paths
        assert f"{PREFIX}b.stl" in tenant.paths
        assert tenant.verb_count("DELETE") == 1

    def test_an_unreadable_version_state_keeps_the_copy(self, harness, file_sync):
        """A throttled or denied listing is not evidence of a delete."""
        _sync, fs, tenant, vams, expected = self._setup(harness, file_sync)

        vams.s3.purge(_s3_key("/a.step"))
        vams.s3.list_object_versions = MagicMock(side_effect=RuntimeError("throttled"))
        fs.lambda_handler(
            _s3_event([_s3_key("/a.step")], "ObjectRemoved:Delete"), MagicMock()
        )

        assert tenant.paths == set(expected)
        assert tenant.verb_count("DELETE") == 0

    @pytest.mark.parametrize("file_events_first", [False, True])
    def test_a_full_cycle_across_both_handlers_preserves_the_tenant(
        self, harness, file_sync, file_events_first
    ):
        """The whole lifecycle, both handlers, in both arrival orders.

        The asset-row stream records and the S3 delete events are produced by
        the same API call but travel independent SNS -> SQS paths, so neither
        order is guaranteed. Asserting one order leaves the race unproven.
        """
        sync, fs, tenant, vams, expected = self._setup(harness, file_sync)

        def _run_asset(records):
            sync.lambda_handler(_stream_records(*records), MagicMock())

        def _run_files(event_name):
            fs.lambda_handler(_s3_event(self._keys(), event_name), MagicMock())

        vams.archive()
        if file_events_first:
            _run_files("ObjectRemoved:DeleteMarkerCreated")
            _run_asset(ARCHIVE_RECORDS)
        else:
            _run_asset(ARCHIVE_RECORDS)
            _run_files("ObjectRemoved:DeleteMarkerCreated")

        vams.unarchive(restore_files=True)
        if file_events_first:
            _run_files("ObjectRemoved:Delete")
            _run_asset(UNARCHIVE_RECORDS)
        else:
            _run_asset(UNARCHIVE_RECORDS)
            _run_files("ObjectRemoved:Delete")

        assert tenant.paths == set(expected), (
            f"Physna copies lost across the archive/unarchive cycle; "
            f"remaining={sorted(tenant.paths)}"
        )
        assert tenant.assets == expected
        assert tenant.verb_count("DELETE") == 0

    def test_object_created_events_still_upload(self, harness, file_sync):
        """POSITIVE CONTROL: the created path is untouched by the delete gate.

        A fix that stopped deleting by stopping every write would pass every
        assertion above.
        """
        _sync, fs, tenant, vams, _ = self._setup(harness, file_sync)
        from backend.backend.handlers.addon.physna import physnaFileSync

        new_key = _s3_key("/fresh.step")
        with patch.object(
            physnaFileSync,
            "_resolve_asset_from_s3_event",
            lambda bucket, key: {
                "databaseId": DB,
                "assetId": ASSET,
                "relativePath": "/fresh.step",
                "bucketName": bucket,
                "s3Key": key,
            },
        ):
            fs.lambda_handler(_s3_event([new_key], "ObjectCreated:Put"), MagicMock())

        assert tenant.paths_for("UPLOAD") == [f"{PREFIX}fresh.step"]


@pytest.mark.unit
class TestRepairUploadIsNotADeletePath:
    """The repair upload must not destroy the copy it is trying to restore.

    ``_upload_file_to_physna_impl`` retires a stale Physna copy before reading
    the replacement bytes, so any S3 read failure between the two -- throttling,
    AccessDenied, a KMS error, or an archived (delete-marked) object -- turns a
    transient condition into permanent loss of indexed geometry. These tests run
    the real implementation; the asset-sync harness stubs it out, which is why
    they wire physnaFileSync directly.
    """

    RELATIVE = "/a.step"

    def _wire(self, monkeypatch, *, physna_metadata, s3_relatives=(RELATIVE,)):
        from backend.backend.handlers.addon.physna import physnaFileSync

        tenant = _FakePhysnaTenant(physna_metadata)
        vams = _VamsSide([_s3_key(r) for r in s3_relatives])
        monkeypatch.setattr(physnaFileSync, "_s3", vams.s3)
        monkeypatch.setattr(
            physnaFileSync, "PhysnaClient", MagicMock(return_value=tenant.client())
        )
        monkeypatch.setattr(
            physnaFileSync, "lookup_physna_asset_id", tenant.uuid_for
        )
        monkeypatch.setattr(physnaFileSync, "get_physna_asset", tenant.get_asset)
        monkeypatch.setattr(
            physnaFileSync,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version, asset_details=None: (
                {"__VAMS__FileVersion": file_version} if file_version else {}
            ),
        )
        monkeypatch.setattr(
            physnaFileSync, "_update_physna_metadata", MagicMock(return_value=True)
        )
        monkeypatch.setattr(physnaFileSync, "_record_file_sync", MagicMock())
        monkeypatch.setattr(physnaFileSync, "delete_folder_if_empty", MagicMock())
        return physnaFileSync, tenant, vams

    def _upload(self, physnaFileSync):
        return physnaFileSync._upload_file_to_physna(
            DB, ASSET, self.RELATIVE, BUCKET, _s3_key(self.RELATIVE)
        )

    def _stale_state(self):
        # Physna's __VAMS__FileVersion does not match the object's current
        # VersionId, which is what routes the file through the delete-then-upload
        # branch.
        return {f"{PREFIX}a.step": {"__VAMS__FileVersion": "ver-old"}}

    def test_a_transient_s3_error_leaves_the_existing_copy_intact(
        self, monkeypatch
    ):
        physnaFileSync, tenant, vams = self._wire(
            monkeypatch, physna_metadata=self._stale_state()
        )
        vams.s3.download_error = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetObject"
        )

        with pytest.raises(ClientError):
            self._upload(physnaFileSync)

        assert f"{PREFIX}a.step" in tenant.paths, (
            "the stale Physna copy was deleted before the replacement bytes "
            "were read; a retryable S3 error became permanent data loss"
        )
        assert tenant.verb_count("DELETE") == 0

    def test_an_archived_object_leaves_the_existing_copy_intact(self, monkeypatch):
        """Archive delete-marks the object, so the download 404s. The copy the
        unarchive relies on must not be reconciled away."""
        physnaFileSync, tenant, vams = self._wire(
            monkeypatch, physna_metadata=self._stale_state()
        )
        vams.archive()

        assert self._upload(physnaFileSync) is True
        assert f"{PREFIX}a.step" in tenant.paths
        assert tenant.verb_count("DELETE") == 0

    def test_a_purged_object_still_reconciles_the_orphan_away(self, monkeypatch):
        """NEGATIVE CONTROL for the 404 branch: no versions left means orphan."""
        physnaFileSync, tenant, vams = self._wire(
            monkeypatch, physna_metadata=self._stale_state(), s3_relatives=()
        )

        assert self._upload(physnaFileSync) is True
        assert tenant.paths == set()
        assert tenant.verb_count("DELETE") == 1

    def test_a_readable_object_still_replaces_the_stale_copy(self, monkeypatch):
        """POSITIVE CONTROL: the stale copy is still retired and re-uploaded.

        Without this, "nothing was deleted" is satisfied by an implementation
        that stopped replacing stale copies at all, which would leave the
        customer's tenant permanently out of date.
        """
        physnaFileSync, tenant, vams = self._wire(
            monkeypatch, physna_metadata=self._stale_state()
        )

        assert self._upload(physnaFileSync) is True
        assert tenant.verb_count("DELETE") == 1
        assert tenant.paths_for("POST") == [f"{PREFIX}a.step"]
        assert f"{PREFIX}a.step" in tenant.paths

    def test_a_candidate_physna_already_holds_is_refreshed_not_replaced(
        self, monkeypatch
    ):
        """The narrowed listing can report a path as missing that Physna holds.

        The exact-path lookup inside the upload resolves it, so a wrong
        candidate costs a metadata refresh rather than a delete plus re-upload.
        """
        physnaFileSync, tenant, vams = self._wire(
            monkeypatch,
            physna_metadata={f"{PREFIX}a.step": {"__VAMS__FileVersion": "ver-1"}},
        )

        assert self._upload(physnaFileSync) is True
        assert tenant.verb_count("DELETE") == 0
        assert tenant.verb_count("POST") == 0
        assert f"{PREFIX}a.step" in tenant.paths


@pytest.mark.unit
class TestPruneRequiresPositiveEvidence:
    """A Physna copy is pruned only when S3 confirms the object is gone.

    Neither inventory the asset sync reads is complete.
    ``list_physna_assets_under`` narrows its scan to the asset folder via the
    Physna ``folders`` parameter, so a copy in a nested subfolder is absent from
    it, and the VAMS metadata index holds no row for a file carrying neither
    metadata nor attributes -- nothing writes one on upload. "Not in the index"
    therefore does not mean "deleted in VAMS".
    """

    def _run(self, harness, **kwargs):
        vams_files = ["/a.step"]
        state = _physna_state(["/a.step", "/plain.step"])
        sync, tenant, vams, _, _ = harness(vams_files, state, **kwargs)
        return sync, tenant, vams

    def test_a_file_with_no_metadata_row_is_not_pruned(self, harness):
        sync, tenant, _vams = self._run(
            harness, s3_files=["/a.step", "/plain.step"]
        )

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert f"{PREFIX}plain.step" in tenant.paths, (
            "a file with no metadata row was pruned from Physna on the strength "
            "of an index that never held it"
        )
        assert tenant.verb_count("DELETE") == 0

    def test_a_file_whose_object_is_purged_is_pruned(self, harness):
        """NEGATIVE CONTROL: the prune direction must survive the new guard."""
        sync, tenant, _vams = self._run(harness, s3_files=["/a.step"])

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert f"{PREFIX}plain.step" not in tenant.paths
        assert tenant.verb_count("DELETE") == 1

    def test_an_unreadable_version_state_does_not_prune(self, harness):
        sync, tenant, vams = self._run(harness, s3_files=["/a.step"])
        vams.s3.list_object_versions = MagicMock(side_effect=RuntimeError("throttled"))

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert f"{PREFIX}plain.step" in tenant.paths
        assert tenant.verb_count("DELETE") == 0

    def test_an_unresolvable_bucket_does_not_prune(self, harness):
        sync, tenant, _vams = self._run(harness, s3_files=["/a.step"], bucket=False)

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert f"{PREFIX}plain.step" in tenant.paths
        assert tenant.verb_count("DELETE") == 0

    def test_an_archived_asset_attempts_no_uploads(self, harness):
        """Every object of an archived asset is delete-marked, so an upload would
        read nothing -- and the repair cap would be spent on files that cannot be
        read for as long as the asset stays archived."""
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, vams, _, _ = harness(vams_files, _physna_state(["/a.step"]))
        vams.archive()

        sync.lambda_handler(_stream_records(*ARCHIVE_RECORDS), MagicMock())

        assert tenant.verb_count("UPLOAD") == 0
        assert tenant.verb_count("DELETE") == 0

    def test_a_live_asset_still_uploads_what_physna_is_missing(self, harness):
        """POSITIVE CONTROL for the archived guard: the live path still repairs."""
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, _vams, _, _ = harness(vams_files, _physna_state(["/a.step"]))

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.paths_for("UPLOAD") == [f"{PREFIX}b.stl"]


# ---------------------------------------------------------------------------
# FIX-045
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResyncReadCostIsIndependentOfFileCount:
    """FIX-045 -- assert the round-trip COUNT, not just that it still works."""

    def _run_resync(self, harness, file_count):
        vams_files = [f"/part-{i}.step" for i in range(file_count)]
        metadata_rows = [_meta_row("/", "assetLevel", "yes")]
        attribute_rows = []
        for relative in vams_files:
            metadata_rows.append(_meta_row(relative, "partFamily", "widgets"))
            attribute_rows.append(_attr_row(relative, "author", "kurt"))
        sync, tenant, _, metadata_table, attribute_table = harness(
            vams_files,
            _physna_state(vams_files),
            metadata_rows=metadata_rows,
            attribute_rows=attribute_rows,
        )

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        return metadata_table.query.call_count + attribute_table.query.call_count

    def test_dynamodb_query_count_does_not_grow_with_the_file_count(self, harness):
        """FIX-045: per-file metadata reads must become one asset-wide read.

        Per-file reads cost 2 queries per file on top of the asset-level reads,
        so the pre-fix count grows with the file count. Comparing two sizes makes
        this an assertion about the read strategy rather than about one fixture.
        """
        small = self._run_resync(harness, 3)
        large = self._run_resync(harness, 30)

        assert small == large, (
            f"read cost still scales with the file count: 3 files -> {small} "
            f"queries, 30 files -> {large}"
        )
        # Per-file baseline: 1 path listing + 1 asset metadata + 2 per file.
        assert large < 2 + 2 * 30

    def test_the_asset_wide_read_is_four_queries(self, harness):
        """Pins the strategy: paths, asset metadata, then one read per table.

        A looser inequality also passes for an implementation that merely halved
        the per-file work, which would still time out on a realistic asset.
        """
        assert self._run_resync(harness, 30) == 4

    def test_the_prefetched_metadata_is_what_gets_patched(self, harness):
        """The cheaper read must carry the same values the per-file read did.

        Without this, the count assertions above are satisfied by a prefetch that
        drops rows -- and the full-replace prune would then delete those fields
        from the customer's Physna tenant.
        """
        vams_files = ["/a.step", "/b.stl"]
        state = {
            f"{PREFIX}a.step": {"stale": "yes", "__VAMS__FileVersion": "v-a"},
            f"{PREFIX}b.stl": {"stale": "yes", "__VAMS__FileVersion": "v-b"},
        }
        metadata_rows = [
            _meta_row("/", "assetLevel", "shared"),
            _meta_row("/a.step", "perFile", "a-only"),
            _meta_row("/b.stl", "perFile", "b-only"),
        ]
        sync, tenant, _, _, _ = harness(
            vams_files,
            state,
            metadata_rows=metadata_rows,
            attribute_rows=[_attr_row("/a.step", "author", "kurt")],
        )

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.assets[f"{PREFIX}a.step"] == {
            "assetLevel": "shared",
            "perFile": "a-only",
            "Attribute_author": "kurt",
            "__VAMS__AssetName": "My Asset",
            "__VAMS__FileVersion": "v-a",
        }
        assert tenant.assets[f"{PREFIX}b.stl"] == {
            "assetLevel": "shared",
            "perFile": "b-only",
            "__VAMS__AssetName": "My Asset",
            "__VAMS__FileVersion": "v-b",
        }

    def test_a_single_file_asset_reads_the_same_metadata_per_file(self, harness):
        """Positive control: an ordinary sync of a live asset still works.

        One file costs the same two queries either way, so it keeps the direct
        per-file read -- this proves that branch was not left behind broken.
        """
        state = {f"{PREFIX}only.step": {"stale": "x", "__VAMS__FileVersion": "v-1"}}
        metadata_rows = [
            _meta_row("/", "assetLevel", "shared"),
            _meta_row("/only.step", "perFile", "value"),
        ]
        sync, tenant, _, metadata_table, attribute_table = harness(
            ["/only.step"],
            state,
            metadata_rows=metadata_rows,
            attribute_rows=[_attr_row("/only.step", "author", "kurt")],
        )

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.assets[f"{PREFIX}only.step"] == {
            "assetLevel": "shared",
            "perFile": "value",
            "Attribute_author": "kurt",
            "__VAMS__AssetName": "My Asset",
            "__VAMS__FileVersion": "v-1",
        }
        assert metadata_table.query.call_count == 3
        assert attribute_table.query.call_count == 1

    def test_unchanged_metadata_issues_no_physna_writes(self, harness):
        """Re-delivery idempotency: a redelivered batch must cost no Physna writes.

        These Lambdas are SQS-driven, so a redelivered message re-runs the whole
        asset. With Physna already holding the computed payload there is nothing
        to register, prune, PATCH or re-upload.
        """
        vams_files = ["/a.step", "/b.stl"]
        state = _physna_state(vams_files)
        sync, tenant, _, _, _ = harness(vams_files, state)

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())
        first_pass = list(tenant.calls)
        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert first_pass == []
        assert tenant.calls == []
        assert tenant.assets == state

    def test_changed_metadata_still_patches_every_file(self, harness):
        """Positive control for the skip above: a real change must still be written."""
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, _, _, _ = harness(
            vams_files, _physna_state(vams_files, extra={"partFamily": "OLD"})
        )

        sync.lambda_handler(_stream_records(_asset_record("MODIFY", DB)), MagicMock())

        assert tenant.verb_count("PATCH") == 2
        for metadata in tenant.assets.values():
            assert metadata["partFamily"] == "widgets"


@pytest.mark.unit
class TestAssetWidePagingAndCursors:
    """FIX-045 -- the asset-wide read must page each table on its own cursor."""

    def test_each_table_pages_with_its_own_cursor(self):
        """A cursor shared across the two tables terminates one of them early.

        The paginated helper in physnaCommon deliberately owns its cursor per
        call; the asset-wide read has to keep that property.
        """
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        metadata_table = MagicMock()
        metadata_table.query.side_effect = [
            {"Items": [_meta_row("/a.step", "m1")], "LastEvaluatedKey": {"c": 1}},
            {"Items": [_meta_row("/b.stl", "m2")], "LastEvaluatedKey": {"c": 2}},
            {"Items": [_meta_row("/b.stl", "m3")]},
        ]
        attribute_table = MagicMock()
        attribute_table.query.side_effect = [
            {"Items": [_attr_row("/a.step", "a1")]},
        ]

        with patch.object(
            physnaCommon, "asset_file_metadata_table", metadata_table
        ), patch.object(physnaCommon, "file_attribute_table", attribute_table):
            result = physnaAssetSync._prefetch_file_metadata(DB, ASSET)

        assert sorted(result) == ["/a.step", "/b.stl"]
        assert sorted(result["/b.stl"][0]) == ["m2", "m3"]
        assert sorted(result["/a.step"][1]) == ["a1"]
        assert metadata_table.query.call_count == 3
        assert attribute_table.query.call_count == 1
        assert metadata_table.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {
            "c": 1
        }
        assert "ExclusiveStartKey" not in attribute_table.query.call_args_list[0].kwargs

    def test_asset_level_and_system_rows_are_excluded(self):
        """The ':/' asset-level rows and system markers must not become file rows."""
        from backend.backend.common.dynamoDbMetadataKeys import (
            REINDEX_METADATA_RECORD_KEY,
        )
        from backend.backend.handlers.addon.physna import physnaAssetSync, physnaCommon

        metadata_table = MagicMock()
        metadata_table.query.side_effect = [
            {
                "Items": [
                    _meta_row("/", "assetLevel", "shared"),
                    _meta_row("/a.step", REINDEX_METADATA_RECORD_KEY),
                    _meta_row("/a.step", "blank", value=""),
                    _meta_row("/a.step", "keepme"),
                ]
            }
        ]
        attribute_table = MagicMock()
        attribute_table.query.side_effect = [{"Items": []}]

        with patch.object(
            physnaCommon, "asset_file_metadata_table", metadata_table
        ), patch.object(physnaCommon, "file_attribute_table", attribute_table):
            result = physnaAssetSync._prefetch_file_metadata(DB, ASSET)

        assert sorted(result) == ["/a.step"]
        assert sorted(result["/a.step"][0]) == ["keepme"]

    def test_paging_is_bounded_when_the_cursor_never_clears(self):
        """Control: an unbounded pager in a stream handler is a hang, not a report."""
        from backend.backend.handlers.addon.physna import physnaAssetSync

        calls = {"n": 0}
        runaway_limit = 500

        def _never_ending(**_kwargs):
            calls["n"] += 1
            if calls["n"] > runaway_limit:
                raise RuntimeError(
                    f"asset index query issued more than {runaway_limit} pages; "
                    f"the paging loop has no cap"
                )
            return {
                "Items": [_meta_row("/a.step", f"k{calls['n']}")],
                "LastEvaluatedKey": {"c": calls["n"]},
            }

        table = MagicMock()
        table.query.side_effect = _never_ending

        physnaAssetSync._query_asset_index_all_pages(table, f"{DB}:{ASSET}")

        assert calls["n"] <= runaway_limit


@pytest.mark.unit
class TestMidBatchFailureBehaviour:
    """FIX-045 -- a partial failure must not leave Physna half-synced."""

    def test_a_read_failure_aborts_before_any_physna_mutation(self, harness):
        """Every DynamoDB read completes before the first Physna write.

        So a throttled read leaves the tenant byte-identical and the record is
        counted as failed rather than half-applied.
        """
        vams_files = ["/a.step", "/b.stl"]
        state = _physna_state(vams_files, extra={"partFamily": "OLD"})
        sync, tenant, _, _, attribute_table = harness(vams_files, state)
        attribute_table.query.side_effect = RuntimeError("throttled")

        response = sync.lambda_handler(
            _stream_records(_asset_record("MODIFY", DB)), MagicMock()
        )

        assert response["body"] == {"successful": 0, "failed": 1}
        assert tenant.calls == [], "Physna was mutated despite the read failure"
        assert tenant.assets == state

    def test_a_failed_record_does_not_stop_the_rest_of_the_batch(self, harness):
        """The remaining messages in the batch are still processed.

        The first record's attribute read raises; the second one's succeeds and
        must still reach Physna.
        """
        vams_files = ["/a.step", "/b.stl"]
        sync, tenant, _, _, attribute_table = harness(
            vams_files, _physna_state(vams_files, extra={"partFamily": "OLD"})
        )
        attribute_table.query.side_effect = [RuntimeError("throttled"), {"Items": []}]

        response = sync.lambda_handler(
            _stream_records(
                _asset_record("MODIFY", DB), _asset_record("MODIFY", DB)
            ),
            MagicMock(),
        )

        assert response["body"] == {"successful": 1, "failed": 1}
        assert tenant.verb_count("PATCH") == 2
        for metadata in tenant.assets.values():
            assert metadata["partFamily"] == "widgets"
