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
               "DEFAULT_SAMPLES", "VIEW_DIRECTIONS", "VIEW_ORDER", "SCENE_FACTS_FILENAME", "NO_VIEWS", "SceneImportError",
               "parse_args", "script_arguments", "write_facts", "main"]


def _run_main(tmp_path, views):
    calls = []
    namespace = {
        "argparse": __import__("argparse"), "json": json, "math": math, "os": os, "sys": sys, "time": time,
        "bpy": SimpleNamespace(context=SimpleNamespace(view_layer=SimpleNamespace(update=lambda: None)),
                               app=SimpleNamespace(version_string="4.5.13")),
        "clear_scene": lambda: calls.append("clear_scene"),
        "import_model": lambda path: calls.append(("import_model", path)),
        "remove_cameras_and_lights": lambda: calls.append("remove_cameras_and_lights"),
        "mesh_objects": lambda: [SimpleNamespace(data=SimpleNamespace(polygons=[1, 2, 3]))],
        "world_corners": lambda objects: ["corners"],
        "combine_bounds": lambda corners: ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),
        "collect_scene_facts": lambda objects, center, size, path: {"schemaVersion": 1, "dimensions": list(size)},
        "ensure_materials": lambda objects: calls.append("ensure_materials"),
        "configure_render": lambda resolution, samples: calls.append(("configure_render", resolution, samples)),
        "setup_lighting": lambda center, radius: calls.append("setup_lighting"),
        "setup_camera": lambda: calls.append("setup_camera") or "camera",
        "render_view": lambda camera, center, size, view, output_dir: calls.append(("render_view", view)),
    }
    exec_named_nodes(_MAIN_NODES, namespace)
    output_dir = tmp_path / "out"
    argv = ["renderScene", "--", "--input", "/tmp/pump.glb", "--output-dir", str(output_dir), "--views", views]
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
