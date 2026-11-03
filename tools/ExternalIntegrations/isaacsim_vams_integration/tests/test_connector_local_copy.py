"""Tests for how the connector brings a VAMS file down for Isaac Sim to open.

A USD layer names its sublayers, references, payloads and textures by path relative to itself, and a
URDF or MJCF file names its meshes the same way. Downloading only the selected file leaves every one
of those paths unresolved, so the stage opens with empty prims and no error. These tests pin which
formats bring the whole asset, that a partial download is reported rather than hidden, and that a
stage export is never satisfied by a file that was already on disk. A file that did not download is
reported with the error the download gave for it. An export is flattened, so its
paths point into those downloads; each download directory is recorded with its asset and the files
the download left there, so the export can name those files relative to the asset it is uploaded to.
A reference with no prim path brings in a file's default prim, so a file without one is referenced at
its only root prim, and one with several root prims is not referenced at all. An import leaves the
stage's defaultPrim as it was, and a failed MJCF import leaves nothing behind at its prim path. An
MJCF file that is not well-formed XML, or that declares a document type, is refused before the
importer runs.
"""

import logging
import os
import sys
import tempfile
import types
from xml.etree import ElementTree

import omni
import pytest

from vams.connector.isaacsim.connector import IsaacVAMSConnector, MalformedXmlError, _Download
from vams.connector.isaacsim.extension import VamsConnectorExtension
from vams.connector.isaacsim.vams_cli_service import (
    Asset,
    AssetFile,
    Database,
    DownloadResult,
    VamsCliError,
    VamsTransferError,
)

ASSET_FILES = ("/scene.usda", "/layers/robot.usdc", "/textures/metal.png")
ASSET_LAYOUT = frozenset(key.lstrip("/") for key in ASSET_FILES)


