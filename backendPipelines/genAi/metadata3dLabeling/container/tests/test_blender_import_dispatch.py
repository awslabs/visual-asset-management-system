# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender import dispatch in `renderScene.py`: every declared input format reaches a real Blender
operator, and an extension with no importer stops the script instead of rendering an empty scene.

`renderScene.py` runs inside Blender and its module body renders, so it cannot be imported here. The
tests instead extract the `if importExtension.lower() == ...` chain from the source with `ast`, execute
just that chain, and supply a stand-in `bpy` that exposes ONLY the operators Blender really has. That
is what makes an attribute chain such as `bpy.ops.wm.bpy.ops.wm.usd_import` fail here: the stand-in has
no `bpy` attribute, so the call raises AttributeError exactly as it does in the container.
"""

import ast
import io
import json
import os
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
_PIPELINE_ROOT = os.path.dirname(_CONTAINER)
RENDER_SCENE = os.path.join(_CONTAINER, "main", "blenderAppScripts", "renderScene.py")
PIPELINE_SCHEMA = os.path.join(_PIPELINE_ROOT, "vamsSchema", "pipeline.json")

# The operator each declared format must reach. These are the real Blender 4.x operator paths.
EXPECTED_IMPORTERS = {
    "fbx": "bpy.ops.import_scene.fbx",
    "glb": "bpy.ops.import_scene.gltf",
    "obj": "bpy.ops.wm.obj_import",
    "stl": "bpy.ops.wm.stl_import",
    "ply": "bpy.ops.wm.ply_import",
    "usd": "bpy.ops.wm.usd_import",
    "dae": "bpy.ops.wm.collada_import",
    "abc": "bpy.ops.wm.alembic_import",
}

# Operators the stand-in bpy exposes, grouped by the module they live on.
_IMPORT_SCENE_OPS = ["fbx", "gltf"]
_WM_OPS = ["obj_import", "stl_import", "ply_import", "usd_import", "collada_import", "alembic_import"]


class _OperatorModule:
    """A `bpy.ops.<module>` stand-in exposing a fixed set of operators and nothing else."""

    def __init__(self, prefix, names, calls):
        self._prefix = prefix
        self._calls = calls
        for name in names:
            setattr(self, name, self._operator(name))

    def _operator(self, name):
        def _call(**kwargs):
            self._calls.append((f"{self._prefix}.{name}", kwargs))

        return _call


def _fake_bpy(calls):
    return SimpleNamespace(ops=SimpleNamespace(
        import_scene=_OperatorModule("bpy.ops.import_scene", _IMPORT_SCENE_OPS, calls),
        wm=_OperatorModule("bpy.ops.wm", _WM_OPS, calls),
    ))


def _source():
    return io.open(RENDER_SCENE, encoding="utf-8").read()


def _dispatch_node():
    """The top-level if/elif chain that selects an importer for the input file extension."""
    for node in ast.parse(_source()).body:
        if isinstance(node, ast.If) and "importExtension" in ast.dump(node.test):
            return node
    raise AssertionError("no importExtension dispatch chain found in renderScene.py")


def _run_dispatch(extension, filepath="/tmp/input/model.bin"):
    """Execute only the dispatch chain and return the (operator, kwargs) calls it made."""
    module = ast.fix_missing_locations(ast.Module(body=[_dispatch_node()], type_ignores=[]))
    calls = []
    namespace = {
        "bpy": _fake_bpy(calls),
        "importExtension": extension,
        "inputFilePath": filepath,
    }
    exec(compile(module, "<renderScene-dispatch>", "exec"), namespace)  # nosemgrep: dangerous-exec-audit
    return calls


def _dispatch_extensions():
    """The extensions the dispatch chain compares against, in source order."""
    extensions = []
    node = _dispatch_node()
    while isinstance(node, ast.If):
        for compare in ast.walk(node.test):
            if isinstance(compare, ast.Compare):
                for comparator in compare.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        extensions.append(comparator.value)
        node = node.orelse[0] if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) else None
    return extensions


def _terminal_else():
    """The body of the chain's final `else`, or None when the chain has no else."""
    node = _dispatch_node()
    while isinstance(node, ast.If):
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
            continue
        return node.orelse or None
    return None


def _declared_extensions():
    """The extensions the pipeline advertises through its vamsSchema input filter."""
    schema = json.load(io.open(PIPELINE_SCHEMA, encoding="utf-8"))
    allow = schema["systemConfig"]["inputFileFilters"]["allow"]
    return sorted(pattern.rsplit(".", 1)[-1].lower() for pattern in allow)


@pytest.mark.unit
@pytest.mark.parametrize("extension", sorted(EXPECTED_IMPORTERS))
def test_every_declared_format_reaches_a_real_importer(extension):
    """Each of the eight advertised formats calls exactly one existing Blender operator.

    Against the doubled-prefix form (`bpy.ops.wm.bpy.ops.wm.usd_import`) the usd/dae/abc cases raise
    AttributeError here, which is the same failure the container hits.
    """
    calls = _run_dispatch(extension, filepath=f"/tmp/input/model.{extension}")
    assert len(calls) == 1, f"{extension} made {len(calls)} importer calls: {calls}"
    operator, kwargs = calls[0]
    assert operator == EXPECTED_IMPORTERS[extension]
    assert kwargs == {"filepath": f"/tmp/input/model.{extension}"}


@pytest.mark.unit
def test_no_doubled_operator_prefix():
    """`bpy.ops.wm.` written twice is an attribute chain that cannot resolve."""
    assert "bpy.ops.wm.bpy" not in _source()


@pytest.mark.unit
def test_unsupported_extension_stops_the_script():
    """An extension with no importer must exit non-zero rather than render an object-less scene.

    Without the terminal `else` the chain silently falls through, Blender renders blank frames, and
    the stage reports SUCCESS with model-derived labels that describe nothing.
    """
    with pytest.raises(SystemExit) as excinfo:
        _run_dispatch("gltf", filepath="/tmp/input/model.gltf")
    assert "gltf" in str(excinfo.value)


@pytest.mark.unit
def test_dispatch_chain_has_a_terminal_raise():
    body = _terminal_else()
    assert body is not None, "the import dispatch chain has no else branch"
    assert any(isinstance(statement, ast.Raise) for statement in body)


@pytest.mark.unit
def test_every_advertised_extension_has_a_dispatch_branch():
    """The vamsSchema allow list and the dispatch chain must agree.

    An advertised extension with no branch now raises rather than rendering blanks, so this guards the
    inverse mistake: widening the filter without adding an importer.
    """
    declared = _declared_extensions()
    dispatched = _dispatch_extensions()
    missing = [extension for extension in declared if extension not in dispatched]
    assert missing == [], f"advertised with no importer branch: {missing}"
    assert declared == sorted(EXPECTED_IMPORTERS), "vamsSchema allow list changed; update the mapping"
