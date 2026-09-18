# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`handler.lambda_handler`: the BLENDER branch contract end to end, with S3, Blender and trimesh replaced.

The fake Blender parses the real command line the handler builds (`--output-dir`, `--views`) and writes the
files the real script would, so the assertions cover the handler's side of the renderScene.py contract
rather than a stub's idea of it. The failure paths are the point: a non-zero exit, a timeout, an import
failure and a partial render must each leave a manifest with attributes and a warning -- never a raise,
because a raise would fail the execution that the render-fault policy says continues attributes-only.
"""

import hashlib
import json
import math
import os
import subprocess
from types import SimpleNamespace

import pytest

from blenderTestSupport import FakeContext, FakeS3, load_handler
from test_blender_render_scene_dispatch import exec_named_nodes

ASSET_BUCKET = "asset-bucket"
AUX_BUCKET = "aux-bucket"
PRIMARY_KEY = "xid1234/parts/pump.glb"
PRIMARY_BYTES = b"glb-bytes-of-the-primary-file"
AUX_PREFIX = "pipelines/system-genai-metadata/exec-1/"
MANIFEST_KEY = AUX_PREFIX + "analysis.json"
CONFIG_KEY = "pipelines/workflowExecutionInputs/exec-1/pipeline1/config.json"

PREWRITTEN_MANIFEST = {
    "schemaVersion": 1,
    "fileClass": "mesh",
    "renderBranch": "BLENDER",
    "attributes": {"sys_file": {
        "name": "pump.glb", "ext": ".glb", "sizeBytes": len(PRIMARY_BYTES),
        "contentType": "model/gltf-binary", "etag": "9b2cf535", "versionId": "v-primary",
    }},
    "renderImages": [],
    "textExcerpt": None,
    "facts": {},
    "warnings": [],
    "renderSkipped": "unsupported",
}

TRIMESH_SECTIONS = {
    "sys_geometry": {"dimensions": {"width": 1.0, "height": 2.0, "depth": 3.0}, "extentMax": 3.0},
    "sys_statistics": {"meshCount": 1, "vertices": 8, "faces": 12},
    "sys_format": {"extension": ".glb", "loader": "trimesh", "loadedType": "Scene"},
    "sys_visual": {"materialCount": 1},
    "sys_scene": {"nodeCount": 2},
}

# What renderScene.py writes for a 1 x 2 x 3 box imported from a metre-converted format.
BLENDER_FACTS = {
    "schemaVersion": 1, "blenderVersion": "4.5.13", "meshObjects": 1, "objectCounts": {"MESH": 1},
    "vertices": 8, "faces": 12, "triangles": 12, "boundsMin": [-0.5, -1.0, -1.5], "boundsMax": [0.5, 1.0, 1.5],
    "dimensions": {"width": 1.0, "height": 2.0, "depth": 3.0}, "surfaceArea": 22.0, "volume": 6.0,
    "watertight": True, "materials": 1, "materialNames": ["Grey"], "images": 0, "hasUv": False,
    "hasVertexColors": False, "hasArmature": False, "hasAnimation": False, "upAxis": "Z", "units": "m",
}


def _event(**overrides):
    event = {
        "inputS3AssetFilePath": f"s3://{ASSET_BUCKET}/{PRIMARY_KEY}",
        "versionId": "v-primary",
        "relativePath": "/parts/pump.glb",  # leading-slash form and unquoted etag: the state table's forms
        "assetId": "xid1234",
        "databaseId": "db1",
        "etag": "9b2cf535",
        "fileSize": len(PRIMARY_BYTES),
        "contentType": "model/gltf-binary",
        "fileClass": "mesh",
        "renderBranch": "BLENDER",
        "inputOutputS3AssetAuxiliaryFilesPath": f"s3://{AUX_BUCKET}/{AUX_PREFIX}",
        "analysisManifestS3Location": f"s3://{AUX_BUCKET}/{MANIFEST_KEY}",
        "inputConfigurationS3Location": f"s3://run-bucket/{CONFIG_KEY}",
    }
    event.update(overrides)
    return {k: v for k, v in event.items() if v is not None}


def _config(**values):
    body = {"includeSiblingFiles": False, "renderViews": 8}
    body.update(values)
    return json.dumps(body).encode("utf-8")


def _argument(command, flag):
    return command[command.index(flag) + 1]


class FakeBlender:
    """Stands in for subprocess.run: writes the files renderScene.py would, records the call."""

    def __init__(self, returncode=0, fail_views=(), facts=BLENDER_FACTS, import_error=None, timeout_after=None):
        self.returncode = returncode
        self.fail_views = set(fail_views)
        self.facts = facts
        self.import_error = import_error
        self.timeout_after = timeout_after
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), "kwargs": kwargs})
        output_dir = _argument(command, "--output-dir")
        views = _argument(command, "--views").split(",")
        if self.timeout_after is not None:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial log", stderr="")
        if self.import_error is not None:
            facts = {"schemaVersion": 1, "blenderVersion": "4.5.13", "error": self.import_error}
        else:
            rendered = [view for view in views if view not in self.fail_views]
            for view in rendered:
                with open(os.path.join(output_dir, f"render_{view}.png"), "wb") as handle:
                    handle.write(b"\x89PNG" + view.encode("utf-8"))
            facts = dict(self.facts)
            facts["renderedViews"] = rendered
            facts["failedViews"] = {view: "RuntimeError: boom" for view in views if view in self.fail_views}
        with open(os.path.join(output_dir, "scene_facts.json"), "w", encoding="utf-8") as handle:
            json.dump(facts, handle)
        return SimpleNamespace(returncode=self.returncode, stdout="Blender 4.5.13\nFra:1 Mem:12M\n", stderr="")


def _setup(monkeypatch, *, config=None, prewritten=True, blender=None, trimesh=None, extra_objects=None):
    objects = {PRIMARY_KEY: PRIMARY_BYTES}
    if prewritten:
        objects[MANIFEST_KEY] = json.dumps(PREWRITTEN_MANIFEST).encode("utf-8")
    if config is not None:
        objects[CONFIG_KEY] = config
    objects.update(extra_objects or {})
    fake_s3 = FakeS3(objects=objects)
    handler = load_handler(fake_s3)
    blender = blender or FakeBlender()
    monkeypatch.setattr(handler.subprocess, "run", blender)
    trimesh_calls = []

    def _trimesh(path, extension):
        trimesh_calls.append((path, extension))
        return (trimesh if trimesh is not None else {k: dict(v) for k, v in TRIMESH_SECTIONS.items()}), []

    monkeypatch.setattr(handler, "compute_trimesh_attributes", _trimesh)
    return handler, fake_s3, blender, trimesh_calls


def _manifest(fake_s3):
    puts = [p for p in fake_s3.puts if p["Key"] == MANIFEST_KEY]
    assert len(puts) == 1, f"expected exactly one manifest write, saw {[p['Key'] for p in fake_s3.puts]}"
    assert puts[0]["Bucket"] == AUX_BUCKET and puts[0]["ContentType"] == "application/json"
    return json.loads(puts[0]["Body"].decode("utf-8"))


@pytest.mark.unit
def test_success_renders_every_view_and_writes_the_manifest(monkeypatch):
    handler, fake_s3, blender, trimesh_calls = _setup(monkeypatch, config=_config())
    event = _event()
    result = handler.lambda_handler(event, FakeContext([600_000]))

    expected_keys = {f"{AUX_PREFIX}renders/render_{view}.png" for view in handler.VIEW_ORDER}
    assert set(result["renderImages"]) == expected_keys and len(result["renderImages"]) == 8
    assert result["renderStatus"] == "SUCCEEDED" and result["renderSkipped"] is None
    assert result["renderBranch"] == "BLENDER"
    assert result["analysisManifestS3Location"] == f"s3://{AUX_BUCKET}/{MANIFEST_KEY}"
    assert result["warnings"] == []
    assert result["renderImageCount"] == 8 and result["warningCount"] == 0, "branch-task counts"
    # The whole state comes back extended (every branch Lambda returns the state), so the state machine may
    # take `$.Payload` as the new state without losing the fields GenerateMetadataTask reads.
    assert result["inputS3AssetFilePath"] == event["inputS3AssetFilePath"] and result["fileClass"] == "mesh"
    assert result["inputConfigurationS3Location"] == event["inputConfigurationS3Location"]
    assert set(event) <= set(result) and "renderStatus" not in event, "input state preserved, not mutated"

    assert {u["key"] for u in fake_s3.uploads} == expected_keys
    assert all(u["bucket"] == AUX_BUCKET and u["extra"] == {"ContentType": "image/png"} for u in fake_s3.uploads)

    manifest = _manifest(fake_s3)
    assert manifest["schemaVersion"] == 1 and manifest["fileClass"] == "mesh" and manifest["renderBranch"] == "BLENDER"
    assert set(manifest["renderImages"]) == expected_keys
    assert manifest["renderSkipped"] is None and manifest["textExcerpt"] is None and manifest["warnings"] == []
    attributes = manifest["attributes"]
    assert set(attributes) == {"sys_file", "sys_geometry", "sys_statistics", "sys_format", "sys_visual", "sys_scene"}
    sys_file = attributes["sys_file"]
    assert sys_file["etag"] == "9b2cf535" and sys_file["versionId"] == "v-primary", \
        "pre-written sys_file carried forward"
    assert sys_file["sha256"] == hashlib.sha256(PRIMARY_BYTES).hexdigest()
    assert attributes["sys_geometry"]["dimensions"] == {"width": 1.0, "height": 2.0, "depth": 3.0}
    assert attributes["sys_scene"]["objectCounts"] == {"MESH": 1}, "Blender facts merged into sys_scene"
    # glTF is metres by specification: the declaration lands beside the loader under sys_format.
    assert attributes["sys_format"]["declaredUnits"] == "m"
    assert attributes["sys_format"]["declaredUnitsSource"] == "specification"
    diagnostics = manifest["renderDiagnostics"]
    assert diagnostics["requestedViews"] == list(handler.VIEW_ORDER)
    assert sorted(diagnostics["renderedViews"]) == sorted(handler.VIEW_ORDER)
    assert diagnostics["blenderExitCode"] == 0 and diagnostics["blender"]["blenderVersion"] == "4.5.13"

    # The primary was fetched at its version and no sibling listing happened (includeSiblingFiles false).
    assert fake_s3.downloads[0]["key"] == PRIMARY_KEY and fake_s3.downloads[0]["extra"] == {"VersionId": "v-primary"}
    assert fake_s3.list_calls == []
    assert trimesh_calls and trimesh_calls[0][1] == ".glb"


@pytest.mark.unit
def test_blender_command_line_is_the_render_scene_contract(monkeypatch):
    handler, _fake_s3, blender, _trimesh_calls = _setup(monkeypatch, config=_config())
    handler.lambda_handler(_event(), FakeContext([600_000]))
    call = blender.calls[0]
    command, kwargs = call["command"], call["kwargs"]
    assert command[0] == "blender"
    assert command[1:4] == ["--background", "-noaudio", "--python"]
    assert command[4].endswith("renderScene.py") and command[5] == "--"
    assert _argument(command, "--input").endswith("pump.glb")
    assert _argument(command, "--views") == ",".join(handler.VIEW_ORDER)
    assert _argument(command, "--resolution") == "768" and _argument(command, "--samples") == "32"
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["timeout"] == 600 - handler.RENDER_RESERVE_SECONDS
    # Every directory Blender writes lives under the invocation's work directory, beside the output dir.
    work_dir = os.path.dirname(_argument(command, "--output-dir"))
    env = kwargs["env"]
    for variable in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "BLENDER_USER_RESOURCES",
                     "TMPDIR"):
        assert os.path.dirname(env[variable]) == work_dir, variable
    assert env["DISPLAY"] == ""


@pytest.mark.unit
def test_render_views_limits_the_views_in_order(monkeypatch):
    handler, fake_s3, blender, _ = _setup(monkeypatch, config=_config(renderViews=3))
    result = handler.lambda_handler(_event(), FakeContext([600_000]))
    assert _argument(blender.calls[0]["command"], "--views") == "perspective_front,front,right"
    assert len(result["renderImages"]) == 3 and result["renderImageCount"] == 3 and len(fake_s3.uploads) == 3
    assert _manifest(fake_s3)["renderDiagnostics"]["requestedViews"] == ["perspective_front", "front", "right"]


@pytest.mark.unit
def test_include_sibling_files_lists_the_parent_prefix(monkeypatch):
    handler, fake_s3, _blender, _ = _setup(
        monkeypatch, config=_config(includeSiblingFiles=True),
        extra_objects={"xid1234/parts/pump.bin": b"bin", "xid1234/parts/tex/wood.png": b"png"},
    )
    fake_s3.listing = [
        {"Key": PRIMARY_KEY, "Size": len(PRIMARY_BYTES)},
        {"Key": "xid1234/parts/pump.bin", "Size": 3},
        {"Key": "xid1234/parts/tex/wood.png", "Size": 3},
    ]
    handler.lambda_handler(_event(), FakeContext([600_000]))
    assert fake_s3.list_calls == [{"Bucket": ASSET_BUCKET, "Prefix": "xid1234/parts/"}]
    assert sorted(d["key"] for d in fake_s3.downloads) == sorted(
        [PRIMARY_KEY, "xid1234/parts/pump.bin", "xid1234/parts/tex/wood.png"])


@pytest.mark.unit
def test_nonzero_exit_without_renders_degrades_instead_of_raising(monkeypatch):
    blender = FakeBlender(returncode=2, import_error="SceneImportError: the file imported no mesh objects")
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config(), blender=blender)
    result = handler.lambda_handler(_event(), FakeContext([600_000]))
    # `renderSkipped: "error"` on the returned STATE is what the usd fallback Choice reads.
    assert result["renderStatus"] == "DEGRADED" and result["renderSkipped"] == "error"
    assert result["renderImages"] == [] and fake_s3.uploads == []
    manifest = _manifest(fake_s3)
    assert manifest["renderSkipped"] == "error" and manifest["renderImages"] == []
    assert result["renderImageCount"] == 0 and result["warningCount"] == len(manifest["warnings"]) >= 2
    assert any("exited with code 2" in w for w in manifest["warnings"])
    assert any("Blender import failed" in w and "no mesh objects" in w for w in manifest["warnings"])
    assert manifest["renderDiagnostics"]["blenderExitCode"] == 2
    # Attributes still land: the trimesh sections plus the carried-forward sys_file.
    assert manifest["attributes"]["sys_geometry"]["extentMax"] == 3.0
    assert manifest["attributes"]["sys_file"]["sha256"] == hashlib.sha256(PRIMARY_BYTES).hexdigest()
    assert "objectCounts" not in manifest["attributes"].get("sys_scene", {}), \
        "facts carrying an error contribute nothing"


@pytest.mark.unit
def test_timeout_is_the_remaining_time_minus_the_reserve_and_degrades(monkeypatch):
    blender = FakeBlender(timeout_after=True)
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config(), blender=blender)
    result = handler.lambda_handler(_event(), FakeContext([600_000]))
    assert blender.calls[0]["kwargs"]["timeout"] == 510
    assert result["renderStatus"] == "DEGRADED" and result["renderSkipped"] == "error"
    manifest = _manifest(fake_s3)
    assert any("timed out" in w for w in manifest["warnings"])
    assert manifest["renderDiagnostics"]["blenderExitCode"] is None
    assert manifest["attributes"]["sys_statistics"]["meshCount"] == 1


@pytest.mark.unit
def test_render_budget_floor_and_no_context_default(monkeypatch):
    handler, _fake_s3, _blender, _ = _setup(monkeypatch, config=_config())
    assert handler.render_budget_seconds(FakeContext([100_000])) == handler.MIN_RENDER_SECONDS
    assert handler.render_budget_seconds(FakeContext([600_000])) == 510
    assert handler.render_budget_seconds(None) == handler.DEFAULT_RENDER_SECONDS_WITHOUT_CONTEXT
    assert handler.MIN_RENDER_SECONDS == 30 and handler.RENDER_RESERVE_SECONDS == 90


@pytest.mark.unit
def test_partial_render_keeps_the_images_and_records_the_failed_views(monkeypatch):
    blender = FakeBlender(fail_views=("bottom",))
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config(), blender=blender)
    result = handler.lambda_handler(_event(), FakeContext([600_000]))
    assert result["renderStatus"] == "SUCCEEDED" and result["renderSkipped"] is None
    assert len(result["renderImages"]) == 7 and result["renderImageCount"] == 7 and result["warningCount"] == 1
    assert f"{AUX_PREFIX}renders/render_bottom.png" not in result["renderImages"]
    manifest = _manifest(fake_s3)
    assert any("views failed to render" in w and "bottom" in w for w in manifest["warnings"])
    assert "bottom" not in manifest["renderDiagnostics"]["renderedViews"]


@pytest.mark.unit
def test_manifest_facts_are_flat_human_readable_strings(monkeypatch):
    # The metadata generator joins `facts` as `label: value` into the model prompt and the embedding source
    # text, so a nested dict or a list here would reach the model as a Python repr; machine diagnostics live
    # under renderDiagnostics.
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config())
    handler.lambda_handler(_event(), FakeContext([600_000]))
    manifest = _manifest(fake_s3)
    facts = manifest["facts"]
    assert facts and all(isinstance(value, str) for value in facts.values()), facts
    assert facts == {"dimensions": "1 x 2 x 3", "meshes": "1", "vertices": "8", "faces": "12",
                     "materials": "1", "armature": "no", "animation": "no"}
    assert set(manifest["renderDiagnostics"]) == {
        "blender", "renderSeconds", "requestedViews", "renderedViews", "blenderExitCode"}
    assert handler.build_facts({}) == {}
    assert handler.build_facts({
        "sys_geometry": {"dimensions": {"width": 1200.5, "height": 0.25, "depth": 3.0}, "units": "m"},
        "sys_visual": {"materialNames": ["Steel", "Rubber"], "textureCount": 2},
    }) == {"dimensions": "1,200.5 x 0.25 x 3 m", "materials": "Steel, Rubber", "textures": "2"}


@pytest.mark.unit
def test_missing_prewritten_manifest_builds_sys_file_from_the_event(monkeypatch):
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config(), prewritten=False)
    handler.lambda_handler(_event(), FakeContext([600_000]))
    sys_file = _manifest(fake_s3)["attributes"]["sys_file"]
    assert sys_file == {
        "name": "pump.glb", "ext": ".glb", "sizeBytes": len(PRIMARY_BYTES), "contentType": "model/gltf-binary",
        "etag": "9b2cf535", "versionId": "v-primary", "sha256": hashlib.sha256(PRIMARY_BYTES).hexdigest(),
    }


@pytest.mark.unit
def test_sha256_is_omitted_above_the_hashing_cap(monkeypatch):
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config(), prewritten=False)
    monkeypatch.setattr(handler, "SHA256_MAX_BYTES", 4)
    handler.lambda_handler(_event(), FakeContext([600_000]))
    assert "sha256" not in _manifest(fake_s3)["attributes"]["sys_file"]


@pytest.mark.unit
def test_unreadable_configuration_uses_defaults_with_a_warning(monkeypatch):
    handler, fake_s3, blender, _ = _setup(monkeypatch, config=b"{not json")
    fake_s3.listing = [{"Key": PRIMARY_KEY, "Size": len(PRIMARY_BYTES)}]
    result = handler.lambda_handler(_event(), FakeContext([600_000]))
    assert _argument(blender.calls[0]["command"], "--views") == ",".join(handler.VIEW_ORDER)
    assert fake_s3.list_calls != [], "the default includeSiblingFiles=true lists the parent prefix"
    assert any("input configuration unreadable" in w for w in result["warnings"])
    assert result["renderStatus"] == "SUCCEEDED"


@pytest.mark.unit
@pytest.mark.parametrize("event_file_class, expected", [(None, "mesh"), ("usd", "usd")])
def test_file_class_on_the_returned_state_is_authoritative(monkeypatch, event_file_class, expected):
    # The branch returns `fileClass` as the authoritative value -- the event's, or this branch's default when
    # the event carries none -- and the manifest records the same value.
    handler, fake_s3, _blender, _ = _setup(monkeypatch, config=_config())
    result = handler.lambda_handler(_event(fileClass=event_file_class), FakeContext([600_000]))
    assert result["fileClass"] == expected and _manifest(fake_s3)["fileClass"] == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing", ["inputS3AssetFilePath", "analysisManifestS3Location", "inputOutputS3AssetAuxiliaryFilesPath"])
def test_missing_required_key_is_a_contract_fault(monkeypatch, missing):
    handler, fake_s3, blender, _ = _setup(monkeypatch, config=_config())
    with pytest.raises(ValueError) as excinfo:
        handler.lambda_handler(_event(**{missing: None}), FakeContext([600_000]))
    assert missing in str(excinfo.value)
    assert blender.calls == [] and fake_s3.puts == [] and fake_s3.downloads == []


@pytest.mark.unit
def test_primary_download_failure_raises_and_removes_the_work_directory(monkeypatch):
    handler, fake_s3, blender, _ = _setup(monkeypatch, config=_config())
    del fake_s3.objects[PRIMARY_KEY]
    created = []
    real_mkdtemp = handler.tempfile.mkdtemp

    def _recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(handler.tempfile, "mkdtemp", _recording_mkdtemp)
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):
        handler.lambda_handler(_event(), FakeContext([600_000]))
    assert len(created) == 1 and not os.path.exists(created[0])
    assert blender.calls == [] and fake_s3.puts == []


@pytest.mark.unit
def test_work_directory_is_removed_after_a_successful_run(monkeypatch):
    handler, _fake_s3, blender, _ = _setup(monkeypatch, config=_config())
    handler.lambda_handler(_event(), FakeContext([600_000]))
    output_dir = _argument(blender.calls[0]["command"], "--output-dir")
    assert not os.path.exists(os.path.dirname(output_dir))


@pytest.mark.unit
def test_trimesh_is_skipped_when_little_time_remains_after_the_render(monkeypatch):
    # First reading sizes the render budget; the second, taken after Blender, decides whether trimesh runs.
    handler, fake_s3, _blender, trimesh_calls = _setup(monkeypatch, config=_config())
    result = handler.lambda_handler(_event(), FakeContext([600_000, 30_000]))
    assert trimesh_calls == []
    assert any("trimesh attributes skipped" in w for w in result["warnings"])
    manifest = _manifest(fake_s3)
    attributes = manifest["attributes"]
    # Every section comes from the Blender facts, including the promotion source keys only Blender supplies here.
    assert attributes["sys_statistics"] == {
        "meshCount": 1, "vertices": 8, "faces": 12, "triangles": 12, "watertight": True}
    assert attributes["sys_geometry"]["units"] == "m" and attributes["sys_geometry"]["volume"] == 6.0
    assert attributes["sys_visual"]["hasUv"] is False and attributes["sys_visual"]["hasVertexColors"] is False
    assert attributes["sys_format"] == {"extension": ".glb", "loader": "blender", "declaredUnits": "m",
                                        "declaredMetersPerUnit": 1.0, "declaredUnitsSource": "specification"}
    assert manifest["facts"]["triangles"] == "12" and manifest["facts"]["materials"] == "Grey", \
        "facts from the Blender sections"
    assert manifest["facts"]["dimensions"] == "1 x 2 x 3 m"
    assert result["renderStatus"] == "SUCCEEDED"


@pytest.mark.unit
def test_declared_units_from_the_file_header_land_under_sys_format(monkeypatch):
    # A centimetre COLLADA asset: Blender's importer rescales it into scene metres (facts `units`), while the
    # author's declaration is read from the header and kept beside the loader under sys_format.
    dae_key = "xid1234/parts/room.dae"
    dae = (b'<?xml version="1.0" encoding="utf-8"?>\n'
           b'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n'
           b'<asset><unit meter="0.01" name="centimeter"/><up_axis>Z_UP</up_axis></asset>\n</COLLADA>\n')
    handler, fake_s3, _blender, _ = _setup(
        monkeypatch, config=_config(), prewritten=False, trimesh={}, extra_objects={dae_key: dae})
    handler.lambda_handler(
        _event(inputS3AssetFilePath=f"s3://{ASSET_BUCKET}/{dae_key}", relativePath="/parts/room.dae",
               contentType="model/vnd.collada+xml", fileSize=len(dae)),
        FakeContext([600_000]))
    attributes = _manifest(fake_s3)["attributes"]
    assert attributes["sys_format"] == {"extension": ".dae", "loader": "blender", "declaredUnits": "cm",
                                        "declaredMetersPerUnit": 0.01, "declaredUnitsSource": "header"}
    assert attributes["sys_geometry"]["units"] == "m", "the numbers are Blender's scene metres, not the file's cm"
    assert attributes["sys_file"]["name"] == "room.dae" and attributes["sys_file"]["ext"] == ".dae"


@pytest.mark.unit
def test_shared_constants_match_render_scene(monkeypatch):
    handler, _fake_s3, _blender, _ = _setup(monkeypatch, config=_config())
    scene = exec_named_nodes(["SCENE_FACTS_FILENAME", "RENDER_FILE_PREFIX", "VIEW_DIRECTIONS", "VIEW_ORDER",
                              "DEFAULT_RESOLUTION_PX", "DEFAULT_SAMPLES"], {"math": math})
    assert handler.SCENE_FACTS_FILENAME == scene["SCENE_FACTS_FILENAME"]
    assert handler.RENDER_FILE_PREFIX == scene["RENDER_FILE_PREFIX"]
    assert handler.VIEW_ORDER == scene["VIEW_ORDER"]
    assert handler.RENDER_RESOLUTION_PX == scene["DEFAULT_RESOLUTION_PX"]
    assert handler.RENDER_SAMPLES == scene["DEFAULT_SAMPLES"]
    assert handler.DEFAULT_RENDER_VIEWS == len(scene["VIEW_ORDER"])