class _FakeCli:
    """Writes the files a download would write, and records which download form was used. The files
    in ``errors`` fail with the error the download report gives for each."""

    def __init__(self, files=ASSET_FILES, fail=(), single_ok=True, single_error=None, errors=None,
                 contents=None):
        self.files = files
        # What each file holds; its own key when not given.
        self.contents = dict(contents or {})
        self.errors = dict(errors or {})
        self.fail = set(fail) | set(self.errors)
        self.single_ok = single_ok
        self.single_error = single_error
        self.calls = []

    def _write(self, root, key):
        path = os.path.join(root, key.lstrip("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(self.contents.get(key, key))

    def download_asset(self, local_path, database_id, asset_id):
        self.calls.append(("asset", local_path, database_id, asset_id))
        for key in self.files:
            if key not in self.fail:
                self._write(local_path, key)
        return DownloadResult(
            overall_success=not self.fail, total_files=len(self.files),
            successful_files=len(self.files) - len(self.fail), failed_files=len(self.fail),
            failed_downloads=[{"relative_key": key, "error": error}
                              for key, error in self.errors.items()])

    def download_file(self, local_path, database_id, asset_id, file_key):
        self.calls.append(("file", local_path, database_id, asset_id, file_key))
        if self.single_error is not None:
            raise self.single_error
        if self.single_ok:
            self._write(local_path, file_key)
        return self.single_ok

    def upload_file(self, file_path, database_id, asset_id):
        self.calls.append(("upload", file_path, database_id, asset_id))
        return {}

    def create_asset(self, database_id, asset_name, description=""):
        self.calls.append(("create", database_id, asset_name))
        return {"assetId": "new1"}


def _connector(cli):
    connector = IsaacVAMSConnector.__new__(IsaacVAMSConnector)
    connector._cli = cli
    connector._authenticated = True
    connector._download_roots = {}
    return connector


class TestFilesThatNameOtherFilesBringTheAsset:
    @pytest.mark.parametrize("file_key", ["/scene.usda", "/layers/robot.usdc", "/Scene.USD",
                                          "/robots/arm.urdf", "/robots/arm.xml", "/robots/arm.mjcf"])
    def test_the_whole_asset_is_downloaded(self, tmp_path, file_key):
        cli = _FakeCli(files=ASSET_FILES + (file_key,))
        local_file, missing = _connector(cli).fetch_local_copy("db", "a1", file_key, str(tmp_path))
        assert [c[0] for c in cli.calls] == ["asset"]
        assert local_file == os.path.abspath(os.path.join(tmp_path, file_key.lstrip("/")))
        assert missing == 0
        # The files the layer names sit where its relative paths expect them.
        assert os.path.isfile(tmp_path / "textures" / "metal.png")

    def test_a_usdz_package_comes_alone(self, tmp_path):
        cli = _FakeCli()
        local_file, missing = _connector(cli).fetch_local_copy("db", "a1", "/pkg/robot.usdz",
                                                               str(tmp_path))
        assert cli.calls == [("file", os.path.abspath(tmp_path), "db", "a1", "/pkg/robot.usdz")]
        assert local_file.endswith(os.path.join("pkg", "robot.usdz"))
        assert missing == 0


class TestFailuresAreReported:
    def test_a_partial_download_returns_the_count_of_missing_files(self, tmp_path):
        cli = _FakeCli(fail={"/textures/metal.png"})
        local_file, missing = _connector(cli).fetch_local_copy("db", "a1", "/scene.usda",
                                                               str(tmp_path))
        assert os.path.isfile(local_file)
        assert missing == 1

    def test_the_selected_file_missing_from_the_download_raises(self, tmp_path):
        cli = _FakeCli(fail={"/scene.usda"})
        with pytest.raises(VamsCliError, match="not found"):
            _connector(cli).fetch_local_copy("db", "a1", "/scene.usda", str(tmp_path))

    def test_a_failed_single_file_download_raises(self, tmp_path):
        cli = _FakeCli(single_ok=False)
        with pytest.raises(VamsCliError, match="Download failed"):
            _connector(cli).fetch_local_copy("db", "a1", "/robot.usdz", str(tmp_path))

    def test_a_key_that_escapes_the_download_directory_is_refused_before_any_download(self,
                                                                                    tmp_path):
        cli = _FakeCli()
        with pytest.raises(VamsCliError, match="outside"):
            _connector(cli).fetch_local_copy("db", "a1", "/../../outside.usda", str(tmp_path))
        assert cli.calls == []

    def test_open_stage_refuses_a_non_usd_file_without_downloading(self, tmp_path, usd):
        cli = _FakeCli()
        assert _connector(cli).download_and_import_asset("db", "a1", "/model.glb",
                                                         str(tmp_path)) is False
        assert cli.calls == []


class TestDownloadRootsAreRecorded:
    def test_a_whole_asset_download_records_its_directory_and_files(self, tmp_path):
        connector = _connector(_FakeCli())
        connector.fetch_local_copy("db", "a1", "/scene.usda", str(tmp_path))
        assert connector._download_roots == {
            os.path.abspath(tmp_path): _Download(("db", "a1"), ASSET_LAYOUT)}

    def test_a_single_file_download_records_the_one_file(self, tmp_path):
        connector = _connector(_FakeCli())
        connector.fetch_local_copy("db", "a1", "/pkg/robot.usdz", str(tmp_path))
        assert connector._download_roots == {
            os.path.abspath(tmp_path): _Download(("db", "a1"), frozenset({"pkg/robot.usdz"}))}

    def test_a_new_private_directory_is_recorded(self, tmp_path, monkeypatch):
        private = tmp_path / "vams_x1"
        monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(private))
        connector = _connector(_FakeCli())
        connector.fetch_local_copy("db", "a1", "/scene.usda")
        assert connector._download_roots == {
            os.path.abspath(private): _Download(("db", "a1"), ASSET_LAYOUT)}

    def test_a_directory_that_held_two_assets_is_recorded_for_neither(self, tmp_path):
        connector = _connector(_FakeCli())
        connector.fetch_local_copy("db", "a1", "/pkg/robot.usdz", str(tmp_path))
        connector.fetch_local_copy("db", "a1", "/scene.usda", str(tmp_path))
        assert connector._download_roots == {
            os.path.abspath(tmp_path): _Download(("db", "a1"), ASSET_LAYOUT | {"pkg/robot.usdz"})}
        connector.fetch_local_copy("db", "a2", "/scene.usda", str(tmp_path))
        assert connector._download_roots[os.path.abspath(tmp_path)].owner is None

    def test_a_failed_download_records_nothing(self, tmp_path):
        connector = _connector(_FakeCli(single_ok=False))
        with pytest.raises(VamsCliError):
            connector.fetch_local_copy("db", "a1", "/robot.usdz", str(tmp_path))
        assert connector._download_roots == {}


class _Layer:
    """The root layer's defaultPrim token."""

    def __init__(self, default_prim=""):
        self.defaultPrim = default_prim

    def HasDefaultPrim(self):
        return bool(self.defaultPrim)

    def ClearDefaultPrim(self):
        self.defaultPrim = ""


class _Stage:
    """Records each reference as (prim path, asset path, prim path within the file or None)."""

    def __init__(self, export_ok=True, prims=(), default_prim=""):
        self.export_ok = export_ok
        self.prims = set(prims)
        self.references = []
        self.layer = _Layer(default_prim)

    def GetRootLayer(self):
        return self.layer

    def RemovePrim(self, path):
        path = str(path)
        self.prims = {p for p in self.prims if p != path and not p.startswith(path + "/")}
        return True

    def Export(self, path):
        if self.export_ok:
            with open(path, "w") as f:
                f.write("#usda 1.0")
        return self.export_ok

    def GetPrimAtPath(self, path):
        stage = self

        class _Prim:
            def IsValid(self):
                return path in stage.prims

            def GetName(self):
                return path.rsplit("/", 1)[-1]

            def GetChildren(self):
                return [stage.GetPrimAtPath(p) for p in sorted(stage.prims)
                        if p.rsplit("/", 1)[0] == path]

            def GetReferences(self):
                return types.SimpleNamespace(
                    AddReference=lambda asset, prim_path=None: stage.references.append(
                        (path, asset, prim_path)))

        return _Prim()

    def DefinePrim(self, path, _type_name=""):
        self.prims.add(path)
        return self.GetPrimAtPath(path)


@pytest.fixture
def usd(monkeypatch):
    """A stub ``omni.usd`` whose context holds a stage the test controls."""
    state = types.SimpleNamespace(stage=_Stage(), opened=[])
    context = types.SimpleNamespace(get_stage=lambda: state.stage,
                                    open_stage=lambda path: state.opened.append(path) or True)
    module = types.ModuleType("omni.usd")
    module.get_context = lambda: context
    monkeypatch.setitem(__import__("sys").modules, "omni.usd", module)
    monkeypatch.setattr(omni, "usd", module, raising=False)
    return state


def _downloads(connector, tmp_path):
    """Opens asset a1 and asset a2 from their own download directories, and returns them."""
    own, other = tmp_path / "vams_own", tmp_path / "vams_other"
    connector.fetch_local_copy("db", "a1", "/scene.usda", str(own))
    connector.fetch_local_copy("db", "a2", "/scene.usda", str(other))
    return own, other


class TestStageExport:
    def test_a_failed_export_is_not_satisfied_by_a_file_already_on_disk(self, tmp_path, usd, pxr):
        stale = tmp_path / "scene.usd"
        stale.write_text("stale")
        usd.stage = _Stage(export_ok=False)
        cli = _FakeCli()
        with pytest.raises(VamsCliError, match="export failed"):
            _connector(cli).export_and_upload_scene("db", "scene", export_path=str(stale),
                                                    asset_id="a1")
        assert cli.calls == []

    def test_the_default_export_path_is_in_a_private_directory(self, usd, pxr):
        cli = _FakeCli()
        _connector(cli).export_and_upload_scene("db", "scene", asset_id="a1")
        uploaded = cli.calls[-1][1]
        assert os.path.basename(uploaded) == "scene.usd"
        assert os.path.basename(os.path.dirname(uploaded)).startswith("vams_export_")

    def test_paths_into_the_target_assets_download_become_relative_to_its_root(self, tmp_path,
                                                                               usd, pxr):
        connector = _connector(_FakeCli())
        own, other = _downloads(connector, tmp_path)
        authored = [(own / "textures" / "metal.png").as_posix(),
                    (other / "textures" / "metal.png").as_posix(),
                    "omniverse://nucleus.example.com/Projects/metal.png"]
        pxr.asset_paths = list(authored)
        export_path = str(tmp_path / "scene.usd")
        result = connector.export_and_upload_scene("db", "scene", export_path=export_path,
                                                   asset_id="a1")
        assert pxr.events == [("reload", export_path, True), ("remap", export_path),
                              ("save", export_path)]
        assert pxr.saved_paths == ["./textures/metal.png", authored[1], authored[2]]
        assert result == {"assetId": "a1", "uploaded": export_path, "unportablePaths": 1}

    def test_the_layer_is_reread_rewritten_and_saved_before_it_is_uploaded(self, tmp_path, usd,
                                                                            pxr):
        """A layer already open under the export path would otherwise be the one rewritten and
        saved, over the export."""
        cli = _FakeCli()
        connector = _connector(cli)
        own, _other = _downloads(connector, tmp_path)
        pxr.asset_paths = [(own / "textures" / "metal.png").as_posix()]
        pxr.events = cli.calls
        export_path = str(tmp_path / "scene.usd")
        connector.export_and_upload_scene("db", "scene", export_path=export_path, asset_id="a1")
        assert [c for c in cli.calls if c[0] in ("reload", "remap", "save", "upload")] == [
            ("reload", export_path, True), ("remap", export_path), ("save", export_path),
            ("upload", export_path, "db", "a1")]

    def test_a_file_created_in_the_download_after_it_is_counted_not_rewritten(self, tmp_path,
                                                                              usd, pxr):
        """Importer output, or a layer saved into the download directory, is not in VAMS."""
        connector = _connector(_FakeCli())
        own, _other = _downloads(connector, tmp_path)
        generated = own / "robot" / "configuration" / "robot_physics.usd"
        generated.parent.mkdir(parents=True)
        generated.write_text("#usda 1.0")
        authored = [(own / "textures" / "metal.png").as_posix(), generated.as_posix()]
        pxr.asset_paths = list(authored)
        result = connector.export_and_upload_scene("db", "scene",
                                                   export_path=str(tmp_path / "scene.usd"),
                                                   asset_id="a1")
        assert pxr.saved_paths == ["./textures/metal.png", authored[1]]
        assert result["unportablePaths"] == 1

    def test_the_count_is_of_distinct_paths(self, tmp_path, usd, pxr):
        """A texture bound by many materials is one file the user has to fix, not many."""
        connector = _connector(_FakeCli())
        _own, other = _downloads(connector, tmp_path)
        elsewhere = (tmp_path / "home" / "wood.png").as_posix()
        pxr.asset_paths = [(other / "textures" / "metal.png").as_posix()] * 3 + [elsewhere] * 2
        result = connector.export_and_upload_scene("db", "scene",
                                                   export_path=str(tmp_path / "scene.usd"),
                                                   asset_id="a1")
        assert result["unportablePaths"] == 2

    def test_an_export_to_a_new_asset_counts_every_local_path(self, tmp_path, usd, pxr):
        cli = _FakeCli()
        connector = _connector(cli)
        own, other = _downloads(connector, tmp_path)
        authored = [(own / "textures" / "metal.png").as_posix(),
                    (other / "textures" / "metal.png").as_posix()]
        pxr.asset_paths = list(authored)
        result = connector.export_and_upload_scene("db", "scene",
                                                   export_path=str(tmp_path / "scene.usd"))
        assert pxr.saved_paths == authored
        assert result == {"assetId": "new1", "unportablePaths": 2}
        assert [c[0] for c in cli.calls[-2:]] == ["create", "upload"]

    @pytest.mark.parametrize("failure", ["open_ok", "reload_ok", "save_ok"])
    def test_an_export_that_cannot_be_rewritten_is_not_uploaded(self, tmp_path, usd, pxr,
                                                                failure):
        setattr(pxr, failure, False)
        cli = _FakeCli()
        with pytest.raises(VamsCliError, match="export failed"):
            _connector(cli).export_and_upload_scene("db", "scene",
                                                    export_path=str(tmp_path / "scene.usd"),
                                                    asset_id="a1")
        assert cli.calls == []


class _Status:
    def __init__(self):
        self.text = ""


class _Model:
    def __init__(self, value=""):
        self.value = value

    def get_value_as_string(self):
        return self.value


class _Exporter:
    """A connector whose export reports ``unportable`` paths, or raises ``error``, and whose asset
    then lists the export."""

    def __init__(self, unportable=0, error=None, list_error=None):
        self.unportable = unportable
        self.error = error
        self.list_error = list_error
        self.listings = 0

    def export_and_upload_scene(self, database_id, asset_name, export_path=None, asset_id=None,
                                description=""):
        if self.error is not None:
            raise self.error
        return {"assetId": asset_id, "uploaded": export_path, "unportablePaths": self.unportable}

    def list_files(self, database_id, asset_id):
        self.listings += 1
        if self.list_error is not None:
            raise self.list_error
        return [AssetFile(relative_path="/", is_folder=True),
                AssetFile(file_name="scene.usda", relative_path="/scene.usda"),
                AssetFile(file_name="export.usd", relative_path="/export.usd")]


@pytest.fixture
def extension(monkeypatch):
    ext = VamsConnectorExtension()
    ext._init_state()
    ext._status_label = _Status()
    monkeypatch.setattr(ext, "_set_status", lambda text: setattr(ext._status_label, "text", text))
    ext._selected_db = Database(database_id="db")
    ext._selected_asset = Asset(asset_id="a1", asset_name="Asset One")
    return ext


class TestTheFileActions:
    def test_open_stage_opens_the_layer_inside_the_downloaded_asset(self, extension, usd):
        cli = _FakeCli()
        extension._connector = _connector(cli)
        extension._on_load_file(AssetFile(file_name="scene.usda", relative_path="/scene.usda"))
        assert [c[0] for c in cli.calls] == ["asset"]
        assert usd.opened and usd.opened[0].endswith("scene.usda")
        assert extension._status_label.text == "Opened /scene.usda"

    def test_open_stage_names_files_that_did_not_download(self, extension, usd):
        extension._connector = _connector(_FakeCli(fail={"/textures/metal.png"}))
        extension._on_load_file(AssetFile(file_name="scene.usda", relative_path="/scene.usda"))
        assert "1 asset file(s) did not download" in extension._status_label.text

    def test_add_reference_needs_a_stage_before_it_downloads(self, extension, usd):
        cli = _FakeCli()
        extension._connector = _connector(cli)
        usd.stage = None
        extension._on_add_usd_reference(AssetFile(file_name="robot.usdc",
                                                  relative_path="/layers/robot.usdc"))
        assert extension._status_label.text == "No active USD stage"
        assert cli.calls == []

    def test_add_reference_references_the_layer_inside_the_downloaded_asset(self, extension, usd,
                                                                            pxr):
        pxr.layers["robot.usdc"] = ("robot", ["robot"], [])
        cli = _FakeCli()
        extension._connector = _connector(cli)
        extension._on_add_usd_reference(AssetFile(file_name="robot.usdc",
                                                  relative_path="/layers/robot.usdc"))
        assert [c[0] for c in cli.calls] == ["asset"]
        assert usd.stage.references[0][1].endswith(os.path.join("layers", "robot.usdc"))

    def test_download_only_writes_under_the_users_home(self, extension, monkeypatch, tmp_path):
        monkeypatch.setattr(os.path, "expanduser", lambda _p: str(tmp_path))
        cli = _FakeCli()
        extension._connector = _connector(cli)
        extension._on_download_file(AssetFile(file_name="a.glb", relative_path="/m/a.glb"))
        assert cli.calls[0][1] == os.path.join(str(tmp_path), "vams_downloads")
        assert extension._status_label.text == (
            "Downloaded to " + os.path.join(str(tmp_path), "vams_downloads", "m/a.glb"))


ROBOT = AssetFile(file_name="robot.usdc", relative_path="/layers/robot.usdc")
NO_DEFAULT_PRIM = ("robot.usdc has no default prim (2 root prims), so a reference would bring in "
                   "nothing. Open it with Open Stage, or set a default prim on the file.")


class TestAddUsdReference:
    """A file whose reference would compose nothing is not referenced, and says why; the stage is
    left as it was."""

    @pytest.fixture
    def add(self, extension, usd, pxr):
        def _add(layer, cli=None, **sublayers):
            pxr.layers["robot.usdc"] = layer
            pxr.layers.update(sublayers)
            extension._connector = _connector(cli or _FakeCli())
            extension._on_add_usd_reference(ROBOT)
            return usd.stage.references, extension._status_label.text

        return _add

    def test_a_file_with_a_default_prim_is_referenced_as_it_is(self, add):
        references, status = add(("robot", ["Materials", "robot"], []))
        assert [(prim, target) for prim, _asset, target in references] == [("/World/robot", None)]
        assert references[0][1].endswith(os.path.join("layers", "robot.usdc"))
        assert status == "Added reference at /World/robot"

    def test_a_file_with_one_root_prim_and_no_default_is_referenced_at_that_prim(self, add):
        references, status = add(("", ["root"], []))
        assert [(prim, target) for prim, _asset, target in references] == [("/World/robot", "/root")]
        assert status == "Added reference at /World/robot"

    def test_a_file_with_several_root_prims_and_no_default_adds_nothing(self, add, usd):
        references, status = add(("", ["Materials", "root"], []))
        assert references == []
        assert "/World/robot" not in usd.stage.prims
        assert status == f"Reference error: {NO_DEFAULT_PRIM}"

    def test_a_file_with_no_root_prims_adds_nothing(self, add, usd):
        references, status = add(("", [], []))
        assert references == []
        assert "/World/robot" not in usd.stage.prims
        assert "robot.usdc has no default prim (0 root prims)" in status

    def test_a_prim_already_at_the_path_is_left_as_it_was(self, add, usd):
        usd.stage = _Stage(prims={"/World", "/World/robot"})
        references, _status = add(("", ["Materials", "root"], []))
        assert references == []
        assert usd.stage.prims == {"/World", "/World/robot"}

    def test_root_prims_of_sublayers_count(self, add):
        """A reference composes the file's sublayers too, so their root prims are siblings."""
        references, _status = add(("", [], ["./layers/body.usda"]),
                                  **{"body.usda": ("", ["root"], [])})
        assert [(prim, target) for prim, _asset, target in references] == [("/World/robot", "/root")]

    def test_a_sibling_root_prim_in_a_sublayer_prevents_the_reference(self, add):
        references, status = add(("", ["root"], ["./layers/materials.usda"]),
                                 **{"materials.usda": ("", ["Materials"], [])})
        assert references == []
        assert status == f"Reference error: {NO_DEFAULT_PRIM}"

    def test_a_second_add_of_the_same_file_gets_a_prim_of_its_own(self, extension, usd, pxr):
        pxr.layers["robot.usdc"] = ("robot", ["robot"], [])
        extension._connector = _connector(_FakeCli())
        extension._on_add_usd_reference(ROBOT)
        extension._on_add_usd_reference(ROBOT)
        assert [prim for prim, _asset, _target in usd.stage.references] == [
            "/World/robot", "/World/robot_1"]
        assert extension._status_label.text == "Added reference at /World/robot_1"

    def test_files_whose_names_sanitize_alike_get_prims_of_their_own(self, extension, usd, pxr):
        keys = ("/a b.usda", "/a_b.usda")
        for key in keys:
            pxr.layers[key.lstrip("/")] = ("root", ["root"], [])
        extension._connector = _connector(_FakeCli(files=keys))
        for key in keys:
            extension._on_add_usd_reference(AssetFile(file_name=key.lstrip("/"), relative_path=key))
        assert [(prim, asset.endswith(key.lstrip("/")))
                for (prim, asset, _target), key in zip(usd.stage.references, keys)] == [
            ("/World/a_b", True), ("/World/a_b_1", True)]

    def test_files_that_did_not_download_are_still_named(self, add):
        _references, status = add(("", ["root"], []), cli=_FakeCli(fail={"/textures/metal.png"}))
        assert status == "Added reference at /World/robot (1 asset file(s) did not download)"

    def test_a_file_that_is_not_a_usd_layer_adds_nothing(self, add, usd):
        references, status = add(None)
        assert references == []
        assert "/World/robot" not in usd.stage.prims
        assert status == "Reference error: Cannot open robot.usdc as a USD layer"


class TestTheScriptingApiReference:
    """``download_and_add_reference`` keeps its bool result and logs the reason."""

    def test_a_file_with_several_root_prims_and_no_default_returns_false(self, tmp_path, usd, pxr,
                                                                         caplog):
        pxr.layers["robot.usdc"] = ("", ["Materials", "root"], [])
        with caplog.at_level(logging.ERROR):
            added = _connector(_FakeCli()).download_and_add_reference(
                "db", "a1", "/layers/robot.usdc", "/World/Robot", str(tmp_path))
        assert added is False
        assert usd.stage.references == []
        assert "/World/Robot" not in usd.stage.prims
        assert NO_DEFAULT_PRIM in caplog.text

    @pytest.mark.parametrize("layer, target", [
        (("robot", ["robot"], []), None),
        (("", ["root"], []), "/root"),
    ])
    def test_a_file_a_reference_can_bring_in_is_referenced(self, tmp_path, usd, pxr, layer,
                                                            target):
        pxr.layers["robot.usdc"] = layer
        added = _connector(_FakeCli()).download_and_add_reference(
            "db", "a1", "/layers/robot.usdc", "/World/Robot", str(tmp_path))
        assert added is True
        assert usd.stage.references == [
            ("/World/Robot", os.path.abspath(tmp_path / "layers" / "robot.usdc"), target)]

    def test_the_prim_path_a_caller_names_is_used_as_it_is(self, tmp_path, usd, pxr):
        """The caller chose the path, so a second reference there is stacked on the same prim."""
        pxr.layers["robot.usdc"] = ("robot", ["robot"], [])
        connector = _connector(_FakeCli())
        for _ in range(2):
            assert connector.download_and_add_reference(
                "db", "a1", "/layers/robot.usdc", "/World/Robot", str(tmp_path)) is True
        assert [prim for prim, _asset, _target in usd.stage.references] == [
            "/World/Robot", "/World/Robot"]

    def test_the_reason_is_a_typed_error_for_callers_that_add_the_reference_themselves(self,
                                                                                         tmp_path,
                                                                                         pxr):
        from vams.connector.isaacsim.connector import UnreferenceableLayerError

        pxr.layers["robot.usdc"] = ("", ["Materials", "root"], [])
        stage = _Stage()
        with pytest.raises(UnreferenceableLayerError, match="no default prim") as excinfo:
            _connector(_FakeCli()).add_reference_to_stage(stage, "/World/Robot",
                                                          str(tmp_path / "robot.usdc"))
        assert isinstance(excinfo.value, VamsCliError)
        assert stage.references == [] and stage.prims == set()


NOT_DISTRIBUTABLE = ("Failed to generate bulk download URLs: Invalid request (400): VAMS General "
                     "Error: Asset not distributable")
FOREIGN = ("/ForeignTex.usda", "/textures/wood.png")


def _transfer_failure(file_key, error):
    return VamsTransferError(f"Transfer failed: 1 of 1 file(s) failed ({file_key})", payload={
        "failed_downloads": [{"relative_key": file_key, "error": error}]})


class _Calls(list):
    """The importer commands run, as (command, arguments); ``imports_nothing`` names the files an
    importer gets nothing from."""


@pytest.fixture
def importers(monkeypatch, usd):
    """Stub URDF and MJCF importers acting on ``usd.stage`` the way Isaac Sim 5.1's were measured to
    behave. URDF puts the robot under the prim the defaultPrim
    names, or at the root without one, as ``<name>_0`` when that is taken, and returns "" for a file
    it cannot import. MJCF defines the prim_path it is given, and any missing parent, with an empty
    ``Looks``, ``joints`` and ``worldBody`` for a file it imports nothing from, and returns no path
    either way. Both set the defaultPrim token to the name of the prim they create. Each
    ``omni.kit.commands.execute`` is recorded as (command, arguments), and an import config records
    its settings on ``settings``."""
    calls = _Calls()
    calls.imports_nothing = set()

    class _ImportConfig:
        def __init__(self):
            self.settings = {}

        def __getattr__(self, name):
            return lambda value: self.settings.__setitem__(name, value)

    def _execute(command, **kwargs):
        calls.append((command, kwargs))
        stage = usd.stage
        source = os.path.basename(kwargs.get("urdf_path") or kwargs.get("mjcf_path"))
        if command == "URDFParseAndImportFile":
            if source in calls.imports_nothing:
                return True, ""
            default = stage.layer.defaultPrim
            parent = f"/{default}" if default and f"/{default}" in stage.prims else ""
            path = f"{parent}/{os.path.splitext(source)[0]}"
            if path in stage.prims:
                path += "_0"
            created = [path, f"{path}/base_link"]
        else:
            path = kwargs["prim_path"]
            created = [path] + [f"{path}/{name}" for name in ("Looks", "joints", "worldBody")]
            if source not in calls.imports_nothing:
                created.append(f"{path}/body")
        for prim in created:
            parts = prim.split("/")
            stage.prims.update("/".join(parts[:i]) for i in range(2, len(parts) + 1))
        stage.layer.defaultPrim = path.rsplit("/", 1)[-1]
        return True, (path if command == "URDFParseAndImportFile" else None)

    commands = types.ModuleType("omni.kit.commands")
    commands.execute = _execute
    monkeypatch.setitem(sys.modules, "omni.kit.commands", commands)
    monkeypatch.setattr(omni.kit, "commands", commands, raising=False)
    for parent in ("isaacsim", "isaacsim.asset", "isaacsim.asset.importer"):
        monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    for name in ("urdf", "mjcf"):
        module = types.ModuleType(f"isaacsim.asset.importer.{name}")
        setattr(module, f"_{name}", types.SimpleNamespace(ImportConfig=_ImportConfig))
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


class TestTheReasonAFileDidNotDownload:
    """The requested file is reported with the error the download gave for it; the count of the
    asset's other files that did not download stays with a file that did arrive."""

    def test_the_error_for_the_requested_file_is_raised(self, tmp_path):
        cli = _FakeCli(files=FOREIGN, errors=dict.fromkeys(FOREIGN, NOT_DISTRIBUTABLE))
        with pytest.raises(VamsCliError) as excinfo:
            _connector(cli).fetch_local_copy("db", "a1", "/ForeignTex.usda", str(tmp_path))
        assert str(excinfo.value) == f"/ForeignTex.usda did not download: {NOT_DISTRIBUTABLE}"

    def test_a_key_given_without_its_leading_slash_is_matched(self, tmp_path):
        cli = _FakeCli(files=FOREIGN, errors=dict.fromkeys(FOREIGN, NOT_DISTRIBUTABLE))
        with pytest.raises(VamsCliError, match="^ForeignTex.usda did not download: Failed"):
            _connector(cli).fetch_local_copy("db", "a1", "ForeignTex.usda", str(tmp_path))

    def test_a_copy_an_earlier_download_left_does_not_stand_in_for_it(self, tmp_path):
        (tmp_path / "ForeignTex.usda").write_text("earlier")
        cli = _FakeCli(files=FOREIGN, errors={"/ForeignTex.usda": NOT_DISTRIBUTABLE})
        with pytest.raises(VamsCliError, match="did not download"):
            _connector(cli).fetch_local_copy("db", "a1", "/ForeignTex.usda", str(tmp_path))

    def test_another_files_error_leaves_the_count(self, tmp_path):
        cli = _FakeCli(errors={"/textures/metal.png": "404 Not Found"})
        local_file, missing = _connector(cli).fetch_local_copy("db", "a1", "/scene.usda",
                                                               str(tmp_path))
        assert os.path.isfile(local_file)
        assert missing == 1

    def test_the_error_for_a_file_downloaded_alone_is_raised(self, tmp_path):
        cli = _FakeCli(single_error=_transfer_failure("/pkg/robot.usdz", "404 Not Found"))
        with pytest.raises(VamsTransferError) as excinfo:
            _connector(cli).fetch_local_copy("db", "a1", "/pkg/robot.usdz", str(tmp_path))
        assert str(excinfo.value) == "/pkg/robot.usdz did not download: 404 Not Found"

    def test_a_report_that_gives_no_error_for_the_file_is_passed_on_as_worded(self, tmp_path):
        failure = VamsTransferError("Transfer failed: 1 of 1 file(s) failed (/m/a.glb)", payload={})
        with pytest.raises(VamsTransferError, match=r"^Transfer failed: 1 of 1"):
            _connector(_FakeCli(single_error=failure)).download_file("db", "a1", "/m/a.glb",
                                                                     str(tmp_path))

    def test_the_scripting_api_logs_the_error(self, tmp_path, caplog):
        cli = _FakeCli(files=("/robots/arm.urdf",), errors={"/robots/arm.urdf": NOT_DISTRIBUTABLE})
        with caplog.at_level(logging.ERROR):
            assert _connector(cli).download_and_import_urdf("db", "a1", "/robots/arm.urdf",
                                                            str(tmp_path)) is None
        assert f"/robots/arm.urdf did not download: {NOT_DISTRIBUTABLE}" in caplog.text

    @pytest.mark.parametrize("handler, file_key, prefix", [
        ("_on_load_file", "/ForeignTex.usda", "Load error"),
        ("_on_add_usd_reference", "/ForeignTex.usda", "Reference error"),
        ("_on_import_urdf", "/robots/arm.urdf", "URDF error"),
        ("_on_import_mjcf", "/robots/arm.xml", "MJCF error"),
    ])
    def test_each_action_shows_the_error_for_the_requested_file(self, extension, usd, importers,
                                                                handler, file_key, prefix):
        files = (file_key, "/textures/wood.png")
        extension._connector = _connector(_FakeCli(
            files=files, errors=dict.fromkeys(files, NOT_DISTRIBUTABLE)))
        getattr(extension, handler)(AssetFile(relative_path=file_key))
        assert extension._status_label.text == (
            f"{prefix}: {file_key} did not download: {NOT_DISTRIBUTABLE}")

    def test_download_only_shows_the_error_for_the_file(self, extension, monkeypatch, tmp_path):
        monkeypatch.setattr(os.path, "expanduser", lambda _p: str(tmp_path))
        extension._connector = _connector(_FakeCli(
            single_error=_transfer_failure("/m/a.glb", "404 Not Found")))
        extension._on_download_file(AssetFile(relative_path="/m/a.glb"))
        assert extension._status_label.text == (
            "Download failed: /m/a.glb did not download: 404 Not Found")


class TestTheImportActions:
    def test_an_imported_urdf_names_files_that_did_not_download(self, extension, importers):
        extension._connector = _connector(_FakeCli(
            files=("/robots/arm.urdf", "/meshes/base.stl"), errors={"/meshes/base.stl": "404"}))
        extension._on_import_urdf(AssetFile(relative_path="/robots/arm.urdf"))
        assert extension._status_label.text == (
            "Imported URDF as /arm (1 asset file(s) did not download)")
        command, arguments = importers[0]
        assert command == "URDFParseAndImportFile"
        assert arguments["urdf_path"].endswith(os.path.join("robots", "arm.urdf"))

    def test_an_imported_mjcf_is_named_after_its_file_under_world(self, extension, importers):
        extension._connector = _connector(_FakeCli(files=("/robots/arm.xml",),
                                                   contents={"/robots/arm.xml": MJ}))
        extension._on_import_mjcf(AssetFile(relative_path="/robots/arm.xml"))
        assert extension._status_label.text == "Imported MJCF /robots/arm.xml as /World/arm"
        command, arguments = importers[0]
        assert (command, arguments["prim_path"]) == ("MJCFCreateAsset", "/World/arm")
        assert arguments["mjcf_path"].endswith(os.path.join("robots", "arm.xml"))

    def test_the_scripting_api_imports_with_the_options_it_is_given(self, tmp_path, importers):
        prim = _connector(_FakeCli(files=("/robots/arm.urdf",))).download_and_import_urdf(
            "db", "a1", "/robots/arm.urdf", str(tmp_path), fix_base=False, merge_fixed_joints=True)
        assert prim == "/arm"
        assert importers[0][1]["import_config"].settings == {
            "set_fix_base": False, "set_merge_fixed_joints": True,
            "set_import_inertia_tensor": True}


MJ = ('<mujoco model="mj"><worldbody><body name="b"><geom type="box" size="0.1 0.1 0.1"/></body>'
      '</worldbody></mujoco>')
BROKEN_MJ = '<mujoco model="broken"><worldbody><body name="b"></body></worldbody>'
# An XML declaration and a comment ahead of the root element are not a document type declaration.
DECLARED_MJ = f'<?xml version="1.0" encoding="utf-8"?>\n<!-- arm -->\n{MJ}\n'
# Each kind of document type declaration, with the line it is on. MJCF uses none. The first declares
# an entity and a second one that expands to it several times, referenced from an attribute.
DOCTYPE_MJ = {
    "internal-entities": ('<?xml version="1.0"?>\n<!DOCTYPE mujoco [\n<!ENTITY a "box">\n'
                          '<!ENTITY b "&a;&a;&a;">\n]>\n<mujoco model="&b;"/>', 2),
    "external-dtd": (f'<!DOCTYPE mujoco SYSTEM "mujoco.dtd">\n{MJ}', 1),
    "bare": (f"<!DOCTYPE mujoco>{MJ}", 1),
}


def _doctype_refusal(line):
    return (f"it declares a document type at line {line}, which MJCF does not use and which can "
            "declare entities")


def _parse_error(text):
    try:
        ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        return str(e)
    raise AssertionError("well-formed")


def _import(extension, handler, file_key, text=MJ):
    extension._connector = _connector(_FakeCli(files=(file_key,), contents={file_key: text}))
    getattr(extension, handler)(AssetFile(relative_path=file_key))
    return extension._status_label.text


class TestTheImportersLeaveTheStageAsItWas:
    """Both importers set the stage's defaultPrim to the prim they create, and the MJCF importer
    creates its prim, and a typeless /World for it, whether or not it imports anything."""

    @pytest.fixture
    def world(self, usd):
        usd.stage = _Stage(prims=("/World",), default_prim="World")
        return usd.stage

    def test_an_mjcf_goes_under_world_at_a_prim_path_of_its_own(self, extension, world, importers):
        statuses = [_import(extension, "_on_import_mjcf", "/mjcf/mj.xml") for _ in range(2)]
        assert statuses == ["Imported MJCF /mjcf/mj.xml as /World/mj",
                            "Imported MJCF /mjcf/mj.xml as /World/mj_1"]
        assert [arguments["prim_path"] for _command, arguments in importers] == [
            "/World/mj", "/World/mj_1"]
        assert world.layer.defaultPrim == "World"

    def test_a_urdf_import_keeps_the_default_prim(self, extension, world, importers):
        statuses = [_import(extension, "_on_import_urdf", "/urdf/bot.urdf") for _ in range(2)]
        assert statuses == ["Imported URDF as /World/bot", "Imported URDF as /World/bot_0"]
        assert world.layer.defaultPrim == "World"

    def test_a_stage_with_no_default_prim_is_left_with_none(self, extension, usd, importers):
        assert _import(extension, "_on_import_urdf", "/urdf/bot.urdf") == "Imported URDF as /bot"
        assert _import(extension, "_on_import_mjcf", "/mjcf/mj.xml") == (
            "Imported MJCF /mjcf/mj.xml as /World/mj")
        assert not usd.stage.layer.HasDefaultPrim()

    def test_an_mjcf_that_is_not_well_formed_is_refused_before_the_importer_runs(
            self, extension, world, importers):
        status = _import(extension, "_on_import_mjcf", "/mjcf/broken.xml", BROKEN_MJ)
        assert status == (f"MJCF error: /mjcf/broken.xml is not well-formed XML: "
                          f"{_parse_error(BROKEN_MJ)}")
        assert importers == []
        assert (world.prims, world.layer.defaultPrim) == ({"/World"}, "World")

    def test_an_mjcf_with_an_xml_declaration_and_a_comment_is_imported(self, extension, world,
                                                                       importers):
        assert _import(extension, "_on_import_mjcf", "/mjcf/arm.xml", DECLARED_MJ) == (
            "Imported MJCF /mjcf/arm.xml as /World/arm")
        assert [arguments["prim_path"] for _command, arguments in importers] == ["/World/arm"]

    @pytest.mark.parametrize("text, line", DOCTYPE_MJ.values(), ids=DOCTYPE_MJ.keys())
    def test_an_mjcf_that_declares_a_document_type_is_refused_before_the_importer_runs(
            self, extension, world, importers, text, line):
        status = _import(extension, "_on_import_mjcf", "/mjcf/dtd.xml", text)
        assert status == f"MJCF error: /mjcf/dtd.xml is refused: {_doctype_refusal(line)}"
        assert importers == []
        assert (world.prims, world.layer.defaultPrim) == ({"/World"}, "World")

    def test_an_mjcf_import_that_brings_in_nothing_is_undone(self, extension, world, importers):
        importers.imports_nothing.add("robot.xml")
        status = _import(extension, "_on_import_mjcf", "/mjcf/robot.xml", '<robot name="r"/>')
        assert status == "MJCF import failed: /mjcf/robot.xml"
        assert (world.prims, world.layer.defaultPrim) == ({"/World"}, "World")

    def test_a_failed_mjcf_import_takes_away_the_world_it_created(self, extension, usd, importers):
        importers.imports_nothing.add("robot.xml")
        _import(extension, "_on_import_mjcf", "/mjcf/robot.xml", '<robot name="r"/>')
        assert (usd.stage.prims, usd.stage.layer.HasDefaultPrim()) == (set(), False)

    def test_a_failed_urdf_import_leaves_the_default_prim(self, extension, world, importers):
        """Control: the URDF importer returns no path, and changes nothing, for a file it cannot
        import."""
        importers.imports_nothing.add("bot.urdf")
        assert _import(extension, "_on_import_urdf", "/urdf/bot.urdf") == "URDF import failed"
        assert (world.prims, world.layer.defaultPrim) == ({"/World"}, "World")

    def test_the_scripting_api_returns_the_mjcf_prim_path(self, tmp_path, world, importers):
        connector = _connector(_FakeCli(files=("/mjcf/mj.xml",), contents={"/mjcf/mj.xml": MJ}))
        assert connector.download_and_import_mjcf("db", "a1", "/mjcf/mj.xml",
                                                  str(tmp_path)) == "/World/mj"

    def test_the_scripting_api_returns_none_for_an_mjcf_that_imports_nothing(self, tmp_path, world,
                                                                             importers):
        importers.imports_nothing.add("robot.xml")
        local_file = tmp_path / "robot.xml"
        local_file.write_text('<robot name="r"/>')
        assert _connector(_FakeCli()).import_mjcf_file(str(local_file)) is None
        assert world.prims == {"/World"}

    def test_the_scripting_api_raises_for_an_mjcf_that_is_not_well_formed(self, tmp_path, world,
                                                                         importers, caplog):
        local_file = tmp_path / "broken.xml"
        local_file.write_text(BROKEN_MJ)
        with pytest.raises(MalformedXmlError) as excinfo:
            _connector(_FakeCli()).import_mjcf_file(str(local_file))
        assert (str(excinfo.value), excinfo.value.reason) == (
            f"broken.xml is not well-formed XML: {_parse_error(BROKEN_MJ)}", _parse_error(BROKEN_MJ))
        connector = _connector(_FakeCli(files=("/mjcf/broken.xml",),
                                        contents={"/mjcf/broken.xml": BROKEN_MJ}))
        with caplog.at_level(logging.ERROR):
            assert connector.download_and_import_mjcf("db", "a1", "/mjcf/broken.xml",
                                                      str(tmp_path / "dl")) is None
        assert "broken.xml is not well-formed XML" in caplog.text
        assert importers == []

    @pytest.mark.parametrize("text, line", DOCTYPE_MJ.values(), ids=DOCTYPE_MJ.keys())
    def test_the_scripting_api_raises_for_an_mjcf_that_declares_a_document_type(
            self, tmp_path, world, importers, caplog, text, line):
        local_file = tmp_path / "dtd.xml"
        local_file.write_text(text)
        with pytest.raises(MalformedXmlError) as excinfo:
            _connector(_FakeCli()).import_mjcf_file(str(local_file))
        assert (str(excinfo.value), excinfo.value.reason) == (
            f"dtd.xml is refused: {_doctype_refusal(line)}", _doctype_refusal(line))
        connector = _connector(_FakeCli(files=("/mjcf/dtd.xml",),
                                        contents={"/mjcf/dtd.xml": text}))
        with caplog.at_level(logging.ERROR):
            assert connector.download_and_import_mjcf("db", "a1", "/mjcf/dtd.xml",
                                                      str(tmp_path / "dl")) is None
        assert "dtd.xml is refused: it declares a document type" in caplog.text
        assert importers == []
        assert world.prims == {"/World"}


class TestTheExportAction:
    @pytest.fixture
    def export(self, extension, monkeypatch, tmp_path):
        monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
        extension._export_name_model = _Model("export.usd")

        def _run(connector):
            extension._connector = connector
            extension._on_export()
            return extension._status_label.text

        return _run

    def test_the_status_says_how_many_paths_will_not_resolve_elsewhere(self, export):
        assert export(_Exporter(unportable=3)) == (
            "Uploaded export.usd to Asset One (3 file path(s) point outside this asset and will "
            "not resolve elsewhere)")

    def test_an_export_whose_paths_all_resolve_says_nothing_more(self, export):
        assert export(_Exporter()) == "Uploaded export.usd to Asset One"

    def test_the_file_list_is_reloaded_to_show_the_export(self, export, extension):
        exporter = _Exporter()
        export(exporter)
        assert exporter.listings == 1
        assert "/export.usd" in [f.relative_path for f in extension._files]

    def test_a_failed_export_leaves_the_file_list_alone(self, export):
        exporter = _Exporter(error=VamsCliError("Stage export failed: could not save"))
        assert export(exporter) == "Export error: Stage export failed: could not save"
        assert exporter.listings == 0

    def test_a_file_list_that_does_not_refresh_still_reports_the_upload(self, export):
        exporter = _Exporter(unportable=2, list_error=VamsCliError("API Error: 500 Server Error"))
        assert export(exporter) == (
            "Uploaded export.usd to Asset One (2 file path(s) point outside this asset and will "
            "not resolve elsewhere), but the file list did not refresh: API Error: 500 Server Error")
