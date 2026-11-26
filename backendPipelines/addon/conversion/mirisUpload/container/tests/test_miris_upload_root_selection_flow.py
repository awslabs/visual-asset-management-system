#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The entry point lists the asset under its resolved root, chooses the root USD file whichever
file triggered the run, checks the size from the listing, and tags and names the Miris asset."""
import json
import os

import pytest

from miris_upload_container_harness import ASSET_ID, METADATA_PREFIX, definition, fake_usd

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"


def _layered_asset(container, root=ROOT):
    for rel in ("scene.usda", "geo/geo.usd", "mat.usdc", "textures/wood.png"):
        container.s3.add("asset-bucket", f"{root}{rel}", "x" * 10)


LAYER_GRAPH = {"scene.usda": {"geo/geo.usd", "mat.usdc"}, "geo/geo.usd": {"mat.usdc"}}


def _metadata_key(container):
    return container.s3.put_object.call_args.kwargs["Key"]


@pytest.mark.parametrize("trigger", ["scene.usda", "geo/geo.usd", "mat.usdc"])
def test_the_root_layer_is_uploaded_whichever_layer_triggered(container, monkeypatch, trigger):
    _layered_asset(container)
    fake_usd(container, monkeypatch, LAYER_GRAPH)

    container.run(definition(trigger_rel=trigger))

    start = container.miris.start_upload.call_args.kwargs
    assert start["content_path"] == "scene.usdz"
    assert _metadata_key(container) == f"{METADATA_PREFIX}scene.usda.metadata.json"
    body = json.loads(container.s3.put_object.call_args.kwargs["Body"])
    version = {e["metadataKey"]: e["metadataValue"] for e in body["metadata"]}["miris_source_version_id"]
    assert version == f"v-{ROOT}scene.usda"


@pytest.mark.parametrize("files, graph", [
    (("a.usda", "b.usda", "shared.usd"), {"a.usda": {"shared.usd"}, "b.usda": {"shared.usd"}}),
    (("a.usdz", "b.usdz"), {}),
], ids=["two-independent-layers", "several-usdz"])
def test_several_roots_fail_as_ambiguous_before_anything_reaches_miris(container, monkeypatch, capsys, files, graph):
    for rel in files:
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x")
    fake_usd(container, monkeypatch, graph)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel=files[0]))

    assert exited.value.code == container.main.EXIT_AMBIGUOUS_ROOT == 10
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"ambiguous_root"' in line)
    assert entry["roots"] == sorted(f for f in files if f != "shared.usd")
    assert entry["metadata_key"] == "miris_root_file"
    container.miris_cls.assert_not_called()
    # Without a report location the log entry is the only record.
    container.s3.put_object.assert_not_called()
    if files[0].endswith(".usdz"):
        assert container.s3.downloads == []


@pytest.mark.parametrize("put_fails", [False, True], ids=["written", "write-fails"])
def test_the_tied_roots_are_written_to_the_run_report(container, monkeypatch, put_fails):
    for rel in ("a.usda", "b.usda"):
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x")
    fake_usd(container, monkeypatch, {})
    if put_fails:
        container.s3.put_object.side_effect = Exception("AccessDenied")
    report = {"bucketName": "asset-bucket", "objectKey": "pipelines/m/job/output/E1/results/ambiguous_root.json"}

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="a.usda", ambiguousRootReport=report))

    assert exited.value.code == 10
    put = container.s3.put_object.call_args.kwargs
    assert (put["Bucket"], put["Key"]) == (report["bucketName"], report["objectKey"])
    assert json.loads(put["Body"]) == {"roots": ["a.usda", "b.usda"], "count": 2}


def test_the_ambiguous_root_entry_names_at_most_ten_roots(container, monkeypatch, capsys):
    for i in range(12):
        container.s3.add("asset-bucket", f"{ROOT}scene_{i:02}.usda", "x")
    fake_usd(container, monkeypatch, {})

    with pytest.raises(SystemExit):
        container.run(definition(trigger_rel="scene_00.usda"))

    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"ambiguous_root"' in line)
    assert (entry["count"], entry["roots"]) == (12, [f"scene_{i:02}.usda" for i in range(10)])


# --- the requested root: the selected file on a manual run, miris_root_file otherwise -----------


ROOM_GRAPH = {"room.usda": {"chair.usd"}}


@pytest.mark.parametrize("manual, expected", [(True, "chair.usd"), (False, "room.usda")],
                         ids=["manual-roots-at-the-selected-file", "automatic-roots-at-the-asset-root"])
def test_a_manual_run_roots_at_the_selected_file(container, monkeypatch, manual, expected):
    for rel in ("room.usda", "chair.usd"):
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x")
    fake_usd(container, monkeypatch, ROOM_GRAPH)

    container.run(definition(trigger_rel="chair.usd", manualRun=manual,
                             mirisRootFile="" if manual else None))

    assert _metadata_key(container) == f"{METADATA_PREFIX}{expected}.metadata.json"
    assert container.miris.start_upload.call_args.kwargs["content_path"] == (
        os.path.splitext(expected)[0] + ".usdz")


@pytest.mark.parametrize("value", ["/b.usda", "b.usda", " ./b.usda "])
def test_miris_root_file_names_the_root_of_an_automatic_run(container, monkeypatch, value):
    # Two unreferenced layers would otherwise fail as ambiguous.
    for rel in ("a.usda", "b.usda"):
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x")
    fake_usd(container, monkeypatch, {})

    container.run(definition(trigger_rel="a.usda", mirisRootFile=value))

    assert _metadata_key(container) == f"{METADATA_PREFIX}b.usda.metadata.json"


@pytest.mark.parametrize("value", ["missing.usda", "textures/wood.png", "../a.usda"])
def test_a_miris_root_file_that_names_no_usd_file_fails_before_any_download(container, capsys, value):
    for rel in ("a.usda", "textures/wood.png"):
        container.s3.add("asset-bucket", f"{ROOT}{rel}", "x")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="a.usda", mirisRootFile=value))

    assert exited.value.code == container.main.EXIT_ROOT_FILE_INVALID == 16
    assert '"root_file_invalid"' in capsys.readouterr().out
    assert container.s3.downloads == []


# --- with pxr: several candidates are never a guess; the root's dependency and geometry checks ---


_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
_GEO = ('#usda 1.0\n(\n    defaultPrim = "Body"\n)\ndef Xform "Body" {\n    def Mesh "Geo" {\n'
        '        asset info:texture = @./textures/wood.png@\n    }\n}\n')
_SCENE = '#usda 1.0\ndef Xform "World" (\n    references = @./geo.usda@\n)\n{\n}\n'
_BASE = {"scene.usda": _SCENE, "geo.usda": _GEO, "textures/wood.png": _PNG}


def _material_library(count):
    """An unused material library: `count` textured materials under a Scope, no geometry."""
    materials = "".join(
        f'    def Material "M{i}" {{\n        def Shader "T" {{\n'
        f'            uniform token info:id = "UsdUVTexture"\n'
        f'            asset inputs:file = @./textures/t{i}.png@\n        }}\n    }}\n'
        for i in range(count))
    library = {"matlib.usda": '#usda 1.0\ndef Scope "Looks" {\n' + materials + '}\n'}
    library.update({f"textures/t{i}.png": _PNG for i in range(count)})
    return library


def _light_rig(count):
    """An unused light rig: `count` textured RectLights, no geometry."""
    lights = "".join(
        f'    def RectLight "L{i}" {{\n        asset inputs:texture:file = @./textures/l{i}.png@\n    }}\n'
        for i in range(count))
    rig = {"lights.usda": '#usda 1.0\ndef Xform "Rig" {\n' + lights + '}\n'}
    rig.update({f"textures/l{i}.png": _PNG for i in range(count)})
    return rig


def _usdz_of(tmp_path, files):
    """The .usdz export of `files`' scene.usda."""
    from pxr import Sdf, UsdUtils

    export = tmp_path / "export"
    for rel, body in files.items():
        (export / rel).parent.mkdir(parents=True, exist_ok=True)
        (export / rel).write_bytes(body if isinstance(body, bytes) else body.encode())
    UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(export / "scene.usda")), str(tmp_path / "scene.usdz"))
    return (tmp_path / "scene.usdz").read_bytes()


