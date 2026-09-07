#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline rejects an unusable run setting before it starts the state machine.

Cosmos 3's numeric settings reach a run as free text -- an asset-metadata value (`COSMOS3_SEED`,
`COSMOS3_GUIDANCE`, `COSMOS3_NUM_FRAMES`) or a hand-edited configuration body, since every shipped
template sets `allowCustomEdit: true` -- and the container is where they are coerced. That coercion
runs after the state machine has submitted the Batch job, so an L40S/H100 has been provisioned and a
multi-gigabyte image pulled before `COSMOS3_NUM_FRAMES = "189 frames"` is discovered. The same is true
of `COSMOS3_CONTROL_PATH`, whose bucket allowlist only the container can evaluate but whose SHAPE is
knowable here: an asset-relative value reaches `aws s3 cp` as a local path.

openPipeline already states this rule for `assetId` and the output path -- "gating them at launch turns
that into an immediate failure carrying the real reason instead of a paid-for job that dies on
startup". These settings follow it.

The gate must accept exactly what the container accepts, so the last test in
TestTheGateAgreesWithTheContainer runs the container's own `parse_number_setting` against the same
values. The container half of the pair is pinned separately by
container/tests/test_numeric_setting_validation.py; the control-path allowlist by
container/tests/test_control_path_allowlist.py.
"""

import ast
import datetime
import importlib
import math
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONTAINER_MAIN = os.path.normpath(
    os.path.join(_LAMBDA_DIR, "..", "container", "__main__.py"))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:Cosmos3",
    "ALLOWED_INPUT_FILEEXTENSIONS": ".mp4,.mov,.jpg,.jpeg,.png,.webp",
    "ORCHESTRATION_BUS_NAME": "vams-orchestration",
    # vamsExecuteCosmos3Pipeline reads this at module import, and the agreement test below loads it.
    # Set here rather than relying on a sibling module that happens to import earlier.
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
}.items():
    os.environ.setdefault(_k, _v)


def _load(name):
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    return importlib.import_module(name)


def _event(**overrides):
    """A text2video run: no input file, prompt supplied, every gate the handler already had satisfied."""
    event = {
        "modelVariant": "nano",
        "taskMode": "text2video",
        "cosmosPrompt": "A drone shot.",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/cosmos3/E1/",
        "sfnExternalTaskToken": "tok-123",
        "assetId": "xidM",
        "databaseId": "dbM",
    }
    event.update(overrides)
    return event


def _transfer_event(**overrides):
    event = _event(taskMode="transfer", inputS3AssetFilePath="s3://abkt/xidM/clip.mp4",
                   cosmosControlType="edge")
    event.update(overrides)
    return event


def _mock_start():
    return MagicMock(return_value={
        "executionArn": "arn:aws:states:us-east-1:1:execution:Cosmos3:cosmos3-nano-x",
        "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0),
    })


def _invoke(event):
    """Run the handler with Step Functions and EventBridge patched; returns (response, start, fail)."""
    mod = _load("openPipeline")
    start = _mock_start()
    fail = MagicMock()
    with patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.sfn, "send_task_failure", fail), \
            patch.object(mod.events_client, "put_events", MagicMock()):
        response = mod.lambda_handler(event, MagicMock())
    return response, start, fail


def _assert_rejected_at_launch(event, expected_in_message):
    response, start, fail = _invoke(event)
    assert response["statusCode"] == 400, response
    assert expected_in_message in response["body"]["message"], response["body"]["message"]
    # No state machine means no Batch job, which is the whole point: the GPU is never provisioned.
    start.assert_not_called()
    # The workflow's callback token is released with the reason, rather than left pending to timeout.
    assert fail.call_count == 1


def _assert_started(event):
    response, start, _ = _invoke(event)
    assert response["statusCode"] == 200, response
    assert start.call_count == 1
    return start


def _container_parse_number_setting():
    """The container's own coercion, lifted out of container/__main__.py by name.

    Importing that module is not an option -- it is a container entry point whose imports and
    module-level constants belong to the GPU image -- so the one function is compiled on its own. A
    rename or a move fails this loudly, which is the correct signal for a test whose subject is that
    the two implementations agree.
    """
    with open(_CONTAINER_MAIN, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "parse_number_setting":
            namespace = {"math": math}
            exec(compile(ast.Module(body=[node], type_ignores=[]),  # nosec B102 - repo source
                         _CONTAINER_MAIN, "exec"), namespace)
            return namespace["parse_number_setting"]
    raise AssertionError(f"{_CONTAINER_MAIN} declares no parse_number_setting")


@pytest.mark.unit
class TestNumericSettingsAreGatedBeforeTheStateMachineStarts:
    @pytest.mark.parametrize("value", ["189 frames", "ninety", "93px", "1,5"])
    def test_a_non_numeric_frame_count_is_rejected(self, value):
        _assert_rejected_at_launch(_event(cosmosNumFrames=value), "cosmosNumFrames")

    def test_a_fractional_frame_count_is_rejected(self):
        # Truncating 3.9 to 3 would generate a video nobody asked for and report success.
        _assert_rejected_at_launch(_event(cosmosNumFrames="3.9"), "cosmosNumFrames")

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_a_frame_count_below_one_is_rejected(self, value):
        _assert_rejected_at_launch(_event(cosmosNumFrames=value), "cosmosNumFrames")

    def test_a_non_numeric_seed_is_rejected(self):
        _assert_rejected_at_launch(_event(cosmosSeed="random"), "cosmosSeed")

    def test_a_fractional_seed_is_rejected(self):
        _assert_rejected_at_launch(_event(cosmosSeed="12.5"), "cosmosSeed")

    def test_a_comma_decimal_guidance_is_rejected(self):
        # The European decimal comma is the realistic typo, and float("7,5") raises.
        _assert_rejected_at_launch(_event(cosmosGuidance="7,5"), "cosmosGuidance")

    def test_a_boolean_setting_is_rejected(self):
        # A JSON `true` from a hand-edited configuration body would otherwise read as 1.
        _assert_rejected_at_launch(_event(cosmosNumFrames=True), "cosmosNumFrames")

    def test_an_infinite_guidance_is_rejected(self):
        _assert_rejected_at_launch(_event(cosmosGuidance="inf"), "cosmosGuidance")


@pytest.mark.unit
class TestTheGateDoesNotNarrowWhatAlreadyWorked:
    """The positive controls. Every rejection above is only meaningful if the runs an operator
    actually makes still start."""

    def test_a_run_supplying_none_of_them_starts(self):
        _assert_started(_event())

    def test_blank_strings_mean_not_supplied_and_still_start(self):
        _assert_started(_event(cosmosSeed="", cosmosGuidance="", cosmosNumFrames="",
                               cosmosControlWeight="", cosmosControlGuidance="",
                               cosmosControlPath=""))

    def test_valid_free_text_numbers_start(self):
        start = _assert_started(_event(cosmosSeed="42", cosmosGuidance="7.5", cosmosNumFrames="93"))
        # The values travel on unchanged -- this is a gate, not a coercion, so the container remains
        # the single place the numbers are parsed.
        import json as _json
        sent = _json.loads(start.call_args.kwargs["input"])
        assert sent["cosmosNumFrames"] == "93"
        assert sent["cosmosSeed"] == "42"

    def test_typed_template_tag_values_start(self):
        # A `"type": "integer"` tag substitutes as a bare JSON number, so the value arrives as an int.
        _assert_started(_event(cosmosNumFrames=93, cosmosSeed=0, cosmosGuidance=7.5))

    def test_a_negative_seed_and_guidance_start(self):
        # Only the frame count carries a minimum; a seed or guidance may legitimately be negative.
        _assert_started(_event(cosmosSeed="-7", cosmosGuidance="-1.5"))


@pytest.mark.unit
class TestTheControlPathShapeIsGatedForATransferRun:
    def test_an_asset_relative_control_path_is_rejected(self):
        # The form four of the six shipped templates described. `aws s3 cp controls/edge.mp4 ...` is a
        # local-to-local copy of a file that does not exist.
        _assert_rejected_at_launch(
            _transfer_event(cosmosControlPath="controls/edge.mp4"), "cosmosControlPath")

    @pytest.mark.parametrize("value", ["s3://abkt", "s3://abkt/", "s3://abkt/prefix/",
                                       "/xidM/controls/edge.mp4", "https://abkt/edge.mp4"])
    def test_a_value_that_is_not_an_object_uri_is_rejected(self, value):
        _assert_rejected_at_launch(_transfer_event(cosmosControlPath=value), "cosmosControlPath")

    def test_a_bad_entry_anywhere_in_the_aligned_list_is_rejected(self):
        # The value is comma-aligned to the control types, so the second entry matters as much as the
        # first.
        _assert_rejected_at_launch(
            _transfer_event(cosmosControlType="edge,blur",
                            cosmosControlPath="s3://abkt/xidM/edge.mp4,blur.mp4"),
            "cosmosControlPath")

    def test_a_full_uri_starts_the_run(self):
        # The positive control, and the owner's ruling: a full URI is the supported form, allowlisted
        # to the deployment's own buckets by the container.
        _assert_started(_transfer_event(cosmosControlPath="s3://abkt/xidM/controls/edge.mp4"))

    def test_a_blank_entry_means_auto_compute_and_starts(self):
        _assert_started(_transfer_event(cosmosControlType="edge,blur",
                                        cosmosControlPath="s3://abkt/xidM/edge.mp4,"))

    def test_a_foreign_bucket_is_left_to_the_container(self):
        # Deliberately NOT rejected here: the allowlist is the set of the deployment's own asset
        # buckets, which this lambda cannot resolve, and duplicating a partial copy of it would
        # reject runs the deployment permits. The container rejects it before the model restore.
        _assert_started(_transfer_event(cosmosControlPath="s3://someone-elses-bucket/private.mp4"))

    def test_a_non_transfer_run_is_not_gated_on_the_control_settings(self):
        # The container consumes the control settings only for a transfer run, so a standing
        # COSMOS3_CONTROL_PATH on an asset must not block an unrelated text2video run.
        _assert_started(_event(cosmosControlPath="controls/edge.mp4",
                               cosmosControlWeight="heavy",
                               cosmosControlGuidance="high"))

    def test_a_transfer_request_on_a_variant_without_transfer_is_not_gated(self):
        # The container downgrades transfer to the variant's default mode with a warning rather than
        # failing, so the settings are unused and must not be gated.
        assert "super-image2video" not in _load("openPipeline").TRANSFER_CAPABLE_VARIANTS
        _assert_started(_event(modelVariant="super-image2video", taskMode="transfer",
                               inputS3AssetFilePath="s3://abkt/xidM/frame.png",
                               cosmosControlPath="controls/edge.mp4"))


@pytest.mark.unit
class TestTheControlNumericSettingsAreGatedForATransferRun:
    def test_a_non_numeric_control_weight_entry_is_rejected(self):
        _assert_rejected_at_launch(
            _transfer_event(cosmosControlType="edge,blur", cosmosControlWeight="1.0,heavy"),
            "cosmosControlWeight")

    def test_a_non_numeric_control_guidance_is_rejected(self):
        _assert_rejected_at_launch(
            _transfer_event(cosmosControlGuidance="high"), "cosmosControlGuidance")

    def test_an_aligned_list_of_numbers_starts(self):
        _assert_started(_transfer_event(cosmosControlType="edge,blur",
                                        cosmosControlWeight="1.0,0.5",
                                        cosmosControlGuidance="1.5"))


@pytest.mark.unit
class TestTheGateAgreesWithTheContainer:
    def test_the_transfer_capable_variants_match_vams_execute(self):
        open_pipeline = _load("openPipeline")
        vams_execute = _load("vamsExecuteCosmos3Pipeline")
        assert (set(open_pipeline.TRANSFER_CAPABLE_VARIANTS)
                == set(vams_execute.TRANSFER_CAPABLE_VARIANTS))

    @pytest.mark.parametrize("value,integer,minimum", [
        ("", False, None), (None, False, None), ("  ", False, None),
        ("42", True, None), ("-7", True, None), ("7.5", False, None), (93, True, 1), (0, True, None),
        ("189 frames", True, None), ("random", True, None), ("7,5", False, None),
        ("3.9", True, None), ("0", True, 1), ("-1", True, 1), (True, True, None), ("inf", False, None),
    ])
    def test_the_two_implementations_accept_and_reject_the_same_values(self, value, integer, minimum):
        """A value the gate rejects but the container would have accepted is a run the operator can no
        longer make; a value the gate accepts but the container rejects is the defect unfixed."""
        mod = _load("openPipeline")
        gate_error = mod.numeric_setting_error(value, "SETTING", integer=integer, minimum=minimum)

        container_parse = _container_parse_number_setting()
        try:
            container_parse(value, "SETTING", 0, integer=integer, minimum=minimum)
            container_rejected = False
        except ValueError:
            container_rejected = True

        assert bool(gate_error) == container_rejected, (value, gate_error, container_rejected)
