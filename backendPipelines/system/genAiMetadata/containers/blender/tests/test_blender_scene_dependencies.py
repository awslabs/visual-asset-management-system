# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The handler's S3 input layer: the primary file is fetched at the manifest's version and mirrored at its
asset-relative path, and the files it references are fetched from its own asset by the shared resolver
(`scene_dependencies`, a byte-identical copy of the 3D thumbnail's), at the versions HeadObject returned
(#1011).

The branch used to list the primary's parent prefix and download every file with a known extension
there, so a USD scene whose layers sit in `../parts/` rendered without them and a large folder filled the
disk in listing order. These cases pin the walk through the handler: `../` chains, refusals before any S3
call, the overflow re-route to the Fargate render job, the report in the manifest, and Blender's USD
dependency check as the missing list.
"""

import io
import json
import os
import zipfile
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from blenderTestSupport import FakeContext, FakeS3, load_handler
from test_blender_handler_contract import (AUX_BUCKET, ASSET_BUCKET, MANIFEST_KEY, PREWRITTEN_MANIFEST, FakeBlender,
                                           _argument, _event)
from test_blender_render_scene_dispatch import exec_named_nodes

PRIMARY = "xid/test/pump.glb"


def _handler(tmp_path, listing=(), objects=None):
    fake = FakeS3(listing=listing, objects=objects or {})
    fake.sandbox = str(tmp_path)
    return load_handler(fake), fake


@pytest.mark.unit
@pytest.mark.parametrize("uri,expected", [
    ("s3://bucket/key", ("bucket", "key")),
    ("s3://bucket/a/b/c.glb", ("bucket", "a/b/c.glb")),
    ("s3://bucket/prefix/", ("bucket", "prefix/")),
])
def test_parse_s3_uri(tmp_path, uri, expected):
    handler, _fake = _handler(tmp_path)
    assert handler.parse_s3_uri(uri) == expected


@pytest.mark.unit
@pytest.mark.parametrize("uri", ["", "bucket/key", "s3://", "s3://bucket", "s3://bucket/", None])
def test_parse_s3_uri_rejects_malformed_values(tmp_path, uri):
    handler, _fake = _handler(tmp_path)
    with pytest.raises(ValueError):
        handler.parse_s3_uri(uri)


@pytest.mark.unit
def test_primary_download_passes_the_version_id_only_when_given(tmp_path):
    handler, fake = _handler(tmp_path, objects={PRIMARY: b"glb"})
    dest = str(tmp_path / "input" / "pump.glb")
    os.makedirs(os.path.dirname(dest))
    handler.download_primary("asset-bucket", PRIMARY, "v123", dest)
    handler.download_primary("asset-bucket", PRIMARY, "", dest)
    assert fake.downloads[0]["extra"] == {"VersionId": "v123"}
    assert fake.downloads[1]["extra"] is None
    assert open(dest, "rb").read() == b"glb"


@pytest.mark.unit
@pytest.mark.parametrize("event,key,root,relative", [
    ({"assetId": "xid"}, "xid/test/pump.glb", "xid/", "test/pump.glb"),
    ({"assetId": "xid", "relativePath": "/test/pump.glb"}, "base/xid/test/pump.glb", "base/xid/", "test/pump.glb"),
    # An asset created on an existing bucket folder: its keys need not contain the asset id.
    ({"assetId": "xid", "relativePath": "/scenes/main.usda"}, "site/a/scenes/main.usda", "site/a/",
     "scenes/main.usda"),
    ({"assetRootS3Key": "site/a/", "assetId": "xid"}, "site/a/scenes/main.usda", "site/a/", "scenes/main.usda"),
    ({}, "xid/test/pump.glb", "xid/test/", "pump.glb"),
])
def test_the_input_is_mirrored_at_its_asset_relative_path(tmp_path, event, key, root, relative):
    handler, _fake = _handler(tmp_path)
    input_root = str(tmp_path / "input")
    root_key, input_rel, local_path, local_root = handler.input_location(event, key, input_root)
    assert (root_key, input_rel) == (root, relative)
    assert local_root == os.path.join(input_root, "asset")
    assert local_path == os.path.realpath(os.path.join(local_root, *relative.split("/")))
    assert os.path.isdir(os.path.dirname(local_path))


@pytest.mark.unit
@pytest.mark.parametrize("body,expected,warning_fragment", [
    (json.dumps({"resolveDependencies": "false", "renderViews": 3}),
     {"resolveDependencies": False, "renderViews": 3}, None),
    (json.dumps({"includeSiblingFiles": False}), {"resolveDependencies": False, "renderViews": 8}, None),
    (json.dumps({"resolveDependencies": True, "includeSiblingFiles": False}),
     {"resolveDependencies": True, "renderViews": 8}, None),
    (json.dumps({"resolveDependencies": True, "renderViews": 99}),
     {"resolveDependencies": True, "renderViews": 8}, None),
    (json.dumps({"renderViews": 0}), {"resolveDependencies": True, "renderViews": 1}, None),
    (json.dumps({"renderViews": "many"}), {"resolveDependencies": True, "renderViews": 8}, "not an integer"),
    (json.dumps({"unrelated": 1}), {"resolveDependencies": True, "renderViews": 8}, None),
    (b"{not json", {"resolveDependencies": True, "renderViews": 8}, "unreadable"),
])
def test_render_options_from_the_template_configuration(tmp_path, body, expected, warning_fragment):
    """Template tag RESOLVE_DEPENDENCIES replaces INCLUDE_SIBLING_FILES; a configuration from before it is
    read through the old key when the new one is absent."""
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    handler, _fake = _handler(tmp_path, objects={"run/config.json": raw})
    options, warnings = handler.load_render_options("s3://run-bucket/run/config.json")
    assert options == dict(expected, dependencyLimits={})
    if warning_fragment is None:
        assert warnings == []
    else:
        assert len(warnings) == 1 and warning_fragment in warnings[0]


@pytest.mark.unit
def test_render_options_when_the_configuration_is_absent_or_unnamed(tmp_path):
    defaults = {"resolveDependencies": True, "renderViews": 8, "dependencyLimits": {}}
    handler, _fake = _handler(tmp_path)
    assert handler.load_render_options("") == (defaults, [])
    options, warnings = handler.load_render_options("s3://run-bucket/run/missing.json")
    assert options == defaults
    assert len(warnings) == 1 and "missing" in warnings[0]


@pytest.mark.unit
def test_limit_overrides_lower_the_lambda_budget_only(tmp_path):
    handler, _fake = _handler(tmp_path, objects={"run/config.json": json.dumps(
        {"maxDependencyFiles": 7, "maxDependencyBytes": 10 ** 15}).encode("utf-8")})
    options, _ = handler.load_render_options("s3://run-bucket/run/config.json")
    limits = handler.dependency_limits(options)
    assert limits.max_files == 7
    assert limits.max_total_bytes == handler.scene_dependencies.LAMBDA_MAX_TOTAL_BYTES


@pytest.mark.unit
def test_read_json_object_distinguishes_missing_from_forbidden(tmp_path):
    handler, fake = _handler(tmp_path, objects={"a/b.json": b'{"ok": true}', "a/list.json": b"[1]"})
    assert handler.read_json_object("bucket", "a/b.json") == {"ok": True}
    assert handler.read_json_object("bucket", "a/none.json") is None
    with pytest.raises(ValueError):
        handler.read_json_object("bucket", "a/list.json")

    def _forbidden(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject")

    fake.get_object = _forbidden
    with pytest.raises(ClientError):
        handler.read_json_object("bucket", "a/b.json")


@pytest.mark.unit
def test_the_walk_stops_in_time_for_the_render(tmp_path):
    handler, _fake = _handler(tmp_path)
    remaining = FakeContext([900_000])
    deadline = handler.dependency_deadline(remaining) - handler.time.monotonic()
    assert deadline == pytest.approx(900 - handler.RENDER_RESERVE_SECONDS - handler.DEPENDENCY_RENDER_RESERVE_SECONDS,
                                     abs=1)
    assert handler.dependency_deadline(FakeContext([60_000])) - handler.time.monotonic() == pytest.approx(
        handler.MIN_DEPENDENCY_SECONDS, abs=1)


# --------------------------------------------------------------------------------------------------
# The walk through the whole handler
# --------------------------------------------------------------------------------------------------

def _gltf(buffers=(), images=()):
    return json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": uri, "byteLength": 1} for uri in buffers],
                       "images": [{"uri": uri} for uri in images]}).encode("utf-8")


def _usda(body="", header=""):
    return f"#usda 1.0\n(\n{header}\n)\n{body}\n".encode("utf-8")


_TRI = ('def Mesh "Tri"\n{\n    int[] faceVertexCounts = [3]\n    int[] faceVertexIndices = [0, 1, 2]\n'
        '    point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]\n}')


def _run(monkeypatch, tmp_path, key, objects, event=None, config=None, blender=None, prepare=None):
    """The handler end to end with S3 and Blender replaced; `prepare(handler)` adjusts the loaded module.
    Every object is also listed, so a branch that lists the input's folder would see them.
    Returns (result, fake S3, blender, the manifest written last, handler)."""
    objects = dict(objects)
    listing = [{"Key": name, "Size": len(data)} for name, data in objects.items()]
    objects[MANIFEST_KEY] = json.dumps(PREWRITTEN_MANIFEST).encode("utf-8")
    if config is not None:
        objects["run/config.json"] = json.dumps(config).encode("utf-8")
    fake = FakeS3(listing=listing, objects=objects)
    handler = load_handler(fake)
    if prepare is not None:
        prepare(handler)
    blender = blender or FakeBlender()
    monkeypatch.setattr(handler.subprocess, "run", blender)
    monkeypatch.setattr(handler, "compute_trimesh_attributes", lambda path, extension: ({}, []))
    event = dict(_event(inputS3AssetFilePath=f"s3://{ASSET_BUCKET}/{key}", **(event or {})))
    event["inputConfigurationS3Location"] = "s3://run-bucket/run/config.json" if config is not None else ""
    result = handler.lambda_handler(event, FakeContext([900_000]))
    puts = [put for put in fake.puts if put["Key"] == MANIFEST_KEY]
    manifest = json.loads(puts[-1]["Body"].decode("utf-8")) if puts else None
    return result, fake, blender, manifest, handler


class _LayoutBlender(FakeBlender):
    """Records which asset-relative files exist beside the input when Blender is started."""

    def __init__(self, expected, input_rel):
        super().__init__()
        self.expected, self.input_rel, self.present = expected, input_rel, None

    def __call__(self, command, **kwargs):
        input_path = _argument(command, "--input")
        local_root = input_path[:-len(self.input_rel)]
        self.present = [rel for rel in self.expected if os.path.isfile(os.path.join(local_root, *rel.split("/")))]
        return super().__call__(command, **kwargs)


def _keys(fake, kind):
    return [entry["key"] for entry in fake.downloads] if kind == "download" else list(getattr(fake, "heads", []))


@pytest.mark.unit
def test_a_three_level_usd_chain_through_parent_directories_is_fetched(monkeypatch, tmp_path):
    """root -> ../../parts/arm.usda -> ./detail/hand.usda (sublayer) -> ../../textures/skin.png. The old
    branch listed only the primary's own folder, so none of these reached Blender."""
    pytest.importorskip("pxr", reason="the USD walk reads layers with usd-core (requirements.txt)")
    objects = {
        "xid1234/scenes/main/root.usda": _usda('def Xform "Arm" (references = @../../parts/arm.usda@) {}'),
        "xid1234/parts/arm.usda": _usda(_TRI, "subLayers = [@./detail/hand.usda@]"),
        "xid1234/parts/detail/hand.usda": _usda('def Mesh "Hand"\n{\n    asset info:skin = @../../textures/skin.png@\n}'),
        "xid1234/textures/skin.png": b"png",
    }
    expected = ["parts/arm.usda", "parts/detail/hand.usda", "textures/skin.png"]
    blender = _LayoutBlender(expected, "scenes/main/root.usda")
    result, fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/scenes/main/root.usda", objects,
                                               event={"relativePath": "/scenes/main/root.usda", "fileClass": "usd"},
                                               config={"resolveDependencies": True}, blender=blender)
    report = manifest["dependencyReport"]
    assert [entry["rel"] for entry in report["fetched"]] == expected
    assert [entry["depth"] for entry in report["fetched"]] == [1, 2, 3]
    downloads = {entry["key"]: entry["extra"] for entry in fake.downloads}
    assert downloads["xid1234/textures/skin.png"] == {"VersionId": "v-xid1234/textures/skin.png"}
    assert fake.list_calls == [], "no prefix listing"
    # Blender opens the root at its asset-relative path with every layer beside it, so ../../parts resolves.
    assert blender.present == expected
    assert result["renderBranch"] == "BLENDER" and "dependencyOverflow" not in result


@pytest.mark.unit
def test_references_outside_the_asset_are_refused_before_any_s3_call(monkeypatch, tmp_path):
    objects = {
        "xid1234/parts/pump.gltf": _gltf(buffers=["pump.bin", "../../other-asset/secret.bin", "/etc/x.bin",
                                                  "https://example.com/x.bin"]),
        "xid1234/parts/pump.bin": b"1",
        "other-asset/secret.bin": b"secret",
    }
    _r, fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                      event={"relativePath": "/parts/pump.gltf"}, config={})
    touched = _keys(fake, "download") + _keys(fake, "head")
    assert all(key.startswith("xid1234/") or key == MANIFEST_KEY for key in touched), touched
    report = manifest["dependencyReport"]
    assert [entry["rel"] for entry in report["fetched"]] == ["parts/pump.bin"]
    assert report["rejectedCount"] == 3 and report["partial"] is True
    assert any("3 references were not followed" in warning for warning in manifest["warnings"])
    assert not any("secret" in warning for warning in manifest["warnings"])


