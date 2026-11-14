#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""``dependencies.resolve``: the deep, two-phase walk over a scene's references (#1011).

A scene is often many files: USD layers that sublayer, reference and pay-load other layers several
levels down, often through ``../``; a glTF or GLB with external buffers and images; a COLLADA document
placing geometry from other files. The walk follows them breadth first from the input, inside the
input's own asset only, fetches each file once at the version its HeadObject returned, reports what is
missing, refused, cyclic or past a bound, and never fails the run. The SYSTEM GenAI metadata pipeline's
Blender image runs a byte-identical copy of this module.

Every refused reference is refused before any Amazon S3 call: the store records every call, and no
call names a key outside the asset root. The USD cases read real layers through ``pxr``.
"""

import json
import os
import shutil
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from conftest import requires_real
from preview_pipeline import dependencies

ROOT = "xid/"
NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)


class FakeStore:
    """HeadObject, ranged GetObject and download over a dict of key -> bytes, recording every call."""

    def __init__(self, objects=None, modified=None):
        self.objects = {}
        self.versions = {}
        self.modified = {}
        self.calls = []
        for key, data in (objects or {}).items():
            self.put(key, data, modified=modified)

    def put(self, key, data, version=None, modified=None):
        self.objects[key] = data.encode("utf-8") if isinstance(data, str) else data
        self.versions[key] = version or f"v-{len(self.versions) + 1}"
        self.modified[key] = modified or (NOW - timedelta(days=1))

    def head(self, key):
        self.calls.append(("head", key))
        if key not in self.objects:
            return None
        return dependencies.ObjectInfo(len(self.objects[key]), self.versions[key], self.modified[key])

    def read_range(self, key, version_id, start, length):
        self.calls.append(("range", key, version_id, start, length))
        return self.objects[key][start:start + length]

    def download(self, key, version_id, path):
        self.calls.append(("download", key, version_id))
        if key not in self.objects:
            return False
        with open(path, "wb") as handle:
            handle.write(self.objects[key])
        return True

    def keys(self, kind):
        return [call[1] for call in self.calls if call[0] == kind]


def _local_input(tmp_path, store, input_key, root=ROOT):
    local_root = str(tmp_path / "local")
    local_input = dependencies.local_path_for(local_root, dependencies.relative_to_root(input_key, root))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    with open(local_input, "wb") as handle:
        handle.write(store.objects[input_key])
    return local_root, local_input


def _resolve(tmp_path, store, input_key, root=ROOT, limits=None, **kwargs):
    local_root, local_input = _local_input(tmp_path, store, input_key, root)
    kwargs.setdefault("late_arrival", False)
    report = dependencies.resolve(store, root, input_key, local_input, local_root,
                                  os.path.splitext(input_key)[1], limits, **kwargs)
    return report, local_root


def _gltf(buffers=(), images=()):
    return json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": uri, "byteLength": 1} for uri in buffers],
                       "images": [{"uri": uri} for uri in images]})


def _glb(buffers=(), images=()):
    chunk = _gltf(buffers, images).encode("utf-8")
    chunk += b" " * (-len(chunk) % 4)
    binary = b"\x00" * 8
    body = struct.pack("<I4s", len(chunk), b"JSON") + chunk + struct.pack("<I4s", len(binary), b"BIN\x00") + binary
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def _fetched(report):
    return [entry["rel"] for entry in report.fetched]


# --------------------------------------------------------------------------------------------------
# Confinement (no pxr needed)
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestRefusedBeforeAnyS3Call:
    REFUSED = ["../../other-asset/data.bin", "/etc/passwd.bin", "https://example.com/data.bin",
               "s3://other-bucket/data.bin", "..%2f..%2fother%2fdata.bin", "file:///etc/data.bin",
               "a/" * 600 + "x.bin", "data\x00.bin"]

    def test_references_outside_the_asset_are_reported_and_never_looked_up(self, tmp_path):
        store = FakeStore({"xid/scene/a.gltf": _gltf(buffers=self.REFUSED + ["ok.bin"]),
                           "xid/scene/ok.bin": b"1", "other-asset/data.bin": b"secret"})
        report, _ = _resolve(tmp_path, store, "xid/scene/a.gltf")
        assert _fetched(report) == ["scene/ok.bin"]
        assert report.rejected_count == len(self.REFUSED)
        assert all(entry["reason"] == dependencies.REJECTED_NOT_IN_ASSET for entry in report.rejected)
        touched = store.keys("head") + store.keys("download") + store.keys("range")
        assert touched and all(key.startswith(ROOT) for key in touched), touched
        assert not any("other-asset" in key or "etc" in key for key in touched)
        # The refused buffers carry geometry: the scene is partial.
        assert report.partial is True

    def test_a_refused_reference_is_quoted_without_control_characters_and_cut_short(self, tmp_path):
        store = FakeStore({"xid/a.gltf": _gltf(images=["/x\x01y.png", "/" + "z" * 500 + ".png"])})
        report, _ = _resolve(tmp_path, store, "xid/a.gltf")
        assert report.rejected[0]["rel"] == "/x?y.png"
        assert len(report.rejected[1]["rel"]) == 203 and report.rejected[1]["rel"].endswith("...")
        # Warnings name counts, never the file's own reference text.
        assert not any("zzz" in line or "x?y" in line for line in report.warnings())

    def test_linked_or_sibling_assets_are_not_followed(self, tmp_path):
        """A reference is followed only inside the input's own asset root, whatever the asset links."""
        store = FakeStore({"xid/a.gltf": _gltf(buffers=["../yid/b.bin"]), "yid/b.bin": b"other asset"})
        report, _ = _resolve(tmp_path, store, "xid/a.gltf")
        assert report.fetched == [] and report.rejected_count == 1
        assert "yid/b.bin" not in store.keys("head")


