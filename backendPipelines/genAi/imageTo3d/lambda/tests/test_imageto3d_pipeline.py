#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the genAi/imageTo3d orchestration lambdas: vamsExecute resolving everything from the
workflow manifest (the Step Functions body carries no paths), model selection resolving CONFIG-FIRST
with an asset-metadata fallback, the openPipeline extension gate, the pipeline definition the
container consumes, and pipelineEnd owning the outer workflow callback."""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub customLogging so the lambdas import without aws_lambda_powertools.
if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:ImageTo3dPipeline",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".png,.jpg,.jpeg",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    "STATE_MACHINE_LOG_GROUP_NAME": "/aws/vendedlogs/VAMSstateMachine-ImageTo3dPipeline",
    "STATE_MACHINE_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:/aws/vendedlogs/VAMSstateMachine-ImageTo3dPipeline:*",
    # boto3.client() resolves credentials eagerly, so importing a lambda module runs whatever
    # credential_process the developer's AWS profile configures. Static test credentials keep the
    # import offline: these tests reach no AWS service, and a machine that cannot reach its
    # credential helper must still be able to run them.
    "AWS_ACCESS_KEY_ID": "testing",
    # pragma: allowlist nextline secret
    "AWS_SECRET_ACCESS_KEY": "testing",  # nosec B105 - dummy value for offline client construction
    "AWS_SESSION_TOKEN": "testing",  # nosec B105 - dummy value for offline client construction
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
    "AWS_CONFIG_FILE": os.devnull,
    "AWS_EC2_METADATA_DISABLED": "true",
}.items():
    os.environ.setdefault(k, v)

# A named profile takes precedence over the static credentials above and would send the import back
# to that profile's credential_process, so it is cleared rather than defaulted.
for _profile_var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
    os.environ.pop(_profile_var, None)


def _s3_reading(*bodies):
    """An S3 client mock whose successive get_object calls return the given JSON bodies."""
    s3 = MagicMock()
    s3.get_object.side_effect = [
        {"Body": MagicMock(read=lambda body=body: json.dumps(body).encode("utf-8"))}
        for body in bodies
    ]
    return s3


# ============================ vamsExecute ============================

@pytest.mark.unit
class TestVamsExecute:
    def _load(self):
        if "vamsExecuteImageTo3dPipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteImageTo3dPipeline"])
        return importlib.import_module("vamsExecuteImageTo3dPipeline")

    def _body(self):
        """A v2.6 Step Functions body: locations and identifiers only, no paths."""
        return {
            "workflowDatabaseId": "dbM",
            "workflowId": "genai-image-to-3d",
            "workflowExecutionId": "E1",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "executingUserName": "someone",
            "executingRequestContext": "ctx",
            "TaskToken": "tok-123",
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/manifest.json",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/config.json",
        }

    def _manifest(self):
        return {
            "inputFiles": [{
                "bucket": "abkt", "key": "xidM/photos/mug.png", "assetId": "xidM",
                "databaseId": "dbM", "assetRootS3Key": "xidM/",
                "auxPreviewPrefix": "dbM/xidM/photos/mug.png/preview",
                "relativePath": "/photos/mug.png",
            }],
            "outputs": {
                "bucket": "abkt",
                "files": "pipelines/imageTo3d/JOB/output/E1/files/",
                "previews": "pipelines/imageTo3d/JOB/output/E1/previews/",
                "metadata": "pipelines/imageTo3d/JOB/output/E1/metadata/",
                "results": "pipelines/imageTo3d/JOB/output/E1/results/",
            },
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/imageTo3d/E1/",
            "auxPreviewPipelineSuffix": "",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _run(self, body=None, manifest=None, config=None, invoke=None):
        """Invoke the handler with the manifest and input configuration served from a mocked S3.

        Only two objects are read: the handler takes its options from the template's configuration body
        and consults no metadata scope, so priming a third body would silently shift the ordered mock and
        feed the configuration where the handler expects the manifest.
        """
        mod = self._load()
        manifest = self._manifest() if manifest is None else manifest
        s3 = _s3_reading(manifest, config or {})
        invoke = invoke or MagicMock(return_value={"StatusCode": 200})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke):
            resp = mod.lambda_handler({"body": json.dumps(body or self._body())}, MagicMock())
        payload = None
        if invoke.call_args is not None:
            payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        return mod, resp, payload

    def test_resolves_from_the_manifest_and_forwards_every_output_path(self):
        """The body carries no paths at all: indexing it for one would raise KeyError. Everything
        the container needs comes out of the manifest."""
        _mod, resp, payload = self._run()
        assert resp["statusCode"] == 200
        assert payload["inputS3AssetFilePath"] == "s3://abkt/xidM/photos/mug.png"
        assert payload["outputS3AssetFilesPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/files/"
        assert payload["outputS3AssetPreviewPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/previews/"
        assert payload["outputS3AssetMetadataPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/metadata/"
        # The prefix the workflow reads onto the EXECUTION. It was in the manifest and the
        # resolver dropped it, so the report went to asset metadata instead -- where an asset
        # holding several models' output keeps only the last run's.
        assert payload["outputS3AssetResultsPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/results/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == "s3://aux/pipelines/imageTo3d/E1/"
        assert payload["assetId"] == "xidM"
        assert payload["databaseId"] == "dbM"
        assert payload["sfnExternalTaskToken"] == "tok-123"
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        # Locations travel onward; content never does.
        assert payload["inputMetadataS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json"
        assert payload["inputConfigurationS3Location"] == "s3://abkt/pipelines/workflowExecutionInputs/E1/p1/config.json"
        assert "inputMetadata" not in payload
        assert "inputParameters" not in payload

    def test_forwards_the_manifests_bucket_regions(self):
        """The input files' bucket and the output bucket may be in other Regions than the deployment;
        the container signs each bucket's requests for its own Region, so both Regions travel onward."""
        manifest = self._manifest()
        manifest["inputFiles"][0]["bucketRegion"] = "eu-west-1"
        manifest["outputs"]["bucketRegion"] = "us-east-2"
        _mod, resp, payload = self._run(manifest=manifest)
        assert resp["statusCode"] == 200
        assert payload["inputBucketRegion"] == "eu-west-1"
        assert payload["outputBucketRegion"] == "us-east-2"
        # A manifest written without them is the deployment Region on both sides.
        _mod, _resp, payload = self._run()
        assert payload["inputBucketRegion"] == "" and payload["outputBucketRegion"] == ""

    def test_a_launch_that_raised_fails_the_waiting_task(self):
        """A RequestResponse invoke of a function that raised still returns StatusCode 200, with the
        failure in FunctionError; read as success, no route reports the token and the workflow task
        waits out its whole timeout."""
        mod = self._load()
        invoke = MagicMock(return_value={"StatusCode": 200, "FunctionError": "Unhandled"})
        send_failure = MagicMock()
        s3 = _s3_reading(self._manifest(), {})
        with patch.object(mod, "s3_client", s3), patch.object(mod.lambda_client, "invoke", invoke),                 patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_body_is_never_indexed_for_paths(self):
        """A body with only the v2.6 fields must be enough — no KeyError, no empty output path."""
        body = self._body()
        for path_key in ("inputS3AssetFilePath", "outputS3AssetFilesPath", "outputS3AssetPreviewPath",
                         "outputS3AssetMetadataPath", "inputOutputS3AssetAuxiliaryFilesPath",
                         "assetId", "databaseId"):
            assert path_key not in body
        _mod, resp, payload = self._run(body=body)
        assert resp["statusCode"] == 200
        assert all(payload[k] for k in (
            "inputS3AssetFilePath", "outputS3AssetFilesPath", "outputS3AssetPreviewPath",
            "outputS3AssetMetadataPath", "inputOutputS3AssetAuxiliaryFilesPath"))

    def test_model_defaults_to_the_pipeline_default(self):
        """Asserted against the module constant rather than a literal, so the two cannot drift."""
        mod, resp, payload = self._run(config={})
        assert resp["statusCode"] == 200
        assert payload["model"] == mod.DEFAULT_MODEL

    def test_model_comes_from_the_input_configuration(self):
        _mod, resp, payload = self._run(config={"MODEL": "TripoSR"})
        assert resp["statusCode"] == 200
        assert payload["model"] == "triposr"

    def _multi_asset_envelope(self):
        """A v2 grouped envelope for a workflow that selected SEVERAL assets, each carrying its own
        standing model. Only the group belonging to this run's input image may supply the value."""
        return {
            "schemaVersion": 2,
            "databases": [{"databaseId": "dbM",
                           "metadata": {"IMAGE_TO_3D_MODEL": "step1x3d"}}],
            "assets": [
                {"databaseId": "dbM", "assetId": "otherA", "assetData": {"assetName": "Bracket"},
                 "files": [{"fileKey": "/", "metadata": {"IMAGE_TO_3D_MODEL": "triposr"}}]},
                {"databaseId": "dbM", "assetId": "xidM", "assetData": {"assetName": "Mug"},
                 "files": [
                     {"fileKey": "/", "metadata": {"IMAGE_TO_3D_MODEL": "sam3d"}},
                     {"fileKey": "/photos/mug.png", "metadata": {}, "attributes": {"width": "1024"}},
                 ]},
                {"databaseId": "dbM", "assetId": "thirdA", "assetData": {},
                 "files": [{"fileKey": "/", "metadata": {}}]},
            ],
        }

    def test_a_multi_asset_envelope_resolves_only_through_the_run_subject(self):
        """Without a subject the envelope is ambiguous — resolve_input_setting's own projection
        refuses to guess between three asset groups and yields "". Projecting for the resolved
        manifest first is what keeps the fallback alive, so the two paths are contrasted directly
        against the real helper."""
        mod = self._load()
        envelope = self._multi_asset_envelope()
        resolved = {"databaseId": "dbM", "assetId": "xidM",
                    "inputFiles": [{"relativePath": "/photos/mug.png"}]}

        # The raw envelope: ambiguous, so the asset-metadata fallback finds nothing.
        assert mod.manifestHelper.resolve_input_setting(
            {}, envelope, ("MODEL",), "IMAGE_TO_3D_MODEL") == ""
        # Projected for this run's subject: the converted asset's own value.
        view = mod.manifestHelper.run_vams_view(envelope, resolved)
        assert mod.manifestHelper.resolve_input_setting(
            {}, view, ("MODEL",), "IMAGE_TO_3D_MODEL") == "sam3d"

    def test_projecting_a_projected_view_again_is_a_no_op(self):
        """resolve_input_setting projects a GROUPED envelope internally, keyed off schemaVersion +
        'assets'. A projected view carries neither, so passing one is not a double projection: the
        internal step is skipped and the view is read as-is."""
        mod = self._load()
        resolved = {"databaseId": "dbM", "assetId": "xidM",
                    "inputFiles": [{"relativePath": "/photos/mug.png"}]}
        view = mod.manifestHelper.run_vams_view(self._multi_asset_envelope(), resolved)
        assert set(view) == {"VAMS"}
        assert mod.manifestHelper.resolve_input_setting(
            {}, mod.manifestHelper.run_vams_view(view, resolved),
            ("MODEL",), "IMAGE_TO_3D_MODEL") == "sam3d"

    def test_database_metadata_is_reachable_from_the_projected_view(self):
        """The projection emits the fifth scope, so a standing per-database value stays reachable
        for a caller that asks for it."""
        mod = self._load()
        resolved = {"databaseId": "dbM", "assetId": "xidM",
                    "inputFiles": [{"relativePath": "/photos/mug.png"}]}
        view = mod.manifestHelper.run_vams_view(self._multi_asset_envelope(), resolved)
        assert view["VAMS"]["databaseMetadata"] == {"IMAGE_TO_3D_MODEL": "step1x3d"}
        assert mod.manifestHelper.resolve_input_setting(
            {}, view, ("MODEL",), "IMAGE_TO_3D_MODEL",
            metadata_scopes=("databaseMetadata",)) == "step1x3d"

    def test_unknown_model_is_rejected(self):
        mod, resp, payload = self._run(config={"MODEL": "not-a-model"})
        assert resp["statusCode"] == 500
        assert "not-a-model" in json.loads(resp["body"])["message"]
        assert payload is None

    def test_unknown_model_fails_the_waiting_task(self):
        mod = self._load()
        s3 = _s3_reading(self._manifest(), {"MODEL": "not-a-model"})
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock()), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"
        assert send_failure.call_args.kwargs["error"] == "ImageTo3dPipelineError"

    def test_multi_file_manifest_is_rejected(self):
        """enforce_single_input_file must run: this pipeline converts one photo per execution."""
        mod = self._load()
        manifest = self._manifest()
        manifest["inputFiles"] = manifest["inputFiles"] * 2
        s3 = _s3_reading(manifest, {}, {})
        invoke = MagicMock()
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert "single input file" in json.loads(resp["body"])["message"]
        invoke.assert_not_called()
        send_failure.assert_called_once()

    def test_enforce_single_input_file_is_called(self):
        mod = self._load()
        s3 = _s3_reading(self._manifest(), {}, {})
        enforce = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock(return_value={"StatusCode": 200})), \
                patch.object(mod.manifestHelper, "enforce_single_input_file", enforce):
            mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        enforce.assert_called_once()

    def _chained_manifest(self):
        """What a refine step receives from a reconstruction step that ran before it: the same model
        as .glb, .obj and .stl, the OBJ's texture sidecar, and -- via PASS_THROUGH_INPUTS -- the
        photograph. Five files, ONE starting mesh."""
        base = {"assetId": "xidM", "databaseId": "dbM", "assetRootS3Key": "xidM/"}
        manifest = self._manifest()
        manifest["inputFiles"] = [
            {"bucket": "abkt", "key": "xidM/user_supplied.png", "relativePath": "/user_supplied.png", **base},
            {"bucket": "abkt", "key": "xidM/user_supplied.glb", "relativePath": "/user_supplied.glb", **base},
            {"bucket": "abkt", "key": "xidM/user_supplied.obj", "relativePath": "/user_supplied.obj", **base},
            {"bucket": "abkt", "key": "xidM/user_supplied.stl", "relativePath": "/user_supplied.stl", **base},
            {"bucket": "abkt", "key": "xidM/material_0.png", "relativePath": "/material_0.png", **base},
        ]
        return manifest

    def test_select_starting_meshes_collapses_same_stem_and_prefers_the_gltf(self):
        """The lambda's own narrowing, byte-for-byte the container's preference: three formats of one
        model are one starting mesh (the glTF), two distinct stems stay two."""
        mod = self._load()
        assert mod.select_starting_meshes(
            ["a/user_supplied.obj", "a/user_supplied.stl", "a/user_supplied.glb"]) == ["a/user_supplied.glb"]
        assert mod.select_starting_meshes(["a/x.glb", "a/y.stl"]) == ["a/x.glb", "a/y.stl"]

    def test_a_chained_step_output_in_several_formats_is_one_starting_mesh(self):
        """The two-step workflow's refine step is handed the reconstruction's whole output. The count
        check runs in this lambda BEFORE the container starts, so it must collapse the formats of one
        model the same way the container does, or a chained refine is refused before it begins. The
        live run failed exactly here: 'At most one mesh may be supplied as a starting point; got 3.'"""
        _mod, resp, payload = self._run(manifest=self._chained_manifest(), config={"MODEL": "blenderagent"})
        assert resp["statusCode"] == 200
        assert payload is not None
        assert any(k.endswith("user_supplied.glb") for k in payload["inputS3AssetFilePaths"])

    def test_two_distinct_meshes_are_still_refused(self):
        """Narrowing collapses formats of one model, not genuinely different models: two stems still
        exceed the one-mesh limit and are refused before the instance is provisioned."""
        manifest = self._chained_manifest()
        manifest["inputFiles"].append(
            {"bucket": "abkt", "key": "xidM/second_model.glb", "relativePath": "/second_model.glb",
             "assetId": "xidM", "databaseId": "dbM", "assetRootS3Key": "xidM/"})
        _mod, resp, payload = self._run(manifest=manifest, config={"MODEL": "blenderagent"})
        assert resp["statusCode"] == 500
        assert "At most one mesh" in json.loads(resp["body"])["message"]
        assert payload is None

    def _files_manifest(self, *names):
        """A manifest holding the named files of the one asset, in that order."""
        manifest = self._manifest()
        manifest["inputFiles"] = [
            {"bucket": "abkt", "key": f"xidM/{name}", "relativePath": f"/{name}", "assetId": "xidM",
             "databaseId": "dbM", "assetRootS3Key": "xidM/"} for name in names]
        return manifest

    def _refused(self, manifest, config):
        """Run the handler expecting a refusal: nothing is invoked, the waiting task is failed, and the message
        is returned."""
        mod = self._load()
        invoke, send_failure = MagicMock(), MagicMock()
        with patch.object(mod, "s3_client", _s3_reading(manifest, config)), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        send_failure.assert_called_once()
        return json.loads(resp["body"])["message"]

    @pytest.mark.parametrize("mode", ["refit", "refit-agent", " Refit-Agent "])
    def test_a_refit_with_a_mesh_and_its_photograph_is_forwarded(self, mode):
        _mod, resp, payload = self._run(manifest=self._files_manifest("cat.png", "cat.glb"),
                                        config={"MODEL": "blenderagent", "BLENDERAGENT_MODE": mode})
        assert resp["statusCode"] == 200 and payload is not None

    @pytest.mark.parametrize("mode", ["refit", "refit-agent"])
    @pytest.mark.parametrize("names, missing", [
        (("cat.png",), "no mesh"), (("cat.glb",), "no photograph"),
        # A texture sidecar handed on beside the mesh is not a photograph, as the container partitions it.
        (("cat.glb", "material_0.png"), "no photograph")], ids=["photograph only", "mesh only", "mesh and sidecar"])
    def test_a_refit_without_what_it_fits_is_refused_before_the_instance_is_provisioned(self, mode, names, missing):
        message = self._refused(self._files_manifest(*names),
                                {"MODEL": "blenderagent", "BLENDERAGENT_MODE": mode})
        assert f"BLENDERAGENT_MODE {mode}" in message and f"supplied {missing}." in message, message

    def test_an_unknown_blender_agent_mode_is_refused_naming_the_modes_there_are(self):
        message = self._refused(self._files_manifest("cat.png", "cat.glb"),
                                {"MODEL": "blenderagent", "BLENDERAGENT_MODE": "Scripted"})
        assert "agent, refit, refit-agent" in message and "'scripted'" in message, message

    @pytest.mark.parametrize("config", [{}, {"BLENDERAGENT_MODE": ""}, {"BLENDERAGENT_MODE": " Agent "}],
                             ids=["absent", "blank", "agent"])
    def test_the_agent_alone_may_still_tidy_a_mesh_with_no_photograph(self, config):
        _mod, resp, payload = self._run(manifest=self._files_manifest("cat.glb"),
                                        config={"MODEL": "blenderagent", **config})
        assert resp["statusCode"] == 200 and payload is not None

    def test_the_mode_is_read_only_for_the_blender_agent(self):
        _mod, resp, payload = self._run(config={"MODEL": "sam3d", "BLENDERAGENT_MODE": "scripted"})
        assert resp["statusCode"] == 200 and payload["model"] == "sam3d"

    def test_missing_input_file_is_rejected(self):
        """A manifest whose first entry yields no s3://bucket/key leaves the input empty; the
        container would die slicing the key on assetId."""
        mod, resp, payload = self._run(manifest={"inputFiles": [], "outputs": {}})
        assert resp["statusCode"] == 500
        assert "No input image resolved" in json.loads(resp["body"])["message"]
        assert payload is None

    def test_missing_asset_id_is_rejected(self):
        manifest = self._manifest()
        manifest["inputFiles"][0].pop("assetId")
        mod, resp, payload = self._run(manifest=manifest)
        assert resp["statusCode"] == 500
        assert "No assetId resolved" in json.loads(resp["body"])["message"]
        assert payload is None

    def test_a_malformed_input_configuration_fails_the_run(self):
        """A configuration that cannot be parsed must not read as an empty one: the run would use
        the pipeline defaults, report SUCCESS, and silently drop every setting the operator chose."""
        mod = self._load()
        s3 = MagicMock()
        s3.get_object.side_effect = [
            {"Body": MagicMock(read=lambda: json.dumps(self._manifest()).encode("utf-8"))},
            {"Body": MagicMock(read=lambda: b"{not json")},
        ]
        invoke = MagicMock()
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", invoke), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert "not valid JSON" in json.loads(resp["body"])["message"]
        invoke.assert_not_called()
        send_failure.assert_called_once()

    def test_missing_task_token_errors(self):
        body = self._body()
        body.pop("TaskToken")
        _mod, resp, _payload = self._run(body=body)
        assert resp["statusCode"] == 500

    def test_failure_after_token_fails_the_task(self):
        mod = self._load()
        s3 = _s3_reading(self._manifest(), {}, {})
        send_failure = MagicMock()
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock(side_effect=Exception("Throttled"))), \
                patch.object(mod.sfn_client, "send_task_failure", send_failure):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500
        assert send_failure.call_args.kwargs["taskToken"] == "tok-123"

    def test_callback_failure_does_not_mask_the_error(self):
        mod = self._load()
        s3 = _s3_reading(self._manifest(), {}, {})
        with patch.object(mod, "s3_client", s3), \
                patch.object(mod.lambda_client, "invoke", MagicMock(side_effect=Exception("Throttled"))), \
                patch.object(mod.sfn_client, "send_task_failure",
                             MagicMock(side_effect=Exception("TaskTimedOut"))):
            resp = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        assert resp["statusCode"] == 500