@pytest.mark.unit
def test_resolve_dependencies_false_fetches_the_primary_alone(monkeypatch, tmp_path):
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["pump.bin"]), "xid1234/parts/pump.bin": b"1"}
    _r, fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                      config={"resolveDependencies": False})
    assert _keys(fake, "download") == ["xid1234/parts/pump.gltf"] and _keys(fake, "head") == []
    assert "dependencyReport" not in manifest


@pytest.mark.unit
def test_an_overflowing_scene_is_re_routed_to_the_fargate_render_job(monkeypatch, tmp_path):
    """The referenced files discovery sized exceed the Lambda budget and the Fargate renderer is deployed:
    the branch returns FARGATE with dependencyOverflow before Blender runs and before the large files are
    downloaded, leaving the discovery report and the reason in the manifest."""
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["big.bin"], images=["tex.png"]),
               "xid1234/parts/big.bin": b"b" * 4000, "xid1234/parts/tex.png": b"p" * 100}
    def _fargate(handler):
        monkeypatch.setattr(handler, "USE_FARGATE_RENDERER", True)
        monkeypatch.setattr(handler.scene_dependencies, "LAMBDA_MAX_TOTAL_BYTES", 1000)

    result, fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                               prepare=_fargate)
    assert result["renderBranch"] == "FARGATE" and result["dependencyOverflow"] is True
    assert result["renderImageCount"] == 0 and result["renderSkipped"] is None
    assert blender.calls == [], "nothing is rendered in Lambda"
    assert _keys(fake, "download") == ["xid1234/parts/pump.gltf"], "the large files are only sized"
    assert manifest["renderBranch"] == "FARGATE"
    assert manifest["dependencyReport"]["predictedBytes"] == 4100
    assert any("Fargate render job" in warning for warning in manifest["warnings"])
    assert manifest["attributes"]["sys_file"]["name"] == "pump.glb", "the pre-written attributes are kept"
    assert result["warningCount"] == len(manifest["warnings"])