@pytest.mark.unit
class TestConfinementRoot:
    def test_the_manifest_asset_root_wins_when_it_is_a_proper_prefix(self):
        assert dependencies.confinement_root("base/folder/a/b.usda", "xid", "/a/b.usda", "base/folder/") == \
            "base/folder/"
        assert dependencies.confinement_root("base/folder/a/b.usda", "", "", "base/folder") == "base/folder/"

    def test_a_hint_that_is_not_a_prefix_or_names_the_whole_bucket_is_ignored(self):
        assert dependencies.confinement_root("xid/a/b.usda", "xid", "", "other/") == "xid/"
        assert dependencies.confinement_root("xid/a/b.usda", "", "", "/") == "xid/a/"

    def test_the_asset_id_segment(self):
        assert dependencies.confinement_root("base/xid/a/b.usda", "xid", "/a/b.usda") == "base/xid/"

    def test_an_asset_on_an_existing_folder_is_rooted_by_its_relative_path(self):
        """An asset created on an existing bucket folder keeps that folder as its root; its keys need
        not contain the asset id, so the root is the key without its asset-relative path."""
        assert dependencies.confinement_root("projects/site-a/scenes/main.usda", "xid",
                                             "/scenes/main.usda") == "projects/site-a/"

    @pytest.mark.parametrize("relative", ["/../x/main.usda", "/scenes/other.usda", ""])
    def test_otherwise_the_input_directory(self, relative):
        assert dependencies.confinement_root("projects/site-a/scenes/main.usda", "xid", relative) == \
            "projects/site-a/scenes/"


@pytest.mark.unit
class TestLimitsFromConfig:
    def test_a_configuration_lowers_but_never_raises(self):
        base = dependencies.FetchLimits(max_files=100, max_total_bytes=1000, max_depth=8)
        lowered = dependencies.limits_from_config(base, {"maxDependencyFiles": 10, "maxDependencyBytes": "500",
                                                         "maxDependencyDepth": 99})
        assert (lowered.max_files, lowered.max_total_bytes, lowered.max_depth) == (10, 500, 8)
        assert (base.max_files, base.max_total_bytes, base.max_depth) == (100, 1000, 8)

    @pytest.mark.parametrize("config", [None, [], {"maxDependencyFiles": -1}, {"maxDependencyFiles": True},
                                        {"maxDependencyFiles": "many"}, {"maxDependencyFiles": {"a": 1}}])
    def test_an_unusable_value_is_ignored(self, config):
        base = dependencies.FetchLimits(max_files=100)
        assert dependencies.limits_from_config(base, config).max_files == 100


