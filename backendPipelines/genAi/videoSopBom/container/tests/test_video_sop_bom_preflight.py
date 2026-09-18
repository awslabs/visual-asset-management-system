# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preflight: the shipped schemas and prompts the container imports, the client configurations, the definition
loader, and the API-probe connectivity + disk checks that run before any download.

Run from the container directory:  python -m pytest tests/test_video_sop_bom_preflight.py -q
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

# The package root: a clobbered __init__.py fails this module at collection, loudly.
from video_sop_bom_pipeline import PROMPT_NAMES, SCHEMA_NAMES, load_prompt, load_schema  # noqa: E402

_PACKAGE = os.path.join(os.path.dirname(_HERE), "video_sop_bom_pipeline")

# The stems the container passes to load_schema / load_prompt; every one must be in the package's registry list.
REQUIRED_SCHEMAS = {
    "definition_schema", "config_schema", "timeline_schema", "frames_schema", "sop_schema",
    "lab_summary_schema", "bom_row_schema", "analysis_report_schema", "summary_schema",
    "window_extraction_schema", "vision_schema", "finalize_schema",
}
REQUIRED_PROMPTS = {"window_extraction", "vision_verification", "finalize", "system_boundary"}


class TestShippedArtefacts:
    """The container imports these; a missing one fails at container runtime, not at build."""

    def test_schema_and_prompt_names_are_the_registry_lists(self):
        assert set(SCHEMA_NAMES) >= REQUIRED_SCHEMAS, REQUIRED_SCHEMAS - set(SCHEMA_NAMES)
        assert set(PROMPT_NAMES) == REQUIRED_PROMPTS

    @pytest.mark.parametrize("name", SCHEMA_NAMES)
    def test_schema_file_exists(self, name):
        path = os.path.join(_PACKAGE, "schemas", f"{name}.json")
        assert os.path.isfile(path), f"shipped schema missing: {path}"

    @pytest.mark.parametrize("name", PROMPT_NAMES)
    def test_prompt_file_exists(self, name):
        path = os.path.join(_PACKAGE, "prompts", f"{name}.md")
        assert os.path.isfile(path), f"shipped prompt missing: {path}"

    def test_the_package_loaders_read_every_schema_and_prompt(self):
        for name in SCHEMA_NAMES:
            assert load_schema(name)["type"] == "object", name
        for name in PROMPT_NAMES:
            assert load_prompt(name).strip(), f"{name} is empty"
        with pytest.raises(KeyError):
            load_schema("video_timeline.json")  # file names are not stems

    def test_vocab_constants_exist(self):
        from video_sop_bom_pipeline import vocab

        assert len(vocab.LCA_BOM_COLUMNS) == 66
        assert vocab.LCA_BOM_COLUMNS[0] == "part_level"
        assert vocab.LCA_BOM_COLUMNS[-1] == "battery_capacity_wh"
        assert "Enclosure" in vocab.PART_TYPES
        assert "Aluminum" in vocab.MATERIAL_TYPES
        assert "Molding - Plastics" in vocab.PRIMARY_TECHNIQUES
        assert vocab.match_part_type("pcba") == "PCBA" and vocab.match_material_type("HDPE ") == "HDPE"

    def test_output_path_extension_is_vendored(self):
        from video_sop_bom_pipeline.outputPathExtension import apply_output_path_extension

        assert apply_output_path_extension("sop-bom/sop.json", "/exec-1/") == "sop-bom/exec-1/sop.json"


class TestErrorCodes:
    def test_the_six_codes_and_the_rejection_carrier(self):
        from video_sop_bom_pipeline import errors, vocab

        assert not hasattr(vocab, "ERROR_CODES"), "one owner per constant: the codes live in errors.py, never in vocab"
        assert errors.ERROR_CODES == (
            "VideoSopBomInputRejected",
            "VideoSopBomLimitExceeded",
            "VideoSopBomTranscribeFailed",
            "VideoSopBomModelOutputInvalid",
            "VideoSopBomConnectivityError",
            "VideoSopBomPipelineError",
        )
        rejection = errors.PipelineRejection(errors.LIMIT_EXCEEDED, "total video duration 5h12m exceeds this deployment's limit of 4h00m (240 minutes).")
        assert rejection.code == "VideoSopBomLimitExceeded"
        assert not rejection.cause.startswith("VideoSopBom")
        with pytest.raises(ValueError):
            errors.PipelineRejection("NotACode", "x")