@pytest.mark.unit
def test_an_overflowing_scene_the_fargate_job_cannot_read_renders_in_lambda(monkeypatch, tmp_path):
    """The Fargate job runs in the isolated subnets: a scene in a bucket whose Region has no cross-Region
    S3 endpoint stays on the Blender Lambda, renders what fits the budget, and says why."""
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["big.bin"], images=["tex.png"]),
               "xid1234/parts/big.bin": b"b" * 4000, "xid1234/parts/tex.png": b"p" * 100}
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-west-2")

    def _fargate(handler):
        monkeypatch.setattr(handler, "USE_FARGATE_RENDERER", True)
        monkeypatch.setattr(handler.scene_dependencies, "LAMBDA_MAX_TOTAL_BYTES", 1000)

    result, _fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                                event={"inputBucketRegion": "eu-west-1"}, prepare=_fargate)
    assert result["renderBranch"] == "BLENDER" and len(blender.calls) == 1
    assert result.get("dependencyOverflow") is not True
    assert manifest["dependencyReport"]["truncated"]["reason"] == "bytes"
    assert any("no cross-Region Amazon S3 endpoint for eu-west-1" in w for w in manifest["warnings"])


@pytest.mark.unit
def test_an_overflowing_scene_in_an_endpoint_served_region_is_re_routed(monkeypatch, tmp_path):
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["big.bin"]), "xid1234/parts/big.bin": b"b" * 4000}
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("CROSS_REGION_S3_ENDPOINT_REGIONS", "us-west-2,eu-west-1")

    def _fargate(handler):
        monkeypatch.setattr(handler, "USE_FARGATE_RENDERER", True)
        monkeypatch.setattr(handler.scene_dependencies, "LAMBDA_MAX_TOTAL_BYTES", 1000)

    result, _fake, blender, _manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                                 event={"inputBucketRegion": "eu-west-1"}, prepare=_fargate)
    assert result["renderBranch"] == "FARGATE" and result["dependencyOverflow"] is True
    assert blender.calls == []