def _layout(name, tmp_path):
    """The asset's files: the scene plus one unreferenced extra USD file."""
    if name == "instanced-scene-with-mesh-proxy":
        instanced = _SCENE.replace("(\n    references", "(\n    instanceable = true\n    references")
        return {**_BASE, "scene.usda": instanced,
                "scene_proxy.usd": '#usda 1.0\ndef Mesh "Proxy" {\n}\n'}
    if name == "self-contained-with-usdz":
        scene = {"scene.usda": '#usda 1.0\ndef Mesh "M" {\n}\n'}
        return {**scene, "scene.usdz": _usdz_of(tmp_path, scene)}
    extra = {
        "usdz-export": lambda: {"scene.usdz": _usdz_of(tmp_path, _BASE)},
        "proxy": lambda: {"scene_proxy.usd": '#usda 1.0\ndef Mesh "Proxy" {\n}\n'},
        "unused-materials": lambda: {"materials.usda": _texture_layer("./textures/wood.png")},
        "material-library": lambda: _material_library(2),
        "light-rig": lambda: _light_rig(2),
        "backup": lambda: {"scene_v001.usda": _SCENE},
    }[name]()
    return {**_BASE, **extra}


@pytest.mark.parametrize("layout, extra", [
    ("usdz-export", "scene.usdz"), ("proxy", "scene_proxy.usd"), ("unused-materials", "materials.usda"),
    ("material-library", "matlib.usda"), ("light-rig", "lights.usda"),
    ("instanced-scene-with-mesh-proxy", "scene_proxy.usd"), ("backup", "scene_v001.usda"),
    ("self-contained-with-usdz", "scene.usdz"),
])
def test_with_pxr_an_unreferenced_extra_file_is_never_guessed_past(container, monkeypatch, tmp_path, capsys,
                                                                   layout, extra):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    for rel, body in _layout(layout, tmp_path).items():
        container.s3.add("asset-bucket", f"{ROOT}{rel}", body)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_AMBIGUOUS_ROOT
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"ambiguous_root"' in line)
    assert entry["roots"] == sorted(["scene.usda", extra])


