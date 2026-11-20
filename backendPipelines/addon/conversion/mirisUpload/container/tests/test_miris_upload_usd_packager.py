#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from usd_packager import choose_roots, local_download_plan, root_candidates, should_skip_packaging

pytestmark = pytest.mark.unit


ASSET_ID = "x86487e0b-9f50-4b7b-9b59-8963c6791b56"
ROOT = f"{ASSET_ID}/"


def test_local_download_plan_maps_keys_under_the_root_to_relative_paths():
    keys = [
        f"{ROOT}",  # folder marker
        f"{ROOT}Red Armchair.usda",
        f"{ROOT}textures/wood.jpg",
        "someOther/thing.usda",
        f"someOtherPrefix/{ASSET_ID}/file.usda",  # the asset id mid-key, not the leading root
    ]
    assert local_download_plan(keys, ROOT) == [
        (f"{ROOT}Red Armchair.usda", "Red Armchair.usda"),
        (f"{ROOT}textures/wood.jpg", "textures/wood.jpg"),
    ]
    root = f"vams/data/{ASSET_ID}/"
    assert local_download_plan([f"{root}scene.usda", f"{ROOT}scene.usda"], root) == [(f"{root}scene.usda", "scene.usda")]


@pytest.mark.parametrize("rel", ["../escape.usda", "a/../../escape.usda", "/etc/passwd"])
def test_local_download_plan_skips_paths_that_leave_the_download_directory(rel):
    assert local_download_plan([f"{ROOT}{rel}"], ROOT) == []


def test_local_download_plan_normalizes_dot_segments():
    keys = [f"{ROOT}./scene.usda", f"{ROOT}geo/x/../geo.usd", f"{ROOT}a//b.usdc"]
    assert [rel for _, rel in local_download_plan(keys, ROOT)] == ["scene.usda", "geo/geo.usd", "a/b.usdc"]


def test_root_candidates_are_distinct_and_match_the_extension_exactly_ignoring_case():
    paths = ["Scene.USDA", "Scene.USDA", "geo.usd", "pump.usdz", "notes.usda.bak", "wood.png", "x.us"]
    assert root_candidates(paths, [".usd", ".usda", ".usdz"]) == ["Scene.USDA", "geo.usd", "pump.usdz"]


def _refs(graph):
    """references_of over a {layer: {referenced layers}} graph, recording each call."""
    calls = []

    def references_of(layer):
        calls.append(layer)
        return set(graph.get(layer, ()))

    references_of.calls = calls
    return references_of


def test_the_unreferenced_layer_is_the_root_whichever_file_triggered():
    graph = {"scene.usda": {"geo/geo.usd", "mat.usdc"}, "geo/geo.usd": {"mat.usdc"}}
    candidates = ["geo/geo.usd", "mat.usdc", "scene.usda"]

    assert choose_roots(candidates, _refs(graph)) == ["scene.usda"]


def test_two_independent_layers_are_two_roots():
    graph = {"a.usda": {"shared.usd"}, "b.usda": {"shared.usd"}}

    assert choose_roots(["a.usda", "b.usda", "shared.usd"], _refs(graph)) == ["a.usda", "b.usda"]


def test_a_single_candidate_is_its_root_without_a_scan():
    references_of = _refs({})

    assert choose_roots(["pump.usdz"], references_of) == ["pump.usdz"]
    assert references_of.calls == []


def test_only_layers_are_read_and_a_usdz_can_be_referenced():
    references_of = _refs({"scene.usda": {"props/chair.usdz", "tex/wood.png"}})

    assert choose_roots(["props/chair.usdz", "scene.usda"], references_of) == ["scene.usda"]
    assert references_of.calls == ["scene.usda"]


def test_a_candidate_referenced_through_a_non_candidate_layer_is_not_a_root():
    """scene.usda references cache.usdc, which is not a candidate (its extension does not trigger
    the pipeline), and cache.usdc references geo.usda."""
    references_of = _refs({"scene.usda": {"cache.usdc"}, "cache.usdc": {"geo.usda"}})

    assert choose_roots(["geo.usda", "scene.usda"], references_of) == ["scene.usda"]


def test_each_layer_is_read_once():
    graph = {f"l{i}.usda": {f"l{i + 1}.usda"} for i in range(50)}
    references_of = _refs(graph)
    candidates = sorted(f"l{i}.usda" for i in range(51))

    assert choose_roots(candidates, references_of) == ["l0.usda"]
    assert sorted(references_of.calls) == candidates


def test_a_layer_cycle_leaves_no_root():
    graph = {"a.usda": {"b.usda"}, "b.usda": {"a.usda"}}

    assert choose_roots(["a.usda", "b.usda"], _refs(graph)) == []


@pytest.mark.parametrize("layers, assets, skip", [(1, 0, True), (2, 0, False), (1, 3, False)])
def test_packaging_is_skipped_only_for_a_root_without_dependencies(layers, assets, skip):
    assert should_skip_packaging(layers, assets) is skip