@pytest.mark.unit
def test_a_budget_the_configuration_lowered_is_not_re_routed(monkeypatch, tmp_path):
    """Fargate would keep a lowered budget too, so re-routing would not render more."""
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["big.bin"]), "xid1234/parts/big.bin": b"b" * 4000}

    def _fargate(handler):
        monkeypatch.setattr(handler, "USE_FARGATE_RENDERER", True)

    result, _fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                                config={"maxDependencyBytes": 100}, prepare=_fargate)
    assert result["renderBranch"] == "BLENDER" and len(blender.calls) == 1
    assert manifest["dependencyReport"]["truncated"]["reason"] == "bytes"


@pytest.mark.unit
def test_without_the_fargate_renderer_what_fits_is_rendered(monkeypatch, tmp_path):
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["big.bin"], images=["tex.png"]),
               "xid1234/parts/big.bin": b"b" * 4000, "xid1234/parts/tex.png": b"p" * 100}

    def _lambda_only(handler):
        monkeypatch.setattr(handler, "USE_FARGATE_RENDERER", False)
        monkeypatch.setattr(handler.scene_dependencies, "LAMBDA_MAX_TOTAL_BYTES", 1000)

    result, fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                               prepare=_lambda_only)
    assert result["renderBranch"] == "BLENDER" and len(blender.calls) == 1
    report = manifest["dependencyReport"]
    assert [entry["rel"] for entry in report["fetched"]] == ["parts/tex.png"]
    assert report["truncated"] == {"reason": "bytes", "at": "parts/big.bin"}
    assert any("bytes limit" in warning for warning in manifest["warnings"])


