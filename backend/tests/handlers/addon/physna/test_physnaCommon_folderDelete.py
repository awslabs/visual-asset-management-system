# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Physna folder cleanup is out of scope, and ``delete_folder_if_empty`` says so.

It is an explicit no-op: it returns ``False`` and issues no request. Empty Physna folders
accumulate; the limitation is recorded in the Physna add-on documentation.

Reporting ``False`` matters more than it looks. A caller that believed a delete had happened
would stop reconciling, and an emptiness check is the expensive way to reach the same no-op:
called with a bare ``databaseId`` there is no ``/`` in the prefix, so
``list_physna_assets_under`` sends no ``folders=`` narrowing and the walk pages the entire
tenant asset list. The third test asserts that enumeration does not happen.

The controls pin what the no-op must not have taken with it: the shared listing helper still
pages and prefix-filters, and both sync handlers still bind the same callable.
"""

import json

import pytest

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaCommon as _pc  # noqa: F401


class _RecordingClient:
    """Records every request so a test can assert none was issued."""

    def __init__(self, assets=None):
        self.requests = []
        self._assets = assets or []

    def request(self, method, path, **kwargs):
        self.requests.append((method, path))

        class _R:
            status = 200
            data = json.dumps(
                {
                    "assets": self._assets,
                    "pageData": {"currentPage": 1, "lastPage": 1},
                }
            ).encode("utf-8")

        return _R()


@pytest.mark.unit
class TestFolderDeleteIsANoOp:
    def test_reports_no_delete_and_issues_no_request(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        client = _RecordingClient()

        assert pc.delete_folder_if_empty(client, "tenant-1", "db-1/asset-1") is False
        assert client.requests == []

    def test_reports_no_delete_for_a_populated_folder_too(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        client = _RecordingClient(
            assets=[{"id": "a-1", "path": "db-1/asset-1/file1.step"}]
        )

        assert pc.delete_folder_if_empty(client, "tenant-1", "db-1/asset-1") is False
        assert client.requests == []

    def test_bare_database_id_does_not_enumerate_the_tenant(self):
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        client = _RecordingClient()

        assert pc.delete_folder_if_empty(client, "tenant-1", "db-1") is False
        # The whole request list, not a filter of it. `delete_folder_if_empty` returns False
        # "having issued no request", so the filtered form is satisfied by a client that was
        # never called -- which is the outcome it is supposed to be distinguishing. Asserting
        # the entire list is the stronger claim and cannot pass vacuously.
        assert client.requests == []


@pytest.mark.unit
class TestFolderDeleteRemovalControls:
    def test_listing_helper_still_pages_and_prefix_filters(self, monkeypatch):
        # Control: the helper the emptiness check used is shared with the asset sync
        # and must keep paging and filtering exactly as before.
        import backend.backend.handlers.addon.physna.physnaCommon as pc

        pages = {
            1: {
                "assets": [
                    {"id": "a-1", "path": "db-1/asset-1/file1.step"},
                    {"id": "a-3", "path": "db-1/other-asset/x.step"},
                ],
                "pageData": {"currentPage": 1, "lastPage": 2},
            },
            2: {
                "assets": [{"id": "a-2", "path": "db-1/asset-1/sub/file2.step"}],
                "pageData": {"currentPage": 2, "lastPage": 2},
            },
        }
        seen = []

        class FakeClient:
            def request(self, method, path, **kwargs):
                seen.append(path)
                page = 2 if "page=2" in path else 1
                body = pages[page]

                class _R:
                    status = 200
                    data = json.dumps(body).encode("utf-8")

                return _R()

        assets = list(
            pc.list_physna_assets_under(FakeClient(), "tenant-1", "db-1/asset-1")
        )

        assert [a["id"] for a in assets] == ["a-1", "a-2"]
        assert len(seen) == 2
        assert all("folders=db-1" in path for path in seen)

    def test_both_sync_handlers_bind_the_same_callable(self):
        # Control: the name and its 3-argument signature are the contract the two
        # sync handlers import; neither changed.
        import backend.backend.handlers.addon.physna.physnaCommon as pc
        from backend.backend.handlers.addon.physna import (
            physnaAssetSync,
            physnaFileSync,
        )

        assert physnaAssetSync.delete_folder_if_empty is pc.delete_folder_if_empty
        assert physnaFileSync.delete_folder_if_empty is pc.delete_folder_if_empty