_SOURCES = ["manual", "miris_root_file", "asset"]


def _chosen_by(source, root):
    """The definition overrides that make `root` the root through `source`."""
    return {"manual": {"manualRun": True}, "miris_root_file": {"mirisRootFile": f"/{root}"}}.get(source, {})


@pytest.mark.parametrize("source", _SOURCES)
@pytest.mark.parametrize("reference", ["./geo.usda", "/mnt/projects/shared/geo.usda"],
                         ids=["not-uploaded-yet", "absolute-path"])
def test_with_pxr_a_missing_dependency_is_reported_before_the_geometry_check(
        container, monkeypatch, capsys, source, reference):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", _SCENE.replace("./geo.usda", reference))

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda", **_chosen_by(source, "scene.usda")))

    assert exited.value.code == container.main.EXIT_UNRESOLVED_REFERENCES == 5
    assert '"root_not_renderable"' not in capsys.readouterr().out


@pytest.mark.parametrize("referenced, body", [
    ("geo.abc", b"Ogawa"), ("geo.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
    ("geo.usd", "not a usd layer"), ("geo.usdz", b"PK not a package"),
], ids=["alembic", "obj", "malformed-usd", "malformed-usdz"])
@pytest.mark.parametrize("named_as", ["reference", "sublayer", "nested-sublayer"])
def test_with_pxr_a_file_pxr_cannot_open_is_named_not_the_root(
        container, monkeypatch, capsys, referenced, body, named_as):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    sublayer = '#usda 1.0\n(\n    subLayers = [@./{}@]\n)\n'
    if named_as == "reference":
        scene = _SCENE.replace("./geo.usda", f"./{referenced}")
    elif named_as == "sublayer":
        scene = sublayer.format(referenced)
    else:
        scene = sublayer.format("mid.usda")
        container.s3.add("asset-bucket", f"{ROOT}mid.usda", sublayer.format(referenced))
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", scene)
    container.s3.add("asset-bucket", f"{ROOT}{referenced}", body)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda", mirisRootFile="/scene.usda"))

    assert exited.value.code == container.main.EXIT_UNREADABLE_LAYER == 11
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"unreadable_layer"' in line)
    assert entry["file"] == referenced


@pytest.mark.parametrize("source", _SOURCES)
def test_with_pxr_a_root_without_geometry_fails_before_packaging(container, monkeypatch, capsys, source):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    packaged = []
    monkeypatch.setattr(container.main, "package_usdz", lambda *a: packaged.append(a))
    container.s3.add("asset-bucket", f"{ROOT}looks.usda", _texture_layer("./textures/wood.png"))
    container.s3.add("asset-bucket", f"{ROOT}textures/wood.png", _PNG)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="looks.usda", **_chosen_by(source, "looks.usda")))

    assert exited.value.code == container.main.EXIT_ROOT_NOT_RENDERABLE == 15
    out = capsys.readouterr().out
    assert '"root_not_renderable"' in out and '"unreadable_layer"' not in out
    assert packaged == []
    container.miris_cls.assert_not_called()