@pytest.mark.unit
def test_a_discovery_fault_is_a_warning_and_the_primary_still_renders(monkeypatch, tmp_path):
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["pump.bin"])}

    def _broken(handler):
        monkeypatch.setattr(handler.scene_dependencies.DependencyResolver, "discover",
                            lambda self, *args: (_ for _ in ()).throw(RuntimeError("boom")))

    result, _fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                                prepare=_broken)
    assert len(blender.calls) == 1 and result["renderBranch"] == "BLENDER"
    assert "dependencyReport" not in manifest


class _UsdCheckBlender(FakeBlender):
    """A Blender whose USD dependency check reports one missing layer under the local root, one path
    outside it, and one file the walk skipped for a limit."""

    def __call__(self, command, **kwargs):
        completed = super().__call__(command, **kwargs)
        output_dir = _argument(command, "--output-dir")
        input_path = _argument(command, "--input")
        local_root = input_path[:-len("parts/pump.gltf")]
        path = os.path.join(output_dir, "scene_facts.json")
        facts = json.load(open(path))
        facts["usdDependencies"] = {"layers": 2, "assets": 1, "unresolvedCount": 3, "unresolved": [
            os.path.join(local_root, "parts", "gone.usda"), "/etc/other.usda", os.path.join(local_root, "parts", "x.bin")]}
        with open(path, "w") as handle:
            json.dump(facts, handle)
        return completed


@pytest.mark.unit
def test_the_usd_dependency_check_is_the_missing_list(tmp_path):
    handler, _fake = _handler(tmp_path)
    local_root = str(tmp_path / "asset")
    os.makedirs(local_root)
    report = {"missing": ["parts/old.png"], "missingRequired": [], "partial": False}
    check = {"unresolved": [os.path.join(local_root, "parts", "gone.usda"), "/etc/passwd.usda",
                            os.path.join(local_root, "parts", "skipped.png"), "relative/never.usda"]}
    verified = handler.apply_usd_verification(report, check, local_root, skipped=["parts/skipped.png"])
    assert verified["missing"] == ["parts/gone.usda"] and verified["missingRequired"] == ["parts/gone.usda"]
    assert verified["partial"] is True and verified["verifiedBy"] == "openusd"
    assert report["missing"] == ["parts/old.png"], "the input report is not modified"
    assert handler.apply_usd_verification(report, None, local_root) is report
    assert handler.apply_usd_verification(report, {"error": "TfError"}, local_root) is report


