#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every MESH format this container advertises must be one trimesh can actually load with the
dependencies the image installs.

trimesh keeps its XML- and COLLADA-backed formats behind optional dependencies and registers an
``ExceptionWrapper`` in place of the loader when they are absent, so a declared format whose dependency
is missing is not a startup error. Here it is worse than a failed run: ``mesh_extractor``'s outer
``except Exception`` turns the load failure into ``{}``, which is uploaded as an attribute file with an
empty ``metadata`` array under HTTP 200 -- a green run carrying no data.

The check is DERIVED rather than a list of known-bad extensions: the optional dependencies absent from
``requirements.txt`` are simulated away and trimesh's own loader registry is then read. A previous
guard hardcoded ``(".dae", ".xaml", ".3dxml")`` and could not see that ``*.3mf`` is gated by the
identical mechanism. Adding the dependencies to ``requirements.txt`` relaxes this test on its own.

CAD formats are out of scope here: ``format_handlers`` routes ``.step`` / ``.stp`` / ``.dxf`` to the
CADQuery extractor, which does not consult trimesh's registry, so only ``MESH_FORMATS`` is checked.

Two measurements behind it:

* trimesh 4.8.3 (the pinned version) carries the same registration guard as the version installed for
  development -- ``try: import networkx as nx; from lxml import etree`` around ``_three_loaders`` --
  and its only hard requirement is ``numpy>=1.20``.
* This image installs CADQuery, whose transitive closure was resolved from package metadata
  (cadquery-ocp, ezdxf, multimethod, nlopt, typish, casadi, path, trame, trame-vtk -> vtk ->
  matplotlib, wslink -> aiohttp, …) and contains neither ``lxml`` nor ``networkx``, so simulating their
  absence matches the image.
"""

import importlib.util
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

# Reads trimesh's registry with the named modules made unimportable. `has_module` re-imports
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

print(json.dumps({
    "version": trimesh.__version__,
    "loaders": sorted(trimesh.available_formats()),
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


def _format_handlers():
    """The extractors' format lists, loaded from format_handlers directly so the CAD extractor's
    cadquery dependency is not needed."""
    spec = importlib.util.spec_from_file_location(
        "meshcad_loader_format_handlers",
        os.path.join(_LAMBDA_DIR, "metadata_extractors", "format_handlers.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(name):
    with open(os.path.join(_SCHEMA_ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


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
        assert {"trimesh", "cadquery"} <= _requirement_names()

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
class TestEveryDeclaredMeshFormatCanBeLoaded:
    def test_every_mesh_format_the_extractors_route_has_a_loader(self, registries):
        handlers = _format_handlers()
        # In-band count: an empty MESH_FORMATS would satisfy the loop while checking nothing.
        assert handlers.MESH_FORMATS, handlers.MESH_FORMATS
        offenders = [extension for extension in handlers.MESH_FORMATS
                     if _format_key(extension) not in registries["loaders"]]
        assert offenders == [], (
            f"routed to the trimesh extractor but trimesh has no loader for them with this "
            f"container's dependencies: {offenders}")

    def test_every_mesh_extension_in_the_bundle_filter_has_a_loader(self, registries):
        handlers = _format_handlers()
        cad = {_format_key(extension) for extension in handlers.CAD_FORMATS}
        for source in ("pipeline.json", "workflow.json"):
            allow = (_bundle(source)["systemConfig"]["inputFileFilters"]["allow"]
                     if source == "pipeline.json"
                     else _bundle(source)["triggers"][0]["inputFileFilters"]["allow"])
            assert allow, source
            offenders = [pattern for pattern in allow
                         if _format_key(pattern) not in cad
                         and _format_key(pattern) not in registries["loaders"]]
            assert offenders == [], (source, offenders)

    def test_the_cad_formats_are_the_ones_excluded_from_the_trimesh_check(self):
        # Names the exemption in-band, so a mesh format quietly moved into CAD_FORMATS to escape the
        # loader check above would change this list rather than pass unnoticed.
        handlers = _format_handlers()
        assert sorted(handlers.CAD_FORMATS) == [".dxf", ".step", ".stp"], handlers.CAD_FORMATS