def test_a_single_usdz_is_measured_and_uploaded_as_is_and_alone(container):
    container.s3.add("asset-bucket", f"{ROOT}scenes/pump.usdz", "usdz")
    container.s3.add("asset-bucket", f"{ROOT}reference/scan.e57", b"x" * 2_000_000)

    container.run(definition(trigger_rel="scenes/pump.usdz"))

    assert [key for _, key, _ in container.s3.downloads] == [f"{ROOT}scenes/pump.usdz"]
    assert container.miris.start_upload.call_args.kwargs["content_path"] == "pump.usdz"


def test_an_asset_without_a_usd_file_fails_with_root_not_found(container):
    container.s3.add("asset-bucket", f"{ROOT}notes.txt", "x")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="model.usdz"))

    assert exited.value.code == container.main.EXIT_ROOT_NOT_FOUND == 6


def test_only_configured_root_extensions_are_candidates(container):
    container.s3.add("asset-bucket", f"{ROOT}pump.usdz", "usdz")
    container.s3.add("asset-bucket", f"{ROOT}draft.usda", "x")

    container.run(definition(trigger_rel="pump.usdz", rootExtensions=[".usdz"]))

    assert container.miris.start_upload.call_args.kwargs["content_path"] == "pump.usdz"


def test_keys_with_dot_segments_name_the_same_layers_as_references(container, monkeypatch):
    """'./scene.usda' and 'geo/x/../geo.usd' are the files a resolved reference names
    'scene.usda' and 'geo/geo.usd'; matching them raw would leave geo.usd looking unreferenced."""
    container.s3.add("asset-bucket", f"{ROOT}./scene.usda", "x")
    container.s3.add("asset-bucket", f"{ROOT}geo/x/../geo.usd", "x")
    fake_usd(container, monkeypatch, {"scene.usda": {"geo/geo.usd"}})

    container.run(definition(trigger_rel="geo/geo.usd"))

    assert _metadata_key(container) == f"{METADATA_PREFIX}scene.usda.metadata.json"


# --- unreadable layers ------------------------------------------------------------------------


def _raise_unreadable_for(container, monkeypatch, bad_rel):
    from usd_packager import UnreadableLayerError

    def direct_references(path, base):
        if path.endswith(bad_rel):
            raise UnreadableLayerError(path, RuntimeError(f"Error in parsing {path}"))
        return set()

    monkeypatch.setattr(container.main, "direct_references", direct_references)


def test_an_unreadable_layer_fails_with_its_asset_relative_name(container, monkeypatch, capsys):
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", "x")
    container.s3.add("asset-bucket", f"{ROOT}geo/broken.usd", "")
    _raise_unreadable_for(container, monkeypatch, "geo/broken.usd")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_UNREADABLE_LAYER == 11
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    entry = next(e for e in logged if e["msg"] == "unreadable_layer")
    assert entry["file"] == "geo/broken.usd"
    assert str(container.workdir) not in json.dumps(entry)
    container.miris_cls.assert_not_called()