@pytest.mark.unit
def test_the_handler_writes_the_verified_missing_list(monkeypatch, tmp_path):
    """The check's unresolved paths replace the walk's missing list in the manifest: outside the local
    root dropped, mapped to asset-relative paths. x.bin was fetched, so it is not missing: the composed
    stage did not load it, which is its own state."""
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["x.bin"]), "xid1234/parts/x.bin": b"1"}
    _r, _fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                       blender=_UsdCheckBlender())
    report = manifest["dependencyReport"]
    assert report["missing"] == ["parts/gone.usda"] and report["verifiedBy"] == "openusd"
    assert report["notComposed"] == ["parts/x.bin"]
    assert report["missingRequired"] == ["parts/gone.usda"] and report["partial"] is True


def _usdz(members):
    """A .usdz package: an uncompressed zip whose first member is the root layer."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, text in members:
            archive.writestr(name, text)
    return buffer.getvalue()


_STATION_ROOT = """#usda 1.0
def Xform "Station"
{
    def "Inner" ( prepend references = @./inner_part.usda@ ) {}
    def "Bolt" ( prepend references = @../parts/station_bolt.usda@ ) {}
}
"""


class _PackageCheckBlender(FakeBlender):
    """OpenUSD's dependency check of the composed station.usdz stage, as Blender reports it: the package
    anchors ../parts/station_bolt.usda inside the package (usdz/station.usdz[../parts/station_bolt.usda]),
    and a second reference to a file the asset does not hold."""

    def __call__(self, command, **kwargs):
        completed = super().__call__(command, **kwargs)
        output_dir = _argument(command, "--output-dir")
        package = _argument(command, "--input")
        path = os.path.join(output_dir, "scene_facts.json")
        facts = json.load(open(path))
        facts["usdDependencies"] = {"layers": 2, "assets": 0, "unresolvedCount": 2, "unresolved": [
            package + "[../parts/station_bolt.usda]", package + "[../parts/gone.usda]"]}
        with open(path, "w") as handle:
            json.dump(facts, handle)
        return completed


@pytest.mark.unit
def test_a_package_reference_the_walk_fetched_is_not_also_missing(monkeypatch, tmp_path):
    """station.usdz's root layer references ../parts/station_bolt.usda outside the package. The
    walk fetches it (the package's folder is usdz/, so the file is parts/station_bolt.usda); OpenUSD
    anchors the reference inside the package and reports it unresolved. The report names it once, as
    fetched but not loaded by the composed stage, and a reference the asset does not hold as missing
    under its asset path."""
    package = _usdz([("station.usda", _STATION_ROOT), ("inner_part.usda", "#usda 1.0\n")])
    objects = {"xid1234/usdz/station.usdz": package, "xid1234/parts/station_bolt.usda": b"#usda 1.0\n"}
    _r, _fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/usdz/station.usdz", objects,
                                       blender=_PackageCheckBlender())
    report = manifest["dependencyReport"]
    assert [entry["rel"] for entry in report["fetched"]] == ["parts/station_bolt.usda"]
    assert report["missing"] == ["parts/gone.usda"], "the asset path, not the package-relative form"
    assert not any("[" in rel for rel in report["missing"])
    assert report.get("notComposed") == ["parts/station_bolt.usda"]
    assert report["partial"] is True
    assert any("1 referenced file fetched but not loaded by the composed USD stage" in warning
               and "parts/station_bolt.usda" in warning for warning in manifest["warnings"])


@pytest.mark.unit
def test_package_relative_paths_are_split_before_the_local_path_is_resolved(tmp_path):
    """OpenUSD's form is `<absolute package path>[<path>]`. A `..` inside the brackets is not a path
    component, so it is resolved against the package's folder, not by the local path normalisation."""
    handler, _fake = _handler(tmp_path)
    local_root = str(tmp_path / "asset")
    os.makedirs(os.path.join(local_root, "usdz"))
    package = os.path.join(local_root, "usdz", "station.usdz")
    report = {"fetched": [{"rel": "parts/station_bolt.usda"}], "missing": [], "partial": False}
    check = {"unresolved": [package + "[../parts/station_bolt.usda]", package + "[../../outside.usda]",
                            package + "[../other/x.usda]", package + "[member_gone.usda]",
                            "/elsewhere/p.usdz[../x.usda]"]}
    verified = handler.apply_usd_verification(report, check, local_root)
    assert verified["notComposed"] == ["parts/station_bolt.usda"]
    assert verified["missing"] == ["other/x.usda", "usdz/member_gone.usda"]
    assert verified["missingRequired"] == ["other/x.usda", "usdz/member_gone.usda"]
    assert verified["partial"] is True


@pytest.mark.unit
@pytest.mark.parametrize("rel, expected", [
    ("usdz/station.usdz[../parts/station_bolt.usda]", "parts/station_bolt.usda"),
    ("usdz/station.usdz[tex/missing.png]", "usdz/tex/missing.png"),
    ("station.usdz[../../outside.usda]", ""),
    ("station.usdz[/abs/x.usda]", ""),
    ("parts/plain.usda", None),
    ("scene.usda[x]", None),
])
def test_a_package_relative_path_maps_to_the_file_the_walk_looked_for(tmp_path, rel, expected):
    handler, _fake = _handler(tmp_path)
    assert handler._package_reference_target(rel) == expected



@pytest.mark.unit
@pytest.mark.parametrize("state,config,walks", [
    ({"resolveDependencies": False}, {"resolveDependencies": True}, False),
    ({"resolveDependencies": "false"}, {}, False),
    ({"resolveDependencies": True}, {"resolveDependencies": False}, True),
    ({}, {"resolveDependencies": False}, False),
    ({}, {}, True),
])
def test_the_state_key_decides_over_the_configuration(monkeypatch, tmp_path, state, config, walks):
    """constructPipeline copies the template's RESOLVE_DEPENDENCIES onto the state as resolveDependencies;
    when the state carries it, it decides, otherwise the configuration file does."""
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["pump.bin"]), "xid1234/parts/pump.bin": b"1"}
    _r, fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects, event=state,
                                      config=config)
    assert ("xid1234/parts/pump.bin" in _keys(fake, "download")) is walks
    assert ("dependencyReport" in manifest) is walks


