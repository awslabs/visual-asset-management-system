# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every pipeline module that lists the keys under an asset's location leaves out nested assets' files.

The pipeline-side counterpart of ``backend/tests/common/test_nested_location_sites.py``, which scans
``backend/`` only. A file belongs to the deepest stored location that contains it, so a pipeline that
lists, fingerprints or downloads an asset's files leaves alone the files of another asset stored
inside that location. A module that reads an asset's root or location and lists keys either handles
the nested locations or is listed below with the reason it does not need them. A new such module
fails here until it does one or the other.
"""

import re
from pathlib import Path

import pytest

PIPELINES_ROOT = Path(__file__).resolve().parents[1]
LOCATION_READ = re.compile(r"assetRoot|asset_root|assetLocation")
KEY_ENUMERATION = re.compile(r"list_objects_v2|list_object_versions")
NESTED_AWARE = re.compile(r"nested_locations|nestedLocations|NestedLocations")

# file -> why it needs no nested locations
REVIEWED = {
    "multi/rapidPipelineEKS/lambda/consolidated_handler.py":
        "lists the execution's run-job records in the auxiliary bucket, not an asset's location",
}


def _sites():
    found = {}
    for path in sorted(PIPELINES_ROOT.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if LOCATION_READ.search(source) and KEY_ENUMERATION.search(source):
            found[path.relative_to(PIPELINES_ROOT).as_posix()] = bool(NESTED_AWARE.search(source))
    return found


@pytest.mark.unit
class TestPipelineNestedLocationSites:
    def test_the_corpus_is_the_pipeline_tree(self):
        sites = _sites()
        assert "addon/conversion/mirisUpload/container/__main__.py" in sites, (
            "no enumeration site found; the guard would be vacuous")

    def test_every_site_handles_the_nested_locations_or_is_reviewed(self):
        unreviewed = sorted(f for f, aware in _sites().items() if not aware and f not in REVIEWED)
        assert not unreviewed, (
            "a pipeline module lists the keys under an asset's location without leaving out the files of "
            "assets nested in it: leave them out, or review it and list it in REVIEWED with its reason: "
            f"{unreviewed}")

    def test_every_reviewed_file_is_still_a_site_without_the_nested_locations(self):
        sites = _sites()
        stale = sorted(f for f in REVIEWED if sites.get(f) is not False)
        assert not stale, f"REVIEWED names files that are no longer unaware enumeration sites: {stale}"