def test_with_pxr_an_unreadable_dependency_of_a_lone_root_is_named(container, capsys):
    """A lone candidate is not scanned for the root choice; its dependency check names the layer."""
    pytest.importorskip("pxr")
    container.s3.add("asset-bucket", f"{ROOT}scene.usda",
                     '#usda 1.0\n(\n    subLayers = [@./geo/bad.usdc@]\n)\n')
    container.s3.add("asset-bucket", f"{ROOT}geo/bad.usdc", "not a crate file")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda", rootExtensions=[".usda"]))

    assert exited.value.code == 11
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"unreadable_layer"' in line)
    assert entry["file"] == "geo/bad.usdc"


# --- paths ------------------------------------------------------------------------------------


def test_a_prefixed_asset_root_is_listed_and_written_relative_to_the_asset(container, monkeypatch):
    """A bucket registered with baseAssetsPrefix "vams/data/" roots the asset there."""
    root = f"vams/data/{ASSET_ID}/"
    _layered_asset(container, root)
    # A same-named asset outside the prefix must not be read.
    container.s3.add("asset-bucket", f"{ASSET_ID}/other.usda", "x")
    fake_usd(container, monkeypatch, LAYER_GRAPH)

    container.run(definition(asset_root=root, trigger_rel="geo/geo.usd"))

    assert container.s3.list_calls == [("asset-bucket", root)]
    assert all(key.startswith(root) for _, key, _ in container.s3.downloads)
    assert _metadata_key(container) == f"{METADATA_PREFIX}scene.usda.metadata.json"


def test_an_existing_key_asset_root_without_the_asset_id_works(container):
    root = "imports/2026/pump-site/"
    container.s3.add("asset-bucket", f"{root}pump.usdz", "usdz")

    container.run(definition(asset_root=root, trigger_rel="pump.usdz"))

    assert _metadata_key(container) == f"{METADATA_PREFIX}pump.usdz.metadata.json"


@pytest.mark.parametrize("free", [10 ** 12, 1_000], ids=["oversized-usdz", "disk-full"])
def test_an_oversized_usdz_or_a_full_disk_fails_before_any_download(container, monkeypatch, free):
    container.s3.add("asset-bucket", f"{ROOT}pump.usdz", b"x" * (2_000_000 if free > 1_000 else 10))
    monkeypatch.setattr(container.main, "free_disk_bytes", lambda path: free)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="pump.usdz"))

    assert exited.value.code == container.main.EXIT_TOO_LARGE == 4
    assert container.s3.downloads == []
    container.miris_cls.assert_not_called()


@pytest.mark.parametrize("texture_size, uploads", [(600_000, True), (1_200_000, False)],
                         ids=["tree-within-limit", "tree-over-limit"])
def test_the_size_limit_applies_to_the_dependency_tree(container, monkeypatch, texture_size, uploads):
    # The tree is scene.usda and wood.exr; unused.exr alone is over the 1,000,000-byte limit.
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", "x" * 10)
    container.s3.add("asset-bucket", f"{ROOT}textures/wood.exr", b"x" * texture_size)
    container.s3.add("asset-bucket", f"{ROOT}textures/unused.exr", b"x" * 2_000_000)
    fake_usd(container, monkeypatch, {})
    monkeypatch.setattr(container.main, "compute_dependencies", lambda path: (
        [path], [os.path.join(os.path.dirname(path), "textures", "wood.exr")], [], []))

    if uploads:
        container.run(definition(trigger_rel="scene.usda"))
        assert container.miris.start_upload.call_args.kwargs["content_path"] == "scene.usdz"
    else:
        with pytest.raises(SystemExit) as exited:
            container.run(definition(trigger_rel="scene.usda"))
        assert exited.value.code == container.main.EXIT_TOO_LARGE == 4
        container.miris_cls.assert_not_called()


@pytest.mark.parametrize("stage_overrides", [
    {"databaseId": ""}, {"assetId": ""}, {"assetRoot": {}},
    {"assetRoot": {"bucketName": "asset-bucket", "objectDir": "/"}},
], ids=["no-database", "no-asset", "no-root", "bucket-root"])
def test_a_definition_without_identity_or_root_fails_before_listing(container, stage_overrides):
    with pytest.raises(SystemExit) as exited:
        container.run(definition(**stage_overrides))

    assert exited.value.code == container.main.EXIT_USAGE == 2
    assert container.s3.list_calls == []


