"""A profile write swaps a whole document in, so no reader or concurrent writer sees part of one.

Two machines that share one home directory over NFS can sign the same profile in at once, while a
connector and a terminal read profiles the CLI may be rewriting. `ProfileManager`
therefore writes each file to a temporary file beside it and swaps that in with `os.replace`: a
reader opens the previous document or the new one, a write that fails leaves the previous one as it
was, and two writers leave one of their documents rather than an interleaving of both.
"""

import json
import os
import stat
import threading
import types

import pytest

import vamscli.utils.profile as profile_module
from vamscli.utils.logging import OWNER_ONLY_FILE_MODE
from vamscli.utils.profile import ProfileManager


PROFILE = "atomic-profile"
AUTH = {"user_id": "someone@example.com", "access_token": "token-one"}

# Every ProfileManager method that persists a file, with the file it writes.
WRITES = {
    "active profile": ("active_profile_file", lambda m: m.set_active_profile(PROFILE)),
    "config": ("config_file", lambda m: m.save_config({"api_gateway_url": "https://vams.example.com"})),
    "auth profile": ("auth_profile_file", lambda m: m.save_auth_profile(AUTH)),
    "credentials": ("credentials_file",
                    lambda m: m.save_credentials({"username": "someone", "password": "Hunter2!"})),
    "override token": ("auth_profile_file",
                       lambda m: m.save_override_token("vams_fixturekey", "someone@example.com")),
    "feature switches": ("auth_profile_file",
                         lambda m: m.save_feature_switches({"featuresEnabled": "LOCATIONSERVICES"})),
}
CREDENTIAL_FILES = ("auth_profile_file", "credentials_file")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    config_dir = tmp_path / "vamscli"
    # Both bindings: ProfileManager reads the name imported into utils.profile, while
    # get_profile_dir() resolves through the constants module's own global.
    monkeypatch.setattr("vamscli.constants.get_config_dir", lambda: config_dir)
    monkeypatch.setattr("vamscli.utils.profile.get_config_dir", lambda: config_dir)
    return config_dir


@pytest.fixture
def manager(config_dir):
    manager = ProfileManager(PROFILE)
    # The feature switch write updates an existing auth profile, so one is present for every case.
    manager.save_auth_profile(AUTH)
    return manager


@pytest.fixture
def file_events(monkeypatch):
    """Records the creation mode of every file opened for creation, and every chmod and replace."""
    events = []
    real_open, real_chmod, real_replace = os.open, os.chmod, os.replace

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        if flags & os.O_CREAT:
            events.append(("create", os.path.normcase(os.fspath(path)), mode))
        return real_open(path, flags, mode, *args, **kwargs)

    def recording_chmod(path, mode, *args, **kwargs):
        events.append(("chmod", os.path.normcase(os.fspath(path)), mode))
        return real_chmod(path, mode, *args, **kwargs)

    def recording_replace(source, destination, *args, **kwargs):
        events.append(("replace", os.path.normcase(os.fspath(source)),
                       os.path.normcase(os.fspath(destination))))
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "chmod", recording_chmod)
    monkeypatch.setattr(os, "replace", recording_replace)
    return events


def _files_under(directory):
    return sorted(os.path.relpath(os.path.join(root, name), directory)
                  for root, _, names in os.walk(directory) for name in names)


def _swapped_in(events, path):
    """The temporary file the last replace moved onto `path`, with what was done to it before."""
    target = os.path.normcase(os.fspath(path))
    swaps = [i for i, event in enumerate(events) if event[0] == "replace" and event[2] == target]
    assert swaps, f"nothing was swapped onto {path}; recorded: {events}"
    index = swaps[-1]
    source = events[index][1]
    before = [event for event in events[:index] if event[1] == source]
    return source, before


@pytest.mark.parametrize("attribute, write", WRITES.values(), ids=WRITES.keys())
def test_a_write_swaps_in_a_whole_document_and_leaves_no_temporary_file(
        manager, config_dir, file_events, attribute, write):
    write(manager)

    path = getattr(manager, attribute)
    source, before = _swapped_in(file_events, path)
    assert os.path.dirname(source) == os.path.dirname(os.path.normcase(os.fspath(path)))
    assert any(event[0] == "create" for event in before)
    json.loads(path.read_text(encoding="utf-8"))
    assert [name for name in _files_under(config_dir) if name.endswith(".tmp")] == []


def _failing_json(**overrides):
    return types.SimpleNamespace(**{**vars(json), **overrides})


def _dump_then_fail(obj, f, **kwargs):
    f.write('{"partial": ')
    raise OSError("No space left on device")


@pytest.mark.parametrize("attribute, write", WRITES.values(), ids=WRITES.keys())
def test_a_write_that_fails_part_way_leaves_the_previous_file_intact(
        manager, config_dir, monkeypatch, attribute, write):
    write(manager)
    path = getattr(manager, attribute)
    previous = path.read_bytes()

    monkeypatch.setattr(profile_module, "json", _failing_json(dump=_dump_then_fail))
    with pytest.raises(OSError, match="No space left on device"):
        write(manager)

    assert path.read_bytes() == previous
    assert [name for name in _files_under(config_dir) if name.endswith(".tmp")] == []


def test_a_swap_that_fails_leaves_the_previous_file_intact(manager, config_dir, monkeypatch):
    previous = manager.auth_profile_file.read_bytes()

    def refuse(source, destination):
        raise OSError("Stale file handle")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="Stale file handle"):
        manager.save_auth_profile({**AUTH, "access_token": "token-two"})

    assert manager.auth_profile_file.read_bytes() == previous
    assert [name for name in _files_under(config_dir) if name.endswith(".tmp")] == []