class TestClientConfigs:
    """Client construction is offline (credentials are resolved at request time), so the four Config
    shapes (the Bedrock read timeout, the one-retry preflight and signal clients — max_attempts 1, two
    attempts — and the shared adaptive retry) are asserted on real clients.

    botocore rewrites `Config.retries` in place when the client is built — `max_attempts` N becomes
    `total_max_attempts` N + 1 in every mode — so the assertions read the post-construction form; the
    literal the module passes is pinned by the repository's source-text ratchet, not here."""

    def test_build_clients_applies_the_four_configs(self):
        from video_sop_bom_pipeline import clients as clients_module

        built = clients_module.build_clients("us-east-1")
        for shared in (built.s3, built.transcribe, built.sfn):
            assert shared.meta.config.retries == {"mode": "adaptive", "total_max_attempts": 6}
        assert built.bedrock.meta.config.read_timeout == 3600
        assert built.bedrock.meta.config.connect_timeout == 10
        assert built.bedrock.meta.config.retries == {"mode": "adaptive", "total_max_attempts": 3}
        for probe in (built.preflight_transcribe, built.preflight_bedrock):
            assert probe.meta.config.connect_timeout == 5
            assert probe.meta.config.retries == {"mode": "standard", "total_max_attempts": 2}
        assert built.sfn_signal.meta.config.connect_timeout == 3
        assert built.sfn_signal.meta.config.read_timeout == 5
        assert built.sfn_signal.meta.config.retries == {"mode": "standard", "total_max_attempts": 2}

    def test_each_member_is_the_right_service(self):
        from video_sop_bom_pipeline import clients as clients_module

        built = clients_module.build_clients("us-east-1")
        assert built.s3.meta.service_model.service_name == "s3"
        assert built.transcribe.meta.service_model.service_name == "transcribe"
        assert built.preflight_transcribe.meta.service_model.service_name == "transcribe"
        assert built.bedrock.meta.service_model.service_name == "bedrock-runtime"
        assert built.preflight_bedrock.meta.service_model.service_name == "bedrock-runtime"
        assert built.sfn.meta.service_model.service_name == "stepfunctions"
        assert built.sfn_signal.meta.service_model.service_name == "stepfunctions"