# --- tags and content path --------------------------------------------------------------------


def test_the_binding_tags_are_always_written_and_reserved_configured_tags_are_dropped(container, caplog):
    container.s3.add("asset-bucket", f"{ROOT}pump.usdz", "usdz")
    container.s3.add("config-bucket", "cfg.json", json.dumps({"mirisTags": [
        "scan", "vams-asset-someone-else", " VAMS-Database-other", "site-7"]}))

    container.run(definition(inputConfigurationS3Location="s3://config-bucket/cfg.json"))

    assert container.miris.start_upload.call_args.kwargs["tags"] == [
        "vams", f"vams-asset-{ASSET_ID}", "vams-database-db1", "scan", "site-7"]
    dropped = [r for r in caplog.records if r.levelname == "WARNING" and "reserved" in r.getMessage()]
    assert len(dropped) == 2


@pytest.mark.parametrize("root_rel", ["scenes/Coral House.usdz", "Red Chair.usda"], ids=["usdz", "packaged-layer"])
def test_the_content_path_is_the_root_name_and_the_uploaded_key_verbatim(container, monkeypatch, root_rel):
    """The temporary-upload key is the content_path, spaces and case included."""
    container.s3.add("asset-bucket", f"{ROOT}{root_rel}", "x")
    if root_rel.endswith(".usda"):
        container.s3.add("asset-bucket", f"{ROOT}geo/geo.usd", "x")
    fake_usd(container, monkeypatch, {"Red Chair.usda": {"geo/geo.usd"}})

    container.run(definition(trigger_rel=root_rel))

    content_path = container.miris.start_upload.call_args.kwargs["content_path"]
    put = container.temp_s3.put_object.call_args.kwargs
    assert content_path == os.path.splitext(os.path.basename(root_rel))[0] + ".usdz"
    assert (put["Bucket"], put["Key"]) == ("miris-temp", f"prefix/{content_path}")


# --- references outside the asset -------------------------------------------------------------


def _outside_file(container, rel):
    """A readable file beside the container's working directory, outside the downloaded asset."""
    path = container.workdir.parent / "outside" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


def _texture_layer(asset_path):
    return ('#usda 1.0\ndef Material "M" {\n    def Shader "T" {\n'
            '        uniform token info:id = "UsdUVTexture"\n'
            f'        asset inputs:file = @{asset_path}@\n    }}\n}}\n')


@pytest.mark.parametrize("reference", ["absolute-texture", "parent-texture", "sublayer"])
def test_with_pxr_a_file_outside_the_asset_is_never_packaged(container, capsys, reference):
    pytest.importorskip("pxr")
    if reference == "sublayer":
        outside = _outside_file(container, "geo.usda")
        outside.write_text('#usda 1.0\ndef Xform "G" {}\n')
        layer = '#usda 1.0\n(\n    subLayers = [@../../outside/geo.usda@]\n)\n'
    else:
        outside = _outside_file(container, "secret.png")
        layer = _texture_layer(str(outside) if reference == "absolute-texture" else "../../outside/secret.png")
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", layer)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_REFERENCE_OUTSIDE_ASSET == 12
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    [entry] = [e for e in logged if e["msg"] == "reference_outside_asset"]
    assert str(outside.resolve()) in entry["paths"]
    container.miris_cls.assert_not_called()
    container.temp_s3.put_object.assert_not_called()
    assert not (container.workdir / "scene.usdz").exists()


def test_with_pxr_a_texture_inside_the_asset_is_packaged(container):
    pytest.importorskip("pxr")
    import zipfile

    container.s3.add("asset-bucket", f"{ROOT}scene.usda", _texture_layer("./textures/wood.png"))
    container.s3.add("asset-bucket", f"{ROOT}textures/wood.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    container.run(definition(trigger_rel="scene.usda"))

    assert container.miris.start_upload.call_args.kwargs["content_path"] == "scene.usdz"
    with zipfile.ZipFile(container.workdir / "scene.usdz") as packaged:
        assert sorted(packaged.namelist()) == ["scene.usda", "textures/wood.png"]
