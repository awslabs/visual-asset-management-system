#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Consistency checks on EVERY cosmos reason vamsSchema bundle.

openPipeline hard-rejects an input whose extension is outside ALLOWED_INPUT_FILEEXTENSIONS (failing
the execution via a task-failure callback), so no bundle may advertise an extension the lambda
refuses. A template's configBody is the run's user-editable knob set, so every key must be one the
container or the vamsExecute lambda reads, and every tag the operator is asked for must reach the
body — the model type and size are fixed per registered pipeline (openPipeline supplies modelType;
the Batch job definition supplies MODEL_SIZE), so they are not config keys.

Each check counts the files it validated and asserts that count, because a bundle-validation test
whose directory resolves to nothing passes while measuring nothing."""

import os
import re
import sys
import json
import types
import importlib
import importlib.util
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# Every bundle shipped under this vamsSchema root. Named rather than globbed so an added bundle
# fails here until it is brought into these checks.
_BUNDLES = ("reason-2b", "reason-8b")

# Keys read from the fetched input configuration: INVALIDATE_COSMOS_MODELS by the container,
# PROMPT / prompt by the vamsExecute lambda.
_CONSUMED_CONFIG_KEYS = {"INVALIDATE_COSMOS_MODELS", "PROMPT", "prompt"}

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

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
    "STATE_MACHINE_ARN": "arn:aws:states:us-east-1:1:stateMachine:CosmosReason",
}.items():
    os.environ.setdefault(_k, _v)


def _load_template_tags():
    """The backend's canonical system tag names, loaded by file path rather than imported as a
    package: this is a pipeline lambda test whose sys.path is the lambda directory, and putting the
    backend package root on sys.path would shadow module names for every other test in the session.
    templateTags itself imports nothing, so loading it standalone is safe. Called from the one test
    that needs it rather than at import time, because this directory is copied into the pipeline's
    Lambda code asset where no repo root is reachable."""
    path = os.path.normpath(os.path.join(
        _LAMBDA_DIR, "..", "..", "..", "..", "..", "..",
        "backend", "backend", "common", "workflows", "templateTags.py"))
    spec = importlib.util.spec_from_file_location("_vamsTemplateTags", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SYSTEM_TAG_NAMES, module.METADATA_DYNAMIC_TAG_PREFIX


def _load(*parts):
    with open(os.path.join(_SCHEMA_ROOT, *parts), encoding="utf-8") as handle:
        return json.load(handle)


def _template_files():
    """Every top-level templates/*.json in every bundle -- the files the registration construct
    uploads and the importer reads."""
    found = []
    for bundle in _BUNDLES:
        template_dir = os.path.join(_SCHEMA_ROOT, bundle, "templates")
        for name in sorted(os.listdir(template_dir)):
            if name.endswith(".json"):
                found.append((bundle, os.path.join(template_dir, name)))
    return found


def _reload_open_pipeline():
    if "openPipeline" in sys.modules:
        return importlib.reload(sys.modules["openPipeline"])
    return importlib.import_module("openPipeline")


def _extension_set(raw):
    return {ext.strip() for ext in raw.split(",") if ext.strip()}


def _lambda_allowed_extensions():
    """openPipeline's accepted extension set. The deployed value comes from the CDK
    (ALLOWED_INPUT_FILEEXTENSIONS); this reads whatever the module resolves, so it covers the
    module default too."""
    return _extension_set(_reload_open_pipeline().ALLOWED_INPUT_FILEEXTENSIONS)


def _module_default_extensions(monkeypatch):
    """openPipeline's accepted set with no env override in force, i.e. the module default itself.
    Measured by removing the variable and reloading rather than by reading the source, and so
    independent of whether a peer test module set it first -- test_manifest_refactor.py sets it at
    import, and pytest decides which module loads first."""
    monkeypatch.delenv("ALLOWED_INPUT_FILEEXTENSIONS", raising=False)
    return _extension_set(_reload_open_pipeline().ALLOWED_INPUT_FILEEXTENSIONS)


def _construct_allowed_extensions():
    """The allow-list the CDK sets on this pipeline's lambdas -- the source of truth the module
    default has to track. Read out of the construct rather than restated here, so this file adds no
    further copy of the value: one place holds it, one place consumes it, and this compares them.
    Returns None when the construct is unreachable, which is the case in the Lambda code-asset
    copies of this directory."""
    path = os.path.normpath(os.path.join(
        _LAMBDA_DIR, "..", "..", "..", "..", "..", "..",
        "infra", "lib", "nestedStacks", "pipelines", "genAi", "nvidia", "cosmos",
        "constructs", "cosmosReason-construct.ts"))
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        found = re.findall(r"""const\s+allowedInputFileExtensions\s*=\s*["']([^"']*)["']""",
                           handle.read())
    # The parse must not fail open. A reformatted construct, or the literal moved into a config
    # field, has to read as a broken check rather than as agreement with nothing.
    assert len(found) == 1, \
        f"expected exactly one allowedInputFileExtensions literal in the construct, found {found}"
    assert found[0].strip(), "the construct's allowedInputFileExtensions literal is empty"
    return _extension_set(found[0])


@pytest.mark.unit
class TestEveryReasonBundle:
    def test_every_bundle_ships_exactly_one_template(self):
        found = _template_files()
        assert len(found) == len(_BUNDLES) == 2, f"found {len(found)} templates: {found}"
        assert sorted(bundle for bundle, _ in found) == sorted(_BUNDLES)

    def test_the_module_default_tracks_the_cdk_allow_list(self, monkeypatch):
        # A deployed lambda always receives the CDK value, so the module default governs only a
        # direct or local invoke -- where a broader default accepts a file the container dies on
        # after a GPU has been provisioned. The expected value is derived from the construct, so a
        # deliberate widening of both stays green while either one drifting alone turns red.
        expected = _construct_allowed_extensions()
        if expected is None:
            pytest.skip("cosmosReason-construct.ts is not reachable from this copy of the tests")
        default = _module_default_extensions(monkeypatch)
        assert default == expected, (
            f"openPipeline's module default {sorted(default)} has drifted from the construct's "
            f"{sorted(expected)}; restore image support in all four places together or in none")

    def test_no_bundle_advertises_an_extension_the_lambda_refuses(self):
        # openPipeline rejects anything outside its set, so an extension advertised here but refused
        # there is a run that fails on arrival. The reverse direction (the lambda accepting more than
        # the bundles offer) is deliberate: the CDK narrows the lambda to video only.
        allowed = _lambda_allowed_extensions()
        offenders, checked = {}, []
        for bundle in _BUNDLES:
            allow = _load(bundle, "pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
            refused = sorted({ext.lstrip("*") for ext in allow} - allowed)
            if refused:
                offenders[bundle] = refused
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 bundles: {checked}"
        assert not offenders, f"pipeline filters advertise extensions openPipeline refuses: {offenders}"

    def test_trigger_filter_matches_the_pipeline_filter(self):
        offenders, checked = {}, []
        for bundle in _BUNDLES:
            pipeline_allow = _load(bundle, "pipeline.json")["systemConfig"]["inputFileFilters"]["allow"]
            trigger_allow = _load(bundle, "workflow.json")["triggers"][0]["inputFileFilters"]["allow"]
            if sorted(trigger_allow) != sorted(pipeline_allow):
                offenders[bundle] = {"trigger": sorted(trigger_allow),
                                     "pipeline": sorted(pipeline_allow)}
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 bundles: {checked}"
        assert not offenders, f"trigger filter diverges from the pipeline filter: {offenders}"

    def test_configBody_keys_are_all_consumed(self):
        offenders, checked = {}, []
        for bundle, path in _template_files():
            with open(path, encoding="utf-8") as handle:
                config = json.loads(json.load(handle)["configBody"])
            unread = sorted(set(config) - _CONSUMED_CONFIG_KEYS)
            if unread:
                offenders[bundle] = unread
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 templates: {checked}"
        assert not offenders, f"configBody keys nothing reads: {offenders}"

    def test_every_declared_tag_is_referenced_by_the_body(self):
        # A declared tag the body never references renders a form field that reaches no pipeline.
        offenders, checked = {}, []
        for bundle, path in _template_files():
            with open(path, encoding="utf-8") as handle:
                template = json.load(handle)
            referenced = {m.group(1) for m in _PLACEHOLDER.finditer(template["configBody"])}
            unused = sorted({tag["tagKey"] for tag in template.get("tagSchema", [])} - referenced)
            if unused:
                offenders[bundle] = unused
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 templates: {checked}"
        assert not offenders, f"tagSchema declares tags the configBody never uses: {offenders}"

    def test_every_body_placeholder_is_declared_or_a_system_tag(self):
        # The reverse direction: an undeclared placeholder renders literally into the config the
        # container reads.
        system_tags, metadata_prefix = _load_template_tags()
        offenders, checked = {}, []
        for bundle, path in _template_files():
            with open(path, encoding="utf-8") as handle:
                template = json.load(handle)
            declared = {tag["tagKey"] for tag in template.get("tagSchema", [])}
            undeclared = sorted(
                m.group(1) for m in _PLACEHOLDER.finditer(template["configBody"])
                if m.group(1) not in declared and m.group(1) not in system_tags
                and not m.group(1).startswith(metadata_prefix))
            if undeclared:
                offenders[bundle] = undeclared
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 templates: {checked}"
        assert not offenders, f"configBody references undeclared tags: {offenders}"

    def test_each_template_is_its_pipeline_default(self):
        # requireTemplate with no default forces every caller to name a templateId.
        offenders, checked = [], []
        for bundle, path in _template_files():
            with open(path, encoding="utf-8") as handle:
                if json.load(handle).get("isDefault") is not True:
                    offenders.append(bundle)
            checked.append(bundle)
        assert len(checked) == 2, f"validated {len(checked)} of 2 templates: {checked}"
        assert not offenders, f"bundles whose only template is not the default: {offenders}"
