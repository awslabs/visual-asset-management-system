#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""genAi/videoSopBom: the asset buckets' Regions travel from the manifest to the Batch container.

``vamsExecute`` runs the isolated-subnet gate on every input (an input Region other than the
deployment Region or one named in ``CROSS_REGION_S3_ENDPOINT_REGIONS`` is refused before any
HeadObject, with one SendTaskFailure and no invoke), signs each admitted input's HeadObject for its
bucket's Region, and forwards ``inputBucketRegion`` / ``outputBucketRegion`` to ``openPipeline``,
which carries them in the state-machine input. ``constructPipeline`` turns the manifest's per-file
and output Regions into the definition's ``bucketRegions`` map (bucket name -> Region, deployment-
Region buckets left out), which the container registers before its first S3 call. Reuses the
fixtures of the sibling suites (same directory; the suite runs alone)."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import test_video_sop_bom_vams_execute as execute_suite  # noqa: E402
import test_video_sop_bom_open_pipeline as open_suite  # noqa: E402
import test_video_sop_bom_construct_pipeline as construct_suite  # noqa: E402

# The suite runs with AWS_REGION=us-east-1 (conftest). An input Region other than that is admitted
# only when the VPC's cross-Region endpoint list names it; the output Region is not gated.
DEPLOYMENT_REGION = "us-east-1"
ENDPOINT_REGION = "eu-west-1"
UNSERVED_REGION = "ap-southeast-2"
OUTPUT_REGION = "us-west-2"


def _entry(name, region=""):
    entry = execute_suite._entry(name)
    entry["bucketRegion"] = region
    return entry


def _manifest(entries, output_region=""):
    manifest = execute_suite._manifest(entries)
    manifest["outputs"]["bucketRegion"] = output_region
    return manifest


