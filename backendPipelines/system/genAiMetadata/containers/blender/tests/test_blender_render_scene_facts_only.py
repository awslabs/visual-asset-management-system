#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""renderScene.py's `--views none`: the facts-only request the handler sends when the pipeline's GenAI layer
is off. The scene is imported and measured and the facts are written, but no view is set up or rendered; an
empty or unknown view list is still a bad-arguments exit. `main` is executed from the source with its
collaborators replaced, the way the dispatch suite executes the importer."""

import json
import math
import os
import sys
import time
from types import SimpleNamespace

import pytest

from test_blender_render_scene_dispatch import exec_named_nodes

_MAIN_NODES = ["EXIT_OK", "EXIT_IMPORT_FAILED", "EXIT_NO_RENDERS", "EXIT_BAD_ARGUMENTS", "DEFAULT_RESOLUTION_PX",
               "DEFAULT_SAMPLES", "VIEW_DIRECTIONS", "VIEW_ORDER", "SCENE_FACTS_FILENAME", "NO_VIEWS", "USD_EXTENSIONS",
               "SceneImportError", "parse_args", "script_arguments", "write_facts", "main"]
USD_CHECK = {"layers": 2, "assets": 1, "unresolved": ["/work/input/asset/parts/gone.usda"], "unresolvedCount": 1}


def _run_main(tmp_path, views, input_path="/tmp/pump.glb", import_error=None):
    calls = []

    def _import(path):
        calls.append(("import_model", path))
        if import_error:
            raise import_error

    namespace = {
        "argparse": __import__("argparse"), "json": json, "math": math, "os": os, "sys": sys, "time": time,
        "bpy": SimpleNamespace(context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)),
                               app=SimpleNamespace(version_string="5.2.3 LTS")),
        "clear_scene": lambda: calls.append("clear_scene"),
        "import_model": _import,
        "usd_dependency_check": lambda path: calls.append(("usd_dependency_check", path)) or dict(USD_CHECK),
        "remove_cameras_and_lights": lambda: calls.append("remove_cameras_and_lights"),
        "mesh_objects": lambda: [SimpleNamespace(data=SimpleNamespace(polygons=[1, 2, 3]))],
        "world_corners": lambda objects: ["corners"],
        "combine_bounds": lambda corners: ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
        "world_boxes": lambda objects: ["boxes"],
        "framing_bounds": lambda boxes: ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
        "collect_scene_facts": lambda objects, center, size, path: {"schemaVersion": 1, "dimensions": list(size)},
        "ensure_materials": lambda objects: calls.append("ensure_materials"),
        "configure_render": lambda resolution, samples: calls.append(("configure_render", resolution, samples)),
        "setup_lighting": lambda center, radius: calls.append("setup_lighting"),
        "setup_camera": lambda: calls.append("setup_camera") or "camera",
        "render_view": lambda camera, center, size, view, output_dir: calls.append(("render_view", view)),
    }
    exec_named_nodes(_MAIN_NODES, namespace)
    output_dir = tmp_path / "out"
    argv = ["renderScene", "--", "--input", input_path, "--output-dir", str(output_dir), "--views", views]
    sys.argv, previous = argv, sys.argv
    try:
        code = namespace["main"]()
    finally:
        sys.argv = previous
    facts_path = output_dir / namespace["SCENE_FACTS_FILENAME"]
    facts = json.loads(facts_path.read_text()) if facts_path.exists() else None
    return code, facts, calls, namespace


@pytest.mark.unit
def test_none_imports_and_measures_without_rendering(tmp_path):
    code, facts, calls, namespace = _run_main(tmp_path, "none")
    assert code == namespace["EXIT_OK"]
    assert facts["dimensions"] == [1.0, 2.0, 3.0] and facts["factsOnly"] is True
    assert facts["renderedViews"] == [] and facts["failedViews"] == {}
    assert "importSeconds" in facts and "renderSeconds" in facts
    assert ("import_model", "/tmp/pump.glb") in calls
    # Nothing render-side ran: no materials, render settings, lighting, camera or view.
    assert not any(call in ("ensure_materials", "setup_lighting", "setup_camera") or
                   (isinstance(call, tuple) and call[0] in ("configure_render", "render_view")) for call in calls)


@pytest.mark.unit
def test_none_is_case_insensitive_and_whitespace_tolerant(tmp_path):
    code, facts, _calls, namespace = _run_main(tmp_path, " NONE ")
    assert code == namespace["EXIT_OK"] and facts["factsOnly"] is True


@pytest.mark.unit
def test_a_named_view_still_renders(tmp_path):
    code, facts, calls, namespace = _run_main(tmp_path, "front")
    assert code == namespace["EXIT_OK"]
    assert ("render_view", "front") in calls and facts["renderedViews"] == ["front"]
    assert "factsOnly" not in facts


@pytest.mark.unit
@pytest.mark.parametrize("views", ["", "sideways", "front,nope"])
def test_empty_or_unknown_views_are_bad_arguments(tmp_path, views):
    code, facts, calls, namespace = _run_main(tmp_path, views)
    assert code == namespace["EXIT_BAD_ARGUMENTS"]
    assert "error" in facts and "unknown views" in facts["error"]
    assert not any(isinstance(call, tuple) and call[0] == "import_model" for call in calls)


@pytest.mark.unit
def test_a_usd_scene_carries_blenders_dependency_check(tmp_path):
    """The composed stage's unresolved paths are the handler's missing list for a USD scene (#1011)."""
    code, facts, calls, namespace = _run_main(tmp_path, "front", input_path="/work/input/asset/scenes/root.usda")
    assert code == namespace["EXIT_OK"]
    assert facts["usdDependencies"] == USD_CHECK
    assert calls.index(("usd_dependency_check", "/work/input/asset/scenes/root.usda")) < \
        calls.index(("import_model", "/work/input/asset/scenes/root.usda")), "checked before the import"


@pytest.mark.unit
def test_the_check_is_reported_when_the_import_fails(tmp_path):
    code, facts, _calls, namespace = _run_main(tmp_path, "front", input_path="/work/a.usdz",
                                               import_error=RuntimeError("missing layer"))
    assert code == namespace["EXIT_IMPORT_FAILED"]
    assert "missing layer" in facts["error"] and facts["usdDependencies"] == USD_CHECK


@pytest.mark.unit
def test_other_formats_are_not_checked(tmp_path):
    _code, facts, calls, _namespace = _run_main(tmp_path, "front")
    assert "usdDependencies" not in facts
    assert not any(isinstance(call, tuple) and call[0] == "usd_dependency_check" for call in calls)
