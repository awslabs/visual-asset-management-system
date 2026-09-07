"""The CLI's on-disk artefacts that can hold credentials must be readable only by their owner.

FIX-010 (finding S6-TOOLS-004). The finding was titled "api-key create writes the plaintext API key
into the rotating log file", but the redaction half of it is **already fixed at HEAD**:
`output_result` (`utils/json_output.py:52`), `log_api_request` and `log_api_response` all route their
payload through `redact_to_text`, and the redactor masks `apiKey` both by key name and by the
`vams_…` value shape. `tests/test_log_redaction.py` pins that, and
`TestRedactionBaselineAlreadyPasses` below restates it explicitly so nobody reads a green
"the key is not in the log" assertion as evidence for the other half.
`tests/test_log_redaction_scope.py` pins the opposite edge of the same boundary — a pagination cursor
such as `starting_token` must reach the log in the clear — because "the secret is masked" is also
satisfied by a redactor that masks everything.

That other half is the **file permissions**, and it spans two directories, not one:

* `ensure_log_dir()` (`utils/logging.py`) called `mkdir` with the default mode, and
  `RotatingFileHandler` created `vamscli.log` through the process umask. On POSIX with the usual
  0o022 umask the log directory was 0o755 and the log file 0o644 — world-readable. The log holds
  redacted payloads but also every URL, user id, profile name and error the CLI has ever seen. It
  now mkdirs at 0o700, narrows a directory it finds already there, and reapplies 0o600 to
  `vamscli.log` on every open, including the one after each rollover.
* By the same omission `ProfileManager` (`utils/profile.py`) wrote `auth_profile.json` and
  `credentials.json` with default permissions. Those hold the **live Amazon Cognito refresh token**
  and, when the user asks for it, the password — a strictly larger secret than a redacted log line.
  A fix scoped only to `logs/` would therefore look complete and not be, which is why the profile
  half is asserted here rather than tracked separately.

Four traps this file is written around:

1. The finding names `~/.vams/logs`, which does not exist. `get_config_dir()`
   (`constants.py:244-259`) resolves to `%APPDATA%/vamscli` on Windows, `~/.config/vamscli` on
   Linux and `~/Library/Application Support/vamscli` on macOS. Every path here is derived from that
   function (or from a patch of it), never spelled out.
2. `os.chmod` on Windows only toggles the read-only bit, so `stat.S_IMODE(...) == 0o600` is
   meaningless on win32 — which is the development platform for this repository. `_assert_owner_only`
   therefore asserts the **real mode** on POSIX (the load-bearing assertion, and the one CI runs) and
   falls back on win32 to asserting that an explicit narrowing was *requested* for that exact path.
   The recognised forms of a request are `os.chmod` / `Path.chmod`, `Path.mkdir(mode=…)` and
   `os.open(..., mode)`; a fix that instead relied on temporarily setting the process umask would
   satisfy the POSIX assertion but not the win32 one. That is deliberate: the umask is
   process-global, is not thread-safe, and does nothing about an already-loose file.
3. `tmp_path_factory.mktemp()` creates its directory at 0o700 on POSIX, so a test that asserts
   0o700 on a directory pytest handed it passes without the fix ever running. Every directory
   asserted on below is created by the code under test, or is deliberately pre-loosened first.
4. A mode assertion on a freshly created path is only about the code under a permissive umask. On a
   hardened host (umask 0o077) `mkdir` and `open` yield 0o700 and 0o600 on their own, so four of the
   tests below would pass with no narrowing in the code at all — they would measure the process
   mask rather than the fix. `permissive_umask` pins the mask at 0o000 for those, and
   `test_permissions_do_not_depend_on_the_ambient_umask` sweeps 0o000, 0o022 and 0o077 to assert the
   mode is invariant, which only an explicit narrowing achieves.

`tools/VamsCLI` configures pytest in `pyproject.toml` (`[tool.pytest.ini_options]`) with
`xfail_strict = true`, so a test parked here as an xfail against a future fix cannot rot into a
silently tolerated XPASS.
"""

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

import vamscli.utils.logging as vamscli_logging
from vamscli.constants import LOG_FILE_NAME
from vamscli.utils.logging import initialize_logging, redact_to_text
from vamscli.utils.profile import ProfileManager