@pytest.mark.unit
class TestVamsExecuteGatesTheInputRegion:
    def _handle(self, manifest, monkeypatch, endpoint_regions, regional_client=None):
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", endpoint_regions)
        mod = execute_suite._load()
        regional = regional_client or MagicMock()
        regional.head_object.return_value = {"ContentLength": 1024}
        with patch.object(mod.manifestHelper, "s3_client_for_region", MagicMock(return_value=regional)) as factory:
            resp, invoke, sfn = execute_suite._run(mod, manifest)
        return resp, invoke, sfn, factory, regional

    def test_an_input_in_the_deployment_region_passes_with_the_module_client(self, monkeypatch):
        manifest = _manifest([_entry("part1.mp4", DEPLOYMENT_REGION), _entry("part2.mov", "")])
        resp, invoke, sfn, factory, _ = self._handle(manifest, monkeypatch, "")
        assert resp["statusCode"] == 200
        invoke.assert_called_once()
        sfn.send_task_failure.assert_not_called()
        # Neither entry is in another Region, so no regional client is ever built.
        factory.assert_not_called()

    def test_an_input_in_an_endpoint_served_region_is_headed_for_its_region_and_reaches_the_payload(self, monkeypatch):
        manifest = _manifest([_entry("part1.mp4", ENDPOINT_REGION), _entry("part2.mov", "")])
        # Two Regions in the list: the match must not depend on the value being the whole string.
        resp, invoke, sfn, factory, regional = self._handle(
            manifest, monkeypatch, f"eu-north-1,{ENDPOINT_REGION}")
        assert resp["statusCode"] == 200
        sfn.send_task_failure.assert_not_called()
        factory.assert_called_once_with(ENDPOINT_REGION)
        # The cross-Region entry's HeadObject went to the regional client, the other to the module one.
        assert regional.head_object.call_args.kwargs["Key"] == "xidV/part1.mp4"
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputBucketRegion"] == ENDPOINT_REGION
        assert payload["outputBucketRegion"] == ""

    def test_an_input_in_an_unserved_region_is_refused_before_any_head_object(self, monkeypatch):
        manifest = _manifest([_entry("part1.mp4", UNSERVED_REGION)])
        mod = execute_suite._load()
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", ENDPOINT_REGION)
        s3 = execute_suite._s3(manifest)
        resp, invoke, sfn = execute_suite._run(mod, manifest, s3=s3)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        s3.head_object.assert_not_called()
        code, cause = execute_suite._rejection(sfn)
        assert code == mod.ERROR_PIPELINE
        assert UNSERVED_REGION in cause and DEPLOYMENT_REGION in cause
        assert "isolated subnets" in cause and "app.useGlobalVpc.addCrossRegionS3Endpoints" in cause

    def test_without_the_variable_every_cross_region_input_is_refused(self, monkeypatch):
        manifest = _manifest([_entry("part1.mp4", ENDPOINT_REGION)])
        mod = execute_suite._load()
        monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "")
        resp, invoke, sfn = execute_suite._run(mod, manifest)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1

    def test_the_output_region_is_forwarded_and_never_gated(self, monkeypatch):
        manifest = _manifest([_entry("part1.mp4", "")], output_region=OUTPUT_REGION)
        resp, invoke, sfn, _, _ = self._handle(manifest, monkeypatch, "")
        assert resp["statusCode"] == 200
        sfn.send_task_failure.assert_not_called()
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["outputBucketRegion"] == OUTPUT_REGION

    def test_a_manifest_written_without_regions_sends_empty_regions(self, monkeypatch):
        manifest = execute_suite._manifest([execute_suite._entry("part1.mp4")])
        for entry in manifest["inputFiles"]:
            entry.pop("bucketRegion", None)
        manifest["outputs"].pop("bucketRegion", None)
        resp, invoke, sfn, _, _ = self._handle(manifest, monkeypatch, "")
        assert resp["statusCode"] == 200
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert (payload["inputBucketRegion"], payload["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestOpenPipelineForwardsBucketRegions:
    def test_the_state_machine_input_carries_the_regions(self):
        mod = open_suite._load()
        resp, start, send_failure, _ = open_suite._invoke(
            mod, open_suite._event(inputBucketRegion=ENDPOINT_REGION, outputBucketRegion=OUTPUT_REGION))
        assert resp["statusCode"] == 200
        send_failure.assert_not_called()
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["inputBucketRegion"] == ENDPOINT_REGION
        assert sfn_input["outputBucketRegion"] == OUTPUT_REGION

    def test_an_event_from_an_older_lambda_forwards_empty_regions(self):
        mod = open_suite._load()
        resp, start, _, _ = open_suite._invoke(mod, open_suite._event())
        assert resp["statusCode"] == 200
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert (sfn_input["inputBucketRegion"], sfn_input["outputBucketRegion"]) == ("", "")


@pytest.mark.unit
class TestConstructPipelineMapsBucketsToRegions:
    def _definition(self, manifest, event=None):
        mod = construct_suite._load(construct_suite._MODULE)
        s3 = construct_suite._s3(manifest=manifest)
        _, s3, send_failure = construct_suite._run(mod, event=event, s3=s3)
        send_failure.assert_not_called()
        _, definition = construct_suite._written_definition(s3)
        return definition

    def test_each_asset_bucket_maps_to_its_region_from_the_manifest(self):
        files = construct_suite._input_files()
        files[0]["bucket"], files[0]["bucketRegion"] = "in-bkt", ENDPOINT_REGION
        files[1]["bucketRegion"] = ""
        manifest = construct_suite._manifest(inputFiles=files)
        manifest["outputs"] = dict(manifest["outputs"], bucket="out-bkt", bucketRegion=OUTPUT_REGION)
        assert self._definition(manifest)["bucketRegions"] == {"in-bkt": ENDPOINT_REGION, "out-bkt": OUTPUT_REGION}

    def test_deployment_region_buckets_and_the_auxiliary_bucket_are_left_out(self):
        manifest = construct_suite._manifest()
        definition = self._definition(manifest)
        assert definition["bucketRegions"] == {}
        assert "aux" not in definition["bucketRegions"]

    def test_the_event_regions_are_the_fallback_for_a_manifest_written_without_them(self):
        manifest = construct_suite._manifest()
        event = construct_suite._event(inputBucketRegion=ENDPOINT_REGION, outputBucketRegion=OUTPUT_REGION)
        # Inputs and outputs share one bucket here ("abkt"); the last writer wins, as for the one-bucket
        # case, and the map still names the bucket once.
        definition = self._definition(manifest, event=event)
        assert set(definition["bucketRegions"]) == {"abkt"}

    def test_the_container_schema_accepts_the_map_and_its_absence(self):
        import importlib.util
        schema_path = os.path.join(
            os.path.dirname(_LAMBDA_DIR), "container", "video_sop_bom_pipeline", "schemas", "definition_schema.json")
        with open(schema_path, encoding="utf-8") as handle:
            schema = json.load(handle)
        assert schema["properties"]["bucketRegions"]["type"] == "object"
        assert "bucketRegions" not in schema["required"], "an older Lambda's definition omits the map"
        if importlib.util.find_spec("jsonschema") is None:
            pytest.skip("jsonschema not installed in this interpreter")
        import jsonschema
        definition = self._definition(construct_suite._manifest())
        jsonschema.validate(definition, schema)
        definition["bucketRegions"] = {"in-bkt": ENDPOINT_REGION}
        jsonschema.validate(definition, schema)
        definition.pop("bucketRegions")
        jsonschema.validate(definition, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(dict(definition, bucketRegions={"in-bkt": ""}), schema)
