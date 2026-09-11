# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata cross-calls executeWorkflow makes address the master ApiRoute templates.

metadataService dispatches on `ApiRoute.matches()`, so the synthetic path these reads send has to be
the constant's template with its parameters substituted, not a matching string literal. The
difference only shows when a template is renamed: with a literal, CDK synth, the route-registry test
and the unit tests all stay green while the read answers "Route not found" — which `_fetch_metadata`,
`_fetch_file_metadata` and `_fetch_database_metadata` each downgrade to a logged warning plus an
empty envelope, so every execution silently launches with no input metadata.

Asserting the path against the constant is what makes a rename fail here instead of in production.
The suite additionally asserts the constant MATCHES the path it produced, because substituting the
wrong parameter name (`{assetId}` left unreplaced) yields a path that is derived from the constant
and still does not dispatch.
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.apiRoutes import (
    API_ASSET_METADATA,
    API_DATABASE_METADATA,
    API_FILE_METADATA,
    METADATA_ROUTES,
)

# The handler resolves env vars and resource names at import; these mirror
# test_execute_metadata_identity_and_gate.py so this module imports standalone as well as inside a
# full-suite run.
for _name, _value in (
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
):
    os.environ.setdefault(_name, _value)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"

DB = "db1"
ASSET = "a1"


def _cross_call_event():
    return {"requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"}},
            "lambdaCrossCall": {"userName": "SYSTEM_USER"}}


def _captured_path(fetch):
    """The path of the single metadata-service invoke `fetch` sends, or None if it sent none."""
    payloads = []

    def _capture(payload):
        payloads.append(payload)
        stream = MagicMock()
        stream.read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps({"metadata": []})}).encode("utf-8")
        return {"Payload": stream}

    with patch(f"{MOD}._metadata_service_lambda", side_effect=_capture):
        fetch(_cross_call_event())
    if not payloads:
        return None
    return payloads[0]["requestContext"]["http"]["path"]


_READS = {
    "asset": (
        lambda event: ew._fetch_metadata(DB, ASSET, {}, event),
        API_ASSET_METADATA,
        lambda route: route.path.replace("{databaseId}", DB).replace("{assetId}", ASSET),
    ),
    "file": (
        lambda event: ew._fetch_file_metadata(DB, ASSET, "/f.glb", "metadata", event),
        API_FILE_METADATA,
        lambda route: route.path.replace("{databaseId}", DB).replace("{assetId}", ASSET),
    ),
    "database": (
        lambda event: ew._fetch_database_metadata(DB, event),
        API_DATABASE_METADATA,
        lambda route: route.path.replace("{databaseId}", DB),
    ),
}


@pytest.mark.unit
class TestTheMetadataCrossCallPathsComeFromTheConstants:
    @pytest.mark.parametrize("label", sorted(_READS))
    def test_the_path_sent_is_the_constant_template_substituted(self, label):
        fetch, route, expected = _READS[label]
        applied = _captured_path(fetch)
        # The cross-call was reached: without this, an assertion comparing None to None would pass
        # on a read that never happened.
        assert applied is not None, f"the {label} read sent no metadata-service invoke"
        assert applied == expected(route)

    @pytest.mark.parametrize("label", sorted(_READS))
    def test_the_route_the_service_dispatches_on_matches_that_path(self, label):
        fetch, route, _expected = _READS[label]
        applied = _captured_path(fetch)
        assert applied is not None
        assert route.matches(applied), (
            f"{applied!r} does not match {route.path!r}, so metadataService would answer "
            "'Route not found'")

    @pytest.mark.parametrize("label", sorted(_READS))
    def test_no_other_metadata_route_claims_the_path(self, label):
        """The asset and file templates are prefixes of one another, so "it matches" is not enough —
        exactly one route may claim each path, or the service dispatches to the wrong handler."""
        fetch, route, _expected = _READS[label]
        applied = _captured_path(fetch)
        claimants = [other.path for other in METADATA_ROUTES if other.matches(applied)]
        assert claimants == [route.path], claimants

    def test_the_three_templates_are_distinguishable(self):
        """NEGATIVE CONTROL for the assertions above: if all three constants carried the same
        template, every test here would pass while telling us nothing."""
        templates = {API_ASSET_METADATA.path, API_FILE_METADATA.path, API_DATABASE_METADATA.path}
        assert len(templates) == 3, templates

    def test_a_renamed_template_would_change_the_path_sent(self):
        """The whole point, exercised rather than argued: with the path built from the constant, a
        template rename moves the path the read sends. A literal would not move."""
        original = API_ASSET_METADATA.path
        renamed = original.replace("/metadata", "/metadataV2")
        assert renamed != original
        # ApiRoute is a NamedTuple, so the rename is a replacement constant in the handler's
        # namespace rather than a mutated attribute.
        with patch.object(ew, "API_ASSET_METADATA", API_ASSET_METADATA._replace(path=renamed)):
            applied = _captured_path(_READS["asset"][0])
        assert applied == renamed.replace("{databaseId}", DB).replace("{assetId}", ASSET)
        assert applied != original.replace("{databaseId}", DB).replace("{assetId}", ASSET)
