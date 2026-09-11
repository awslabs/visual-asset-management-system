"""A Physna lookup that FAILED and a Physna lookup that answered "nothing there" are different
outcomes, and conflating them acknowledges a divergence that no later event will revisit.

Both routes covered here reach the same wrong conclusion from a transient Physna error:

*   **Permanent-delete route.** `_delete_physna_asset` looked up the asset UUID, treated a raised
    lookup as "already gone", issued no DELETE, wrote no sync-tracking row, and returned normally so
    the Amazon SQS record was acknowledged. The VAMS file is permanently deleted — every version
    purged — so no future event names that key: the Physna copy is orphaned in the tenant for good.

*   **Upload route, 409.** The pre-upload existence lookup is deliberately best-effort: when it
    fails the code proceeds to upload and lets Physna answer 409 if the path is taken. But a 409
    means this upload did NOT replace the bytes, and with the lookup having failed there is no way to
    know whose bytes they are. Tagging them with the current S3 `VersionId` labels possibly-stale
    geometry as current, and every later staleness decision reads that key — so the divergence is
    never revisited. The guard for this existed but keyed only on `stale_delete_failed`, which is the
    case where a copy WAS identified; the failed-lookup case reached the plain already-exists path
    five lines away.

What each test pins is the pair (does it report, and does it record), because either alone is
insufficient: a `failed` row that still acks leaves the queue believing the work is done, and a
report with no row leaves an operator with nothing to look at.
"""

import json

import pytest
from unittest.mock import MagicMock

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
RELATIVE = "/part.step"
BUCKET = "bucket-1"
S3_KEY = "prefix/asset-1/part.step"

LOOKUP_BOOM = "Physna 503 Service Unavailable on the asset-id lookup"


def _record_fields(records):
    out = []
    for args, kwargs in records:
        action = args[3] if len(args) > 3 else kwargs.get("action")
        status = args[4] if len(args) > 4 else kwargs.get("sync_status")
        out.append((action, status, kwargs))
    return out


def _recorded(records):
    """Set containment only — an extra record, or another order, is not worse."""
    return {(action, status) for action, status, _kwargs in _record_fields(records)}


# ---------------------------------------------------------------------------------------------------
# Permanent-delete route
# ---------------------------------------------------------------------------------------------------


@pytest.fixture
def delete_route(monkeypatch):
    """The permanent-delete route with a controllable UUID lookup."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(*, lookup_raises=False, lookup_result="uuid-1", delete_status=200):
        state = {"records": [], "requests": []}

        monkeypatch.setattr(
            pfs,
            "_record_file_sync",
            lambda *args, **kwargs: state["records"].append((args, kwargs)),
        )

        def _lookup(_client, _tenant, _path):
            if lookup_raises:
                raise pfs.PhysnaError(LOOKUP_BOOM)
            return lookup_result

        monkeypatch.setattr(pfs, "lookup_physna_asset_id", _lookup)
        monkeypatch.setattr(
            pfs, "delete_folder_if_empty", lambda *a, **k: None
        )
        monkeypatch.setattr(pfs, "build_physna_folder_path", lambda *a, **k: None)

        response = MagicMock()
        response.status = delete_status
        response.data = b""
        client = MagicMock()

        def _request(method, path, **kwargs):
            state["requests"].append((method, path))
            return response

        client.request.side_effect = _request
        state["client"] = client
        return pfs, state, client

    return _wire


@pytest.mark.unit
class TestPermanentDeleteLookupFailure:
    def test_a_failed_lookup_does_not_acknowledge_the_delete(self, delete_route):
        pfs, state, client = delete_route(lookup_raises=True)

        with pytest.raises(pfs.PhysnaError):
            pfs._delete_physna_asset(
                client, DB, ASSET, RELATIVE, skip_s3_existence_check=True
            )

        assert not state["requests"], (
            "no DELETE can have been issued — the UUID the endpoint is keyed on was never resolved"
        )

    def test_the_operator_gets_a_failed_row_with_the_reason(self, delete_route):
        pfs, state, client = delete_route(lookup_raises=True)

        with pytest.raises(pfs.PhysnaError):
            pfs._delete_physna_asset(
                client, DB, ASSET, RELATIVE, skip_s3_existence_check=True
            )

        assert (pfs.SYNC_ACTION_DELETE, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        )
        reasons = [
            kwargs.get("error_message", "")
            for _a, _s, kwargs in _record_fields(state["records"])
        ]
        assert any("lookup failed" in r for r in reasons), reasons

    def test_a_lookup_that_answers_nothing_there_still_acks(self, delete_route):
        """The control that keeps the fix narrow.

        Physna answering "no asset at this path" is a settled outcome: there is nothing to delete and
        a retry re-reads the same answer. Reporting it would redrive every delete of a file Physna
        never held, to a dead-letter queue, forever. Only a RAISED lookup may report.
        """
        pfs, state, client = delete_route(lookup_raises=False, lookup_result=None)

        pfs._delete_physna_asset(
            client, DB, ASSET, RELATIVE, skip_s3_existence_check=True
        )

        assert not state["requests"]
        assert _recorded(state["records"]) == set(), (
            "nothing happened, so there is nothing to record"
        )

    def test_a_successful_delete_still_acks_and_records_success(self, delete_route):
        """Positive control: the ordinary path is untouched."""
        pfs, state, client = delete_route(lookup_raises=False, lookup_result="uuid-1")

        pfs._delete_physna_asset(
            client, DB, ASSET, RELATIVE, skip_s3_existence_check=True
        )

        assert [m for m, _p in state["requests"]] == ["DELETE"]
        assert (pfs.SYNC_ACTION_DELETE, pfs.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )


# ---------------------------------------------------------------------------------------------------
# Upload route, 409 after a failed existence lookup
# ---------------------------------------------------------------------------------------------------


@pytest.fixture
def upload_409(monkeypatch):
    """The upload route where Physna answers 409 and the pre-upload lookup may have failed."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(
        *,
        lookup_raises,
        recovers_after_conflict=False,
        existing_uuid="uuid-existing",
        s3_version="v-current",
    ):
        state = {"records": [], "metadata_calls": [], "lookups": 0}

        monkeypatch.setattr(
            pfs,
            "_record_file_sync",
            lambda *args, **kwargs: state["records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pfs,
            "_resolve_asset_from_s3_event",
            lambda bucket, key: {
                "databaseId": DB,
                "assetId": ASSET,
                "relativePath": RELATIVE,
                "bucketName": bucket,
                "s3Key": key,
                "assetDetails": {},
            },
        )
        monkeypatch.setattr(
            pfs,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version=None, asset_details=None: {"user_key": "v"},
        )
        monkeypatch.setattr(pfs, "_get_s3_version_id", lambda bucket, key: s3_version)

        def _lookup(_client, _tenant, _path):
            state["lookups"] += 1
            # `recovers_after_conflict` models the realistic transient error: the pre-upload lookup
            # fails, and by the time the 409 is handled Physna is answering again. That interleaving
            # is the dangerous one — with a UUID back in hand, the metadata write can proceed and tag
            # bytes this upload never replaced. When BOTH lookups fail, a separate pre-existing guard
            # ("UUID unobtainable, metadata set not attempted") already refuses the write, so that
            # case never exhibited the defect and is not what needs pinning.
            if lookup_raises and (state["lookups"] == 1 or not recovers_after_conflict):
                raise pfs.PhysnaError(LOOKUP_BOOM)
            return existing_uuid

        monkeypatch.setattr(pfs, "lookup_physna_asset_id", _lookup)
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            # Physna holds a copy already tagged with the CURRENT version, so nothing is
            # identified as stale and the no-op refresh path is not what is under test.
            lambda _c, _t, _u: {
                "id": existing_uuid,
                "metadata": {pfs.VAMS_RESERVED_FILE_VERSION_KEY: s3_version},
            },
        )
        monkeypatch.setattr(pfs, "_delete_physna_asset_by_uuid", lambda *a, **k: None)

        def _download(_bucket, _key, local_path):
            with open(local_path, "wb") as handle:
                handle.write(b"cad-bytes")

        monkeypatch.setattr(pfs._s3, "download_file", _download)

        def _update_metadata(_client, _full_path, uuid, payload):
            state["metadata_calls"].append((uuid, payload))
            return True

        monkeypatch.setattr(pfs, "_update_physna_metadata", _update_metadata)

        conflict = MagicMock()
        conflict.status = 409
        conflict.data = json.dumps({"message": "asset already exists at path"}).encode("utf-8")
        client = MagicMock()
        client.request.return_value = conflict
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: client)
        state["client"] = client
        return pfs, state

    return _wire