def _texture_layer(asset_path):
    return ('#usda 1.0\ndef Material "M" {\n    def Shader "T" {\n'
            '        uniform token info:id = "UsdUVTexture"\n'
            f'        asset inputs:file = @{asset_path}@\n    }}\n}}\n')


def _write_usda_with_texture(dir_path):
    """root.usda referencing ./tex.png, with the texture beside it. Returns the root path."""
    with open(os.path.join(dir_path, "tex.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    root = os.path.join(dir_path, "root.usda")
    with open(root, "w") as f:
        f.write(_texture_layer("./tex.png"))
    return root


def test_compute_dependencies_finds_a_texture_and_reports_a_missing_one(tmp_path):
    pytest.importorskip("pxr")
    from usd_packager import compute_dependencies

    _, assets, unresolved, _ = compute_dependencies(_write_usda_with_texture(str(tmp_path)))
    assert unresolved == []
    assert any(a.endswith("tex.png") for a in assets)

    missing = tmp_path / "missing.usda"
    missing.write_text(_texture_layer("/nonexistent/abs/missing.png"))
    assert compute_dependencies(str(missing))[2], "expected the missing absolute reference to be unresolved"


def test_package_usdz_bundles_root_and_texture(tmp_path):
    pytest.importorskip("pxr")
    from usd_packager import package_usdz

    root = _write_usda_with_texture(str(tmp_path))
    out = os.path.join(str(tmp_path), "out.usdz")
    package_usdz(root, out)

    assert os.path.exists(out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert any(n.endswith("root.usda") for n in names)
    assert any(n.endswith("tex.png") for n in names)


def _write_layer_tree(dir_path):
    """root.usda sublayers sub/geo.usda (which pays-load mat.usda) and references props/chair.usdz."""
    from pxr import Sdf, UsdUtils

    os.makedirs(os.path.join(dir_path, "sub"))
    os.makedirs(os.path.join(dir_path, "props"))
    with open(os.path.join(dir_path, "root.usda"), "w") as f:
        f.write('#usda 1.0\n(\n    subLayers = [@./sub/geo.usda@]\n)\n'
                'def Xform "W" (prepend references = @./props/chair.usdz@) {}\n')
    with open(os.path.join(dir_path, "sub", "geo.usda"), "w") as f:
        f.write('#usda 1.0\ndef Xform "G" (prepend payload = @../mat.usda@) {}\n')
    with open(os.path.join(dir_path, "mat.usda"), "w") as f:
        f.write('#usda 1.0\ndef "M" {}\n')
    inner = os.path.join(dir_path, "chair_src.usda")
    with open(inner, "w") as f:
        f.write('#usda 1.0\ndef "Chair" {}\n')
    assert UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(inner), os.path.join(dir_path, "props", "chair.usdz"))
    os.remove(inner)


def test_direct_references_lists_what_a_layer_names_relative_to_the_asset(tmp_path):
    pytest.importorskip("pxr")
    from usd_packager import direct_references

    _write_layer_tree(str(tmp_path))

    assert direct_references(str(tmp_path / "root.usda"), str(tmp_path)) == {
        "sub/geo.usda", "props/chair.usdz"}
    assert direct_references(str(tmp_path / "sub" / "geo.usda"), str(tmp_path)) == {"mat.usda"}
    assert direct_references(str(tmp_path / "missing.usda"), str(tmp_path)) == set()


def _refs_on_disk(base):
    from usd_packager import direct_references

    return lambda rel: direct_references(str(base / rel), str(base))


def test_value_clip_layers_are_referenced_by_their_root(tmp_path):
    """Clip layers, listed or named by a template, are dependencies of the layer that uses them."""
    pytest.importorskip("pxr")
    os.makedirs(tmp_path / "frames")
    for i in (1, 2, 3):
        with open(tmp_path / "frames" / f"f.{i}.usd", "w") as f:
            f.write('#usda 1.0\ndef "C" {}\n')
    with open(tmp_path / "frames" / "manifest.usda", "w") as f:
        f.write('#usda 1.0\ndef "C" {}\n')
    with open(tmp_path / "anim.usda", "w") as f:
        f.write('#usda 1.0\ndef Xform "C" (\n    clips = {\n        dictionary default = {\n'
                '            string templateAssetPath = "./frames/f.#.usd"\n'
                '            double templateStartTime = 1\n            double templateEndTime = 3\n'
                '            double templateStride = 1\n'
                '            asset manifestAssetPath = @./frames/manifest.usda@\n'
                '            string primPath = "/C"\n        }\n    }\n) {}\n')
    candidates = ["anim.usda", "frames/f.1.usd", "frames/f.2.usd", "frames/f.3.usd",
                  "frames/manifest.usda"]

    assert choose_roots(candidates, _refs_on_disk(tmp_path)) == ["anim.usda"]


@pytest.mark.parametrize("name, content", [
    ("bad.usda", ""), ("bad.usda", "#usda 1.0\ndef garbage {{{\n"), ("bad.usd", "\x00\x01\x02 random bytes"),
], ids=["empty", "malformed", "binary-usd"])
def test_an_unreadable_layer_raises_with_its_path(tmp_path, name, content):
    pytest.importorskip("pxr")
    from usd_packager import UnreadableLayerError, compute_dependencies, direct_references

    bad = tmp_path / name
    bad.write_text(content)

    # pxr returns no layers, rather than raising, for an unreadable `.usd` root.
    with pytest.raises(UnreadableLayerError) as raised:
        compute_dependencies(str(bad))
    assert raised.value.path == str(bad)
    if name.endswith(".usda"):
        with pytest.raises(UnreadableLayerError):
            direct_references(str(bad), str(tmp_path))


def test_paths_outside_lists_files_whose_real_path_leaves_the_asset(tmp_path):
    from usd_packager import paths_outside

    asset = tmp_path / "asset"
    (asset / "textures").mkdir(parents=True)
    base = str(asset)
    paths = [
        os.path.join(base, "root.usda"),
        os.path.join(base, "textures", "wood.png"),
        os.path.join(base, "props", "chair.usdz[chair.usdc]"),
        os.path.join(base, "textures", "..", "..", "outside", "secret.png"),
        "/etc/passwd",
        str(tmp_path / "asset-other" / "tex.png"),  # a sibling sharing the prefix
    ]

    assert paths_outside(paths, base) == sorted({
        os.path.realpath(str(tmp_path / "outside" / "secret.png")), os.path.realpath("/etc/passwd"),
        os.path.realpath(str(tmp_path / "asset-other" / "tex.png"))})


@pytest.mark.parametrize("layers, has_geometry", [
    ({"root.usda": 'over "World" {\n}\n'}, False),
    ({"root.usda": 'def Scope "Looks" {\n    def Material "M" {\n    }\n}\n'}, False),
    ({"root.usda": 'def Xform "W" {\n    def Camera "Cam" {\n    }\n}\n'}, False),
    ({"root.usda": 'class Mesh "Proto" {\n}\n'}, False),
    ({"root.usda": 'def Mesh "M" (\n    active = false\n)\n{\n}\n'}, False),
    ({"root.usda": 'def Xform "W" {\n    def Mesh "Body" {\n    }\n}\n'}, True),
    ({"root.usda": 'def Xform "Rig" {\n    def SphereLight "Fill" {\n    }\n}\n'}, False),
    ({"root.usda": 'def Xform "W" (\n    instanceable = true\n    references = @./body.usda@\n)\n{\n}\n',
      "body.usda": '(\n    defaultPrim = "Body"\n)\ndef Xform "Body" {\n    def Mesh "Geo" {\n    }\n}\n'},
     True),
    ({"root.usda": 'def Xform "W" (\n    instanceable = true\n    payload = @./body.usda@\n)\n{\n}\n',
      "body.usda": '(\n    defaultPrim = "Body"\n)\ndef Xform "Body" {\n    def Mesh "Geo" {\n    }\n}\n'},
     True),
    ({"root.usda": 'def Xform "W" {\n}\n'}, False),
    ({"root.usda": 'def Cube "Box" {\n}\n'}, True),
    ({"root.usda": 'def BasisCurves "Hair" {\n}\n'}, True),
    ({"root.usda": 'def Volume "Smoke" {\n}\n'}, True),
    ({"root.usda": 'def PointInstancer "Trees" {\n}\n'}, True),
    ({"root.usda": 'def Xform "Asset" (\n    payload = @./body.usda@\n)\n{\n}\n',
      "body.usda": '(\n    defaultPrim = "Body"\n)\ndef Xform "Body" {\n    def Mesh "Geo" {\n    }\n}\n'},
     True),
], ids=["over-only", "scope-of-materials", "xform-and-camera", "class-only", "inactive-mesh", "mesh",
        "sphere-light-only", "instanceable-reference", "instanceable-payload", "empty-xform", "cube", "curves", "volume", "point-instancer",
        "geometry-in-a-payload"])
def test_a_root_renders_only_with_defined_geometry(tmp_path, layers, has_geometry):
    pytest.importorskip("pxr")
    from usd_packager import defines_geometry

    for name, body in layers.items():
        (tmp_path / name).write_text("#usda 1.0\n" + body)

    assert defines_geometry(str(tmp_path / "root.usda")) is has_geometry


@pytest.mark.parametrize("light", ["RectLight", "SphereLight", "DistantLight", "DomeLight", "DiskLight",
                                   "CylinderLight", "PortalLight"])
def test_a_light_is_not_geometry(tmp_path, light):
    pytest.importorskip("pxr")
    from pxr import UsdLux
    from usd_packager import defines_geometry

    if not hasattr(UsdLux, light):
        pytest.skip(f"{light} is not in this pxr")
    (tmp_path / "root.usda").write_text(f'#usda 1.0\ndef Xform "Rig" {{\n    def {light} "L" {{\n    }}\n}}\n')

    assert defines_geometry(str(tmp_path / "root.usda")) is False
