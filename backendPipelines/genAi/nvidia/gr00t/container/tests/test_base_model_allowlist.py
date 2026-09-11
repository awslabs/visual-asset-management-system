#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""FIX-053 follow-up: the CONTAINER must apply the baseModelPath allowlist too.

``vamsExecuteGr00tFinetunePipeline`` validates the two sources it can see (the asset's
``GROOT_BASE_MODEL_PATH`` metadata and the execute-time input configuration) and forwards the validated
value as ``gr00tConfig``. The container then merges a THIRD source over it — ``gr00t_config.json``,
read out of the asset itself after the S3 download — so before this check anyone able to write a file
into the asset could replace the validated value and have an arbitrary HuggingFace repository
downloaded into the shared EFS ``HF_HOME`` cache (which every later run restores from) and loaded by
``from_pretrained``. The lambda-side check alone closed nothing.

What each group of tests pins:

*   **The asset file is validated.** A third-party value in ``gr00t_config.json`` is rejected even when
    the lambda validated an allowed value, which is the exact bypass.
*   **The rejection reaches the container's failure path.** This container reports failure by RAISING:
    the exception leaves ``main()``, Python exits non-zero, the Batch job is FAILED, and the state
    machine's ``addCatch`` routes to ``pipelineEnd``, which calls ``SendTaskFailure`` with the external
    task token. A rejection swallowed by ``resolve_config``'s parse handlers — one ``logger.warning``
    and carry on — would run the rejected model; a rejection after ``run_training`` would already have
    downloaded it.
*   **No drift from the lambda copy.** The image carries only the files the Dockerfile COPYs, so the
    lambda module cannot be imported at runtime and the rule is duplicated. Two copies of a security
    rule diverging silently is the failure mode, so both the source text and the accept/reject
    behaviour are compared against the twin here.
*   **An ordinary run still proceeds.** An allowlist that rejects everything satisfies every rejection
    test while making the pipeline unusable, and the shipped templates plus a full ``main()`` run are
    where that shows.
"""

import importlib
import importlib.util
import inspect
import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

_TEMPLATES_DIR = os.path.abspath(os.path.join(_CONTAINER_DIR, "..", "vamsSchema", "templates"))


def _load_container_entrypoint():
    """The entrypoint is ``__main__.py``, which cannot be imported under that name — it belongs to the
    running interpreter. Loaded from its path under an alias, which also leaves its
    ``if __name__ == "__main__"`` guard inert so importing it does not start a pipeline."""
    spec = importlib.util.spec_from_file_location(
        "gr00t_container_entrypoint", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load_container_entrypoint()


def _load_lambda_twin():
    """The lambda-side copy of the rule, imported from the sibling ``lambda/`` directory purely so the
    two copies can be compared. Nothing in the running container does this."""
    lambda_dir = os.path.abspath(os.path.join(_CONTAINER_DIR, "..", "lambda"))
    if lambda_dir not in sys.path:
        sys.path.insert(0, lambda_dir)
    if "customLogging" not in sys.modules:
        package = types.ModuleType("customLogging")
        module = types.ModuleType("customLogging.logger")
        module.safeLogger = lambda **kwargs: MagicMock()
        package.logger = module
        sys.modules["customLogging"] = package
        sys.modules["customLogging.logger"] = module
    for key, value in {
        "OPEN_PIPELINE_FUNCTION_NAME": "test-open-pipeline",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
    }.items():
        os.environ.setdefault(key, value)
    return importlib.import_module("vamsExecuteGr00tFinetunePipeline")


try:
    _lambda_twin = _load_lambda_twin()
    _lambda_twin_error = None
except Exception as exc:  # surfaced by the drift test, rather than failing this file's collection
    _lambda_twin = None
    _lambda_twin_error = exc


def _shipped_template_config(template_id):
    """The rendered config body of a shipped template, with its ``{{TAG}}`` placeholders filled the way
    the execute form would fill them."""
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


def _resolve(tmp_path, asset_file_config=None, lambda_config=None):
    """Run the container's config resolution the way ``main()`` does: the lambda's validated
    ``gr00tConfig`` first, then the asset's own ``gr00t_config.json`` merged over it."""
    if asset_file_config is not None:
        (tmp_path / "gr00t_config.json").write_text(
            json.dumps(asset_file_config), encoding="utf-8")
    definition = {"gr00tConfig": json.dumps(lambda_config or {})}
    return container.resolve_config(definition, tmp_path)


