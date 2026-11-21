# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The static reading of which handlers query the location index (`locationIndexReaders.py`) is honest.

The infra test `assetLocationIndexReaderGrants.test.ts` grants-checks every handler this reading names, so a
reading that misses a reader would let a function ship without `dynamodb:Query` on `AssetLocationGSI`, as
executeWorkflow did (#1207 D5). These tests pin that the reading finds the index queries in
`common/assetLocations.py`, follows them through imports, aliases and module attributes, and names the
handlers known to read the index.
"""

import json
import subprocess
import sys
import textwrap

import pytest

from tests.common import locationIndexReaders as lir


def _tree(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(tmp_path)


@pytest.mark.unit
class TestTheReading:
    def test_the_index_queries_of_the_location_module_are_the_seeds(self):
        readers = lir.index_readers()
        for name in ("_index_query_kwargs", "_lookup_exact", "_first_under", "load_bucket_locations",
                     "nested_locations_in_index", "resolve_owner", "find_conflicts", "nested_locations_of"):
            assert ("common.assetLocations", name) in readers, name
        # Pure rules read no index
        for name in ("contains", "normalize_location", "is_nested_in", "index_ready"):
            assert ("common.assetLocations", name) not in readers, name

    def test_the_known_readers_are_found(self):
        handlers = lir.reader_handlers(lir.handler_entry_points())
        for handler in (
            "handlers.workflows.executeWorkflow.lambda_handler",
            "handlers.workflows.sfn.workflowTriggerDispatch.lambda_handler",
            "handlers.assets.zipDownloadService.lambda_handler",
            "handlers.assets.assetFiles.lambda_handler",
            "handlers.assets.uploadFile.lambda_handler",
            "handlers.assets.createAsset.lambda_handler",
            "handlers.indexing.sqsBucketSync.lambda_handler_created",
            "handlers.indexing.sqsBucketSync.lambda_handler_deleted",
            "handlers.osSemanticSearch.osFileIndexer.lambda_handler",
        ):
            assert handler in handlers, handler
        # Control: a handler that never touches asset locations is not one
        assert "handlers.auth.routes.lambda_handler" not in handlers

    def test_reads_follow_imports_aliases_module_attributes_and_methods(self, tmp_path):
        root = _tree(tmp_path, {
            "common/__init__.py": "",
            "common/loc.py": '''
                LOCATION_INDEX_NAME = "AssetLocationGSI"
                def query(table):
                    return table.query(IndexName=LOCATION_INDEX_NAME)
                def pure(key):
                    return key.strip("/")
            ''',
            "common/helper.py": '''
                from common.loc import query as q
                def lookup(table):
                    return q(table)
            ''',
            "handlers/__init__.py": "",
            "handlers/a.py": '''
                from common.helper import lookup
                def lambda_handler(event, context):
                    return lookup(event)
            ''',
            "handlers/b.py": '''
                import common.loc as loc
                class Service:
                    def run(self, t):
                        return loc.query(t)
                def lambda_handler(event, context):
                    return Service().run(event)
            ''',
            "handlers/c.py": '''
                from common import loc
                ROUTES = {"GET": None}
                def get(event):
                    return loc.query(event)
                def lambda_handler(event, context):
                    return {"GET": get}["GET"](event)
            ''',
            "handlers/d.py": '''
                from common.loc import pure
                def lambda_handler(event, context):
                    return pure(event)
            ''',
            "handlers/e.py": '''
                def lambda_handler(event, context):
                    return event.query(IndexName="AssetLocationGSI")
            ''',
        })
        found = {h for h in lir.reader_handlers(lir.handler_entry_points(root), root) if h.endswith(".lambda_handler")}
        assert found == {"handlers.a.lambda_handler", "handlers.b.lambda_handler", "handlers.c.lambda_handler",
                         "handlers.e.lambda_handler"}

    def test_the_command_line_prints_every_reader_handler(self):
        """The infra test runs the script with python3 and an empty stdin, the standard library only."""
        out = subprocess.run([sys.executable, "-I", lir.__file__], input="", capture_output=True, text=True,
                             check=True).stdout
        printed = json.loads(out)
        assert printed == sorted(lir.reader_handlers(lir.handler_entry_points()))
        assert "handlers.workflows.executeWorkflow.lambda_handler" in printed