class TestDefinitionLoading:
    def test_loads_from_a_local_file_and_validates(self, tmp_path):
        import json

        from conftest import make_clients, make_definition
        from video_sop_bom_pipeline import definition as definition_module

        path = tmp_path / "definition.json"
        path.write_text(json.dumps(make_definition()), encoding="utf-8")
        loaded = definition_module.load_definition(str(path), make_clients())
        assert loaded["executionId"] == "exec-0001"
        assert loaded["config"]["mode"] == "full"

    def test_loads_from_an_s3_pointer(self):
        import json

        from conftest import FakeS3, make_clients, make_definition
        from video_sop_bom_pipeline import definition as definition_module

        s3 = FakeS3()
        s3.put_bytes("aux-bucket", "pipelines/genai-video-sop-bom/exec-0001/definition.json", json.dumps(make_definition()).encode("utf-8"))
        loaded = definition_module.load_definition(
            "s3://aux-bucket/pipelines/genai-video-sop-bom/exec-0001/definition.json", make_clients(s3=s3)
        )
        assert loaded["auxTempPrefix"] == "pipelines/genai-video-sop-bom/exec-0001/"

    def test_parse_s3_uri(self):
        from video_sop_bom_pipeline.definition import parse_s3_uri

        assert parse_s3_uri("s3://aux-bucket/a/b/definition.json") == ("aux-bucket", "a/b/definition.json")
        with pytest.raises(ValueError):
            parse_s3_uri("https://aux-bucket/a")
        with pytest.raises(ValueError):
            parse_s3_uri("s3://bucket-only")

    def test_an_invalid_definition_is_a_readable_pipeline_error(self):
        from conftest import make_definition
        from video_sop_bom_pipeline import definition as definition_module
        from video_sop_bom_pipeline.errors import PipelineRejection

        broken = make_definition()
        del broken["inputFiles"]
        with pytest.raises(PipelineRejection) as raised:
            definition_module.validate_definition(broken)
        assert raised.value.code == "VideoSopBomPipelineError"
        assert "definition document invalid" in raised.value.cause
        # Positive control: the unbroken document passes the same validator.
        assert definition_module.validate_definition(make_definition())["schemaVersion"] == 1

    def test_an_invalid_config_block_is_rejected_by_the_shipped_config_schema(self):
        from conftest import make_definition
        from video_sop_bom_pipeline import definition as definition_module
        from video_sop_bom_pipeline.errors import PipelineRejection

        broken = make_definition(config={"mode": "video"})
        with pytest.raises(PipelineRejection) as raised:
            definition_module.validate_definition(broken)
        assert "config" in raised.value.cause

    def test_a_full_mode_config_block_missing_a_typed_tag_is_rejected(self):
        from conftest import make_definition
        from video_sop_bom_pipeline import definition as definition_module
        from video_sop_bom_pipeline.errors import PipelineRejection

        # The shipped config_schema.json requires the three typed tags when mode is "full"; constructPipeline
        # refuses such a body first, and this second validation of the definition.json copy pins the rule.
        broken = make_definition()
        del broken["config"]["partLevelBase"]
        with pytest.raises(PipelineRejection) as raised:
            definition_module.validate_definition(broken)
        assert raised.value.cause == (
            "definition document config block invalid at <root>: 'partLevelBase' is a required property")
        # Positive control: the same document with the key present passes.
        assert definition_module.validate_definition(make_definition())["config"]["partLevelBase"] == "0"

    def test_non_json_pointer_content_is_a_readable_pipeline_error(self, tmp_path):
        from conftest import make_clients
        from video_sop_bom_pipeline import definition as definition_module
        from video_sop_bom_pipeline.errors import PipelineRejection

        path = tmp_path / "definition.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(PipelineRejection) as raised:
            definition_module.load_definition(str(path), make_clients())
        assert raised.value.code == "VideoSopBomPipelineError"
        assert "not valid JSON" in raised.value.cause