# The one-time response shape of `api-key create`, used for the redaction baseline.
API_KEY_CREATE_RESPONSE = {
    "apiKeyId": "key-abc123",
    "apiKeyName": "ci-runner",
    "userId": "someone@example.com",
    "expiresAt": "2027-01-01T00:00:00Z",
    "apiKey": "vams_LIVEONETIMESECRETVALUE0123456789",
}


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


class _NarrowingRecorder:
    """Records every explicit permission request the code under test makes against a path.

    Used only where the real mode is not observable (win32). See trap 2 in the module docstring.
    """

    def __init__(self):
        self.events = []

    def record(self, path, mode):
        try:
            self.events.append((_norm(path), mode))
        except Exception:
            pass

    def modes_for(self, path):
        key = _norm(path)
        return [mode for recorded, mode in self.events if recorded == key]

    def mode_for(self, path):
        modes = self.modes_for(path)
        return modes[-1] if modes else None

    def clear(self):
        self.events.clear()


@pytest.fixture
def narrowing_recorder(monkeypatch):
    recorder = _NarrowingRecorder()
    real_os_chmod = os.chmod
    real_path_chmod = Path.chmod
    real_path_mkdir = Path.mkdir
    real_os_open = os.open

    def os_chmod(path, mode, *args, **kwargs):
        recorder.record(path, mode)
        return real_os_chmod(path, mode, *args, **kwargs)

    def path_chmod(self, mode, **kwargs):
        recorder.record(self, mode)
        return real_path_chmod(self, mode, **kwargs)

    def path_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        # 0o777 is pathlib's default, i.e. "no opinion"; only an explicit mode is a narrowing.
        if mode != 0o777:
            recorder.record(self, mode)
        return real_path_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    def os_open(path, flags, mode=0o777, **kwargs):
        if mode != 0o777 and flags & os.O_CREAT:
            recorder.record(path, mode)
        return real_os_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", os_chmod)
    monkeypatch.setattr(Path, "chmod", path_chmod)
    monkeypatch.setattr(Path, "mkdir", path_mkdir)
    monkeypatch.setattr(os, "open", os_open)
    return recorder


@pytest.fixture
def permissive_umask():
    """Run the test body under umask 0o000, the most permissive case.

    Without this, a mode assertion on a path the code under test just created measures the ambient
    process mask instead of the code: under umask 0o077 a plain `mkdir` already yields 0o700 and a
    plain `open` 0o600. Pinning the mask at 0o000 makes 0o700/0o600 reachable only by an explicit
    narrowing. Meaningless but harmless on win32, where the mask does not govern file modes.

    The mask is process-global, so this must stay a per-test context that restores what it found.
    """
    previous_umask = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(previous_umask)


def _assert_owner_only(recorder, path, expected_mode, what):
    # Control for the whole assertion: a mode check on a path that was never created would be
    # satisfied by any implementation, including one that writes nothing at all.
    assert os.path.exists(path), f"{what} was never created at {path}"

    if os.name == "posix":
        actual = stat.S_IMODE(os.stat(path).st_mode)
        assert actual == expected_mode, (
            f"{what} at {path} is {oct(actual)}; expected {oct(expected_mode)} "
            f"(group/other access to a credential-bearing path)"
        )
        return

    requested = recorder.mode_for(path)
    assert requested is not None, (
        f"no explicit permission narrowing was requested for {what} at {path}; on win32 the real "
        f"mode is not observable, so the request is the only evidence available "
        f"(recorded: {recorder.events})"
    )
    assert requested & 0o077 == 0, (
        f"{what} at {path} was created with mode {oct(requested)}, which grants group/other access"
    )


@contextmanager
def _real_logging_into(log_dir: Path):
    """Run the real `initialize_logging()` against `log_dir`, then tear the handlers down.

    Overrides the autouse `redirect_log_dir` fixture's patch so the directory under test is one this
    test owns (and, where relevant, one the code under test has to create itself).
    """
    vamscli_logging._logger = None
    with patch("vamscli.utils.logging.get_log_dir", return_value=log_dir):
        try:
            yield
        finally:
            if vamscli_logging._logger is not None:
                for handler in list(vamscli_logging._logger.handlers):
                    handler.close()
                    vamscli_logging._logger.removeHandler(handler)
            vamscli_logging._logger = None