@pytest.mark.unit
def test_apply_state_options_leaves_the_configuration_alone_without_the_key(tmp_path):
    handler, _fake = _handler(tmp_path)
    options = {"resolveDependencies": False, "renderViews": 3, "dependencyLimits": {}}
    assert handler.apply_state_options(options, {"includeSiblingFiles": True}) is options
    assert handler.apply_state_options(options, {"resolveDependencies": "yes"})["resolveDependencies"] is True
    assert handler.apply_state_options(options, {"resolveDependencies": "maybe"})["resolveDependencies"] is False
    assert options["resolveDependencies"] is False, "the input options are not modified"


@pytest.mark.unit
def test_a_fetch_fault_is_a_warning_and_the_report_shows_what_was_fetched(monkeypatch, tmp_path):
    objects = {"xid1234/parts/pump.gltf": _gltf(buffers=["pump.bin"]), "xid1234/parts/pump.bin": b"1"}

    def _broken(handler):
        monkeypatch.setattr(handler.scene_dependencies.DependencyResolver, "fetch",
                            lambda self: (_ for _ in ()).throw(OSError("No space left on device")))

    result, _fake, blender, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/parts/pump.gltf", objects,
                                                prepare=_broken)
    assert len(blender.calls) == 1 and result["renderBranch"] == "BLENDER"
    assert manifest["dependencyReport"]["fetched"] == [] and manifest["dependencyReport"]["predictedBytes"] == 1


# --- OpenUSD's unresolved paths and the asset paths that load nothing ------------------------------
#
# Real USD layers through usd-core: the walk reads them with pxr, and the Blender stand-in reports what
# renderScene's usd_dependency_check reports in the image, OpenUSD's ComputeAllDependencies over the
# files the walk fetched. A layer or prim metadata asset path (customLayerData, an assetInfo
# identifier) loads nothing, so its absence leaves the scene whole; a sublayer or a texture does not.

