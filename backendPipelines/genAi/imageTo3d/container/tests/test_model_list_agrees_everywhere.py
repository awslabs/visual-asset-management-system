#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The set of models is declared in seven places and every one of them has to agree.

There is no single source of truth for the model list: the container dispatches on one table, filters
settings against another, the two Lambdas validate against their own tuples, openPipeline names each
model's Batch state for its log registration, and the CDK builds an ECR repository, a CodeBuild
project and a Batch job definition from a third. Adding a model to five
of the six is not a build failure and not a test failure -- it is a runtime rejection at the
vamsExecute boundary ("Unsupported image-to-3D model"), discovered only after an image has been built
and a job submitted. These assertions replace that feedback loop with a local one.

The default model is duplicated the same way, and a disagreement there is worse than an unknown key:
a run that names no model would dispatch to one model in the Lambda and another in the container,
which routes a job to the wrong image without any error at all.
"""

import json
import os
import re

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = os.path.dirname(CONTAINER_DIR)
LAMBDA_DIR = os.path.join(PIPELINE_DIR, "lambda")
INFRA_DIR = os.path.abspath(
    os.path.join(PIPELINE_DIR, "..", "..", "..", "infra"))

CONSTRUCTS = (
    os.path.join(INFRA_DIR, "lib", "nestedStacks", "pipelines", "genAi", "imageTo3d",
                 "constructs", "imageTo3d-construct.ts"),
    os.path.join(INFRA_DIR, "lib", "nestedStacks", "pipelines", "genAi", "imageTo3d",
                 "constructs", "imageTo3dCodeBuild-construct.ts"),
)
LAMBDAS = (
    os.path.join(LAMBDA_DIR, "constructPipeline.py"),
    os.path.join(LAMBDA_DIR, "vamsExecuteImageTo3dPipeline.py"),
)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _python_tuple(path, name):
    """The string members of a module-level tuple constant, read as text."""
    match = re.search(rf"^{name}\s*=\s*\(([^)]*)\)", _read(path), re.M)
    assert match, f"{os.path.basename(path)} declares no {name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _python_default(path):
    match = re.search(r'^DEFAULT_MODEL\s*=\s*"([^"]+)"', _read(path), re.M)
    assert match, f"{os.path.basename(path)} declares no DEFAULT_MODEL"
    return match.group(1)


def _ts_model_keys(path):
    match = re.search(r"const MODEL_KEYS = \[([^\]]*)\]", _read(path))
    assert match, f"{os.path.basename(path)} declares no MODEL_KEYS"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _batch_state_models():
    """The models openPipeline names a Batch state for, read from its BATCH_STATE_NAMES keys."""
    match = re.search(r"^BATCH_STATE_NAMES\s*=\s*\{([^}]*)\}",
                      _read(os.path.join(LAMBDA_DIR, "openPipeline.py")), re.M)
    assert match, "openPipeline.py declares no BATCH_STATE_NAMES"
    return set(re.findall(r'^\s*"([^"]+)":', match.group(1), re.M))


def _container_config_models():
    with open(os.path.join(CONTAINER_DIR, "config.json"), encoding="utf-8") as handle:
        return set(json.load(handle)["models"])


def _adapter_keys():
    """The dispatch table's keys, read from the adapter modules' KEY declarations."""
    keys = set()
    models_dir = os.path.join(CONTAINER_DIR, "models")
    for name in os.listdir(models_dir):
        if not name.endswith(".py") or name in ("__init__.py", "base.py"):
            continue
        match = re.search(r'^\s*KEY = "([^"]+)"', _read(os.path.join(models_dir, name)), re.M)
        if match:
            keys.add(match.group(1))
    return keys


def _config_ts_models():
    """The per-model flags declared on the ConfigPublic interface.

    Anchored on the imageTo3d block's own preceding fields: other pipelines declare a ``models`` map
    too, so matching the first one would compare this pipeline's models against another's.
    """
    source = _read(os.path.join(INFRA_DIR, "config", "config.ts"))
    start = re.search(r"maxVCpus:\s*number;\s*models:\s*\{", source)
    assert start, "config.ts declares no imageTo3d models interface block"
    # The block ends at the brace that balances its opening one; a model's own type may span lines.
    depth, end = 1, start.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(source[end], 0)
        end += 1
    return set(re.findall(r"(\w+):\s*\{\s*enabled:\s*boolean[^}]*\}", source[start.end():end - 1]))


def test_the_scan_finds_every_declaration_site():
    """Guards the assertions below: a regex that matched nothing would compare empty sets."""
    for path in CONSTRUCTS + LAMBDAS:
        assert os.path.isfile(path), f"missing declaration site: {path}"
    assert len(_container_config_models()) >= 4
    assert len(_adapter_keys()) >= 4
    assert len(_config_ts_models()) >= 4
    assert len(_batch_state_models()) >= 4


def test_every_declaration_of_the_model_set_agrees():
    sets = {
        "container/config.json": _container_config_models(),
        "container/models/*.py KEY": _adapter_keys(),
        "infra/config/config.ts": _config_ts_models(),
    }
    for path in CONSTRUCTS:
        sets[f"infra {os.path.basename(path)}"] = _ts_model_keys(path)
    for path in LAMBDAS:
        sets[f"lambda {os.path.basename(path)}"] = _python_tuple(path, "SUPPORTED_MODELS")
    sets["lambda openPipeline.py BATCH_STATE_NAMES"] = _batch_state_models()

    reference = sets["container/config.json"]
    mismatched = {name: sorted(value ^ reference) for name, value in sets.items()
                  if value != reference}
    assert not mismatched, (
        f"the model set disagrees across declaration sites (differences against "
        f"container/config.json): {mismatched}")


def test_every_declaration_of_the_default_model_agrees():
    defaults = {os.path.basename(p): _python_default(p) for p in LAMBDAS}
    generate = re.search(r"^DEFAULT_MODEL = (\w+)Adapter\.KEY",
                         _read(os.path.join(CONTAINER_DIR, "generate.py")), re.M)
    assert generate, "generate.py declares no DEFAULT_MODEL"
    adapter_module = os.path.join(CONTAINER_DIR, "models", f"{generate.group(1).lower()}.py")
    key = re.search(r'^\s*KEY = "([^"]+)"', _read(adapter_module), re.M)
    assert key, f"{adapter_module} declares no KEY"
    defaults["container/generate.py"] = key.group(1)

    assert len(set(defaults.values())) == 1, (
        f"the default model disagrees across declaration sites, so a run naming no model would "
        f"route to different images in the Lambda and the container: {defaults}")


def test_the_default_model_is_one_of_the_supported_models():
    assert _python_default(LAMBDAS[0]) in _container_config_models()