class TestLogFilePermissions:
    @pytest.mark.no_mock_logging
    def test_initialize_logging_creates_an_owner_only_dir_and_file(
            self, tmp_path, narrowing_recorder, permissive_umask):
        """FIX-010: the log directory must be 0o700 and vamscli.log 0o600."""
        log_dir = tmp_path / "vamscli" / "logs"
        assert not log_dir.exists(), "the code under test must be the thing that creates it"

        with _real_logging_into(log_dir):
            logger = initialize_logging(verbose=False)
            logger.info("permission probe")
            for handler in logger.handlers:
                handler.flush()

            log_file = log_dir / LOG_FILE_NAME
            # Control: the probe really reached the file, so the mode assertions below are about a
            # live log rather than an empty placeholder.
            assert "permission probe" in log_file.read_text(encoding="utf-8")

            _assert_owner_only(narrowing_recorder, log_dir, 0o700, "the log directory")
            _assert_owner_only(narrowing_recorder, log_file, 0o600, "the log file")

    @pytest.mark.no_mock_logging
    @pytest.mark.parametrize("ambient_umask", [0o000, 0o022, 0o077])
    def test_permissions_do_not_depend_on_the_ambient_umask(
            self, tmp_path, narrowing_recorder, ambient_umask):
        """FIX-010: the mode must be set explicitly, not inherited from the umask.

        Swept across the permissive case (0o000), the usual default (0o022) and a hardened host
        (0o077). Only an explicit narrowing makes the result invariant: a fix that leans on the
        ambient umask satisfies 0o077 and leaves every default-umask developer and CI machine
        exposed, and 0o000 is the case that separates the two.
        """
        log_dir = tmp_path / "vamscli" / "logs"
        previous_umask = os.umask(ambient_umask)
        try:
            with _real_logging_into(log_dir):
                logger = initialize_logging(verbose=False)
                logger.info("umask probe")
                for handler in logger.handlers:
                    handler.flush()

                log_file = log_dir / LOG_FILE_NAME
                assert "umask probe" in log_file.read_text(encoding="utf-8")
                _assert_owner_only(narrowing_recorder, log_dir, 0o700, "the log directory")
                _assert_owner_only(narrowing_recorder, log_file, 0o600, "the log file")
        finally:
            os.umask(previous_umask)

    @pytest.mark.no_mock_logging
    def test_rotation_recreates_the_log_file_owner_only(
            self, tmp_path, narrowing_recorder, permissive_umask):
        """FIX-010: the mode must survive a rollover.

        `RotatingFileHandler.doRollover()` renames the current file aside and opens a **new**
        `vamscli.log`. A chmod applied once during `initialize_logging` silently stops protecting the
        log at the very next rotation — and the CLI keeps up to six of these files.
        """
        log_dir = tmp_path / "vamscli" / "logs"
        with _real_logging_into(log_dir), \
                patch("vamscli.utils.logging.LOG_MAX_BYTES", 256):
            logger = initialize_logging(verbose=False)
            # Control: prove a rollover actually happened before asserting anything about it.
            for index in range(40):
                logger.info(f"rotation probe {index} " + "x" * 64)
            for handler in logger.handlers:
                handler.flush()

            rotated = log_dir / f"{LOG_FILE_NAME}.1"
            assert rotated.exists(), (
                "no rollover occurred, so this test would assert nothing about rotation")

            log_file = log_dir / LOG_FILE_NAME
            _assert_owner_only(narrowing_recorder, rotated, 0o600, "the rotated log file")
            _assert_owner_only(narrowing_recorder, log_file, 0o600, "the post-rollover log file")

    @pytest.mark.no_mock_logging
    def test_a_preexisting_loose_log_dir_and_file_are_tightened(
            self, tmp_path, narrowing_recorder):
        """FIX-010: a create-time-only fix leaves every machine that has already run the CLI exposed.

        The log directory and file already exist on every developer and CI machine that has run
        `vamscli` even once, so the fix has to narrow what it finds, not only what it makes.
        """
        log_dir = tmp_path / "vamscli" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / LOG_FILE_NAME
        log_file.write_text("pre-existing content\n", encoding="utf-8")
        # nosec B103 - the permissive mode IS the subject under test: this reproduces a log directory
        # and file left world-writable by an earlier release, so the assertions below can prove the
        # logger NARROWS a pre-existing path rather than only creating new ones tightly. Bandit cannot
        # tell a fixture that builds the insecure precondition from production code that ships it.
        os.chmod(log_dir, 0o777)  # nosec B103
        os.chmod(log_file, 0o666)  # nosec B103
        narrowing_recorder.clear()  # do not count this test's own setup as a narrowing

        with _real_logging_into(log_dir):
            logger = initialize_logging(verbose=False)
            logger.info("tighten probe")
            for handler in logger.handlers:
                handler.flush()

            # Control: the handler appended to the pre-existing file rather than replacing it, so
            # this really is the loose file being asserted on.
            contents = log_file.read_text(encoding="utf-8")
            assert "pre-existing content" in contents and "tighten probe" in contents

            _assert_owner_only(narrowing_recorder, log_dir, 0o700, "the pre-existing log directory")
            _assert_owner_only(narrowing_recorder, log_file, 0o600, "the pre-existing log file")