# --------------------------------------------------------------------------------------------------
# Two phases, versions, bounds
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestTwoPhases:
    def test_discovery_downloads_documents_only_and_predicts_the_bytes(self, tmp_path):
        store = FakeStore({"xid/a.obj": "mtllib m.mtl\n", "xid/m.mtl": "newmtl x\nmap_Kd big.png\n",
                           "xid/big.png": b"p" * 1000})
        local_root, local_input = _local_input(tmp_path, store, "xid/a.obj")
        resolver = dependencies.DependencyResolver(store, ROOT, local_root, late_arrival=False)
        resolver.discover("a.obj", local_input, ".obj")
        assert store.keys("download") == ["xid/m.mtl"], "only the document is downloaded in discovery"
        assert resolver.predicted_bytes == len(store.objects["xid/m.mtl"]) + 1000
        resolver.fetch()
        assert store.keys("download") == ["xid/m.mtl", "xid/big.png"]

    def test_every_download_is_at_the_version_head_object_returned(self, tmp_path):
        store = FakeStore()
        store.put("xid/a.gltf", _gltf(buffers=["a.bin"], images=["t.png"]))
        store.put("xid/a.bin", b"1", version="ver-bin")
        store.put("xid/t.png", b"2", version="ver-png")
        report, _ = _resolve(tmp_path, store, "xid/a.gltf")
        downloads = {call[1]: call[2] for call in store.calls if call[0] == "download"}
        assert downloads == {"xid/a.bin": "ver-bin", "xid/t.png": "ver-png"}
        assert {entry["rel"]: entry["versionId"] for entry in report.fetched} == {"a.bin": "ver-bin",
                                                                                  "t.png": "ver-png"}

    def test_the_client_store_passes_the_version_to_get_object(self, tmp_path):
        calls = []

        class Client:
            def head_object(self, Bucket, Key):
                return {"ContentLength": 4, "VersionId": "ver-7"}

            def download_file(self, bucket, key, path, ExtraArgs=None, Config=None):
                calls.append((bucket, key, ExtraArgs))

            def get_object(self, **kwargs):
                calls.append(kwargs)
                return {"Body": _Body(b"glTF")}

        store = dependencies.ClientStore(Client(), "abkt")
        info = store.head("xid/a.bin")
        assert (info.size, info.version_id) == (4, "ver-7")
        assert store.download("xid/a.bin", info.version_id, str(tmp_path / "a.bin"))
        assert store.read_range("xid/a.glb", "ver-7", 0, 4) == b"glTF"
        assert calls[0] == ("abkt", "xid/a.bin", {"VersionId": "ver-7"})
        assert calls[1] == {"Bucket": "abkt", "Key": "xid/a.glb", "Range": "bytes=0-3", "VersionId": "ver-7"}

    def test_a_failed_head_object_reads_as_missing(self, tmp_path):
        from botocore.exceptions import ClientError

        class Client:
            def head_object(self, Bucket, Key):
                raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

        assert dependencies.ClientStore(Client(), "abkt").head("xid/a.bin") is None


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self, length=None):
        return self.data[:length]

    def close(self):
        pass


@pytest.mark.unit
class TestBounds:
    def _wide(self, count=5):
        store = FakeStore({"xid/a.gltf": _gltf(images=[f"t{i}.png" for i in range(count)])})
        for index in range(count):
            store.put(f"xid/t{index}.png", b"x" * 10)
        return store

    def test_the_file_count(self, tmp_path):
        report, _ = _resolve(tmp_path, self._wide(), "xid/a.gltf", limits=dependencies.FetchLimits(max_files=2))
        assert _fetched(report) == ["t0.png", "t1.png"]
        assert report.truncated == {"reason": "files", "at": "t2.png"}

    def test_the_lookup_count_bounds_head_calls_for_names_that_do_not_exist(self, tmp_path):
        store = FakeStore({"xid/a.gltf": _gltf(images=[f"gone{i}.png" for i in range(50)])})
        report, _ = _resolve(tmp_path, store, "xid/a.gltf", limits=dependencies.FetchLimits(max_files=5))
        assert len(store.keys("head")) == 10, "twice the file count"
        assert report.truncated["reason"] == "lookups" and len(report.missing) == 10

    def test_the_byte_total(self, tmp_path):
        report, _ = _resolve(tmp_path, self._wide(), "xid/a.gltf",
                             limits=dependencies.FetchLimits(max_total_bytes=25))
        assert _fetched(report) == ["t0.png", "t1.png"] and report.total_bytes == 20
        assert report.predicted_bytes == 50
        assert report.truncated == {"reason": "bytes", "at": "t2.png"}

    def test_free_disk(self, tmp_path):
        with patch.object(dependencies.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10, 10, 0)):
            report, _ = _resolve(tmp_path, self._wide(), "xid/a.gltf")
        assert report.fetched == [] and report.truncated["reason"] == "disk"

    def test_the_depth(self, tmp_path, monkeypatch):
        store = FakeStore()
        for index in range(6):
            store.put(f"xid/c{index}.chain", f"c{index + 1}.chain")
        monkeypatch.setitem(dependencies._SCENE_ONLY_COLLECTORS, ".chain",
                            lambda path: [(open(path).read(), False, True)])
        report, _ = _resolve(tmp_path, store, "xid/c0.chain", limits=dependencies.FetchLimits(max_depth=2))
        assert _fetched(report) == ["c1.chain", "c2.chain", "c3.chain"]
        assert report.max_depth == 3
        assert report.truncated == {"reason": "depth", "at": "c3.chain"}

    def test_the_references_one_file_may_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dependencies, "MAX_REFERENCES_PER_FILE", 2)
        report, _ = _resolve(tmp_path, self._wide(), "xid/a.gltf")
        assert _fetched(report) == ["t0.png", "t1.png"]
        assert report.truncated == {"reason": "references", "at": "a.gltf"}

    def test_the_deadline(self, tmp_path):
        ticks = iter(range(1000))
        report, _ = _resolve(tmp_path, self._wide(), "xid/a.gltf", deadline=4, clock=lambda: next(ticks))
        assert report.truncated["reason"] == "deadline"
        assert len(report.fetched) < 5


