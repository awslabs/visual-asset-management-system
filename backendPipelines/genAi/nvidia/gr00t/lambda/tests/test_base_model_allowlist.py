#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FIX-053: ``baseModelPath`` must be checked against an allowlist before the GR00T job is launched.

The value reaches the container as ``BASE_MODEL_PATH`` and is handed straight to HuggingFace's
``from_pretrained``, which DOWNLOADS the named repository into the shared EFS ``HF_HOME`` cache and
loads it. ``vamsExecuteGr00tFinetunePipeline`` accepts whatever the asset's ``GROOT_BASE_MODEL_PATH``
metadata or the execute-time input configuration says, so anyone able to set asset metadata can point
a GPU job at an arbitrary third-party repository and have its weights and custom code pulled into a
cache every later run reads from.

Three things about WHERE the check goes are load-bearing, and each has its own test:

*   **It must validate the MERGED value.** ``baseModelPath`` is merged from two sources with different
    precedence — asset metadata first (``GROOT_BASE_MODEL_PATH``), then the input configuration, which
    overrides it. Validating each source separately lets a metadata value through whenever the
    configuration omits the key, and wrongly rejects a metadata value the configuration has replaced.
*   **A rejection must RAISE, not return a 4xx.** This pipeline runs with a Step Functions task-token
    callback; the ``except`` arm is what calls ``send_task_failure``. An early
    ``{'statusCode': 400}`` return leaves the workflow task waiting for its (hours-long) taskTimeout
    while the user sees RUNNING rather than FAILED.
*   **The allowlist must not be too restrictive.** A LOCAL/EFS path is a legitimate value — the
    container defaults to and accepts one (``finetune_gr00t.py`` ``BASE_MODEL_PATH`` default,
    ``inference.py``, ``__main__.py``), the EFS HuggingFace cache lives at
    ``/mnt/efs/gr00t-models/hf_cache``, and an evaluation run scores a checkpoint from a previous
    ``gr00tOutput_*`` run. ``tests/test_manifest_refactor.py`` already asserts a merged
    ``baseModelPath`` of ``/m/base``, and both shipped templates must stay runnable.

