#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The pre-flight before packaging: texture formats by extension and bytes (PNG, JPEG and EXR pass,
WebP fails, anything else warns), unset stage metadata warns, a file pxr cannot open fails the run
whatever else the root defines, and a run that uploads writes its warnings to the results folder."""
import json
import zipfile

import pytest

from miris_upload_container_harness import ASSET_ID, definition

pytestmark = pytest.mark.unit

ROOT = f"{ASSET_ID}/"
REPORT_KEY = "pipelines/miris/job/output/E1/results/preflight_warnings.txt"
REPORT = {"preflightWarningsReport": {"bucketName": "asset-bucket", "objectKey": REPORT_KEY}}

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
_EXR = b"\x76\x2f\x31\x01" + b"0" * 32
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 32
_TGA = b"\x00\x00\x02\x00" + b"0" * 32


def _textured_scene(*paths):
    """A mesh whose material reads each of `paths`."""
    shaders = "".join(
        f'        def Shader "T{i}" {{\n            uniform token info:id = "UsdUVTexture"\n'
        f'            asset inputs:file = @./{path}@\n        }}\n' for i, path in enumerate(paths))
    return ('#usda 1.0\n(\n    upAxis = "Y"\n    metersPerUnit = 1\n)\ndef Mesh "M" {\n}\n'
            'def Material "Mat" {\n' + shaders + '}\n')


def _report_bodies(container):
    return [call.kwargs["Body"].decode("utf-8") for call in container.s3.put_object.call_args_list
            if call.kwargs["Key"] == REPORT_KEY]


def test_png_jpeg_and_exr_pass_by_extension_and_bytes():
    from preflight import classify

    files = [("a.png", _PNG), ("b.jpg", _JPEG), ("c.JPEG", _JPEG), ("dome.exr", _EXR),
             ("pkg.usdz[tex/d.png]", _PNG)]

    assert classify(files) == ([], [])


def test_a_texture_whose_bytes_do_not_match_its_extension_warns():
    from preflight import classify

    webp, warnings = classify([("a.png", _JPEG), ("b.exr", b"")])

    assert webp == []
    assert [w.split(":")[0] for w in warnings] == ["a.png", "b.exr"]
    assert "contents are not PNG" in warnings[0]


@pytest.mark.parametrize("layout", ["referenced", "inside-usdz-root", "png-named-webp"])
def test_with_pxr_a_webp_texture_fails_before_anything_reaches_miris(container, tmp_path, capsys, layout):
    pytest.importorskip("pxr")
    if layout == "inside-usdz-root":
        from pxr import Sdf, UsdUtils

        export = tmp_path / "export"
        (export / "textures").mkdir(parents=True)
        (export / "scene.usda").write_text(_textured_scene("textures/wood.webp"))
        (export / "textures" / "wood.webp").write_bytes(_WEBP)
        UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(export / "scene.usda")), str(tmp_path / "s.usdz"))
        container.s3.add("asset-bucket", f"{ROOT}scene.usdz", (tmp_path / "s.usdz").read_bytes())
        trigger, expected = "scene.usdz", "scene.usdz[textures/wood.webp]"
    else:
        name = "wood.webp" if layout == "referenced" else "wood.png"
        container.s3.add("asset-bucket", f"{ROOT}scene.usda", _textured_scene(f"textures/{name}"))
        container.s3.add("asset-bucket", f"{ROOT}textures/{name}", _WEBP)
        trigger, expected = "scene.usda", f"textures/{name}"

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel=trigger))

    assert exited.value.code == container.main.EXIT_UNSUPPORTED_TEXTURE_WEBP == 18
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"unsupported_texture_webp"' in line)
    assert entry["files"] == [expected] and "PNG or JPEG" in entry["remedy"]
    container.miris_cls.assert_not_called()


def test_with_pxr_another_texture_format_warns_and_the_run_uploads_and_reports_it(container, capsys):
    pytest.importorskip("pxr")
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", _textured_scene("textures/a.tga", "textures/b.png"))
    container.s3.add("asset-bucket", f"{ROOT}textures/a.tga", _TGA)
    container.s3.add("asset-bucket", f"{ROOT}textures/b.png", _PNG)

    container.run(definition(trigger_rel="scene.usda", **REPORT))

    container.miris.start_upload.assert_called_once()
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["warning"] for e in logged if e["msg"] == "preflight_warning"] == [
        "textures/a.tga: .tga is not PNG, JPEG or EXR"]
    [body] = _report_bodies(container)
    assert body.splitlines()[1:] == ["- textures/a.tga: .tga is not PNG, JPEG or EXR"]


def test_with_pxr_a_run_without_warnings_writes_no_report(container):
    pytest.importorskip("pxr")
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", _textured_scene("textures/b.png"))
    container.s3.add("asset-bucket", f"{ROOT}textures/b.png", _PNG)

    container.run(definition(trigger_rel="scene.usda", **REPORT))

    container.miris.start_upload.assert_called_once()
    assert _report_bodies(container) == []


def test_the_report_is_bounded_and_counts_what_it_leaves_out():
    from preflight import REPORT_MAX_BYTES, report_text

    text = report_text("scene.usda", [f"textures/{i:04}.tga: .tga is not PNG, JPEG or EXR" for i in range(1000)])

    assert len(text.encode("utf-8")) <= REPORT_MAX_BYTES
    assert text.startswith("Miris upload pre-flight warnings for scene.usda (1000):")
    assert text.rstrip().endswith("more; the container log lists them.")


@pytest.mark.parametrize("metadata, unset", [
    ('(\n    upAxis = "Z"\n    metersPerUnit = 0.01\n)\n', []),
    ('(\n    upAxis = "Z"\n)\n', ["metersPerUnit"]),
    ("", ["upAxis", "metersPerUnit"]),
], ids=["both-set", "no-meters-per-unit", "neither"])
def test_with_pxr_unset_up_axis_or_meters_per_unit_is_reported(tmp_path, metadata, unset):
    pytest.importorskip("pxr")
    from usd_packager import check_geometry

    (tmp_path / "root.usda").write_text(f'#usda 1.0\n{metadata}def Mesh "M" {{\n}}\n')

    assert check_geometry(str(tmp_path / "root.usda"), str(tmp_path)) == (True, [], unset)


def test_with_pxr_unset_stage_metadata_warns_and_the_run_uploads(container, monkeypatch):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", '#usda 1.0\ndef Mesh "M" {\n}\n')

    container.run(definition(trigger_rel="scene.usda", **REPORT))

    container.miris.start_upload.assert_called_once()
    [body] = _report_bodies(container)
    assert "does not set upAxis" in body and "does not set metersPerUnit" in body


@pytest.mark.parametrize("referenced, body", [
    ("geo.abc", b"Ogawa"), ("geo.obj", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"),
    ("geo.usd", "not a usd layer"), ("geo.usdz", b"PK not a package"),
], ids=["alembic", "obj", "malformed-usd", "malformed-usdz"])
@pytest.mark.parametrize("named_as", ["reference", "payload", "sublayer", "nested-sublayer"])
def test_with_pxr_an_unopenable_file_fails_even_beside_valid_geometry(
        container, monkeypatch, capsys, referenced, body, named_as):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    sublayer = '#usda 1.0\n(\n    subLayers = [@./{}@]\n)\n'
    geometry = 'def Mesh "Body" {\n    asset info:texture = @./textures/wood.png@\n}\n'
    if named_as in ("reference", "payload"):
        arc = "references" if named_as == "reference" else "payload"
        scene = f'#usda 1.0\n{geometry}def Xform "Part" (\n    {arc} = @./{referenced}@\n)\n{{\n}}\n'
    else:
        target = referenced if named_as == "sublayer" else "mid.usda"
        scene = sublayer.format(target) + geometry
        if named_as == "nested-sublayer":
            container.s3.add("asset-bucket", f"{ROOT}mid.usda", sublayer.format(referenced))
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", scene)
    container.s3.add("asset-bucket", f"{ROOT}textures/wood.png", _PNG)
    container.s3.add("asset-bucket", f"{ROOT}{referenced}", body)

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda", mirisRootFile="/scene.usda"))

    assert exited.value.code == container.main.EXIT_UNREADABLE_LAYER == 11
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"unreadable_layer"' in line)
    assert entry["files"] == [referenced] and entry["error"] == "pxr could not open a file the root names"
    container.miris_cls.assert_not_called()


_UNSELECTED_VARIANT = (
    '#usda 1.0\ndef Xform "Asset" (\n    variants = {\n        string lod = "hi"\n    }\n'
    '    prepend variantSets = "lod"\n)\n{\n    variantSet "lod" = {\n'
    '        "hi" {\n            def Mesh "M" {\n            }\n        }\n'
    '        "lo" {\n            def Xform "L" (\n                references = @./geo.abc@\n'
    '            )\n            {\n            }\n        }\n    }\n}\n')
_INACTIVE_PRIM = ('#usda 1.0\ndef Mesh "M" {\n}\n'
                  'def Xform "Off" (\n    active = false\n    references = @./geo.abc@\n)\n{\n}\n')


@pytest.mark.parametrize("scene", [_UNSELECTED_VARIANT, _INACTIVE_PRIM], ids=["unselected-variant", "inactive-prim"])
def test_with_pxr_an_unopenable_file_anywhere_in_the_layers_fails(container, monkeypatch, capsys, scene):
    pytest.importorskip("pxr")
    monkeypatch.setattr(container.main, "check_geometry", container.real_check_geometry)
    container.s3.add("asset-bucket", f"{ROOT}scene.usda", scene)
    container.s3.add("asset-bucket", f"{ROOT}geo.abc", b"Ogawa")

    with pytest.raises(SystemExit) as exited:
        container.run(definition(trigger_rel="scene.usda"))

    assert exited.value.code == container.main.EXIT_UNREADABLE_LAYER == 11
    entry = next(json.loads(line) for line in capsys.readouterr().out.splitlines()
                 if '"unreadable_layer"' in line)
    assert entry["files"] == ["geo.abc"]


def _recorded_stage_opens(monkeypatch):
    """Every (load argument, stage) Usd.Stage.Open returns from here on; the stages stay held."""
    from pxr import Usd

    opened, open_stage = [], Usd.Stage.Open

    def record(*args, **kwargs):
        stage = open_stage(*args, **kwargs)
        opened.append((args[1] if len(args) > 1 else kwargs.get("load"), stage))
        return stage

    monkeypatch.setattr(Usd.Stage, "Open", record)
    return opened


_PAYLOAD_SCENE = '#usda 1.0\ndef Xform "P" (\n    payload = @./geo.usda@\n)\n{\n}\n'
_PAYLOAD_GEO = '#usda 1.0\n(\n    defaultPrim = "G"\n)\ndef Xform "G" {\n    def Mesh "Geo" {\n    }\n}\n'


def test_with_pxr_a_root_with_direct_geometry_never_loads_its_payloads(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from pxr import Sdf, Usd
    from usd_packager import check_geometry

    (tmp_path / "root.usda").write_text(_PAYLOAD_SCENE.replace("#usda 1.0\n", '#usda 1.0\ndef Mesh "M" {\n}\n'))
    (tmp_path / "geo.usda").write_text(_PAYLOAD_GEO)
    opened = _recorded_stage_opens(monkeypatch)

    assert check_geometry(str(tmp_path / "root.usda"), str(tmp_path))[0] is True
    # The one stage is still held here, so a loaded payload would still be open.
    [(load, stage)] = opened
    assert load == Usd.Stage.LoadNone and list(stage.GetLoadSet()) == []
    assert Sdf.Layer.Find(str(tmp_path / "geo.usda")) is None


def test_with_pxr_geometry_only_in_payloads_is_found_by_loading_them_on_the_same_stage(tmp_path, monkeypatch):
    pytest.importorskip("pxr")
    from pxr import Sdf, Usd
    from usd_packager import check_geometry

    (tmp_path / "root.usda").write_text(_PAYLOAD_SCENE)
    (tmp_path / "geo.usda").write_text(_PAYLOAD_GEO)
    opened = _recorded_stage_opens(monkeypatch)

    assert check_geometry(str(tmp_path / "root.usda"), str(tmp_path))[0] is True
    # One stage, opened unloaded, then its payloads loaded: never a second, payload-loaded stage.
    [(load, stage)] = opened
    assert load == Usd.Stage.LoadNone
    assert list(stage.GetLoadSet()) == [Sdf.Path("/P")]


def test_a_package_that_is_not_a_zip_is_left_to_the_stage_check(tmp_path):
    from preflight import upload_files

    (tmp_path / "bad.usdz").write_bytes(b"PK not a package")
    with zipfile.ZipFile(tmp_path / "ok.usdz", "w") as package:
        package.writestr("scene.usda", "#usda 1.0\n")
        package.writestr("tex/a.png", _PNG)

    assert upload_files([str(tmp_path / "bad.usdz"), str(tmp_path / "ok.usdz")], str(tmp_path)) == [
        ("ok.usdz[tex/a.png]", _PNG[:12])]