class TestConnectivityPreflight:
    def test_expected_transcribe_bad_request_and_a_converse_reply_pass(self):
        from conftest import FakeBedrock, FakeS3, FakeTranscribe, make_clients, make_definition, text_response
        from video_sop_bom_pipeline import preflight

        bedrock = FakeBedrock(lambda request, n: text_response("pong"))
        transcribe, s3 = FakeTranscribe(), FakeS3()
        preflight.check_connectivity(make_clients(s3=s3, transcribe=transcribe, bedrock=bedrock), make_definition())
        assert transcribe.get_calls == ["vams-video-sop-bom-preflight"]
        assert bedrock.calls[0]["modelId"] == "global.anthropic.claude-sonnet-5"
        assert bedrock.calls[0]["inferenceConfig"] == {"maxTokens": 1}
        assert s3.head_bucket_calls == ["aux-bucket"]

    @pytest.mark.parametrize("exc_class_name", ["ConnectTimeoutError", "EndpointConnectionError"])
    def test_transcribe_connection_errors_name_the_host_and_the_missing_endpoint(self, exc_class_name):
        """ConnectTimeoutError is a SIBLING of EndpointConnectionError under botocore ConnectionError, and the
        missing-endpoint case raises the former; catching only the latter misses it (FM-09)."""
        import botocore.exceptions

        from conftest import make_clients, make_definition
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        exc = getattr(botocore.exceptions, exc_class_name)(endpoint_url="https://transcribe.us-east-1.amazonaws.com/")

        class Raising:
            def get_transcription_job(self, **kwargs):
                raise exc

        with pytest.raises(PipelineRejection) as raised:
            preflight.check_connectivity(make_clients(preflight_transcribe=Raising()), make_definition())
        assert raised.value.code == "VideoSopBomConnectivityError"
        assert raised.value.cause == (
            "could not connect to transcribe.us-east-1.amazonaws.com from the isolated subnet (connect timeout) "
            "— the Amazon Transcribe interface endpoint is missing (app.useGlobalVpc.addVpcEndpoints)."
        )

    def test_bedrock_connect_timeout_names_the_bedrock_runtime_endpoint(self):
        from botocore.exceptions import ConnectTimeoutError

        from conftest import make_clients, make_definition
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        class Raising:
            def converse(self, **kwargs):
                raise ConnectTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/")

        with pytest.raises(PipelineRejection) as raised:
            preflight.check_connectivity(make_clients(preflight_bedrock=Raising()), make_definition())
        assert raised.value.cause == (
            "could not connect to bedrock-runtime.us-east-1.amazonaws.com from the isolated subnet (connect timeout) "
            "— the Bedrock Runtime interface endpoint is missing (app.useGlobalVpc.addVpcEndpoints)."
        )

    @pytest.mark.parametrize("code", ["AccessDeniedException", "ResourceNotFoundException", "ValidationException"])
    def test_bedrock_model_access_codes_are_readable(self, code):
        from conftest import FakeBedrock, client_error, make_clients, make_definition
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        bedrock = FakeBedrock(lambda request, n: client_error(code, "denied", "Converse"))
        with pytest.raises(PipelineRejection) as raised:
            preflight.check_connectivity(make_clients(bedrock=bedrock), make_definition())
        assert raised.value.code == "VideoSopBomConnectivityError"
        assert raised.value.cause == (
            f"Amazon Bedrock model global.anthropic.claude-sonnet-5 is not accessible from this account/Region ({code}) "
            "— enable model access in the Bedrock console."
        )

    def test_an_unexpected_transcribe_code_is_a_connectivity_error_naming_the_code(self):
        from conftest import FakeTranscribe, client_error, make_clients, make_definition
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        transcribe = FakeTranscribe(preflight_error=client_error("AccessDeniedException", "not authorized", "GetTranscriptionJob"))
        with pytest.raises(PipelineRejection) as raised:
            preflight.check_connectivity(make_clients(transcribe=transcribe), make_definition())
        assert "AccessDeniedException" in raised.value.cause and "GetTranscriptionJob" in raised.value.cause

    def test_an_unreadable_aux_bucket_is_a_connectivity_error(self):
        from conftest import FakeS3, client_error, make_clients, make_definition
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        class DeniedS3(FakeS3):
            def head_bucket(self, Bucket):
                raise client_error("403", "Forbidden", "HeadBucket")

        with pytest.raises(PipelineRejection) as raised:
            preflight.check_connectivity(make_clients(s3=DeniedS3()), make_definition())
        assert "aux-bucket" in raised.value.cause and "403" in raised.value.cause


class TestDiskPreflight:
    GIB = 2**30

    def _with_free(self, monkeypatch, free_bytes):
        import shutil

        from video_sop_bom_pipeline import preflight

        usage = shutil._ntuple_diskusage(free_bytes * 2, free_bytes, free_bytes)
        monkeypatch.setattr(preflight.shutil, "disk_usage", lambda path: usage)

    def test_the_formula_is_inputs_times_1_5_plus_2_gib(self):
        from video_sop_bom_pipeline import preflight

        assert preflight.required_bytes(4 * self.GIB) == int(4 * self.GIB * 1.5) + 2 * self.GIB

    def test_a_run_that_does_not_fit_is_refused_naming_the_figures_and_the_knob(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import preflight
        from video_sop_bom_pipeline.errors import PipelineRejection

        self._with_free(monkeypatch, 2 * self.GIB)
        with pytest.raises(PipelineRejection) as raised:
            preflight.check_disk_budget(str(tmp_path), 4 * self.GIB)
        assert raised.value.code == "VideoSopBomLimitExceeded"
        assert "GiB" in raised.value.cause
        assert "VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB" in raised.value.cause

    def test_a_run_that_fits_is_not_refused(self, monkeypatch, tmp_path):
        from video_sop_bom_pipeline import preflight

        self._with_free(monkeypatch, 100 * self.GIB)
        preflight.check_disk_budget(str(tmp_path), 4 * self.GIB)