# --------------------------------------------------------------------------------------------------
# Graph: diamonds, cycles, required references, late arrival
# --------------------------------------------------------------------------------------------------

def _lister(path):
    """A test format: one reference per line, ``!`` marking a required one."""
    with open(path) as handle:
        lines = [line.strip() for line in handle if line.strip()]
    return [(line.lstrip("!"), False, line.startswith("!")) for line in lines]


@pytest.fixture
def lister(monkeypatch):
    monkeypatch.setitem(dependencies._SCENE_ONLY_COLLECTORS, ".lst", _lister)


@pytest.mark.unit
class TestGraph:
    def test_a_diamond_is_fetched_once_and_is_not_a_cycle(self, tmp_path, lister):
        store = FakeStore({"xid/root.lst": "left.lst\nright.lst\n", "xid/left.lst": "shared.lst\n",
                           "xid/right.lst": "shared.lst\n", "xid/shared.lst": ""})
        report, _ = _resolve(tmp_path, store, "xid/root.lst")
        assert sorted(_fetched(report)) == ["left.lst", "right.lst", "shared.lst"]
        assert store.keys("download").count("xid/shared.lst") == 1
        assert store.keys("head").count("xid/shared.lst") == 1
        assert report.cycles == []
        assert report.graph["left.lst"] == ["shared.lst"] and report.graph["right.lst"] == ["shared.lst"]

    def test_a_cycle_ends_the_walk_and_is_reported(self, tmp_path, lister):
        store = FakeStore({"xid/a.lst": "!b.lst\n", "xid/b.lst": "!c.lst\n", "xid/c.lst": "!a.lst\nb.lst\n"})
        report, _ = _resolve(tmp_path, store, "xid/a.lst")
        assert sorted(_fetched(report)) == ["b.lst", "c.lst"]
        assert store.keys("head").count("xid/a.lst") == 0, "the input is not looked up again"
        assert sorted(report.cycles) == [["a.lst", "b.lst", "c.lst", "a.lst"], ["b.lst", "c.lst", "b.lst"]]
        assert "cycles" in " ".join(report.warnings())

    def test_a_cross_edge_cycle_below_the_input_is_found(self, tmp_path, lister):
        store = FakeStore({"xid/root.lst": "b.lst\nc.lst\n", "xid/b.lst": "c.lst\n", "xid/c.lst": "b.lst\n"})
        report, _ = _resolve(tmp_path, store, "xid/root.lst")
        assert report.cycles == [["b.lst", "c.lst", "b.lst"]]

    def test_a_missing_required_reference_marks_the_scene_partial(self, tmp_path, lister):
        store = FakeStore({"xid/a.lst": "!gone.lst\noptional.png\n"})
        report, _ = _resolve(tmp_path, store, "xid/a.lst")
        assert report.missing == ["gone.lst", "optional.png"] and report.missing_required == ["gone.lst"]
        assert report.partial is True
        store = FakeStore({"xid/a.lst": "optional.png\n"})
        report, _ = _resolve(tmp_path / "2", store, "xid/a.lst")
        assert report.partial is False

    def test_the_report_shape(self, tmp_path, lister):
        store = FakeStore({"xid/a.lst": "b.lst\n", "xid/b.lst": ""})
        report, _ = _resolve(tmp_path, store, "xid/a.lst")
        shape = report.to_dict()
        assert set(shape) >= {"fetched", "missing", "rejected", "cycles", "truncated", "totalBytes", "maxDepth",
                              "graph"}
        assert shape["fetched"] == [{"rel": "b.lst", "versionId": store.versions["xid/b.lst"], "bytes": 0,
                                     "depth": 1}]
        assert shape["input"] == "a.lst" and shape["truncated"] is None and shape["maxDepth"] == 1
        json.dumps(shape)