# ============================ openPipeline ============================

@pytest.mark.unit
class TestOpenPipeline:
    def _load(self):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")

    def _event(self, key="xidM/photos/mug.png"):
        return {
            "model": "trellis2",
            "inputS3AssetFilePath": f"s3://abkt/{key}",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/metadata/",
            "outputS3AssetResultsPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/results/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/imageTo3d/E1/",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "assetId": "xidM",
            "databaseId": "dbM",
            "sfnExternalTaskToken": "tok-123",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }

    def _mock_start(self):
        import datetime

        # A fresh response per call: the handler formats startDate in place, so a shared dict would
        # break a second invocation.
        def _response(**_kwargs):
            return {
                "executionArn": "arn:aws:states:us-east-1:1:execution:ImageTo3dPipeline:ImageTo3dJob_x",
                "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
            }

        return MagicMock(side_effect=_response)

    def _run(self, event):
        mod = self._load()
        start = self._mock_start()
        send_failure = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_failure", send_failure), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            resp = mod.lambda_handler(event, MagicMock())
        return mod, resp, start, send_failure

    @pytest.mark.parametrize("key", [
        "xidM/photos/mug.png", "xidM/mug.jpg", "xidM/mug.jpeg",
        "xidM/MUG.PNG", "xidM/MUG.JPG", "xidM/MUG.JPEG",
    ])
    def test_accepts_supported_image_extensions(self, key):
        _mod, resp, start, _sf = self._run(self._event(key))
        assert resp["statusCode"] == 200
        start.assert_called_once()

    @pytest.mark.parametrize("key", [
        "xidM/anim.gif",           # a plausible image type this pipeline cannot consume
        "xidM/photo.jp",           # near-miss: a substring of ".jpg" must not pass
        "xidM/scan.zip",
        "xidM/noextension",
    ])
    def test_rejects_unsupported_extensions(self, key):
        _mod, resp, start, send_failure = self._run(self._event(key))
        assert resp["statusCode"] == 400
        start.assert_not_called()
        send_failure.assert_called_once()

    def test_rejects_a_folder_input(self):
        _mod, resp, start, _sf = self._run(self._event("xidM/photos/"))
        assert resp["statusCode"] == 400
        start.assert_not_called()

    def test_starts_the_state_machine_with_the_renamed_token_and_a_job_name(self):
        _mod, resp, start, _sf = self._run(self._event())
        assert resp["statusCode"] == 200
        job_name = start.call_args.kwargs["name"]
        assert job_name.startswith("ImageTo3dJob_")
        sfn_input = json.loads(start.call_args.kwargs["input"])
        assert sfn_input["jobName"] == job_name
        assert sfn_input["externalSfnTaskToken"] == "tok-123"
        assert "sfnExternalTaskToken" not in sfn_input
        assert sfn_input["model"] == "trellis2"
        assert sfn_input["assetId"] == "xidM"
        assert sfn_input["databaseId"] == "dbM"
        assert sfn_input["outputS3AssetFilesPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/files/"
        assert sfn_input["outputS3AssetPreviewPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/previews/"
        assert sfn_input["outputS3AssetMetadataPath"] == "s3://abkt/pipelines/imageTo3d/JOB/output/E1/metadata/"
        assert sfn_input["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert sfn_input["inputConfigurationS3Location"] == "s3://abkt/.../config.json"

    def test_job_names_do_not_collide_within_a_second(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(self._event(), MagicMock())
            mod.lambda_handler(self._event(), MagicMock())
        names = [call.kwargs["name"] for call in start.call_args_list]
        assert names[0] != names[1]

    def test_job_names_are_distinct_at_millisecond_concurrency(self):
        """One upload can fan out to several simultaneous runs of this same pipeline, so the name
        must survive a frozen clock: the random suffix is what separates them, not the stamp."""
        mod = self._load()
        start = self._mock_start()
        import datetime as _dt

        class _FrozenDateTime(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 1, 12, 0, 0, 123000)

        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()), \
                patch.object(mod.datetime, "datetime", _FrozenDateTime):
            for _ in range(40):
                mod.lambda_handler(self._event(), MagicMock())
        names = [call.kwargs["name"] for call in start.call_args_list]
        assert len(set(names)) == len(names)

    def test_job_name_is_a_legal_step_functions_execution_name(self):
        """Step Functions caps an execution name at 80 characters and rejects ':' and '/'."""
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock()):
            mod.lambda_handler(self._event(), MagicMock())
        name = start.call_args.kwargs["name"]
        assert len(name) <= 80
        assert ":" not in name and "/" not in name

    def test_registers_sub_execution(self):
        mod = self._load()
        start = self._mock_start()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        entry = put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["executionArn"].endswith("ImageTo3dJob_x")
        assert detail["logs"][0]["logGroupName"] == "/aws/vendedlogs/VAMSstateMachine-ImageTo3dPipeline"

    def test_registers_the_run_models_batch_state_as_a_container_log_source(self, monkeypatch):
        monkeypatch.setenv("BATCH_JOB_LOG_GROUP_NAME", "/aws/batch/job")
        monkeypatch.setenv("BATCH_JOB_LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:/aws/batch/job")
        monkeypatch.setenv("BATCH_JOB_DEFINITION_NAME_TRELLIS2", "ImageTo3dJobDef-trellis2-abc")
        monkeypatch.setenv("BATCH_JOB_DEFINITION_NAME_SAM3D", "ImageTo3dJobDef-sam3d-abc")
        mod = self._load()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", self._mock_start()), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["label"] == "Image to 3D processing"
        state_machine, container = detail["logs"]
        assert state_machine["sourceType"] == "stateMachine"
        assert state_machine["label"] == "Image to 3D state machine"
        # Only the state the run is routed to: the event names trellis2, so sam3d's is not registered.
        assert container == {
            "logGroupArn": "arn:aws:logs:us-east-1:1:log-group:/aws/batch/job",
            "logGroupName": "/aws/batch/job",
            "logStreamName": "",
            "logStreamPrefix": "ImageTo3dJobDef-trellis2-abc/default/",
            "stageName": "ImageTo3dBatchJob-trellis2",
            "sourceType": "batch",
            "label": "ImageTo3dBatchJob-trellis2 container",
        }
        # The values the registration handler validates: the colon-form log group ARN, and a stream
        # prefix with no ':' or '*'.
        assert ":log-group:/aws/batch/job" in container["logGroupArn"]
        assert ":" not in container["logStreamPrefix"] and "*" not in container["logStreamPrefix"]

    def test_a_model_with_no_job_definition_registers_no_container_source(self, monkeypatch):
        monkeypatch.setenv("BATCH_JOB_LOG_GROUP_NAME", "/aws/batch/job")
        monkeypatch.delenv("BATCH_JOB_DEFINITION_NAME_TRELLIS2", raising=False)
        mod = self._load()
        put_events = MagicMock()
        with patch.object(mod.sfn, "start_execution", self._mock_start()), \
                patch.object(mod.events_client, "put_events", put_events):
            mod.lambda_handler(self._event(), MagicMock())
        detail = json.loads(put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert [entry["sourceType"] for entry in detail["logs"]] == ["stateMachine"]

    def test_every_model_has_a_batch_state_name(self):
        mod = self._load()
        assert set(mod.BATCH_STATE_NAMES) == {
            "sam3d", "stable3dgen", "trellis2", "triposr", "step1x3d", "hunyuan3d", "blenderagent"}
        for model, state in mod.BATCH_STATE_NAMES.items():
            assert state == f"ImageTo3dBatchJob-{model}"

    def test_registration_failure_never_fails_pipeline(self):
        mod = self._load()
        start = self._mock_start()
        with patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.events_client, "put_events", MagicMock(side_effect=Exception("AccessDenied"))):
            resp = mod.lambda_handler(self._event(), MagicMock())
        assert resp["statusCode"] == 200


    def test_forwards_every_output_path_it_was_given(self):
        """This lambda enumerates the output paths explicitly, so one added upstream is dropped here
        unless it is named -- which is exactly what happened to the results prefix: vamsExecute
        forwarded it and constructPipeline read it, and this hop in between lost it silently, leaving
        the container with an empty results target and its report written into asset metadata."""
        _mod, _resp, start, _fail = self._run(self._event())
        payload = json.loads(start.call_args.kwargs["input"])
        for key in ("outputS3AssetFilesPath", "outputS3AssetPreviewPath",
                    "outputS3AssetMetadataPath", "outputS3AssetResultsPath"):
            assert payload.get(key), f"{key} was not forwarded to the state machine"

    def test_forwards_the_bucket_regions(self):
        event = self._event()
        event.update(inputBucketRegion="eu-west-1", outputBucketRegion="us-east-2")
        _mod, _resp, start, _fail = self._run(event)
        payload = json.loads(start.call_args.kwargs["input"])
        assert payload["inputBucketRegion"] == "eu-west-1"
        assert payload["outputBucketRegion"] == "us-east-2"

    def test_forwards_the_whole_view_list_for_a_multi_view_run(self):
        """The same hop lost the view list, and the symptom was worse than an empty target.

        vamsExecute resolved four views and constructPipeline was ready to read them; this lambda did not
        name the field, so the container received one view, ran the SINGLE-view checkpoint, and returned a
        result BIT-IDENTICAL to a single-view control -- same genus, same face count, same fill. A run
        that silently reconstructs from a quarter of its input and succeeds is the failure mode this whole
        class of test exists for; only a deliberate control run distinguished it from success.
        """
        event = self._event()
        views = [f"s3://in-bucket/asset/cat_{name}.png"
                 for name in ("front", "left", "back", "right")]
        event["inputS3AssetFilePaths"] = views
        _mod, resp, start, _fail = self._run(event)
        assert resp["statusCode"] == 200
        payload = json.loads(start.call_args.kwargs["input"])
        assert payload.get("inputS3AssetFilePaths") == views, (
            "the view list was dropped at this hop, which reconstructs from one view and looks successful")

    def test_a_view_of_an_unsupported_type_is_refused_here(self):
        """Every view is gated, not just the primary: an unreadable second view would otherwise fail
        inside the container, on a GPU instance, after the model had loaded."""
        event = self._event()
        event["inputS3AssetFilePaths"] = ["s3://in-bucket/asset/cat_front.png",
                                          "s3://in-bucket/asset/cat_left.txt"]
        _mod, resp, start, send_failure = self._run(event)
        assert resp["statusCode"] == 400
        assert not start.called, "no state machine may start with an unusable view"
        # The workflow task is waiting on a token, so a rejection has to report against it or the task
        # stays RUNNING for its full timeout -- hours, on this pipeline.
        assert send_failure.called, "the rejection was not reported against the task token"


# ============================ constructPipeline ============================

@pytest.mark.unit
# ============================ constructPipeline ============================


class TestConstructPipeline:
    def _load(self):
        if "constructPipeline" in sys.modules:
            return importlib.reload(sys.modules["constructPipeline"])
        return importlib.import_module("constructPipeline")

    def _event(self, **overrides):
        event = {
            "jobName": "ImageTo3dJob_20260101_000000_abc123",
            "model": "trellis2",
            "inputS3AssetFilePath": "s3://abkt/xidM/photos/mug.png",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/files/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/imageTo3d/JOB/output/E1/metadata/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/imageTo3d/E1",
            "inputMetadataS3Location": "s3://abkt/.../metadata.json",
            "inputConfigurationS3Location": "s3://abkt/.../config.json",
            "assetId": "xidM",
            "databaseId": "dbM",
            "externalSfnTaskToken": "tok-123",
        }
        event.update(overrides)
        return event

    def test_returns_the_model_key_discriminator_and_a_definition_list(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(), MagicMock())
        assert set(out) == {"jobName", "modelKey", "definition", "inputMetadataS3Location",
                            "inputConfigurationS3Location", "externalSfnTaskToken", "status"}
        assert out["modelKey"] == "trellis2"
        assert out["status"] == "STARTING"
        assert out["definition"][:2] == ["python", "__main__.py"]

    def test_definition_carries_the_results_prefix_for_the_execution(self):
        """A report written here is read onto the execution rather than into the asset."""
        mod = self._load()
        out = mod.lambda_handler(self._event(
            outputS3AssetResultsPath="s3://abkt/pipelines/imageTo3d/JOB/output/E1/results/"),
            MagicMock())
        definition = json.loads(out["definition"][2])
        assert definition["outputResults"] == {
            "bucketName": "abkt", "objectDir": "pipelines/imageTo3d/JOB/output/E1/results/"}

    def test_a_missing_results_prefix_is_empty_rather_than_absent(self):
        """A direct invocation carries no workflow context; the container falls back to asset
        metadata, so the key must exist and be empty rather than be missing."""
        mod = self._load()
        out = mod.lambda_handler(self._event(), MagicMock())
        definition = json.loads(out["definition"][2])
        assert definition["outputResults"] == {"bucketName": "", "objectDir": ""}

    def test_definition_carries_the_asset_id_and_output_dirs(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(), MagicMock())
        definition = json.loads(out["definition"][2])
        assert isinstance(definition, dict)
        assert definition["assetId"] == "xidM"
        assert definition["databaseId"] == "dbM"
        assert definition["model"] == "trellis2"
        assert definition["inputFile"] == {
            "bucketName": "abkt", "objectKey": "xidM/photos/mug.png", "fileExtension": ".png"}
        assert definition["outputFiles"] == {
            "bucketName": "abkt", "objectDir": "pipelines/imageTo3d/JOB/output/E1/files/"}
        assert definition["outputMetadata"] == {
            "bucketName": "abkt", "objectDir": "pipelines/imageTo3d/JOB/output/E1/metadata/"}
        # A prefix delivered without a trailing slash is normalized so the container can append.
        assert definition["temporaryFiles"] == {
            "bucketName": "aux", "objectDir": "pipelines/imageTo3d/E1/"}
        assert definition["inputMetadataS3Location"] == "s3://abkt/.../metadata.json"
        assert definition["inputConfigurationS3Location"] == "s3://abkt/.../config.json"

    def test_definition_maps_each_asset_bucket_outside_the_deployment_region(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(
            inputS3AssetFilePaths=["s3://abkt/xidM/photos/mug.png", "s3://abkt/xidM/photos/mug_back.png"],
            outputS3AssetFilesPath="s3://runbkt/pipelines/imageTo3d/JOB/output/E1/files/",
            outputS3AssetMetadataPath="s3://runbkt/pipelines/imageTo3d/JOB/output/E1/metadata/",
            outputS3AssetResultsPath="s3://runbkt/pipelines/imageTo3d/JOB/output/E1/results/",
            inputBucketRegion="eu-west-1", outputBucketRegion="us-east-2"), MagicMock())
        definition = json.loads(out["definition"][2])
        # The auxiliary bucket is the deployment's own and is never mapped.
        assert definition["bucketRegions"] == {"abkt": "eu-west-1", "runbkt": "us-east-2"}

    def test_buckets_in_the_deployment_region_are_left_out(self):
        mod = self._load()
        definition = json.loads(mod.lambda_handler(self._event(), MagicMock())["definition"][2])
        assert definition["bucketRegions"] == {}

    def test_the_container_never_sees_the_task_token(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(), MagicMock())
        definition = json.loads(out["definition"][2])
        assert "externalSfnTaskToken" not in definition
        # The token still rides the state machine payload for pipelineEnd.
        assert out["externalSfnTaskToken"] == "tok-123"

    def test_absent_output_metadata_stays_empty(self):
        mod = self._load()
        out = mod.lambda_handler(self._event(outputS3AssetMetadataPath=""), MagicMock())
        definition = json.loads(out["definition"][2])
        assert definition["outputMetadata"] == {"bucketName": "", "objectDir": ""}

    def test_falls_back_to_an_input_adjacent_output_dir(self):
        """Only a direct/local invocation arrives with no workflow output path."""
        mod = self._load()
        out = mod.lambda_handler(self._event(outputS3AssetFilesPath=""), MagicMock())
        definition = json.loads(out["definition"][2])
        assert definition["outputFiles"] == {
            "bucketName": "abkt", "objectDir": "xidM/photos/mug/genAi/imageTo3d/"}


# ============================ pipelineEnd ============================

@pytest.mark.unit
class TestPipelineEnd:
    def _load(self):
        if "pipelineEnd" in sys.modules:
            return importlib.reload(sys.modules["pipelineEnd"])
        return importlib.import_module("pipelineEnd")

    def test_sends_success_when_there_is_no_error(self):
        mod = self._load()
        event = {"jobName": "ImageTo3dJob_x", "externalSfnTaskToken": "tok-123"}
        success, failure = MagicMock(), MagicMock()
        with patch.object(mod.sfn, "send_task_success", success), \
                patch.object(mod.sfn, "send_task_failure", failure):
            out = mod.lambda_handler(event, MagicMock())
        failure.assert_not_called()
        assert success.call_args.kwargs["taskToken"] == "tok-123"
        assert json.loads(success.call_args.kwargs["output"]) == {"status": "Pipeline Success"}
        assert out == event

    def test_sends_failure_when_the_batch_catch_recorded_an_error(self):
        mod = self._load()
        event = {
            "jobName": "ImageTo3dJob_x",
            "externalSfnTaskToken": "tok-123",
            "error": {"Error": "States.TaskFailed", "Cause": "Essential container in task exited"},
        }
        success, failure = MagicMock(), MagicMock()
        with patch.object(mod.sfn, "send_task_success", success), \
                patch.object(mod.sfn, "send_task_failure", failure):
            out = mod.lambda_handler(event, MagicMock())
        success.assert_not_called()
        assert failure.call_args.kwargs["taskToken"] == "tok-123"
        assert failure.call_args.kwargs["error"] == "States.TaskFailed"
        assert failure.call_args.kwargs["cause"] == "Essential container in task exited"
        assert out == event

    def test_truncates_an_oversized_error_and_cause(self):
        mod = self._load()
        event = {
            "externalSfnTaskToken": "tok-123",
            "error": {"Error": "E" * 500, "Cause": "C" * 40000},
        }
        failure = MagicMock()
        with patch.object(mod.sfn, "send_task_failure", failure):
            mod.lambda_handler(event, MagicMock())
        assert len(failure.call_args.kwargs["error"]) == 256
        assert len(failure.call_args.kwargs["cause"]) == 32000

    def test_no_token_sends_nothing(self):
        mod = self._load()
        success, failure = MagicMock(), MagicMock()
        with patch.object(mod.sfn, "send_task_success", success), \
                patch.object(mod.sfn, "send_task_failure", failure):
            out = mod.lambda_handler({"externalSfnTaskToken": ""}, MagicMock())
        success.assert_not_called()
        failure.assert_not_called()
        assert out == {"externalSfnTaskToken": ""}
