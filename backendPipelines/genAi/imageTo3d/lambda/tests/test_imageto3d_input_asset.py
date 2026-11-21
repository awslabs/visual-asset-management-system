#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/imageTo3d: where the primary input sits in its asset travels from the manifest to the container.

``vamsExecute`` forwards the first input entry's ``relativePath``, ``assetRootS3Key`` and
``assetRootBucket`` as ``inputRelativePath``, ``inputAssetRootS3Key`` and ``inputAssetRootBucket``;
``openPipeline`` names them on the state-machine input (it enumerates its fields, so one it does not name
is dropped); ``constructPipeline`` puts them on the definition only when the event carries them, so a
definition without a manifest entry is the one written before. The container takes the output
subdirectory from ``inputRelativePath`` (its ``test_output_paths``). Reuses the fixtures of
``test_imageto3d_pipeline`` and ``test_imageto3d_input_version`` (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_imageto3d_pipeline as pipeline  # noqa: E402
import test_imageto3d_input_version as versions  # noqa: E402

# Fixture classes from the pipeline suite, bound to names pytest does not collect here.
_construct = pipeline.TestConstructPipeline
_open = pipeline.TestOpenPipeline
_execute = pipeline.TestVamsExecute

FIELDS = {"inputRelativePath": "/photos/mug.png", "inputAssetRootS3Key": "projects/site-a/",
          "inputAssetRootBucket": "abkt"}


@pytest.mark.unit
class TestVamsExecuteForwardsThePrimaryInputsLocation:
    def test_the_payload_carries_the_first_entrys_fields(self):
        manifest = versions._views_manifest((versions.MUG, versions.VERSION))
        manifest["inputFiles"][0].update(assetRootS3Key="projects/site-a/", assetRootBucket="abkt")
        _mod, resp, payload = _execute()._run(manifest=manifest)
        assert resp["statusCode"] == 200
        assert {k: payload[k] for k in FIELDS} == FIELDS

    def test_an_entry_without_them_sends_empty_values(self):
        manifest = versions._views_manifest((versions.MUG, versions.VERSION))
        for name in ("relativePath", "assetRootS3Key", "assetRootBucket"):
            manifest["inputFiles"][0].pop(name, None)
        _mod, _resp, payload = _execute()._run(manifest=manifest)
        assert [payload[k] for k in FIELDS] == ["", "", ""]


@pytest.mark.unit
class TestOpenPipelineForwardsThem:
    def test_the_state_machine_input_carries_them(self):
        _mod, resp, start, _fail = _open()._run(dict(_open()._event(), **FIELDS))
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert {k: sfn_input[k] for k in FIELDS} == FIELDS

    def test_an_event_from_an_older_lambda_forwards_empty_values(self):
        _mod, _resp, start, _fail = _open()._run(_open()._event())
        assert [json.loads(start.call_args.kwargs["input"])[k] for k in FIELDS] == ["", "", ""]


@pytest.mark.unit
class TestConstructPipelinePutsThemOnTheDefinition:
    def _command(self, **overrides):
        construct = _construct()
        return construct._load().lambda_handler(construct._event(**overrides), MagicMock())["definition"]

    def test_the_definition_names_them(self):
        definition = json.loads(self._command(**FIELDS)[2])
        assert {k: definition[k] for k in FIELDS} == FIELDS

    def test_without_them_the_definition_is_the_one_development_writes(self):
        assert self._command()[2] == versions.DEVELOPMENT_DEFINITION
        assert self._command(inputRelativePath="", inputAssetRootS3Key="", inputAssetRootBucket="")[2] == (
            versions.DEVELOPMENT_DEFINITION)
