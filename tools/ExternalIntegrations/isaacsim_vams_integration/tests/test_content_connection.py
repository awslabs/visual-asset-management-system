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
        self.removed = []

    def get_discovery_servers(self):
        return list(self.servers)

    def start_discovery(self, url):
        self.started.append(url)
        return None

    def remove_discovery_server(self, url):
        self.removed.append(url)
        self.servers = [s for s in self.servers if s.discovery_url != url]


class _NoLoop:
    """Stands in for Kit's event loop: runs nothing, so no real loop is created in the tests."""

    def call_soon_threadsafe(self, fn):
        pass


def make(cli_env, ready=None, api=None, token_result=None, launches=None, registry=None, clock=None, tokens=None):
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
        if tokens:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"token": tokens[0]}) + "\n", stderr="")
        return token_result or subprocess.CompletedProcess(args, 0, stdout=json.dumps({"token": "vams_tok"}) + "\n",
                                                           stderr="")
    def schedule(coroutine):
        try:
            coroutine.send(None)
        except StopIteration:
            pass
    conn = cc.VamsContentConnection("edh", vamscli, omni_client=client, content_browser_api=api, popen=popen, run=run,
                                    registry=registry or FakeRegistry(), schedule=schedule, loop=_NoLoop(),
                                    **({"clock": clock} if clock else {}))
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
    assert args[0] == cli_env[1] and args[1] == cc.SERVER_SCRIPT and args[2:] == ["--profile", "edh", "--watch-stdin"]
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stdin"] == subprocess.PIPE
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


def test_start_removes_vams_entries_left_by_an_earlier_session_from_the_registry(cli_env):
    stale = [types.SimpleNamespace(name="VAMS", discovery_url="http://127.0.0.1:1111/discovery", service_id=3),
             types.SimpleNamespace(name="VAMS", discovery_url="https://vams.example.com/discovery", service_id=4),
             types.SimpleNamespace(name="Other", discovery_url="http://127.0.0.1:2222/discovery", service_id=5),
             types.SimpleNamespace(name="VAMS", discovery_url="", service_id=6)]
    registry, api = FakeRegistry(stale), FakeApi()
    conn, _, _ = make(cli_env, api=api, registry=registry)
    conn.start()
    assert registry.removed == ["http://127.0.0.1:1111/discovery"] and api.removed == []


def test_stop_also_removes_the_entry_from_the_registry(cli_env):
    registry, api = FakeRegistry(), FakeApi()
    conn, _, _ = make(cli_env, api=api, registry=registry)
    url = conn.start()
    conn.stop()
    assert api.removed == [7] and registry.removed == [url]


ORIGIN = "http://127.0.0.1:40123/discovery"


def test_a_second_request_for_the_same_token_fails_at_once_instead_of_waiting(cli_env):
    now = [100.0]
    conn, client, _ = make(cli_env, api=FakeApi(), clock=lambda: now[0])
    conn.start()
    ask = client.callbacks[0]
    assert ask(1, ORIGIN) is True and client.tokens == [(1, "vams_tok")]
    # VAMS refused it, so the client library asks again: cancelled, not handed out again.
    assert ask(2, ORIGIN) is True and client.cancelled == [2]
    now[0] += 30
    assert ask(3, ORIGIN) is True and client.cancelled == [2, 3]
    assert len(client.tokens) == 1


def test_a_refused_token_is_tried_again_after_the_retry_interval(cli_env):
    now = [100.0]
    conn, client, _ = make(cli_env, api=FakeApi(), clock=lambda: now[0])
    conn.start()
    ask = client.callbacks[0]
    ask(1, ORIGIN)
    ask(2, ORIGIN)
    now[0] += cc.REFUSED_RETRY_SECONDS + 1
    assert ask(3, ORIGIN) is True and client.tokens[-1] == (3, "vams_tok")


def test_a_new_token_in_the_profile_is_handed_out_at_once(cli_env):
    now, tokens = [100.0], ["vams_old"]
    conn, client, _ = make(cli_env, api=FakeApi(), clock=lambda: now[0], tokens=tokens)
    conn.start()
    ask = client.callbacks[0]
    ask(1, ORIGIN)
    ask(2, ORIGIN)
    assert client.cancelled == [2]
    tokens[0] = "vams_renewed"
    assert ask(3, ORIGIN) is True and client.tokens[-1] == (3, "vams_renewed")


def test_a_refusal_is_logged_once_per_episode(cli_env, caplog):
    now = [100.0]
    conn, client, _ = make(cli_env, api=FakeApi(), clock=lambda: now[0])
    conn.start()
    ask = client.callbacks[0]
    ask(1, ORIGIN)
    with caplog.at_level("WARNING"):
        for handle in range(2, 12):
            ask(handle, ORIGIN)
    assert sum("refused" in r.getMessage() for r in caplog.records) == 1
    assert all("vams_tok" not in r.getMessage() for r in caplog.records)


class ImmediateLoop:
    def __init__(self):
        self.calls = 0

    def call_soon_threadsafe(self, fn):
        self.calls += 1
        fn()


def test_a_refusal_starts_one_watch_for_a_new_sign_in(cli_env):
    now = [100.0]
    conn, client, _ = make(cli_env, api=FakeApi(), clock=lambda: now[0])
    loop, scheduled = ImmediateLoop(), []
    conn._loop, conn._schedule = loop, lambda coroutine: (scheduled.append(coroutine), coroutine.close())
    conn.start()
    ask = client.callbacks[0]
    ask(1, ORIGIN)
    for handle in range(2, 6):
        ask(handle, ORIGIN)
    watches = [c for c in scheduled if c.cr_code.co_name == "_sign_in_watch"]
    assert loop.calls == 1 and len(watches) == 1


def test_the_watch_reconnects_once_the_profile_holds_a_usable_sign_in(cli_env, monkeypatch):
    import asyncio

    monkeypatch.setattr(cc, "SIGN_IN_POLL_SECONDS", 0)
    now, tokens = [100.0], ["vams_old"]
    api = FakeApi()
    conn, client, procs = make(cli_env, api=api, clock=lambda: now[0], tokens=tokens)
    conn.start()
    ask = client.callbacks[0]
    ask(1, ORIGIN)
    ask(2, ORIGIN)
    tokens[0] = "vams_renewed"
    asyncio.run(conn._sign_in_watch())
    assert procs[0].terminated and len(procs) == 2 and conn.running
    assert api.removed == [7] and len(api.added) == 2
    new_ask = client.callbacks[-1]
    assert new_ask(9, ORIGIN) is True and client.tokens[-1] == (9, "vams_renewed")


def test_reconnect_does_nothing_when_not_running(cli_env):
    conn, _, procs = make(cli_env, api=FakeApi())
    assert conn.reconnect() is None and procs == []


def test_the_sign_in_callback_hands_out_no_token_once_the_adapter_has_exited(cli_env):
    conn, client, procs = make(cli_env, api=FakeApi())
    conn.start()
    procs[0].returncode = 1
    assert client.callbacks[0](8, "http://127.0.0.1:40123/discovery") is True
    assert client.cancelled == [8] and client.tokens == []


def test_stale_entries_are_removed_even_when_the_adapter_does_not_start(cli_env):
    stale = [types.SimpleNamespace(name="VAMS", discovery_url="http://127.0.0.1:1111/discovery", service_id=3)]
    registry = FakeRegistry(stale)
    conn, _, _ = make(cli_env, ready="", api=FakeApi(), registry=registry)
    with pytest.raises(cc.ContentConnectionError):
        conn.start()
    assert registry.removed == ["http://127.0.0.1:1111/discovery"]