The acceptance tests below are the control that stops an allowlist that rejects everything — a
reject-by-default fix satisfies every rejection test perfectly while making the pipeline unusable, and
that failure mode is visible nowhere else.
"""

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

# vamsExecute reads these at import time (boto3 clients + module-level env).
for k, v in {
    "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
}.items():
    os.environ.setdefault(k, v)

import vamsExecuteGr00tFinetunePipeline  # noqa: E402,F401

TASK_TOKEN = "tok-fix053"

# The vamsSchema bundle the deployment registers. Read from the shipped files so a template edit that
# breaks the allowlist is caught here rather than at deploy time.
_TEMPLATES_DIR = os.path.abspath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema", "templates"))


def _shipped_template_config(template_id):
    """The rendered config body of a shipped template, with its ``{{TAG}}`` placeholders filled the
    way the execute form would fill them."""
    with open(os.path.join(_TEMPLATES_DIR, f"{template_id}.json"), encoding="utf-8") as handle:
        template = json.load(handle)
    body = template["configBody"]
    for placeholder, value in {
        "{{CHECKPOINT_FOLDER}}": "gr00tOutput_N1.5-3B_20260101T000000_abcd1234",
        "{{EVAL_TRAJECTORIES}}": "5",
        "{{EVAL_STEPS}}": "150",
    }.items():
        body = body.replace(placeholder, value)
    return json.loads(body)


class _Harness:
    """Drives vamsExecuteGr00tFinetunePipeline with a manifest, asset metadata and an input
    configuration, and reports whether the run launched or was rejected."""

    def _load(self):
        if "vamsExecuteGr00tFinetunePipeline" in sys.modules:
            return importlib.reload(sys.modules["vamsExecuteGr00tFinetunePipeline"])
        return importlib.import_module("vamsExecuteGr00tFinetunePipeline")

    def _body(self):
        return {
            "TaskToken": TASK_TOKEN,
            "inputManifestS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "executingUserName": "user@x",
        }

    def _manifest(self):
        return {
            "inputFiles": [{"bucket": "abkt", "key": "xidM/asset/", "assetId": "xidM",
                            "databaseId": "dbM", "assetRootS3Key": "xidM/"}],
            "outputs": {"bucket": "abkt", "files": "pipelines/p1/MJOB/output/E1/files/"},
            "auxBucket": "aux",
            "auxTempPrefix": "pipelines/gr00t/E1/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "systemConfig": {"orchestrationBusArn": "arn:bus",
                             "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
        }

    def _s3(self, asset_metadata, config):
        manifest = self._manifest()
        envelope = {"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": asset_metadata}}}

        def get_object(Bucket, Key):  # noqa: N803 - boto3 kwarg names
            if Key.endswith("manifest.json"):
                body = json.dumps(manifest).encode("utf-8")
            elif Key.endswith("metadata.json"):
                body = json.dumps(envelope).encode("utf-8")
            elif Key.endswith("config.json"):
                body = json.dumps(config).encode("utf-8")
            else:
                raise Exception(f"unexpected key {Key}")
            return {"Body": MagicMock(read=lambda b=body: b)}

        s3 = MagicMock()
        s3.get_object.side_effect = get_object
        return s3

    def run(self, asset_metadata=None, config=None):
        """Returns (response, invoke mock, sfn mock). ``invoke`` is the openPipeline launch."""
        mod = self._load()
        invoke = MagicMock(return_value={"StatusCode": 200})
        sfn = MagicMock()
        with patch.object(mod, "s3_client", self._s3(asset_metadata or {}, config or {})), \
                patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", invoke):
            response = mod.lambda_handler({"body": json.dumps(self._body())}, MagicMock())
        return response, invoke, sfn

    def merged_config(self, invoke):
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        return json.loads(payload["gr00tConfig"])

    def assert_launched(self, response, invoke, sfn):
        assert response["statusCode"] == 200, response
        assert invoke.call_count == 1, "the pipeline should have launched"
        sfn.send_task_failure.assert_not_called()

    def assert_rejected(self, response, invoke, sfn):
        """A rejection must stop the launch AND report the task token."""
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1, (
            "the rejection must raise inside the handler's try so the except arm fails the task "
            "token; an early 4xx return hangs the workflow task until its taskTimeout")
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == TASK_TOKEN
        assert response["statusCode"] == 500, response


# ============================ rejection: the finding ============================

@pytest.mark.unit
class TestDisallowedBaseModelIsRejected(_Harness):

    def test_rejected_when_supplied_only_via_asset_metadata(self):
        """FIX-053: the real vector. The input configuration omits baseModelPath, so the value comes
        from the asset's own GROOT_BASE_MODEL_PATH metadata — a validator that only looks at the input
        configuration passes this test's happy path and misses this entirely."""
        response, invoke, sfn = self.run(
            asset_metadata={"GROOT_BASE_MODEL_PATH": "attacker/evil-model"},
            config={"mode": "finetune", "datasetPath": "dataset"})
        self.assert_rejected(response, invoke, sfn)

    def test_rejected_when_supplied_via_input_configuration(self):
        """FIX-053: the execute-time input configuration (a template's rendered config body) is the
        second source and overrides asset metadata, so it needs the same check."""
        response, invoke, sfn = self.run(
            asset_metadata={},
            config={"mode": "finetune", "datasetPath": "dataset",
                    "baseModelPath": "attacker/evil-model"})
        self.assert_rejected(response, invoke, sfn)

    def test_rejected_for_a_url_shaped_value(self):
        """FIX-053: a URL is neither an allowlisted repo id nor a local path."""
        response, invoke, sfn = self.run(
            asset_metadata={},
            config={"mode": "finetune", "baseModelPath": "https://evil.example.com/model.tar"})
        self.assert_rejected(response, invoke, sfn)

    def test_rejected_when_the_configuration_overrides_an_allowed_metadata_value(self):
        """FIX-053: the MERGED value is what reaches the container. An allowed value on the asset must
        not launder a disallowed value supplied at execute time."""
        response, invoke, sfn = self.run(
            asset_metadata={"GROOT_BASE_MODEL_PATH": "nvidia/GR00T-N1.5-3B"},
            config={"mode": "finetune", "baseModelPath": "attacker/evil-model"})
        self.assert_rejected(response, invoke, sfn)

    def test_rejected_in_evaluate_mode_too(self):
        """FIX-053: one pipeline hosts both templates (mode finetune and mode evaluate), and the
        evaluate path loads the base model as well — the check has to run on both."""
        config = _shipped_template_config("gr00t-evaluate-default")
        config["baseModelPath"] = "attacker/evil-model"
        response, invoke, sfn = self.run(asset_metadata={}, config=config)
        self.assert_rejected(response, invoke, sfn)


# ============================ acceptance: the over-restriction control ============================

