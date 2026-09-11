#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every format this container advertises must be one trimesh can actually load with the dependencies
the image installs, and every format it offers as a conversion target must be one trimesh can write.

trimesh keeps its XML- and COLLADA-backed formats behind optional dependencies and registers an
``ExceptionWrapper`` in place of the loader when they are absent, so a declared format whose dependency
is missing is not a startup error -- it is a run that fails at the load, after the file has been
downloaded. ``requirements.txt`` installs neither ``pycollada`` (COLLADA) nor ``lxml`` + ``networkx``
(3MF, XAML, 3DXML), and ``poetry.lock`` resolves this container to exactly aws-lambda-powertools,
jmespath, numpy, trimesh and typing-extensions -- lxml, networkx and pycollada appear there only inside
trimesh's optional ``easy`` extra.

The check is DERIVED rather than a list of known-bad extensions: the dependencies absent from
``requirements.txt`` are simulated away and trimesh's own loader and exporter registries are then read.
A previous guard hardcoded ``(".dae", ".xaml", ".3dxml")`` and could not see that ``*.3mf`` is gated by
the identical mechanism. Adding the dependencies to ``requirements.txt`` relaxes this test on its own.

Two measurements behind it:

* trimesh 4.8.3 (the pinned version) carries the same registration guard as the version installed for
  development -- ``try: import networkx as nx; from lxml import etree`` around ``_three_loaders`` /
  ``_3mf_exporters`` -- and its only hard requirement is ``numpy>=1.20``.
* Simulating the absence of these three alone gives the same answer as simulating the absence of every
  trimesh soft dependency (scipy, PIL, shapely, rtree, embreex, meshio, …), so the narrow simulation
  cannot report a format as unloadable that the image would in fact load.
"""

import ast
import json
import os
import re
import subprocess
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCHEMA_ROOT = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "vamsSchema"))

# The distribution each optional trimesh format loader needs, and the module name it imports. A
# distribution named in requirements.txt is present in the image and is not simulated away.
_OPTIONAL_FORMAT_DEPENDENCIES = {
    "pycollada": "collada",
    "lxml": "lxml",
    "networkx": "networkx",
}

# The trimesh format keys each optional distribution gates, used to assert the simulation bites rather
# than to decide what the bundle may declare.
_FORMATS_GATED_BY = {
    "pycollada": ("dae",),
    "lxml": ("3mf", "xaml", "3dxml"),
    "networkx": ("3mf",),
}

# Formats trimesh loads with numpy alone. The positive control: if the probe were broken -- trimesh
# failing to import, an empty registry -- these would be missing too and every absence assertion below
# would pass for the wrong reason.
_FORMATS_NEEDING_NO_OPTIONAL_DEPENDENCY = ("stl", "obj", "ply", "glb", "gltf")

# Reads trimesh's registries with the named modules made unimportable. `has_module` re-imports
# `find_spec` on every call and is what gates the COLLADA loaders, so that has to answer "not found"
# too -- a meta-path finder alone leaves `has_module` returning True and dae registering a loader that
# raises when called, which reads as available.
_PROBE = r"""
import importlib.abc, importlib.machinery, importlib.util, json, sys

BLOCK = {name for name in sys.argv[1].split(",") if name}

_real_find_spec = importlib.util.find_spec


def _find_spec(name, package=None):
    if name.split(".")[0] in BLOCK:
        return None
    return _real_find_spec(name, package)


importlib.util.find_spec = _find_spec


class RaisingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        raise ImportError("simulated absence of " + spec.name)

    def exec_module(self, module):
        raise ImportError("simulated absence of " + module.__name__)


class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCK:
            return importlib.machinery.ModuleSpec(name, RaisingLoader())
        return None


sys.meta_path.insert(0, Blocker())

import trimesh
from trimesh.exchange import export as trimesh_export

