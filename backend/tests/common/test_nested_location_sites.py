# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every backend module that turns an asset's stored location into S3 keys is a reviewed one.

A file belongs to the deepest stored location that contains it, so an asset's file operations leave alone the
files of an asset nested in its location (``assetLocations.NestedLocations``, read with
``fileAttribution.nested_locations_of_asset``). A module that reads ``assetLocation`` and lists, resolves or
relates keys either reads the nested locations or is listed below with the reason it does not need them. A new
such module fails here until it does one or the other.
"""

import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
LOCATION_READ = "assetLocation"
KEY_ENUMERATION = re.compile(r"list_objects_v2|list_object_versions|list_all_object_versions\(|held_objects\("
                             r"|object_key_for\(|relative_path_in\(|resolve_asset_file_path\(")
NESTED_AWARE = re.compile(r"nested_locations|NestedLocations")

# file -> why it needs no nested locations
REVIEWED = {
    "common/indexing/fileEnumeration.py":
        "the reindex walk attributes each listed key to its deepest owner, live or archived, through the location "
        "index once it is ready (BucketLocations.owner) and keeps it only when that owner is live; by its first "
        "folder before, as before",
    "common/s3PathPatterns.py": "pure path joining; callers decide ownership",
    "handlers/addon/garnetFramework/garnetDataIndexFile.py":
        "attributes each file event with fileAttribution.attribute_object (the deepest owner)",
    "handlers/addon/physna/physnaAssetSync.py":
        "the asset's files come from its file metadata rows (keyed by asset id), not from a listing",
    "handlers/addon/physna/physnaFileSync.py":
        "attributes each file event with fileAttribution.attribute_object (the deepest owner)",
    "handlers/assets/assetExportService.py":
        "exports asset rows, links and metadata rows keyed by asset id; lists no S3 keys",
    "handlers/assets/createAsset.py":
        "the create-time ownership check refuses a location that shares a key with another asset",
    "handlers/compliance/complianceEvaluationStore.py":
        "attributes each file event with fileAttribution.attribute_object (the deepest owner)",
    "handlers/indexing/crReindexer.py":
        "attributes each listed key to its deepest owner through the location index once it is ready",
    "handlers/osSemanticSearch/osFileIndexer.py":
        "attributes each file event with fileAttribution.attribute_object (the deepest owner)",
    "handlers/osVectorSearch/vectorIndexer.py":
        "attributes each file event with fileAttribution.attribute_object (the deepest owner)",
    "handlers/workflows/sfn/processWorkflowExecutionOutput.py":
        "outputs are written through the upload completion, which refuses a nested path; the refusal is "
        "user-correctable (USER_CORRECTABLE_WRITE_BACK_REFUSALS)",
}


def _sites():
    found = {}
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if LOCATION_READ in source and KEY_ENUMERATION.search(source):
            found[path.relative_to(BACKEND_ROOT).as_posix()] = bool(NESTED_AWARE.search(source))
    return found


@pytest.mark.unit
class TestNestedLocationSites:
    def test_the_corpus_is_the_backend_tree(self):
        sites = _sites()
        assert "handlers/assets/assetFiles.py" in sites, "no enumeration site found; the guard would be vacuous"
        assert sites["handlers/assets/assetFiles.py"] is True

    def test_every_site_reads_the_nested_locations_or_is_reviewed(self):
        unreviewed = sorted(f for f, aware in _sites().items() if not aware and f not in REVIEWED)
        assert not unreviewed, (
            "a module turns an asset location into keys without the nested locations: leave the files of "
            "nested assets out with fileAttribution.nested_locations_of_asset, or review it and list it in "
            f"REVIEWED with its reason: {unreviewed}")

    def test_every_reviewed_file_is_still_a_site_without_the_nested_locations(self):
        sites = _sites()
        stale = sorted(f for f in REVIEWED if sites.get(f) is not False)
        assert not stale, f"remove these from REVIEWED: {stale}"
