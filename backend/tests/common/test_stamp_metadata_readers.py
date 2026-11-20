# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every read of the stamp metadata keys (`databaseid`, `assetid`) in backend code is a reviewed one.

The stamp is what bucket sync's metadata self-copy wrote onto directly written objects. Attribution no
longer depends on it: consumers resolve a file's asset through `common.fileAttribution` (the record's
binding, the location index), and read the stamp only as a fallback while the location index is not
ready. This guard is what lets the self-copy be removed safely: a new read of the stamp keys anywhere
else fails here until it is reviewed and listed.

Writes are not reads: VAMS sets the ids on the objects it writes itself (uploads, file copies and moves,
pipeline outputs, exports), at write time. Those are listed in WRITERS for the record and not enforced.
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
STAMP_CONSTANTS = {"ASSET_ID_METADATA_KEY", "DATABASE_ID_METADATA_KEY"}
STAMP_LITERALS = {"assetid", "databaseid"}
DEFINING_MODULE = "common/s3MetadataKeys.py"

# file -> why its read of the stamp keys is safe
REVIEWED_READERS = {
    "common/fileAttribution.py":
        "fallback behind the helper: the stamp is read only while the location index is not ready, and a "
        "stamped asset is accepted only in the event's bucket and when its stored location holds the key",
    "handlers/indexing/crReindexer.py":
        "fallback behind the helper: the HeadObject stamp read runs only when the location index is not "
        "ready; once it is, the bucket's index partition attributes every key",
    "handlers/indexing/sqsBucketSync.py":
        "the echo check of the metadata self-copy: it compares the current stamp before writing one",
}

# file -> what it writes (not enforced)
WRITERS = {
    "handlers/assets/assetFiles.py": "file copy and move set the destination asset's ids",
    "handlers/assets/sqsUploadFileLarge.py": "upload completion sets the ids on the uploaded object",
    "handlers/assets/uploadFile.py": "uploads set the ids on the objects they write",
    "handlers/workflows/sfn/processWorkflowExecutionOutput.py": "pipeline outputs set the ids",
    "handlers/assets/assetExportService.py": "the export manifest names the ids",
    "handlers/indexing/sqsBucketSync.py": "the metadata self-copy writes the stamp",
}


def _is_stamp_key(node) -> bool:
    if isinstance(node, ast.Name) and node.id in STAMP_CONSTANTS:
        return True
    if isinstance(node, ast.Attribute) and node.attr in STAMP_CONSTANTS:
        return True
    return isinstance(node, ast.Constant) and node.value in STAMP_LITERALS


def stamp_reads(source: str):
    """(line, kind) of each read of a stamp key: an argument of `.get(...)`, a loaded subscript, or a
    comparison operand."""
    tree = ast.parse(source)
    reads = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and _is_stamp_key(node.args[0]):
                reads.append((node.lineno, "get"))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_stamp_key(node.slice):
            reads.append((node.lineno, "subscript"))
        elif isinstance(node, ast.Compare) and any(_is_stamp_key(n) for n in [node.left, *node.comparators]):
            reads.append((node.lineno, "compare"))
    return reads


def _backend_files():
    return sorted(p for p in BACKEND_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _readers():
    found = {}
    for path in _backend_files():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == DEFINING_MODULE:
            continue
        reads = stamp_reads(path.read_text(encoding="utf-8"))
        if reads:
            found[relative] = reads
    return found


@pytest.mark.unit
class TestStampMetadataReaders:
    def test_the_scanner_finds_each_read_shape_and_ignores_writes(self):
        source = (
            "a = md.get(ASSET_ID_METADATA_KEY)\n"
            "b = md[DATABASE_ID_METADATA_KEY]\n"
            "c = md.get('assetid')\n"
            "d = s3MetadataKeys.ASSET_ID_METADATA_KEY == x\n"
            "md[ASSET_ID_METADATA_KEY] = 'a1'\n"
            "e = {DATABASE_ID_METADATA_KEY: 'db'}\n"
        )
        assert {line for line, _ in stamp_reads(source)} == {1, 2, 3, 4}

    def test_the_corpus_is_the_backend_tree(self):
        files = _backend_files()
        assert files, "no backend source found; a zero below would be vacuous"
        assert (BACKEND_ROOT / DEFINING_MODULE) in files

    def test_every_stamp_read_is_in_a_reviewed_file(self):
        readers = _readers()
        unreviewed = {f: reads for f, reads in readers.items() if f not in REVIEWED_READERS}
        assert not unreviewed, (
            "a new read of the stamp metadata keys: attribute the file with common.fileAttribution "
            f"instead, or review it and list it in REVIEWED_READERS with its reason: {unreviewed}")

    def test_every_reviewed_file_still_reads_the_stamp(self):
        # A listed file that no longer reads the stamp keeps an exception nothing uses.
        readers = _readers()
        stale = sorted(set(REVIEWED_READERS) - set(readers))
        assert not stale, f"remove these from REVIEWED_READERS: {stale}"