@pytest.mark.unit
class TestLateArrival:
    def _store(self, modified):
        return FakeStore({"xid/a.lst": "!part.lst\n"}, modified=modified)

    def test_a_fresh_upload_waits_once_for_its_missing_parts(self, tmp_path, lister):
        store = self._store(NOW - timedelta(seconds=60))
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            store.put("xid/part.lst", "")  # the rest of the upload lands meanwhile

        report, _ = _resolve(tmp_path, store, "xid/a.lst", late_arrival=True, sleep=sleep, now=lambda: NOW)
        assert sleeps == [dependencies.LATE_ARRIVAL_WAIT_SECONDS]
        assert _fetched(report) == ["part.lst"] and report.missing == [] and report.partial is False
        assert report.late_arrival_retry is True

    def test_an_older_upload_does_not_wait(self, tmp_path, lister):
        store = self._store(NOW - timedelta(minutes=10))
        sleeps = []
        report, _ = _resolve(tmp_path, store, "xid/a.lst", late_arrival=True, sleep=sleeps.append, now=lambda: NOW)
        assert sleeps == [] and report.missing == ["part.lst"] and report.late_arrival_retry is False

    def test_no_wait_when_the_deadline_is_near(self, tmp_path, lister):
        store = self._store(NOW - timedelta(seconds=60))
        sleeps = []
        _resolve(tmp_path, store, "xid/a.lst", late_arrival=True, sleep=sleeps.append, now=lambda: NOW,
                 clock=lambda: 0.0, deadline=40.0)
        assert sleeps == []

    def test_an_optional_reference_never_waits(self, tmp_path, lister):
        store = FakeStore({"xid/a.lst": "texture.png\n"}, modified=NOW)
        sleeps = []
        _resolve(tmp_path, store, "xid/a.lst", late_arrival=True, sleep=sleeps.append, now=lambda: NOW)
        assert sleeps == []


# --------------------------------------------------------------------------------------------------
# Formats: GLB, COLLADA (no pxr needed)
# --------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestGlb:
    def test_an_input_glb_fetches_what_its_json_chunk_names(self, tmp_path):
        store = FakeStore({"xid/m/a.glb": _glb(buffers=["data.bin"], images=["../tex/wood.png"]),
                           "xid/m/data.bin": b"1", "xid/tex/wood.png": b"2"})
        report, _ = _resolve(tmp_path, store, "xid/m/a.glb")
        assert sorted(_fetched(report)) == ["m/data.bin", "tex/wood.png"]

    def test_a_child_glb_is_read_by_ranged_get_then_fetched_once(self, tmp_path, lister):
        store = FakeStore({"xid/a.lst": "part.glb\n", "xid/part.glb": _glb(images=["t.png"]), "xid/t.png": b"3"})
        report, _ = _resolve(tmp_path, store, "xid/a.lst")
        ranges = [call for call in store.calls if call[0] == "range"]
        assert [call[3] for call in ranges] == [0, 20]
        assert ranges[0][2] == store.versions["xid/part.glb"]
        assert sorted(_fetched(report)) == ["part.glb", "t.png"]
        assert store.keys("download").count("xid/part.glb") == 1