@pytest.mark.unit
class TestAllowedBaseModelStillLaunches(_Harness):
    """Controls for FIX-053. A reject-by-default allowlist satisfies every rejection test above while
    making the pipeline unrunnable, and that failure mode is only visible here."""

    def test_nvidia_repo_is_accepted(self):
        """FIX-053: the minimum allowlist entry the owner required — the `nvidia/` prefix."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": "nvidia/GR00T-N1.5-3B"})
        self.assert_launched(response, invoke, sfn)
        assert self.merged_config(invoke)["baseModelPath"] == "nvidia/GR00T-N1.5-3B"

    @pytest.mark.parametrize("local_path", [
        "/m/base",                                                      # asserted by test_manifest_refactor
        "/mnt/efs/gr00t-models/hf_cache",                               # the EFS HuggingFace cache
        "/opt/ml/input/gr00tOutput_N1.5-3B_20260101T000000_abcd1234",    # a previous run's checkpoint
    ])
    def test_local_and_efs_paths_are_accepted(self, local_path):
        """FIX-053: an absolute local/EFS path is a legitimate base model — the container defaults to
        and accepts one, and an evaluation scores a checkpoint from a previous gr00tOutput_* run. An
        `nvidia/`-only allowlist would break the evaluate template and the existing tests."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": local_path})
        self.assert_launched(response, invoke, sfn)
        assert self.merged_config(invoke)["baseModelPath"] == local_path

    def test_absent_base_model_is_accepted(self):
        """FIX-053: an omitted baseModelPath is valid — the container falls back to its own
        `nvidia/GR00T-N1.5-3B` default, so requiring the field would break plain runs."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "datasetPath": "dataset"})
        self.assert_launched(response, invoke, sfn)
        assert "baseModelPath" not in self.merged_config(invoke)

    def test_metadata_value_replaced_by_an_allowed_configuration_value_is_accepted(self):
        """FIX-053: only the MERGED value matters. A disallowed standing value on the asset that the
        execute-time configuration replaces with an allowed one must NOT be rejected — validating each
        source separately would reject a run that never uses the bad value."""
        response, invoke, sfn = self.run(
            asset_metadata={"GROOT_BASE_MODEL_PATH": "attacker/evil-model"},
            config={"mode": "finetune", "baseModelPath": "nvidia/GR00T-N1.5-3B"})
        self.assert_launched(response, invoke, sfn)
        assert self.merged_config(invoke)["baseModelPath"] == "nvidia/GR00T-N1.5-3B"

    @pytest.mark.parametrize("template_id", ["gr00t-finetune-default", "gr00t-evaluate-default"])
    def test_shipped_templates_stay_runnable(self, template_id):
        """FIX-053: both templates in the registered vamsSchema bundle must pass validation unchanged,
        read from the shipped files rather than restated here."""
        config = _shipped_template_config(template_id)
        response, invoke, sfn = self.run(asset_metadata={}, config=config)
        self.assert_launched(response, invoke, sfn)
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["mode"] == ("evaluate" if "evaluate" in template_id else "finetune")
        assert self.merged_config(invoke)["baseModelPath"] == "nvidia/GR00T-N1.5-3B"

    def test_a_leading_slash_value_is_a_local_path_not_a_repo_id(self):
        """FIX-053: classification is by SHAPE. A leading slash makes a value an absolute local path,
        never an allowlisted repo id — HuggingFace cannot fetch a repository id beginning with '/', so
        such a value downloads nothing. Rejecting every leading-slash value would break the local/EFS
        paths above, including the `/m/base` that test_manifest_refactor asserts."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": "/nvidia/GR00T-N1.5-3B"})
        self.assert_launched(response, invoke, sfn)
        assert self.merged_config(invoke)["baseModelPath"] == "/nvidia/GR00T-N1.5-3B"

    def test_blank_base_model_is_dropped_rather_than_forwarded(self):
        """FIX-053: a template that renders an empty baseModelPath leaves the container on its own
        default; forwarding "" would hand from_pretrained an empty path."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": "   "})
        self.assert_launched(response, invoke, sfn)
        assert "baseModelPath" not in self.merged_config(invoke)

    def test_owner_added_by_the_deployment_is_accepted(self, monkeypatch):
        """FIX-053: a deployment with its own mirror or internal fine-tuning base widens the allowlist
        through GR00T_ADDITIONAL_BASE_MODEL_OWNERS rather than editing the pipeline."""
        monkeypatch.setenv(
            vamsExecuteGr00tFinetunePipeline.ADDITIONAL_BASE_MODEL_OWNERS_ENV, "my-org")
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": "my-org/gr00t-base"})
        self.assert_launched(response, invoke, sfn)
        assert self.merged_config(invoke)["baseModelPath"] == "my-org/gr00t-base"


# ============================ rejection: lookalikes and traversal ============================

@pytest.mark.unit
class TestAllowlistLookalikesAreRejected(_Harness):
    """A membership test written with `in`, or a prefix test written against 'nvidia' rather than
    'nvidia/', accepts every value here while loading something other than an NVIDIA repository."""

    @pytest.mark.parametrize("base_model_path", [
        "nvidia-evil/x",                    # a different owner whose name starts with the allowed one
        "x/nvidia/y",                       # the allowed owner buried mid-path
        "attacker/nvidia",                  # the allowed owner used as the REPOSITORY name
        "//nvidia/GR00T-N1.5-3B",           # extra leading slash: neither a repo id nor a clean path
        "nvidia/../attacker/evil-model",    # traversal out of the allowed owner
        "/mnt/efs/../../attacker/model",    # traversal out of a local path
        "hf.co/nvidia/GR00T-N1.5-3B",       # host-qualified, so the owner is not the first segment
        "GR00T-N1.5-3B",                    # a bare canonical repo id, owned by nobody allowlisted
        "nvidia",                           # an owner on its own is not a repository
        "nvidia/GR00T;evil",                # punctuation no repository name carries
    ])
    def test_lookalike_and_traversal_values_are_rejected(self, base_model_path):
        """FIX-053: the value is classified by shape first (absolute path vs `owner/name`) and only
        then by owner, so a value that merely CONTAINS the allowed owner is rejected."""
        response, invoke, sfn = self.run(
            asset_metadata={}, config={"mode": "finetune", "baseModelPath": base_model_path})
        self.assert_rejected(response, invoke, sfn)

    def test_a_lookalike_from_asset_metadata_is_rejected_too(self):
        """FIX-053: the lookalikes arrive by the same two sources as any other value, and asset
        metadata is the one an ordinary user can write."""
        response, invoke, sfn = self.run(
            asset_metadata={"GROOT_BASE_MODEL_PATH": "nvidia-evil/x"},
            config={"mode": "finetune", "datasetPath": "dataset"})
        self.assert_rejected(response, invoke, sfn)


# ============================ the validator as a pure function ============================

@pytest.mark.unit
class TestValidateBaseModelPath:
    """FIX-053 at the function level, where the allowlist itself is observable."""

    def test_the_default_allowlist_is_not_empty_and_names_nvidia(self):
        """FIX-053: an empty allowlist rejects every base model, and a wide-open one validates
        nothing — both are silent failures, so the shipped default is asserted directly."""
        owners = vamsExecuteGr00tFinetunePipeline.allowed_base_model_owners()
        assert owners, "an empty allowlist would reject every base model"
        assert "nvidia" in owners

    @pytest.mark.parametrize("value", [
        "nvidia/GR00T-N1.5-3B",
        "NVIDIA/GR00T-N1.5-3B",             # owner matching is case-insensitive
        "nvidia/GR00T-N1-2B",
        "/m/base",
        "/mnt/efs/gr00t-models/hf_cache/",  # a trailing slash is a path, not a missing segment
        "",
    ])
    def test_accepted_values_are_returned_unchanged(self, value):
        assert vamsExecuteGr00tFinetunePipeline.validate_base_model_path(value) == value

    def test_an_absent_value_resolves_to_the_container_default(self):
        assert vamsExecuteGr00tFinetunePipeline.validate_base_model_path(None) == ""

    def test_surrounding_whitespace_is_stripped(self):
        assert vamsExecuteGr00tFinetunePipeline.validate_base_model_path(
            "  nvidia/GR00T-N1.5-3B\n") == "nvidia/GR00T-N1.5-3B"

    @pytest.mark.parametrize("value", [
        "attacker/evil-model",
        "nvidia-evil/x",
        "x/nvidia/y",
        "//nvidia/GR00T-N1.5-3B",
        "nvidia/../attacker/evil-model",
        "/mnt/efs/../../attacker/model",
        "/",                                # the filesystem root is not a model
        "https://evil.example.com/model.tar",
        "s3://bucket/model",
        "nvidia/GR00T;evil",
    ])
    def test_rejected_values_raise(self, value):
        with pytest.raises(Exception, match="baseModelPath"):
            vamsExecuteGr00tFinetunePipeline.validate_base_model_path(value)

    def test_a_deployment_added_owner_widens_the_allowlist(self, monkeypatch):
        monkeypatch.setenv(
            vamsExecuteGr00tFinetunePipeline.ADDITIONAL_BASE_MODEL_OWNERS_ENV, "my-org, other_org")
        validate = vamsExecuteGr00tFinetunePipeline.validate_base_model_path
        assert validate("my-org/gr00t-base") == "my-org/gr00t-base"
        assert validate("other_org/gr00t-base") == "other_org/gr00t-base"
        with pytest.raises(Exception, match="baseModelPath"):
            validate("attacker/evil-model")

    def test_a_malformed_added_owner_is_ignored_and_nvidia_survives(self, monkeypatch):
        """FIX-053: the environment list is additive, so a junk entry can neither empty the allowlist
        nor open it up."""
        monkeypatch.setenv(
            vamsExecuteGr00tFinetunePipeline.ADDITIONAL_BASE_MODEL_OWNERS_ENV, "*, ../evil, /, ")
        assert vamsExecuteGr00tFinetunePipeline.allowed_base_model_owners() == ("nvidia",)
        assert vamsExecuteGr00tFinetunePipeline.validate_base_model_path(
            "nvidia/GR00T-N1.5-3B") == "nvidia/GR00T-N1.5-3B"
