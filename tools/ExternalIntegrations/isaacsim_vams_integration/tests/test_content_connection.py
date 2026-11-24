"""The Kit side of the Content window connection: interpreter lookup, the sign-in callback, start and stop."""
import io
import json
import os
import subprocess
import types

import pytest

from vams.connector.isaacsim import content_connection as cc


@pytest.fixture
def cli_env(tmp_path):
    """A VAMS CLI environment: bin/vamscli next to bin/python."""
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    vamscli = bin_dir / "vamscli"
    vamscli.write_text(f"#!{python}\n")
    vamscli.chmod(0o755)
    return str(vamscli), str(python)


class FakeClient:
    def __init__(self):
        self.tokens, self.cancelled, self.callbacks, self.direct = [], [], [], []

    def register_storage_auth_callback(self, fn):
        self.callbacks.append(fn)
        return types.SimpleNamespace(stop=lambda: self.callbacks.remove(fn))

    def set_storage_access_token(self, handle, token):
        self.tokens.append((handle, token))
        return True

    def authentication_cancel(self, handle):
        self.cancelled.append(handle)

    def register_storage_from_discovery(self, url):
        self.direct.append(url)
        return types.SimpleNamespace(stop=lambda: self.direct.remove(url))


class FakeApi:
    def __init__(self):
        self.added, self.removed = [], []

    def add_discovery_server(self, url, service_name=None):
        self.added.append((url, service_name))
        return 7

    def remove_discovery_server(self, service_id):
        self.removed.append(service_id)


class FakeProcess:
    def __init__(self, ready):
        self.stdout = io.StringIO(ready)
        self.stderr = io.StringIO("")
        self.terminated = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = -9


def ready_line(host="127.0.0.1", port=40123):
    return json.dumps({"discovery": f"http://{host}:{port}/discovery", "grpc": f"http://{host}:40124",
                       "root": "vams://vams"}) + "\n"


class FakeRegistry:
    def __init__(self, servers=()):
        self.servers = list(servers)
        self.started = []

    def get_discovery_servers(self):
        return list(self.servers)

    def start_discovery(self, url):
        self.started.append(url)
        return None


def make(cli_env, ready=None, api=None, token_result=None, launches=None, registry=None):
    vamscli, _ = cli_env
    client = FakeClient()
    procs = []

    def popen(args, **kwargs):
        if launches is not None:
            launches.append((args, kwargs))
        proc = FakeProcess(ready if ready is not None else ready_line())
        procs.append(proc)
        return proc

    def run(args, **kwargs):
        return token_result or subprocess.CompletedProcess(args, 0, stdout=json.dumps({"token": "vams_tok"}) + "\n",
                                                           stderr="")
    def schedule(coroutine):
        try:
            coroutine.send(None)
        except StopIteration:
            pass
    conn = cc.VamsContentConnection("edh", vamscli, omni_client=client, content_browser_api=api, popen=popen, run=run,
                                    registry=registry or FakeRegistry(), schedule=schedule)
    return conn, client, procs


def test_cli_python_is_the_interpreter_beside_the_cli(cli_env):
    vamscli, python = cli_env
    assert cc.cli_python(vamscli) == python


def test_cli_python_follows_a_symlink_to_the_cli(cli_env, tmp_path):
    vamscli, python = cli_env
    link = tmp_path / "vamscli-link"
    os.symlink(vamscli, link)
    assert cc.cli_python(str(link)) == python


def test_cli_python_is_none_without_a_cli(tmp_path):
    assert cc.cli_python(str(tmp_path / "missing")) is None


@pytest.mark.parametrize("url,origin", [
    ("http://127.0.0.1:8099/discovery", ("http", "127.0.0.1", 8099)),
    ("HTTP://127.0.0.1:8099", ("http", "127.0.0.1", 8099)),
    ("https://example.com/x", ("https", "example.com", 443)),
    ("http://user:pw@127.0.0.1:8099", None),
    ("not a url", None),
])
def test_origin_of(url, origin):
    assert cc.origin_of(url) == origin