_DAE = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <library_images>
    <image id="wood"><init_from>textures/my%20wood.png</init_from></image>
    <image id="abs"><init_from>file:///C:/Users/a/wood.png</init_from></image>
  </library_images>
  <library_effects><effect id="e"><profile_COMMON><newparam sid="s">
    <surface type="2D"><init_from>wood</init_from></surface></newparam></profile_COMMON></effect></library_effects>
  <library_visual_scenes><visual_scene id="s">
    <node id="n1"><instance_geometry url="#local-geometry"/></node>
    <node id="n2"><instance_node url="parts/bolt.dae#bolt"/></node>
  </visual_scene></library_visual_scenes>
</COLLADA>
"""
_DAE_15 = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2008/03/COLLADASchema" version="1.5.0">
  <library_images><image id="i"><init_from><ref>maps/stone.png</ref></init_from></image></library_images>
</COLLADA>
"""


@pytest.mark.unit
class TestCollada:
    def test_images_and_external_instances(self, tmp_path):
        store = FakeStore({"xid/a.dae": _DAE, "xid/textures/my wood.png": b"1", "xid/parts/bolt.dae": _DAE_15,
                           "xid/parts/maps/stone.png": b"2"})
        report, _ = _resolve(tmp_path, store, "xid/a.dae")
        assert sorted(_fetched(report)) == ["parts/bolt.dae", "parts/maps/stone.png", "textures/my wood.png"]
        assert report.rejected_count == 1, "the absolute file URI"
        # The <surface> init_from names an image id, not a file, so it is never looked up.
        assert "xid/wood" not in store.keys("head")

    def test_a_document_type_declaration_is_refused(self, tmp_path):
        bomb = ('<?xml version="1.0"?><!DOCTYPE c [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
                '<COLLADA><library_images><image><init_from>&b;.png</init_from></image></library_images></COLLADA>')
        store = FakeStore({"xid/a.dae": bomb})
        report, _ = _resolve(tmp_path, store, "xid/a.dae")
        assert report.fetched == [] and store.keys("head") == []
        with pytest.raises(dependencies.UnsafeXmlError):
            dependencies._dae_references(os.path.join(str(tmp_path / "local"), "a.dae"))

    def test_an_external_entity_is_refused(self, tmp_path):
        path = tmp_path / "x.dae"
        path.write_text('<?xml version="1.0"?><!DOCTYPE c SYSTEM "http://example.com/c.dtd"><COLLADA/>')
        with pytest.raises(dependencies.UnsafeXmlError):
            dependencies._dae_references(str(path))


# --------------------------------------------------------------------------------------------------
# USD (real pxr)
# --------------------------------------------------------------------------------------------------

def _usda(body="", header=""):
    return f"#usda 1.0\n(\n{header}\n)\n{body}\n"


_TRI = ('def Mesh "Tri"\n{\n    int[] faceVertexCounts = [3]\n    int[] faceVertexIndices = [0, 1, 2]\n'
        '    point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]\n}')


def _textured(name, path):
    return f'def Mesh "{name}"\n{{\n    asset info:texture = @{path}@\n}}'


