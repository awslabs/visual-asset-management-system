"""Human-readable output survives a legacy console code page, and a reported failure exits non-zero.

Two defects found by running the installed CLI on Windows rather than by any test:

1. The human-output layer uses Unicode status glyphs throughout (several hundred occurrences across
   33 modules). Python chooses stdout's encoding from the locale whenever stdout is not a console, so
   on a default Windows install that is cp1252 — which holds none of those glyphs. Redirecting or
   piping any command that emitted one raised UnicodeEncodeError, and `vamscli profile list > file`
   produced a single line naming a charmap error instead of the profiles. Scripting, logging and CI
   all redirect, so the breakage was confined to exactly the non-interactive uses.

2. `profile list` caught the resulting exception, reported it, and then returned — leaving the exit
   code at 0. The command failed and the shell saw success.

These run the CLI as a SUBPROCESS. That is not incidental: the encoding of `sys.stdout` is the thing
under test, and `CliRunner` replaces `sys.stdout` with an in-memory buffer that has no code page at
all, so the defect is invisible to it by construction. It is also why the bug survived — the existing
subprocess suite (`test_json_output_purity.py`) sets `PYTHONIOENCODING=utf-8` in its fixture to keep
the glyphs encodable, which fixed the symptom for the tests and left it in production. So the fixture
here deliberately does the opposite and forces a legacy code page.
"""

import io
import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from vamscli.commands.profile import profile
from vamscli.utils.profile import ProfileManager
from vamscli.main import _use_utf8_output


@pytest.fixture
def legacy_codepage_env(tmp_path):
    """Env with a configured throwaway profile and stdout forced to a NON-UTF-8 code page.

    `PYTHONIOENCODING=cp1252` reproduces a default en-US Windows console without depending on the
    machine actually having one, so the case runs identically on Linux CI. The profile only has to
    exist so the setup gate does not fire before the listing is reached; no request is made.
    """
    config_home = tmp_path / "confighome"
    # Mirrors get_config_dir(): POSIX ~/.config/vamscli, Windows %APPDATA%/vamscli.
    for base in (config_home / ".config" / "vamscli", config_home / "vamscli"):
        profile_dir = base / "profiles" / "default"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.json").write_text(
            json.dumps({"api_gateway_url": "https://example.invalid/api"}), encoding="utf-8")
        (base / "active_profile.json").write_text(
            json.dumps({"active_profile": "default"}), encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(config_home)
    env["USERPROFILE"] = str(config_home)
    env["APPDATA"] = str(config_home)
    env["PYTHONIOENCODING"] = "cp1252"
    # PYTHONUTF8 would override the code page and hide the defect, so make sure it is not inherited.
    env.pop("PYTHONUTF8", None)
    return env


def _run(args, env):
    proc = subprocess.run([sys.executable, "-m", "vamscli.main", *args],
                          capture_output=True, timeout=120, env=env)
    # Decode ourselves rather than with text=True: the point is what BYTES the child emitted, and a
    # decode error here should surface as a readable assertion rather than an exception in the runner.
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode, out + err


class TestHumanOutputSurvivesALegacyCodePage:
    def test_profile_list_is_not_defeated_by_cp1252(self, legacy_codepage_env):
        rc, combined = _run(["profile", "list"], legacy_codepage_env)
        assert "charmap" not in combined, (
            "the codec, not the command, decided the outcome:\n" + combined[:400])
        assert "codec can't encode" not in combined, combined[:400]
        assert "Available profiles" in combined, combined[:400]
        assert rc == 0, f"exit {rc}\n{combined[:400]}"

    def test_the_case_really_exercises_a_glyph(self, legacy_codepage_env):
        """Positive control.

        If the listing ever stops emitting a non-cp1252 character, the test above keeps passing while
        testing nothing — it would be asserting that glyph-free output encodes cleanly, which is
        trivially true. So prove the character is actually on the path being exercised.
        """
        rc, combined = _run(["profile", "list"], legacy_codepage_env)
        assert rc == 0, combined[:300]
        offending = [ch for ch in combined if ord(ch) > 0xFF]
        assert offending, (
            "no character outside the cp1252 range reached stdout, so this suite is no longer "
            "covering the defect it was written for:\n" + combined[:400])

    def test_json_output_is_unaffected(self, legacy_codepage_env):
        """Negative control: the JSON path never carried glyphs, so it must behave the same as before.

        This is also what keeps the external connectors in scope of the suite — they consume
        `--json-output` through a pipe, which is the same channel this case exercises.
        """
        rc, combined = _run(["profile", "list", "--json-output"], legacy_codepage_env)
        assert rc == 0, combined[:300]
        payload = json.loads(combined.strip())
        assert "profiles" in payload, combined[:300]


class TestTheEntryPointReconfiguresTheStreams:
    def test_stdout_and_stderr_become_utf8(self, monkeypatch):
        class FakeStream:
            def __init__(self):
                self.encoding = "cp1252"
                self.errors = "strict"

            def reconfigure(self, encoding=None, errors=None):
                if encoding:
                    self.encoding = encoding
                if errors:
                    self.errors = errors

        out, err = FakeStream(), FakeStream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        _use_utf8_output()
        assert (out.encoding, out.errors) == ("utf-8", "replace")
        assert (err.encoding, err.errors) == ("utf-8", "replace")

    def test_a_stream_without_reconfigure_is_left_alone(self, monkeypatch):
        """Click's test runner and any wrapper substitute a plain buffer, which has no `reconfigure`.

        Reconfiguring unconditionally would raise AttributeError at entry and make every command fail
        under those conditions, so the absence of the method must be tolerated silently.
        """
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        monkeypatch.setattr(sys, "stderr", buf)
        _use_utf8_output()          # must not raise
        assert not buf.getvalue(), "reconfiguring must not write anything"

    def test_a_stream_that_refuses_reconfiguration_is_tolerated(self, monkeypatch):
        class Detached:
            encoding = "cp1252"

            def reconfigure(self, encoding=None, errors=None):
                raise ValueError("underlying buffer has been detached")

        monkeypatch.setattr(sys, "stdout", Detached())
        monkeypatch.setattr(sys, "stderr", Detached())
        _use_utf8_output()          # must not raise


class TestAReportedFailureExitsNonZero:
    def test_profile_list_does_not_exit_zero_when_it_fails(self, monkeypatch):
        """The handler used to report the error and `return`, leaving the code at 0.

        Driven through CliRunner because the exit code is all that is under test here and it needs a
        forced failure, which a subprocess cannot be made to produce reliably.
        """
        def boom():
            raise RuntimeError("profile store unreadable")

        monkeypatch.setattr(ProfileManager, "get_all_profiles_info",
                            staticmethod(boom))
        result = CliRunner().invoke(profile, ["list"])
        assert result.exit_code != 0, (
            "a failed listing reported success; a script cannot detect this.\n" + result.output)
        assert "profile store unreadable" in result.output, result.output

    def test_a_working_listing_still_exits_zero(self, monkeypatch):
        # Control: the change must affect the failure path only.
        monkeypatch.setattr(ProfileManager, "get_all_profiles_info",
                            staticmethod(lambda: []))
        result = CliRunner().invoke(profile, ["list"])
        assert result.exit_code == 0, result.output
        assert "No profiles found" in result.output, result.output