class TestProfileSecretPermissions:
    """`auth_profile.json` holds the live Cognito refresh token; `credentials.json` holds the
    password when the user opted to store it. Both are a bigger secret than a redacted log line, so
    a permission fix scoped to `logs/` is incomplete."""

    @pytest.fixture
    def profile_config_dir(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "vamscli"
        # Patch both bindings: ProfileManager reads the name imported into utils.profile, while
        # get_profile_dir() resolves through the constants module's own global.
        monkeypatch.setattr("vamscli.constants.get_config_dir", lambda: config_dir)
        monkeypatch.setattr("vamscli.utils.profile.get_config_dir", lambda: config_dir)
        return config_dir

    def test_auth_profile_and_credentials_are_owner_only(
            self, profile_config_dir, narrowing_recorder, permissive_umask):
        """FIX-010 (profile companion): the stored refresh token must not be world-readable."""
        manager = ProfileManager("fix010-profile")
        manager.save_auth_profile({
            "user_id": "someone@example.com",
            "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
            "refresh_token": "LIVE-REFRESH-TOKEN-VALUE",
        })
        manager.save_credentials({"username": "someone@example.com", "password": "Hunter2!"})

        # Control: the secrets really are on disk in cleartext, so the mode is the only thing
        # standing between another local user and the caller's session.
        assert "LIVE-REFRESH-TOKEN-VALUE" in manager.auth_profile_file.read_text(encoding="utf-8")
        assert "Hunter2!" in manager.credentials_file.read_text(encoding="utf-8")

        _assert_owner_only(narrowing_recorder, manager.auth_profile_file, 0o600, "auth_profile.json")
        _assert_owner_only(narrowing_recorder, manager.credentials_file, 0o600, "credentials.json")

    def test_the_profile_directory_is_owner_only(
            self, profile_config_dir, narrowing_recorder, permissive_umask):
        """FIX-010 (profile companion): the directory containing the token files must be 0o700."""
        manager = ProfileManager("fix010-profile")
        manager.save_auth_profile({"refresh_token": "LIVE-REFRESH-TOKEN-VALUE"})

        assert manager.profile_dir.is_dir()
        _assert_owner_only(narrowing_recorder, manager.profile_dir, 0o700, "the profile directory")


class TestTheEvidencePathIsWired:
    """Control for the win32 half of `_assert_owner_only`.

    On win32 the real mode is not observable, so every assertion above rests on the recorder having
    seen the narrowing request — and a recorder whose patches never took effect would report the
    same nothing as code that never narrowed anything. This performs the narrowings the code
    performs (a directory created with an explicit mode, a chmod on a file), asserts the recorder
    sees them and `_assert_owner_only` accepts them, and asserts the converse: a default `mkdir` or
    `open` is not reported as a narrowing. Passes on both platforms.
    """

    def test_an_explicit_mkdir_mode_and_chmod_are_observed(self, tmp_path, narrowing_recorder):
        hardened_dir = tmp_path / "hardened"
        hardened_dir.mkdir(mode=0o700)
        hardened_file = hardened_dir / "secret.json"
        hardened_file.write_text("{}", encoding="utf-8")
        os.chmod(hardened_file, 0o600)

        assert narrowing_recorder.mode_for(hardened_dir) == 0o700
        assert narrowing_recorder.mode_for(hardened_file) == 0o600
        _assert_owner_only(narrowing_recorder, hardened_dir, 0o700, "a hardened directory")
        _assert_owner_only(narrowing_recorder, hardened_file, 0o600, "a hardened file")

    def test_a_path_chmod_is_observed(self, tmp_path, narrowing_recorder):
        hardened_file = tmp_path / "secret.json"
        hardened_file.write_text("{}", encoding="utf-8")
        hardened_file.chmod(0o600)

        assert narrowing_recorder.mode_for(hardened_file) == 0o600
        _assert_owner_only(narrowing_recorder, hardened_file, 0o600, "a hardened file")

    def test_a_default_mkdir_and_open_are_not_counted_as_narrowing(
            self, tmp_path, narrowing_recorder):
        # The other half of the control: the recorder must not report a narrowing that never
        # happened, or the win32 assertions above would be satisfied by nothing at all.
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        plain_file = plain_dir / "plain.json"
        plain_file.write_text("{}", encoding="utf-8")

        assert narrowing_recorder.mode_for(plain_dir) is None
        assert narrowing_recorder.mode_for(plain_file) is None


class TestPermissionHardeningMustNotBreakLogging:
    """Controls. These bound the hardening rather than prove the finding."""

    @pytest.mark.no_mock_logging
    def test_logging_still_works_when_chmod_is_refused(self, tmp_path, monkeypatch):
        """A chmod on a path a fixture redirects can raise on Windows or on a mounted volume.

        Logging must never be able to fail a command, so the hardening has to be best-effort:
        `ensure_log_dir()` and the file handler both narrow a path, and every one of those calls has
        to tolerate a refusal.
        """
        def deny(*_args, **_kwargs):
            raise PermissionError("chmod refused")

        monkeypatch.setattr(os, "chmod", deny)
        monkeypatch.setattr(Path, "chmod", deny)

        log_dir = tmp_path / "vamscli" / "logs"
        with _real_logging_into(log_dir):
            logger = initialize_logging(verbose=False)
            assert logger is not None
            logger.info("chmod-denied probe")
            for handler in logger.handlers:
                handler.flush()
            assert "chmod-denied probe" in (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")

    def test_the_redirected_log_dir_fixture_still_isolates_the_suite(self, redirect_log_dir):
        # tests/test_logging_dir_isolation.py owns this guarantee; restated here because a chmod
        # added to ensure_log_dir() runs against whatever path that fixture points at.
        assert vamscli_logging.get_log_dir() == redirect_log_dir
        assert redirect_log_dir.is_dir()


class TestRedactionBaselineAlreadyPasses:
    """The redaction half of S6-TOOLS-004 is ALREADY FIXED AT HEAD.

    These assertions pass on unfixed HEAD. They are recorded here so that a green
    "the plaintext key never reaches the log" result is not mistaken for evidence about the file
    permissions, which is the part FIX-010 still has to deliver.
    """

    def test_api_key_create_payload_is_redacted_for_the_log(self):
        rendered = redact_to_text(API_KEY_CREATE_RESPONSE)
        assert "vams_LIVEONETIMESECRETVALUE0123456789" not in rendered
        assert vamscli_logging.REDACTED in rendered
        # Negative control: over-redaction would destroy the diagnostic value the log exists for.
        for surviving in ("key-abc123", "ci-runner", "someone@example.com",
                          "2027-01-01T00:00:00Z"):
            assert surviving in rendered

    def test_redaction_leaves_the_callers_payload_untouched(self):
        # The value is shown exactly once and can never be retrieved again, so redaction must
        # produce a copy — otherwise `api-key create` would print ***REDACTED*** and be useless.
        redact_to_text(API_KEY_CREATE_RESPONSE)
        assert API_KEY_CREATE_RESPONSE["apiKey"] == "vams_LIVEONETIMESECRETVALUE0123456789"