@pytest.mark.unit
@requires_real("pxr")
class TestUsd:
    def test_a_three_level_chain_through_parent_directories(self, tmp_path):
        """root -> ../../parts/arm.usda -> ./detail/hand.usda (sublayer) -> ../../textures/skin.png."""
        store = FakeStore({
            "xid/scenes/main/root.usda": _usda('def Xform "Arm" (references = @../../parts/arm.usda@) {}'),
            "xid/parts/arm.usda": _usda(_TRI, "subLayers = [@./detail/hand.usda@]"),
            "xid/parts/detail/hand.usda": _usda(_textured("Hand", "../../textures/skin.png")),
            "xid/textures/skin.png": b"png",
        })
        report, local_root = _resolve(tmp_path, store, "xid/scenes/main/root.usda")
        assert _fetched(report) == ["parts/arm.usda", "parts/detail/hand.usda", "textures/skin.png"]
        assert [entry["depth"] for entry in report.fetched] == [1, 2, 3] and report.max_depth == 3
        assert os.path.isfile(os.path.join(local_root, "textures", "skin.png"))
        assert report.missing == [] and report.partial is False

    def test_sublayer_and_reference_cycles_are_reported(self, tmp_path):
        store = FakeStore({
            "xid/a.usda": _usda(header="subLayers = [@./b.usda@]"),
            "xid/b.usda": _usda(header="subLayers = [@./a.usda@]"),
            "xid/r.usda": _usda('def Xform "X" (references = @./a.usda@) {}\ndef Xform "Y" (references = @./s.usda@) {}'),
            "xid/s.usda": _usda('def Xform "Z" (references = @./r.usda@) {}'),
        })
        report, _ = _resolve(tmp_path, store, "xid/r.usda")
        assert sorted(_fetched(report)) == ["a.usda", "b.usda", "s.usda"]
        assert sorted(report.cycles) == [["a.usda", "b.usda", "a.usda"], ["r.usda", "s.usda", "r.usda"]]

    def test_every_variant_is_fetched(self, tmp_path):
        store = FakeStore({
            "xid/v.usda": _usda('def Xform "Thing" (\n variants = { string look = "a" }\n prepend variantSets = "look"\n)'
                                '\n{\n variantSet "look" = {\n "a" (references = @./a.usda@) {}\n'
                                ' "b" (references = @./b.usda@) {}\n }\n}'),
            "xid/a.usda": _usda(_TRI), "xid/b.usda": _usda(_TRI),
        })
        report, _ = _resolve(tmp_path, store, "xid/v.usda")
        assert sorted(_fetched(report)) == ["a.usda", "b.usda"]

    def test_a_usdz_fetches_its_external_references_only(self, tmp_path):
        package = tmp_path / "pkg.usdz"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("root.usda", _usda('def Xform "In" (references = @./inner.usda@) {}\n'
                                                'def Xform "Out" (references = @../parts/outer.usda@) {}\n'
                                                + _textured("M", "textures/inner.png")))
            archive.writestr("inner.usda", _usda(_TRI))
            archive.writestr("textures/inner.png", b"png")
        store = FakeStore({"xid/scene/pkg.usdz": package.read_bytes(), "xid/parts/outer.usda": _usda(_TRI)})
        report, _ = _resolve(tmp_path, store, "xid/scene/pkg.usdz")
        assert _fetched(report) == ["parts/outer.usda"]
        assert store.keys("head") == ["xid/parts/outer.usda"], "package members are never looked up"

    def test_references_outside_the_asset_are_refused_before_any_call(self, tmp_path):
        store = FakeStore({
            "xid/a.usda": _usda('def Xform "A" (references = @../other/b.usda@) {}\n'
                                'def Xform "B" (references = @/etc/b.usda@) {}\n'
                                'def Xform "C" (references = @https://example.com/b.usda@) {}\n'
                                'def Xform "D" (references = @sub/../../../b.usda@) {}'),
            "other/b.usda": _usda(_TRI),
        })
        report, _ = _resolve(tmp_path, store, "xid/a.usda")
        assert report.rejected_count == 4 and report.fetched == []
        assert store.calls == []
        assert report.partial is True


_USD_WITH_METADATA = """#usda 1.0
(
    customLayerData = {
        asset sourceFile = @./authoring/source.usd@
    }
)

def Xform "Robot" (
    assetInfo = {
        asset identifier = @./authoring/robot_source.usda@
    }
    references = @./parts/arm.usda@
)
{
    asset material:mdl = @OmniPBR.mdl@
}
"""


@pytest.mark.unit
@requires_real("pxr")
class TestUsdPathsThatLoadNothing:
    """A USD path in layer or prim metadata, and a renderer's own bare MDL module, remove nothing from
    the scene when absent: the scene report does not call them missing or the scene partial (the rule
    the 3D thumbnail's incomplete preview applies)."""

    def test_only_a_missing_composition_arc_makes_the_scene_partial(self, tmp_path):
        store = FakeStore({"xid/scene/robot.usda": _USD_WITH_METADATA})
        report, _local_root = _resolve(tmp_path, store, "xid/scene/robot.usda")
        assert report.missing == ["scene/parts/arm.usda"]
        assert report.missing_required == ["scene/parts/arm.usda"]
        assert report.partial is True

    def test_a_scene_missing_only_metadata_paths_is_whole(self, tmp_path):
        store = FakeStore({"xid/scene/robot.usda": _USD_WITH_METADATA,
                           "xid/scene/parts/arm.usda": "#usda 1.0\n"})
        report, _local_root = _resolve(tmp_path, store, "xid/scene/robot.usda")
        assert report.missing == [] and report.missing_required == []
        assert report.partial is False
        assert not any("not in the asset" in line for line in report.warnings())


