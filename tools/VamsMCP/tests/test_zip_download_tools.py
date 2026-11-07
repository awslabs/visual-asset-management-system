"""The Download as Zip tools: their gate tiers, the parameters reaching the APIClient, the
disabled-feature error as data, and the URL tool kept out of the autoApprove sample."""

import importlib
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vams_mcp import server as server_module
from vamscli.utils.exceptions import ZipDownloadDisabledError, ZipDownloadStateError

README = Path(server_module.__file__).resolve().parents[1] / "README.md"
GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


@pytest.fixture
def gated(monkeypatch):
    """The server reloaded with both gates on and a mocked client; restored afterwards."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        assert server_module.CONFIG.enable_writes and server_module.CONFIG.enable_destructive
        client = MagicMock()
        client.unwrap_message = server_module.VamsClient.unwrap_message
        client.config = server_module.CONFIG
        monkeypatch.setattr(server_module, "CLIENT", client)
        yield server_module, client
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


@pytest.fixture
def reads(monkeypatch):
    client = MagicMock()
    client.config = server_module.CONFIG
    client.unwrap_message = server_module.VamsClient.unwrap_message
    client.paginate = lambda *a, **k: server_module.VamsClient.paginate(client, *a, **k)
    monkeypatch.setattr(server_module, "CLIENT", client)
    return client


def _line_of(text):
    return next(i for i, line in enumerate(server_module.__loader__.get_source(
        server_module.__name__).splitlines()) if line.startswith(text))


def _def_line(name):
    source = server_module.__loader__.get_source(server_module.__name__).splitlines()
    return next(i for i, line in enumerate(source) if re.match(rf"\s*def {name}\(", line))


def test_tools_sit_in_their_tiers():
    writes = _line_of("if CONFIG.enable_writes:")
    destructive = _line_of("if CONFIG.enable_destructive:")
    main = _line_of("def main()")
    for name in ("list_zip_downloads", "get_zip_download"):
        assert _def_line(name) < writes, f"{name} is a read tool"
    for name in ("create_zip_download", "get_zip_download_url"):
        assert writes < _def_line(name) < destructive, f"{name} belongs to the write tier"
    for name in ("cancel_zip_download", "delete_zip_download"):
        assert destructive < _def_line(name) < main, f"{name} belongs to the destructive tier"


@pytest.mark.asyncio
async def test_gated_zip_tools_are_off_by_default():
    names = {tool.name for tool in await server_module.mcp.list_tools()}
    assert {"list_zip_downloads", "get_zip_download"} <= names
    assert not names & {"create_zip_download", "get_zip_download_url", "cancel_zip_download",
                        "delete_zip_download"}


def test_list_forwards_scope_and_token(reads):
    reads.api.list_zip_downloads.return_value = {"Items": [{"requestId": "r1"}], "NextToken": None}
    result = server_module.list_zip_downloads("db", "a", max_items=7, starting_token="tok")
    assert result["Items"] == [{"requestId": "r1"}]
    kwargs = reads.api.list_zip_downloads.call_args.kwargs
    assert kwargs["database_id"] == "db" and kwargs["asset_id"] == "a"
    assert kwargs["starting_token"] == "tok" and kwargs["page_size"] == 7


def test_disabled_is_returned_as_data(reads):
    reads.api.get_zip_download.side_effect = ZipDownloadDisabledError(
        "Zip downloads are not available for this deployment")
    assert server_module.get_zip_download("db", "a", "r1") == {
        "error": "Zip downloads are not available for this deployment",
        "error_type": "ZipDownloadDisabledError",
    }


def test_create_builds_the_cli_body(gated):
    module, client = gated
    client.api.create_zip_download.return_value = {"requestId": "r1", "status": "QUEUED"}
    module.create_zip_download("db", "a", files=["/m.glb@v1"], folders=["tex"],
                               archive_format="zip", compression="deflate",
                               archive_file_name="out")
    args = client.api.create_zip_download.call_args.args
    assert args[:2] == ("db", "a")
    assert args[2] == {
        "selection": [{"type": "file", "relativePath": "/m.glb", "versionId": "v1"},
                      {"type": "folder", "relativePath": "/tex/"}],
        "archiveFormat": "zip", "compression": "deflate", "archiveFileName": "out",
    }


def test_create_refusal_is_data_and_sends_nothing(gated):
    module, client = gated
    result = module.create_zip_download("db", "a", files=["/a"], whole_asset=True)
    assert result["error_type"] == "InvalidZipDownloadRequestError"
    client.api.create_zip_download.assert_not_called()


def test_whole_asset_pinned(gated):
    module, client = gated
    module.create_zip_download("db", "a", whole_asset=True, asset_version_alias="release")
    body = client.api.create_zip_download.call_args.args[2]
    assert body["selection"] == [{"type": "asset", "relativePath": "/"}]
    assert body["assetVersionIdAlias"] == "release"


def test_url_cancel_delete_reach_the_client(gated):
    module, client = gated
    module.get_zip_download_url("db", "a", "r1")
    module.cancel_zip_download("db", "a", "r1")
    module.delete_zip_download("db", "a", "r1")
    client.api.get_zip_download_url.assert_called_once_with("db", "a", "r1")
    client.api.cancel_zip_download.assert_called_once_with("db", "a", "r1")
    client.api.delete_zip_download.assert_called_once_with("db", "a", "r1")


def test_state_error_is_data(gated):
    module, client = gated
    client.api.delete_zip_download.side_effect = ZipDownloadStateError("still RUNNING")
    assert module.delete_zip_download("db", "a", "r1")["error_type"] == "ZipDownloadStateError"


def _auto_approve():
    text = README.read_text(encoding="utf-8")
    block = re.search(r'"autoApprove": (\[.*?\])', text, re.S)
    assert block, "README has no autoApprove sample"
    return json.loads(block.group(1))


def test_only_the_reads_are_auto_approved():
    approved = _auto_approve()
    assert {"list_zip_downloads", "get_zip_download"} <= set(approved)
    for gated_tool in ("create_zip_download", "get_zip_download_url", "cancel_zip_download",
                       "delete_zip_download"):
        assert gated_tool not in approved


def test_url_tool_says_it_returns_a_bearer_credential():
    doc = server_module.__loader__.get_source(server_module.__name__)
    start = doc.index("def get_zip_download_url(")
    docstring = doc[start:start + 1500]
    assert "bearer credential" in docstring and "autoApprove" in docstring