print(json.dumps({
    "version": trimesh.__version__,
    "loaders": sorted(trimesh.available_formats()),
    "exporters": sorted(
        key for key, value in trimesh_export._mesh_exporters.items()
        if type(value).__name__ != "ExceptionWrapper"
    ),
}))
"""


def _requirement_names():
    """The distributions requirements.txt installs, lowercased."""
    names = set()
    with open(os.path.join(_LAMBDA_DIR, "requirements.txt"), encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line)
            if match:
                names.add(match.group(1).lower().replace("_", "-"))
    return names


def _simulated_absent():
    """The optional distributions the image does NOT install, as import names keyed by distribution."""
    installed = _requirement_names()
    return {dist: module for dist, module in _OPTIONAL_FORMAT_DEPENDENCIES.items()
            if dist not in installed}


def _probe():
    absent = _simulated_absent()
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [sys.executable, "-c", _PROBE, ",".join(sorted(absent.values()))],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _declared_formats(name):
    """A module-level format set read out of lambda.py's syntax tree, so the sets are checked without
    importing the module (and therefore without trimesh, boto3 or powertools)."""
    with open(os.path.join(_LAMBDA_DIR, "lambda.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"lambda.py declares no module-level {name}")


def _pipeline_allow():
    with open(os.path.join(_SCHEMA_ROOT, "pipeline.json"), encoding="utf-8") as handle:
        return json.load(handle)["systemConfig"]["inputFileFilters"]["allow"]


def _format_key(pattern):
    """`*.3mf` / `.3mf` -> `3mf`, the key trimesh registers a loader under."""
    return pattern.lower().lstrip("*").lstrip(".")


@pytest.fixture(scope="module")
def registries():
    return _probe()


@pytest.mark.unit
class TestTheDependencySetIsTheOneThisTestAssumes:
    def test_the_optional_format_dependencies_are_absent_from_requirements(self):
        # The premise. If a later change installs them, the simulation empties and the guard below
        # relaxes on its own rather than reporting a format as unloadable that now loads.
        assert _simulated_absent(), (
            "requirements.txt now installs every optional format dependency; nothing is simulated "
            "away and the assertions below no longer describe the image")
        assert "trimesh" in _requirement_names()

    def test_the_probe_reports_the_formats_that_need_no_optional_dependency(self, registries):
        # The positive control for every absence assertion in this file.
        for key in _FORMATS_NEEDING_NO_OPTIONAL_DEPENDENCY:
            assert key in registries["loaders"], (key, registries)

    def test_each_absent_dependency_actually_removes_its_formats(self, registries):
        # The mechanism control: the simulation bites, so a format missing from the registry is
        # missing because its dependency is absent and not because the probe read nothing.
        for dist in _simulated_absent():
            for key in _FORMATS_GATED_BY[dist]:
                assert key not in registries["loaders"], (dist, key, registries)


@pytest.mark.unit
class TestEveryDeclaredFormatCanBeLoaded:
    def test_every_extension_in_the_bundle_filter_has_a_loader(self, registries):
        offenders = [pattern for pattern in _pipeline_allow()
                     if _format_key(pattern) not in registries["loaders"]]
        assert offenders == [], (
            f"declared in inputFileFilters.allow but trimesh has no loader for them with this "
            f"container's dependencies: {offenders}")

    def test_every_accepted_input_format_has_a_loader(self, registries):
        offenders = [extension for extension in sorted(_declared_formats("SUPPORTED_INPUT_FORMATS"))
                     if _format_key(extension) not in registries["loaders"]]
        assert offenders == [], offenders


@pytest.mark.unit
class TestEveryOfferedTargetCanBeWritten:
    def test_every_output_format_has_an_exporter(self, registries):
        # Loading a format is not the same as writing it, and the requested output type is checked
        # against this set -- so an unwritable target is accepted at the gate and fails at the export.
        offenders = [extension for extension in sorted(_declared_formats("SUPPORTED_OUTPUT_FORMATS"))
                     if _format_key(extension) not in registries["exporters"]]
        assert offenders == [], offenders

    def test_every_template_target_has_an_exporter(self, registries):
        templates = sorted(name for name in os.listdir(os.path.join(_SCHEMA_ROOT, "templates"))
                           if name.endswith(".json"))
        # Asserted in-band: a listing that matched nothing would leave this test checking no template.
        assert len(templates) == 4, templates
        for name in templates:
            with open(os.path.join(_SCHEMA_ROOT, "templates", name), encoding="utf-8") as handle:
                template = json.load(handle)
            output_type = json.loads(template["configBody"])["outputType"]
            assert _format_key(output_type) in registries["exporters"], (name, output_type)