@pytest.mark.unit
class TestReferencesThatLoadNothing:
    """`loads_nothing` names the files the walk saw only as references that load nothing (a USD metadata
    asset path, a renderer's own MDL module), so a caller that checks the scene with OpenUSD leaves them
    out of its missing list. A file also named by an arc that loads it is not among them."""

    @staticmethod
    def _walk(tmp_path, references):
        store = FakeStore({"xid/scene/root.refs": "root"})
        local_root, local_input = _local_input(tmp_path, store, "xid/scene/root.refs")
        resolver = dependencies.DependencyResolver(
            store, ROOT, local_root, collectors={".refs": lambda path: list(references)}, late_arrival=False)
        resolver.discover("scene/root.refs", local_input, ".refs")
        return resolver

    def test_a_metadata_path_and_a_bare_mdl_module_load_nothing(self, tmp_path):
        resolver = self._walk(tmp_path, [
            ("source/scene.usd", False, False, dependencies.KIND_METADATA),
            ("OmniPBR.mdl", False, False, ""),
            ("textures/gone.png", False, False, dependencies.KIND_TEXTURE_OR_OTHER),
        ])
        assert resolver.loads_nothing() == ["scene/source/scene.usd", "scene/OmniPBR.mdl"]
        assert resolver.report().missing == ["scene/textures/gone.png"]

    def test_a_path_named_as_metadata_first_and_then_as_a_sublayer_loads(self, tmp_path):
        resolver = self._walk(tmp_path, [
            ("layers/gone.usda", False, False, dependencies.KIND_METADATA),
            ("layers/gone.usda", False, True, dependencies.KIND_GEOMETRY_LAYER),
        ])
        assert resolver.loads_nothing() == []
        report = resolver.report()
        assert report.missing == ["scene/layers/gone.usda"]
        assert report.missing_required == ["scene/layers/gone.usda"] and report.partial is True


def _fbx_naming(*names):
    """A binary FBX naming each texture by an absolute FileName and a RelativeFilename, as exporters do."""
    def record(text):  # a binary FBX string property: 'S', uint32 length, bytes
        return b"S" + struct.pack("<I", len(text)) + text

    body = b"Kaydara FBX Binary  \x00\x1a\x00" + b"\x00\x05\x00\x00\x00"
    for name in names:
        body += record(b"C:\\Users\\artist\\scene\\" + name) + b"\x00\x00" + record(name) + b"\x00\x01"
    return body


@pytest.mark.unit
class TestFbxAndLccReferencesInTheSceneWalk:
    """The FBX texture and LCC data file references are optional appearance or data, never required, and
    a missing FBX texture is still reported in the scene report the Blender image reads: Blender's FBX
    importer loads the textures, unlike the thumbnail's loader, whose incomplete preview leaves them out
    (``test_thumbnail_incomplete_preview.py``)."""

    def test_collector_entries_are_optional_with_their_kind(self, tmp_path):
        fbx = tmp_path / "scene.fbx"
        fbx.write_bytes(_fbx_naming(b"Body.jpg"))
        lcc = tmp_path / "meta.lcc"
        lcc.write_text(json.dumps({"fileType": "Quality"}))
        for entry in dependencies._fbx_references(str(fbx)):
            assert len(entry) == 4 and entry[2] is False
            assert entry[3] == dependencies.KIND_UNSAMPLED_TEXTURE
        for entry in dependencies._lcc_references(str(lcc)):
            assert len(entry) == 4 and entry[2] is False
            assert entry[3] == dependencies.KIND_DATA_FILE

    def test_a_missing_fbx_texture_is_in_the_scene_report_and_the_scene_is_not_partial(self, tmp_path):
        store = FakeStore({"xid/scene/model.fbx": _fbx_naming(b"Body.jpg", b"Eyes.jpg"),
                           "xid/scene/Body.jpg": b"j"})
        report, _ = _resolve(tmp_path, store, "xid/scene/model.fbx")
        assert _fetched(report) == ["scene/Body.jpg"]
        assert report.missing == ["scene/Eyes.jpg"]
        assert report.missing_required == [] and report.partial is False

    def test_missing_lcc_data_files_are_not_required(self, tmp_path):
        store = FakeStore({"xid/scan/meta.lcc": json.dumps({"fileType": "Quality"}).encode(),
                           "xid/scan/index.bin": b"i"})
        report, _ = _resolve(tmp_path, store, "xid/scan/meta.lcc")
        assert "scan/data.bin" in report.missing
        assert report.missing_required == [] and report.partial is False