@pytest.mark.unit
class TestConflictAfterFailedExistenceLookup:
    def test_the_record_is_not_reported_as_a_success(self, upload_409):
        pfs, state = upload_409(lookup_raises=True, recovers_after_conflict=True)

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert ok is False, (
            "a 409 means this upload replaced nothing, and with the lookup having failed the bytes "
            "Physna holds are of an undetermined version"
        )
        assert (pfs.SYNC_ACTION_CREATE, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ) or (pfs.SYNC_ACTION_MODIFY, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), _recorded(state["records"])

    def test_the_current_version_tag_is_not_written_onto_unreplaced_bytes(self, upload_409):
        """The consequential half: the tag is what every later staleness decision reads.

        This is the case a UUID IS available for — Physna recovered by the time the 409 was handled —
        so nothing else stops the write. Refusing it has to come from the failed-lookup guard.
        """
        pfs, state = upload_409(lookup_raises=True, recovers_after_conflict=True)

        pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert state["metadata_calls"] == [], (
            "writing the current S3 VersionId here labels possibly-stale geometry as current, so "
            "the divergence is never revisited"
        )

    def test_the_reason_names_the_failed_lookup_not_a_stale_delete(self, upload_409):
        """The two routes into this branch must be distinguishable in the tracking row."""
        pfs, state = upload_409(lookup_raises=True, recovers_after_conflict=True)

        pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        reasons = [
            kwargs.get("error_message", "")
            for _a, _s, kwargs in _record_fields(state["records"])
        ]
        assert any("lookup failed" in r for r in reasons), reasons
        assert not any("could not be deleted" in r for r in reasons), (
            "no stale copy was identified, so the stale-delete wording would misdescribe it"
        )

    def test_a_409_with_a_lookup_that_succeeded_is_unchanged(self, upload_409):
        """Control: a 409 where the lookup DID resolve the copy is the ordinary already-exists path.

        Physna's copy is tagged with the current version here, so nothing was stale, nothing needed
        deleting, and the metadata refresh proceeding is correct. Without this control the fix could
        be widened into failing every 409.
        """
        pfs, state = upload_409(lookup_raises=False)

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert ok is not False, _recorded(state["records"])
        assert pfs.SYNC_STATUS_FAILED not in {
            status for _action, status in _recorded(state["records"])
        }, _recorded(state["records"])
