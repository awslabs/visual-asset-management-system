"""Tests for what the extension's file browser and file actions tell the user about the files."""

import contextlib
import types

import omni.ui as ui

from vams.connector.isaacsim.extension import VamsConnectorExtension
from vams.connector.isaacsim.vams_cli_service import Asset, AssetFile, Database


def test_download_only_names_the_folder_the_file_is_saved_in(monkeypatch):
    tooltips = {}
    monkeypatch.setattr(ui, "Label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui, "VStack", contextlib.contextmanager(lambda *_args, **_kwargs: (yield)))
    monkeypatch.setattr(ui, "Button", lambda text="", **kwargs: tooltips.__setitem__(
        text, kwargs.get("tooltip", "")))
    ext = VamsConnectorExtension()
    ext._init_state()
    ext._selected_file = AssetFile(relative_path="/snowman/textures/color.jpg", size=581036)

    ext._build_file_actions()

    tooltip = tooltips["Download Only (save to disk)"]
    assert "~/vams_downloads" in tooltip
    assert "path within the asset" in tooltip
    assert "temporary" not in tooltip


def test_the_file_count_leaves_out_folders():
    """The listing carries folder entries, the asset root "/" among them, which the list does not
    show as files."""
    ext = VamsConnectorExtension()
    ext._init_state()
    ext._status_label = types.SimpleNamespace(text="")
    ext._selected_db = Database(database_id="db")
    ext._selected_asset = Asset(asset_id="a1", asset_name="GingerbreadHouse")
    ext._connector = types.SimpleNamespace(list_files=lambda _db, _asset: [
        AssetFile(relative_path="/", is_folder=True),
        AssetFile(relative_path="/house/", is_folder=True),
        AssetFile(relative_path="/GingerbreadHouse.usda"),
        AssetFile(relative_path="/house/GingerBreadHouse.usdc"),
        AssetFile(relative_path="/", file_name=""),
    ])

    ext._load_files()

    assert ext._status_label.text == "  2 file(s) in GingerbreadHouse"


def test_a_long_status_is_set_whole_and_carried_in_its_tooltip():
    ext = VamsConnectorExtension()
    ext._init_state()
    ext._status_label = types.SimpleNamespace(text="", tooltip="")
    status = ("Uploaded hunt-export-3.usd to Gingerbread House (1 file path(s) point outside this "
              "asset and will not resolve elsewhere)")

    ext._set_status(status)

    assert ext._status_label.text == f"  {status}"
    assert ext._status_label.tooltip == status


def test_the_status_bar_wraps_its_text(monkeypatch):
    """A label that does not wrap sets the width of the window's scroll frame, which then scrolls
    sideways and moves every centred button label out of view."""
    labels, stacks = [], []
    stack = contextlib.contextmanager(lambda **kwargs: (yield stacks.append(kwargs)))
    monkeypatch.setattr(ui, "ZStack", stack)
    monkeypatch.setattr(ui, "VStack", stack)
    monkeypatch.setattr(ui, "Rectangle", lambda **_kwargs: None)
    monkeypatch.setattr(ui, "Spacer", lambda **_kwargs: None)
    monkeypatch.setattr(ui, "Label", lambda text="", **kwargs: labels.append(kwargs))
    ext = VamsConnectorExtension()
    ext._init_state()

    ext._build_status_bar()

    assert labels[0].get("word_wrap") is True
    # A fixed height would clip the lines the label wraps onto.
    assert [kwargs.get("height", 0) for kwargs in stacks] == [0] * len(stacks)