def test_start_launches_the_adapter_with_the_cli_python_and_registers_with_the_content_browser(cli_env):
    launches, api = [], FakeApi()
    conn, client, procs = make(cli_env, api=api, launches=launches)
    url = conn.start()
    assert url == "http://127.0.0.1:40123/discovery"
    args, kwargs = launches[0]
    assert args[0] == cli_env[1] and args[1] == cc.SERVER_SCRIPT and args[2:] == ["--profile", "edh"]
    assert kwargs["stdout"] == subprocess.PIPE
    assert api.added == [(url, "VAMS")]
    assert len(client.callbacks) == 1 and client.direct == []


def test_start_without_a_content_browser_registers_with_the_client_library(cli_env):
    conn, client, _ = make(cli_env, api=None)
    url = conn.start()
    assert client.direct == [url]


def test_start_refuses_an_adapter_that_reports_a_non_loopback_address(cli_env):
    conn, client, procs = make(cli_env, ready=ready_line(host="0.0.0.0"), api=FakeApi())
    with pytest.raises(cc.ContentConnectionError):
        conn.start()
    assert procs[0].terminated and client.callbacks == []


def test_start_fails_cleanly_when_the_adapter_never_becomes_ready(cli_env):
    conn, client, procs = make(cli_env, ready="", api=FakeApi())
    with pytest.raises(cc.ContentConnectionError):
        conn.start()
    assert procs[0].terminated and not conn.running


def test_start_needs_storage_support_in_the_client_library(cli_env):
    conn, _, _ = make(cli_env)
    conn._client = types.SimpleNamespace()
    with pytest.raises(cc.ContentConnectionError, match="6.1"):
        conn.start()


def test_the_sign_in_callback_answers_only_for_the_adapters_exact_origin(cli_env):
    conn, client, _ = make(cli_env, api=FakeApi())
    conn.start()
    callback = client.callbacks[0]
    assert callback(1, "http://127.0.0.1:40124/discovery") is False
    assert callback(2, "http://localhost:40123/discovery") is False
    assert callback(3, "https://127.0.0.1:40123/discovery") is False
    assert callback(4, "http://127.0.0.1:40123/discovery") is True
    assert client.tokens == [(4, "vams_tok")]


def test_the_sign_in_callback_cancels_when_the_profile_has_no_sign_in(cli_env):
    result = subprocess.CompletedProcess([], 3, stdout="", stderr="")
    conn, client, _ = make(cli_env, api=FakeApi(), token_result=result)
    conn.start()
    assert client.callbacks[0](5, "http://127.0.0.1:40123/discovery") is True
    assert client.cancelled == [5] and client.tokens == []


def test_stop_unregisters_and_stops_the_adapter(cli_env):
    api = FakeApi()
    conn, client, procs = make(cli_env, api=api)
    conn.start()
    conn.stop()
    assert api.removed == [7] and client.callbacks == [] and procs[0].terminated
    assert conn.discovery_url is None
    conn.stop()
    assert api.removed == [7]


def test_the_token_helper_reports_no_sign_in_for_an_expired_profile():
    from vams.connector.isaacsim.storage_adapter import token_helper

    assert token_helper._expired("2000-01-01T00:00:00Z") is True
    assert token_helper._expired("2999-01-01T00:00:00Z") is False
    assert token_helper._expired(1) is True
    assert token_helper._expired(None) is False


def test_start_begins_discovery_so_vams_urls_resolve_before_the_entry_is_expanded(cli_env):
    registry = FakeRegistry()
    conn, _, _ = make(cli_env, api=FakeApi(), registry=registry)
    url = conn.start()
    assert registry.started == [url]


def test_start_removes_vams_entries_left_by_an_earlier_session(cli_env):
    stale = [types.SimpleNamespace(name="VAMS", discovery_url="http://127.0.0.1:1111/discovery", service_id=3),
             types.SimpleNamespace(name="VAMS", discovery_url="https://vams.example.com/discovery", service_id=4),
             types.SimpleNamespace(name="Other", discovery_url="http://127.0.0.1:2222/discovery", service_id=5),
             types.SimpleNamespace(name="VAMS", discovery_url="", service_id=6)]
    api = FakeApi()
    conn, _, _ = make(cli_env, api=api, registry=FakeRegistry(stale))
    conn.start()
    assert api.removed == [3]