# ============================ rejection: the asset's own config file ============================

class TestAssetConfigFileIsValidated:
    """The file lives in the asset, so it is writable by anyone who can upload to the asset."""

    def test_a_third_party_repo_in_the_asset_config_file_is_rejected(self, tmp_path):
        with pytest.raises(Exception, match="baseModelPath"):
            _resolve(tmp_path, asset_file_config={"baseModelPath": "attacker/evil-model"})

    def test_it_is_rejected_even_after_the_lambda_validated_an_allowed_value(self, tmp_path):
        """The bypass. The lambda checked its own sources and forwarded `nvidia/GR00T-N1.5-3B`; the
        asset file overrides it afterwards, so validating only in the lambda closes nothing."""
        with pytest.raises(Exception, match="baseModelPath"):
            _resolve(tmp_path,
                     lambda_config={"baseModelPath": "nvidia/GR00T-N1.5-3B"},
                     asset_file_config={"baseModelPath": "attacker/evil-model"})

    def test_a_disallowed_value_from_the_lambda_config_is_rejected_here_too(self, tmp_path):
        """Defence in depth: an older or hand-invoked lambda that forwards an unchecked value must not
        get it loaded either. This is the half that makes the container's check independent."""
        with pytest.raises(Exception, match="baseModelPath"):
            _resolve(tmp_path, lambda_config={"baseModelPath": "attacker/evil-model"})

    def test_the_rejected_value_never_reaches_the_returned_config(self, tmp_path):
        """`resolve_config` must raise rather than return the value with a warning — everything
        downstream (`BASE_MODEL_PATH`, `from_pretrained`, the output folder name) reads the returned
        dict."""
        with pytest.raises(Exception):
            _resolve(tmp_path, asset_file_config={"baseModelPath": "attacker/evil-model"})

    def test_a_malformed_asset_config_file_fails_with_its_own_error(self, tmp_path):
        """Control for WHERE the allowlist check sits, by exception TYPE. An unreadable
        gr00t_config.json fails the run as `AssetConfigurationError`, while a value the allowlist
        rejects fails as the allowlist's own error — so the allowlist check is still reached on its own,
        outside the parse, rather than being one of the things a parse failure hides."""
        (tmp_path / "gr00t_config.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(container.AssetConfigurationError, match="gr00t_config.json"):
            container.resolve_config({"gr00tConfig": "{}"}, tmp_path)


# ============================ rejection: lookalikes ============================

class TestLookalikesAreRejected:
    """A check written with `in`, or a prefix test against 'nvidia' rather than 'nvidia/', accepts every
    value here while loading something other than an NVIDIA repository."""

    @pytest.mark.parametrize("base_model_path", [
        "nvidia-evil/x",                        # a different owner whose name starts with the allowed one
        "x/nvidia/y",                           # the allowed owner buried mid-path
        "attacker/nvidia",                      # the allowed owner used as the REPOSITORY name
        "nvidia/../attacker/evil-model",        # traversal out of the allowed owner
        "//nvidia/GR00T-N1.5-3B",               # leading slashes with an empty segment
        "/nvidia/../attacker/evil-model",       # a leading slash does not license traversal
        "/mnt/efs/../../attacker/model",        # traversal out of a local path
        "/",                                    # the filesystem root is not a model
        "https://evil.example.com/model.tar",   # a URL, not a repository id
        "s3://bucket/model",                    # likewise
        "hf.co/nvidia/GR00T-N1.5-3B",           # host-qualified, so the owner is not the first segment
        "GR00T-N1.5-3B",                        # a bare repo name, owned by nobody allowlisted
        "nvidia",                               # an owner on its own is not a repository
        "nvidia/GR00T;evil",                    # punctuation no repository name carries
    ])
    def test_lookalike_values_in_the_asset_config_file_are_rejected(self, tmp_path, base_model_path):
        with pytest.raises(Exception, match="baseModelPath"):
            _resolve(tmp_path, asset_file_config={"baseModelPath": base_model_path})


# ============================ acceptance: the over-restriction control ============================

class TestOrdinaryValuesStillResolve:
    """An allowlist that rejects everything passes every rejection test above while making the pipeline
    unrunnable, and that failure mode is visible only here."""

    def test_an_nvidia_repo_in_the_asset_config_file_is_accepted(self, tmp_path):
        config = _resolve(tmp_path, asset_file_config={"baseModelPath": "nvidia/GR00T-N1.5-3B"})
        assert config["baseModelPath"] == "nvidia/GR00T-N1.5-3B"

    def test_the_asset_file_still_overrides_the_lambda_value_when_allowed(self, tmp_path):
        """The asset file remains the highest-priority source; the allowlist narrows WHICH values it may
        carry, it does not freeze the lambda's value."""
        config = _resolve(tmp_path,
                          lambda_config={"baseModelPath": "nvidia/GR00T-N1.5-3B"},
                          asset_file_config={"baseModelPath": "nvidia/GR00T-N1-2B"})
        assert config["baseModelPath"] == "nvidia/GR00T-N1-2B"

    def test_a_plain_run_with_no_base_model_named_uses_the_container_default(self, tmp_path):
        config = _resolve(tmp_path, asset_file_config={"maxSteps": 10})
        assert config["baseModelPath"] == container.DEFAULTS["baseModelPath"]

    def test_a_blank_value_falls_back_to_the_container_default(self, tmp_path):
        """A template or asset file that renders an empty string must not hand `from_pretrained` an
        empty path, and must not fail the run either."""
        config = _resolve(tmp_path, asset_file_config={"baseModelPath": "   "})
        assert config["baseModelPath"] == container.DEFAULTS["baseModelPath"]

    @pytest.mark.parametrize("local_path", [
        # test_manifest_refactor on the lambda side already asserts a merged baseModelPath of /m/base.
        "/m/base",
        "/mnt/efs/gr00t-models/hf_cache",                              # the EFS HuggingFace cache
        "/tmp/checkpoint",                                             # where evaluation stages a checkpoint
        "/opt/ml/input/gr00tOutput_N1.5-3B_20260101T000000_abcd1234",   # a previous run's checkpoint
    ])
    def test_absolute_local_paths_are_accepted(self, tmp_path, local_path):
        """Classification is by SHAPE, and a leading slash makes a value a path read from the
        container's own filesystem — HuggingFace cannot fetch a repository id starting with '/', so such
        a value downloads nothing and needs no owner. Rejecting every leading-slash value would break
        the EFS cache, the evaluation checkpoint path and the owner's not-too-restrictive constraint."""
        config = _resolve(tmp_path, asset_file_config={"baseModelPath": local_path})
        assert config["baseModelPath"] == local_path

    @pytest.mark.parametrize("template_id", ["gr00t-finetune-default", "gr00t-evaluate-default"])
    def test_shipped_templates_stay_runnable(self, tmp_path, template_id):
        """Both templates in the registered vamsSchema bundle must survive the check, read from the
        shipped files rather than restated here."""
        rendered = _shipped_template_config(template_id)
        config = _resolve(tmp_path, lambda_config=rendered)
        assert config["baseModelPath"] == "nvidia/GR00T-N1.5-3B"

    def test_the_default_allowlist_is_not_empty_and_names_nvidia(self):
        """An empty allowlist rejects every base model and a wide-open one validates nothing — both are
        silent failures, so the shipped default is asserted directly."""
        owners = container.allowed_base_model_owners()
        assert owners, "an empty allowlist would reject every base model"
        assert "nvidia" in owners

    def test_an_owner_added_by_the_deployment_widens_the_allowlist(self, tmp_path, monkeypatch):
        """A deployment with its own mirror or internal base widens the list by environment. The
        variable has to be set on the Batch job definition as well as on the lambda, since the check now
        runs in both."""
        monkeypatch.setenv(container.ADDITIONAL_BASE_MODEL_OWNERS_ENV, "my-org")
        config = _resolve(tmp_path, asset_file_config={"baseModelPath": "my-org/gr00t-base"})
        assert config["baseModelPath"] == "my-org/gr00t-base"

    def test_a_malformed_added_owner_neither_empties_nor_opens_the_allowlist(self, monkeypatch):
        monkeypatch.setenv(container.ADDITIONAL_BASE_MODEL_OWNERS_ENV, "*, ../evil, /, ")
        assert container.allowed_base_model_owners() == ("nvidia",)
        assert container.validate_base_model_path(
            "nvidia/GR00T-N1.5-3B") == "nvidia/GR00T-N1.5-3B"


# ============================ the rejection reaches the failure path ============================

def _prepare_run(monkeypatch, tmp_path, asset_file_config=None, lambda_config=None, mode="finetune"):
    """Stage a full ``main()`` run with the AWS and GPU work stubbed out, standing in for the asset
    download that puts ``gr00t_config.json`` in place. Returns the record of what the run did."""
    input_dir = tmp_path / "input"
    (input_dir / "dataset").mkdir(parents=True)
    if asset_file_config is not None:
        (input_dir / "gr00t_config.json").write_text(
            json.dumps(asset_file_config), encoding="utf-8")

    calls = {"training": [], "uploads": []}

    def _training(**kwargs):
        # Writes a checkpoint, because that is what training does: the run refuses to upload an empty
        # output folder, so a stub that records the call without producing anything would stand in for
        # a training run that silently produced nothing.
        checkpoint = container.OUTPUT_DIR / "checkpoint-10"
        checkpoint.mkdir(parents=True, exist_ok=True)
        (checkpoint / "model.safetensors").write_text("weights", encoding="utf-8")
        calls["training"].append(kwargs)

    monkeypatch.setattr(container, "INPUT_DIR", input_dir)
    monkeypatch.setattr(container, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setenv("S3_MODEL_BUCKET", "model-cache-bucket")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(container, "load_pipeline_definition", lambda: {
        "inputS3AssetPath": "s3://abkt/xidM/",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "assetId": "xidM",
        "databaseId": "dbM",
        "gr00tConfig": json.dumps(lambda_config or {}),
        "mode": mode,
    })
    monkeypatch.setattr(container, "ensure_models_cached", lambda **kwargs: None)
    monkeypatch.setattr(container, "download_asset_from_s3", lambda *args, **kwargs: None)
    monkeypatch.setattr(container, "backup_cache_to_s3", lambda **kwargs: None)
    monkeypatch.setattr(container.manifest_io, "fetch_input_configuration",
                        lambda *args, **kwargs: {})
    monkeypatch.setattr(container, "run_training", _training)
    monkeypatch.setattr(container, "upload_output_to_s3",
                        lambda *args, **kwargs: calls["uploads"].append(args) or "s3://out/")
    return calls


class TestRejectionReachesTheFailurePath:
    """This container reports failure by raising: a non-zero exit fails the Batch job, the state
    machine's addCatch routes to pipelineEnd, and pipelineEnd calls SendTaskFailure with the external
    task token. A `sys.exit(0)`, a swallowed exception or a bare `return` would leave the workflow task
    waiting on its token for the full taskTimeout."""

    def test_a_rejected_base_model_propagates_out_of_main(self, monkeypatch, tmp_path):
        _prepare_run(monkeypatch, tmp_path,
                     asset_file_config={"baseModelPath": "attacker/evil-model"})
        with pytest.raises(Exception, match="baseModelPath"):
            container.main()

    def test_a_rejected_base_model_never_reaches_training_or_upload(self, monkeypatch, tmp_path):
        """The rejection has to land BEFORE the model is fetched. Training is what calls
        `from_pretrained`, and an upload would report a successful run with no checkpoint."""
        calls = _prepare_run(monkeypatch, tmp_path,
                             asset_file_config={"baseModelPath": "attacker/evil-model"})
        with pytest.raises(Exception):
            container.main()
        assert calls["training"] == [], "the disallowed model was handed to training anyway"
        assert calls["uploads"] == [], "a rejected run must not upload an output"

    def test_an_ordinary_run_proceeds_to_training_and_upload(self, monkeypatch, tmp_path):
        """Positive control for the whole path: an allowlist that rejects everything, or a check placed
        so that it fires on ordinary values, fails here and nowhere else."""
        calls = _prepare_run(monkeypatch, tmp_path,
                             lambda_config=_shipped_template_config("gr00t-finetune-default"),
                             asset_file_config={"maxSteps": 10})
        container.main()
        assert len(calls["training"]) == 1
        assert calls["training"][0]["config"]["baseModelPath"] == "nvidia/GR00T-N1.5-3B"
        assert len(calls["uploads"]) == 1

    def test_an_allowed_asset_file_value_still_reaches_training(self, monkeypatch, tmp_path):
        calls = _prepare_run(monkeypatch, tmp_path,
                             lambda_config={"baseModelPath": "nvidia/GR00T-N1.5-3B"},
                             asset_file_config={"baseModelPath": "nvidia/GR00T-N1-2B"})
        container.main()
        assert calls["training"][0]["config"]["baseModelPath"] == "nvidia/GR00T-N1-2B"


# ============================ no drift from the lambda copy ============================

_SHARED_RULE_VALUES = [
    "nvidia/GR00T-N1.5-3B",
    "NVIDIA/GR00T-N1.5-3B",
    "nvidia/GR00T-N1-2B",
    "/m/base",
    "/mnt/efs/gr00t-models/hf_cache/",
    "",
    "attacker/evil-model",
    "nvidia-evil/x",
    "x/nvidia/y",
    "attacker/nvidia",
    "//nvidia/GR00T-N1.5-3B",
    "nvidia/../attacker/evil-model",
    "/mnt/efs/../../attacker/model",
    "/",
    "https://evil.example.com/model.tar",
    "s3://bucket/model",
    "hf.co/nvidia/GR00T-N1.5-3B",
    "GR00T-N1.5-3B",
    "nvidia",
    "nvidia/GR00T;evil",
]


class TestNoDriftFromTheLambdaCopy:
    """The rule is duplicated because the image carries only the files the Dockerfile COPYs, so the
    lambda module is unreachable from the container. Duplication is only safe while the copies stay
    identical, which is what these tests hold."""

    def test_the_lambda_copy_is_readable(self):
        assert _lambda_twin is not None, (
            "the lambda copy of the rule could not be imported, so drift cannot be checked: "
            f"{_lambda_twin_error!r}")

    @pytest.mark.parametrize("name", ["allowed_base_model_owners", "validate_base_model_path"])
    def test_the_function_source_is_identical(self, name):
        assert _lambda_twin is not None, f"lambda copy unavailable: {_lambda_twin_error!r}"
        ours = inspect.getsource(getattr(container, name)).strip()
        theirs = inspect.getsource(getattr(_lambda_twin, name)).strip()
        assert ours == theirs, (
            f"container/__main__.py and lambda/vamsExecuteGr00tFinetunePipeline.py disagree on "
            f"{name}. Both copies of the allowlist must change together — propagate the edit rather "
            "than relaxing this test.")

    def test_the_allowlist_constants_are_identical(self):
        assert _lambda_twin is not None, f"lambda copy unavailable: {_lambda_twin_error!r}"
        assert (container.ALLOWED_BASE_MODEL_OWNERS
                == _lambda_twin.ALLOWED_BASE_MODEL_OWNERS)
        assert (container.ADDITIONAL_BASE_MODEL_OWNERS_ENV
                == _lambda_twin.ADDITIONAL_BASE_MODEL_OWNERS_ENV)
        assert (container._MODEL_PATH_SEGMENT.pattern
                == _lambda_twin._MODEL_PATH_SEGMENT.pattern)

    @pytest.mark.parametrize("value", _SHARED_RULE_VALUES)
    def test_both_copies_reach_the_same_verdict(self, value):
        """Behavioural comparison as well as textual, so a copy that is refactored rather than edited is
        still held to the same verdicts."""
        assert _lambda_twin is not None, f"lambda copy unavailable: {_lambda_twin_error!r}"

        def verdict(validate):
            try:
                return ("accepted", validate(value))
            except Exception:
                return ("rejected", None)

        assert (verdict(container.validate_base_model_path)
                == verdict(_lambda_twin.validate_base_model_path)), (
            f"the two copies of the allowlist disagree about {value!r}")
