#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The execute handler names the workflow's own selection rule before the output target it cannot resolve.

A request with no input file for an arity-one workflow, or two assets for a single-asset workflow, also
has no single input asset to lock the output to. Reporting the output-target consequence first told the
caller to supply an output asset when the actual defect was the selection; ``workflow_selection_errors``
is what the handler consults first.
"""
import pytest

from common.workflows import executionValidation as ev


def _wsc(arity, **scope):
    return {
        "inputFileArity": arity,
        "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                       "folderAllowed": False, "wholeAssetAllowed": False, **scope},
        "outputTarget": {"locationType": "asset", "allowOverride": True},
    }


def _file(asset_id, key):
    return {"databaseId": "db", "assetId": asset_id, "relativeFileKey": key}


@pytest.mark.unit
class TestWorkflowSelectionErrors:
    def test_no_input_on_an_arity_one_workflow_names_the_arity(self):
        errors = ev.workflow_selection_errors(_wsc("one"), [], {})
        assert any("requires exactly one input file" in e for e in errors), errors

    def test_two_assets_on_a_single_asset_workflow_names_the_span(self):
        errors = ev.workflow_selection_errors(
            _wsc("one"), [_file("a", "/a1.txt"), _file("b", "/b1.txt")], {})
        assert any("single input file" in e for e in errors), errors

    def test_no_input_on_a_multi_workflow_names_the_arity(self):
        errors = ev.workflow_selection_errors(_wsc("multi", crossAssetAllowed=True, singleAssetOnly=False), [], {})
        assert any("at least one input file" in e for e in errors), errors

    def test_two_assets_without_an_output_on_a_cross_asset_multi_workflow_names_the_output(self):
        errors = ev.workflow_selection_errors(
            _wsc("multi", crossAssetAllowed=True, singleAssetOnly=False),
            [_file("a", "/a1.txt"), _file("b", "/b1.txt")], {})
        assert any("requires an output asset" in e for e in errors), errors

    def test_a_legal_selection_reports_nothing(self):
        assert ev.workflow_selection_errors(_wsc("one"), [_file("a", "/a1.txt")], {}) == []