@pytest.mark.parametrize("attribute, write",
                         [WRITES[k] for k in ("auth profile", "credentials", "override token",
                                              "feature switches")],
                         ids=["auth profile", "credentials", "override token", "feature switches"])
def test_a_credential_file_is_owner_only_before_it_is_swapped_in(
        manager, file_events, attribute, write):
    write(manager)

    _, before = _swapped_in(file_events, getattr(manager, attribute))
    requested = [event[2] for event in before if event[0] in ("create", "chmod")]
    assert requested, f"no mode was requested for the temporary file: {before}"
    assert all(mode & 0o077 == 0 for mode in requested), requested


@pytest.mark.skipif(os.name != "posix", reason="Windows reflects only the read-only bit of a mode")
@pytest.mark.parametrize("attribute", CREDENTIAL_FILES)
def test_a_loosened_credential_file_is_owner_only_again_after_a_write(manager, attribute):
    # pragma: allowlist nextline secret
    manager.save_credentials({"username": "someone", "password": "Hunter2!"})
    path = getattr(manager, attribute)
    os.chmod(path, 0o644)

    manager.save_auth_profile(AUTH)
    # pragma: allowlist nextline secret
    manager.save_credentials({"username": "someone", "password": "Hunter2!"})

    assert stat.S_IMODE(os.stat(path).st_mode) == OWNER_ONLY_FILE_MODE


@pytest.mark.parametrize("attribute, write", [WRITES["config"], WRITES["active profile"]],
                         ids=["config", "active profile"])
def test_any_other_file_keeps_the_mode_of_the_file_it_replaces(
        manager, file_events, attribute, write):
    write(manager)
    path = getattr(manager, attribute)
    if os.name == "posix":
        os.chmod(path, 0o640)
    kept = stat.S_IMODE(os.stat(path).st_mode)
    file_events.clear()

    write(manager)

    source, before = _swapped_in(file_events, path)
    assert ("chmod", source, kept) in before
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="the process umask does not govern Windows modes")
def test_a_new_file_other_than_a_credential_gets_the_default_mode(manager):
    previous_umask = os.umask(0o022)
    try:
        manager.save_config({"api_gateway_url": "https://vams.example.com"})
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(os.stat(manager.config_file).st_mode) == 0o644


class TestARefusedSwap:
    """Windows refuses os.replace while another process holds the destination open, as a CLI reading
    the profile does for a moment, so the swap is retried there."""

    @pytest.fixture
    def refusals(self, monkeypatch):
        real_replace = os.replace
        calls = []

        def refuse_then_replace(source, destination):
            calls.append(source)
            if len(calls) <= refusals.count:
                raise PermissionError(13, "Access is denied")
            return real_replace(source, destination)

        refusals = types.SimpleNamespace(count=0, calls=calls)
        monkeypatch.setattr(os, "replace", refuse_then_replace)
        monkeypatch.setattr(profile_module.time, "sleep", lambda seconds: None)
        return refusals

    def test_is_retried_on_windows_until_it_succeeds(self, manager, refusals, monkeypatch):
        monkeypatch.setattr(profile_module, "_RETRY_REFUSED_REPLACE", True)
        refusals.count = 2

        manager.save_auth_profile({**AUTH, "access_token": "token-two"})

        assert len(refusals.calls) == 3
        assert json.loads(manager.auth_profile_file.read_text())["access_token"] == "token-two"

    def test_gives_up_after_its_attempts_and_leaves_the_previous_file(
            self, manager, config_dir, refusals, monkeypatch):
        monkeypatch.setattr(profile_module, "_RETRY_REFUSED_REPLACE", True)
        refusals.count = profile_module._REPLACE_ATTEMPTS
        previous = manager.auth_profile_file.read_bytes()

        with pytest.raises(PermissionError):
            manager.save_auth_profile({**AUTH, "access_token": "token-two"})

        assert len(refusals.calls) == profile_module._REPLACE_ATTEMPTS
        assert manager.auth_profile_file.read_bytes() == previous
        assert [name for name in _files_under(config_dir) if name.endswith(".tmp")] == []

    def test_is_not_retried_elsewhere(self, manager, refusals, monkeypatch):
        monkeypatch.setattr(profile_module, "_RETRY_REFUSED_REPLACE", False)
        refusals.count = 1

        with pytest.raises(PermissionError):
            manager.save_auth_profile({**AUTH, "access_token": "token-two"})

        assert len(refusals.calls) == 1


def test_concurrent_writers_and_a_reader_only_ever_see_whole_documents(manager):
    """Two sign-ins of one profile at once, with a reader of the same file throughout."""
    documents = [{"api_gateway_url": f"https://writer-{n}.example.com", "padding": str(n) * 200_000}
                 for n in (1, 2)]
    done = threading.Event()
    torn = []
    reads = []
    errors = []

    def write(document):
        try:
            writer = ProfileManager(PROFILE)
            for _ in range(15):
                writer.save_config(document)
        except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(error)

    def read():
        while not done.is_set():
            try:
                text = manager.config_file.read_text(encoding="utf-8")
            except (FileNotFoundError, PermissionError):
                # Windows refuses an open while the file is being swapped; the next read sees it.
                continue
            reads.append(text)
            try:
                json.loads(text)
            except json.JSONDecodeError:
                torn.append(len(text))

    reader = threading.Thread(target=read)
    writers = [threading.Thread(target=write, args=(document,)) for document in documents]
    reader.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    done.set()
    reader.join()

    assert errors == []
    assert reads, "the reader never read the file, so the torn-read assertion would be vacuous"
    assert torn == []
    assert json.loads(manager.config_file.read_text(encoding="utf-8")) in documents