_USD_GEOMETRY = """#usda 1.0
def Cube "Box"
{
    double size = 1
}
"""


def _usd_root(custom_source="", info_identifier="", sublayer="./layers/geometry.usda", texture=""):
    header = []
    if custom_source:
        header.append(f"    customLayerData = {{\n        asset sourceFile = @{custom_source}@\n    }}")
    header.append(f"    subLayers = [@{sublayer}@]")
    info = (f' (\n    assetInfo = {{\n        asset identifier = @{info_identifier}@\n    }}\n)'
            if info_identifier else "")
    body = f'    asset inputs:file = @{texture}@\n' if texture else ""
    return "#usda 1.0\n(\n" + "\n".join(header) + "\n)\n\n" + f'def Xform "World"{info}\n{{\n{body}}}\n'


class _OpenUsdCheckBlender(FakeBlender):
    """Blender's dependency check of the composed stage, run with the real pxr over the local input."""

    def __call__(self, command, **kwargs):
        completed = super().__call__(command, **kwargs)
        scene = exec_named_nodes(["MAX_UNRESOLVED_REPORTED", "usd_dependency_check"], {})
        path = os.path.join(_argument(command, "--output-dir"), "scene_facts.json")
        facts = json.load(open(path))
        facts["usdDependencies"] = scene["usd_dependency_check"](_argument(command, "--input"))
        with open(path, "w") as handle:
            json.dump(facts, handle)
        return completed


def _usd_report(monkeypatch, tmp_path, root_text, extra_objects=None):
    objects = {"xid1234/scene/root.usda": root_text.encode("utf-8"),
               "xid1234/scene/layers/geometry.usda": _USD_GEOMETRY.encode("utf-8")}
    objects.update(extra_objects or {})
    _r, _fake, _b, manifest, _h = _run(monkeypatch, tmp_path, "xid1234/scene/root.usda", objects,
                                       blender=_OpenUsdCheckBlender())
    return manifest["dependencyReport"]


@pytest.mark.unit
class TestOpenUsdPathsThatLoadNothing:
    @pytest.fixture(autouse=True)
    def _pxr(self):
        pytest.importorskip("pxr", reason="the USD walk and OpenUSD's check read layers with usd-core")

    def test_a_missing_usd_in_custom_layer_data_is_not_missing(self, monkeypatch, tmp_path):
        report = _usd_report(monkeypatch, tmp_path, _usd_root(custom_source="./source/scene_source.usd"))
        assert report["verifiedBy"] == "openusd"
        assert report["missing"] == [] and report["missingRequired"] == []
        assert report["partial"] is False

    def test_a_missing_ma_in_custom_layer_data_and_an_asset_info_identifier_is_not_missing(
            self, monkeypatch, tmp_path):
        report = _usd_report(monkeypatch, tmp_path, _usd_root(
            custom_source="./source/scene_source.ma", info_identifier="./published/scene_published.ma"))
        assert report["verifiedBy"] == "openusd"
        assert report["missing"] == [] and report["partial"] is False

    def test_a_missing_sublayer_is_still_missing_and_required(self, monkeypatch, tmp_path):
        report = _usd_report(monkeypatch, tmp_path, _usd_root(
            custom_source="./source/scene_source.usd", sublayer="./layers/gone.usda"))
        assert report["missing"] == ["scene/layers/gone.usda"]
        assert report["missingRequired"] == ["scene/layers/gone.usda"]
        assert report["partial"] is True

    def test_a_missing_texture_is_still_missing_and_not_required(self, monkeypatch, tmp_path):
        report = _usd_report(monkeypatch, tmp_path, _usd_root(
            custom_source="./source/scene_source.usd", texture="./textures/gone.png"))
        assert report["missing"] == ["scene/textures/gone.png"]
        assert report["missingRequired"] == [] and report["partial"] is False

    def test_a_file_named_as_metadata_and_as_a_sublayer_is_missing(self, monkeypatch, tmp_path):
        """The same path in customLayerData and in subLayers loads, so its absence is reported."""
        report = _usd_report(monkeypatch, tmp_path, _usd_root(
            custom_source="./layers/gone.usda", sublayer="./layers/gone.usda"))
        assert report["missing"] == ["scene/layers/gone.usda"]
        assert report["missingRequired"] == ["scene/layers/gone.usda"] and report["partial"] is True
