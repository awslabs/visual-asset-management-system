# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender import dispatch in `renderScene.py`: every extension the BLENDER branch receives (the mesh
and usd file classes) reaches exactly one real Blender importer, an extension with none raises instead of
rendering an empty scene, and the unit label of the imported numbers follows what each importer converts.

`renderScene.py` imports `bpy` at the top and its module body calls `sys.exit(main())`, so it cannot be
imported here. The tests parse it with `ast`, execute only the named top-level nodes they need, and supply
a stand-in `bpy` exposing ONLY the operators Blender 4.5 really has -- a doubled prefix such as
`bpy.ops.wm.bpy.ops.wm.usd_import` raises AttributeError here exactly as it does in the container.
"""

import ast
import io
import os
from types import SimpleNamespace

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
RENDER_SCENE = os.path.join(_CONTAINER, "renderScene.py")

# The importer each branch extension must reach. Real Blender 4.5 operator paths; `.blend` is appended
# through the data API because there is no importer operator for Blender's own format. `.abc` and `.blend`
# are renderer-capable but not allow-listed: no viewer declares them, so the classifier never routes them
# here; the branches stay so a direct invocation works and a viewer declaration needs no change.
EXPECTED_IMPORTERS = {
    ".glb": "bpy.ops.import_scene.gltf",
    ".gltf": "bpy.ops.import_scene.gltf",
    ".fbx": "bpy.ops.import_scene.fbx",
    ".obj": "bpy.ops.wm.obj_import",
    ".dae": "bpy.ops.wm.collada_import",
    ".abc": "bpy.ops.wm.alembic_import",
    ".blend": "bpy.data.libraries.load",
    ".stl": "bpy.ops.wm.stl_import",
    ".ply": "bpy.ops.wm.ply_import",
    ".usd": "bpy.ops.wm.usd_import",
    ".usda": "bpy.ops.wm.usd_import",
    ".usdc": "bpy.ops.wm.usd_import",
    ".usdz": "bpy.ops.wm.usd_import",
}

# The unit of the world-space numbers after each importer ran (Blender 4.5): glTF is metres by
# specification; import_scene.fbx multiplies by GlobalSettings UnitScaleFactor / 100; wm.collada_import
# with import_units=False rescales root objects to the scene's units; wm.usd_import with
# apply_unit_conversion_scale=True multiplies by the stage's metersPerUnit. OBJ, STL, PLY, Alembic and
# .blend carry no unit the importer reads.
EXPECTED_UNITS = {
    ".glb": "m", ".gltf": "m", ".fbx": "m", ".dae": "m", ".usd": "m", ".usda": "m", ".usdc": "m", ".usdz": "m",
    ".obj": None, ".stl": None, ".ply": None, ".abc": None, ".blend": None,
}

_IMPORT_SCENE_OPS = ["fbx", "gltf"]
_WM_OPS = ["obj_import", "stl_import", "ply_import", "usd_import", "collada_import", "alembic_import"]


class _OperatorModule:
    """A `bpy.ops.<module>` stand-in exposing a fixed set of operators and nothing else."""

    def __init__(self, prefix, names, calls):
        for name in names:
            setattr(self, name, self._operator(f"{prefix}.{name}", calls))

    @staticmethod
    def _operator(qualified, calls):
        def _call(**kwargs):
            calls.append((qualified, kwargs))
            return {"FINISHED"}

        return _call


class _Libraries:
    """`bpy.data.libraries` stand-in: `load()` yields (data_from, data_to) and materialises objects on exit."""

    def __init__(self, calls):
        self._calls = calls

    def load(self, filepath, link=False):
        self._calls.append(("bpy.data.libraries.load", {"filepath": filepath, "link": link}))
        data_from = SimpleNamespace(objects=["Body", "Wheel", "Lamp"])
        data_to = SimpleNamespace(objects=[])

        class _Context:
            def __enter__(self_inner):
                return data_from, data_to

            def __exit__(self_inner, *_exc):
                data_to.objects = [SimpleNamespace(name=n, type="MESH") for n in data_to.objects]
                return False

        return _Context()


def _fake_bpy(calls, linked):
    return SimpleNamespace(
        ops=SimpleNamespace(
            import_scene=_OperatorModule("bpy.ops.import_scene", _IMPORT_SCENE_OPS, calls),
            wm=_OperatorModule("bpy.ops.wm", _WM_OPS, calls),
        ),
        data=SimpleNamespace(libraries=_Libraries(calls)),
        context=SimpleNamespace(
            scene=SimpleNamespace(collection=SimpleNamespace(objects=SimpleNamespace(link=linked.append)))
        ),
    )


def _source():
    return io.open(RENDER_SCENE, encoding="utf-8").read()


def _node_name(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def exec_named_nodes(names, namespace):
    """Execute the named top-level definitions of renderScene.py, in source order, into `namespace`."""
    wanted = set(names)
    body = [node for node in ast.parse(_source()).body if _node_name(node) in wanted]
    found = {_node_name(node) for node in body}
    missing = wanted - found
    assert not missing, f"renderScene.py lacks top-level definitions: {sorted(missing)}"
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, "<renderScene-selected>", "exec"), namespace)  # nosemgrep: dangerous-exec-audit
    return namespace


def _dispatch_namespace():
    calls, linked = [], []
    namespace = {"os": __import__("os"), "bpy": _fake_bpy(calls, linked)}
    exec_named_nodes(
        ["MESH_EXTENSIONS", "USD_EXTENSIONS", "SceneImportError", "_import_blend", "IMPORT_DISPATCH", "import_model"],
        namespace,
    )
    return namespace, calls, linked


def _units_namespace():
    return exec_named_nodes(
        ["MESH_EXTENSIONS", "USD_EXTENSIONS", "METRE_CONVERTED_EXTENSIONS", "imported_units"], {"os": __import__("os")})


@pytest.mark.unit
@pytest.mark.parametrize("extension", sorted(EXPECTED_IMPORTERS))
def test_every_branch_extension_reaches_exactly_one_real_importer(extension):
    namespace, calls, linked = _dispatch_namespace()
    path = f"/work/input/model{extension}"
    namespace["import_model"](path)
    assert len(calls) == 1, f"{extension} made {len(calls)} importer calls: {calls}"
    operator, kwargs = calls[0]
    assert operator == EXPECTED_IMPORTERS[extension]
    assert kwargs["filepath"] == path
    if extension == ".blend":
        assert kwargs["link"] is False
        # Every object the file carries is appended into the scene; cameras/lights are culled afterwards.
        assert [obj.name for obj in linked] == ["Body", "Wheel", "Lamp"]
    else:
        assert kwargs == {"filepath": path}, "importer defaults are the unit-converting ones the units table assumes"
        assert linked == []


@pytest.mark.unit
def test_extension_matching_is_case_insensitive():
    namespace, calls, _linked = _dispatch_namespace()
    namespace["import_model"]("/work/input/MODEL.GLB")
    assert [c[0] for c in calls] == ["bpy.ops.import_scene.gltf"]


@pytest.mark.unit
def test_unsupported_extension_raises_before_any_importer_runs():
    namespace, calls, _linked = _dispatch_namespace()
    with pytest.raises(namespace["SceneImportError"]) as excinfo:
        namespace["import_model"]("/work/input/cloud.xyz")
    assert ".xyz" in str(excinfo.value)
    assert calls == []


@pytest.mark.unit
def test_dispatch_covers_exactly_the_mesh_and_usd_rows():
    namespace, _calls, _linked = _dispatch_namespace()
    declared = set(namespace["MESH_EXTENSIONS"]) | set(namespace["USD_EXTENSIONS"])
    assert declared == set(EXPECTED_IMPORTERS), "the mesh + usd extension sets changed; update the mapping"
    assert set(namespace["IMPORT_DISPATCH"]) == declared, "an extension is declared without an importer (or vice versa)"
    assert all(ext == ext.lower() and ext.startswith(".") for ext in declared)


@pytest.mark.unit
@pytest.mark.parametrize("extension", sorted(EXPECTED_UNITS))
def test_units_after_import_follow_the_importers_conversion(extension):
    namespace = _units_namespace()
    assert namespace["imported_units"](extension) == EXPECTED_UNITS[extension]
    assert namespace["imported_units"](extension.upper()) == EXPECTED_UNITS[extension]


@pytest.mark.unit
def test_metre_converted_extensions_are_exactly_the_labelled_branch_extensions():
    namespace = _units_namespace()
    declared = set(namespace["MESH_EXTENSIONS"]) | set(namespace["USD_EXTENSIONS"])
    converted = set(namespace["METRE_CONVERTED_EXTENSIONS"])
    assert converted <= declared, "a unit label for an extension this branch never imports"
    assert converted == {ext for ext, units in EXPECTED_UNITS.items() if units == "m"}
    assert set(EXPECTED_UNITS) == set(EXPECTED_IMPORTERS)
    assert namespace["imported_units"](None) is None and namespace["imported_units"]("") is None


@pytest.mark.unit
def test_no_doubled_operator_prefix():
    """`bpy.ops.wm.` written twice is an attribute chain that cannot resolve (the legacy container's bug)."""
    assert "bpy.ops.wm.bpy" not in _source()
    assert "bpy.ops.import_scene.bpy" not in _source()


@pytest.mark.unit
def test_only_render_scene_imports_bpy():
    """Every other module in this image must import without Blender."""
    for name in ("handler.py", "meshAttributes.py", "formatUnits.py", "containerLogger.py"):
        path = os.path.join(_CONTAINER, name)
        if not os.path.isfile(path):
            continue
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert not imported & {"bpy", "bmesh", "mathutils", "pxr"}, f"{name} imports Blender-only modules"
