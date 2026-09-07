"""Every --json-output invocation emits ONLY parseable JSON, including on failures.

Click reports a bad or missing option by printing usage text and exiting before any command body
runs, so the flag was previously ignored on that path: a caller parsing stdout got
"Usage: vamscli ..." and a JSONDecodeError. main() now converts UsageError/ClickException to JSON
when the flag is present, which makes the contract total rather than best-effort.

These cases run the CLI as a SUBPROCESS, because the behavior under test lives in `main()` — the
`standalone_mode=False` call plus its exception handling — which `CliRunner` bypasses. That has one
consequence worth stating: `check_setup_required` skips itself when `'pytest' in sys.modules`, and a
subprocess has no pytest loaded, so the setup gate is live there. On a machine with a real `vamscli`
profile the gate passes and Click's parsing is reached; on a clean machine (CI, or any fresh checkout)
it fires first and every case sees a SetupRequired payload instead of the usage error it meant to
exercise. So each subprocess gets its own configured profile in a temp config home via HOME/APPDATA,
which both fixes CI and makes the suite independent of whatever profiles the developer happens to have.
"""

import json
import os
import subprocess
import sys

import pytest

# Argument errors reachable without any deployment or credentials: each omits a required option, so
# Click rejects it during parsing.
USAGE_ERROR_CASES = [
    ["pipeline", "get"],
    ["pipeline", "template", "list"],
    ["pipeline", "tag-schema", "get", "-d", "GLOBAL", "-p", "some-pipeline"],
    ["workflow", "get"],
    ["workflow", "execute"],
    ["workflow", "trigger", "list"],
    ["execution", "details-metadata"],
    ["assets", "get"],
    ["database", "get"],
]


@pytest.fixture
def cli_env(tmp_path):
    """Env pointing the CLI at a throwaway config home holding one configured profile.

    The profile only has to exist for the setup gate to pass; no request is ever made, because every
    case here fails during argument parsing. Both HOME and APPDATA are set so the same fixture works
    on Linux/macOS (`~/.config/vamscli`) and Windows (`%APPDATA%/vamscli`).
    """
    config_home = tmp_path / "confighome"
    # Mirrors get_config_dir(): POSIX uses ~/.config/vamscli, Windows uses %APPDATA%/vamscli.
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
    # Keep the CLI's own Unicode status glyphs encodable regardless of the runner's console codepage.
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args, env):
    proc = subprocess.run([sys.executable, "-m", "vamscli.main", *args],
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace", env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


@pytest.mark.parametrize("args", USAGE_ERROR_CASES, ids=lambda a: " ".join(a))
def test_a_usage_error_is_json_when_json_output_is_requested(args, cli_env):
    rc, combined = _run([*args, "--json-output"], cli_env)
    assert rc != 0, "a missing required option must not exit 0"
    payload = json.loads(combined.strip())  # raises if anything non-JSON was emitted
    assert payload["error_type"] == "UsageError", combined[:300]
    assert payload["error"], "the message must name what was wrong"


@pytest.mark.parametrize("args", USAGE_ERROR_CASES[:3], ids=lambda a: " ".join(a))
def test_without_the_flag_the_human_readable_usage_text_is_kept(args, cli_env):
    # The negative control: the JSON path must be conditional on the flag, not replace usage text.
    rc, combined = _run(args, cli_env)
    assert rc != 0
    assert combined.lstrip().startswith("Usage:"), combined[:300]


def test_a_successful_command_still_exits_zero(cli_env):
    # standalone_mode=False changes how Click returns, so pin that a normal run is unaffected.
    rc, combined = _run(["--version"], cli_env)
    assert rc == 0, combined[:200]


def test_the_fixture_really_satisfies_the_setup_gate(cli_env):
    """Control for the fixture itself.

    Without a configured profile the subprocess exits on SetupRequired before Click parses anything,
    and every assertion above would be testing the wrong failure. This proves the temp profile is
    actually being found, so a usage error is what the other cases really reach.
    """
    rc, combined = _run(["pipeline", "get", "--json-output"], cli_env)
    assert "Setup Required" not in combined, combined[:300]
    assert rc != 0
    assert json.loads(combined.strip())["error_type"] == "UsageError"


class TestCancellationIsAlsoJson:
    """Ctrl-C must produce a cancellation payload, not an empty stdout.

    S6-TOOLS-020. `global_exceptions.py` carried a `KeyboardInterrupt` branch that emitted
    `{"error": "Operation cancelled by user"}` under `--json-output` — but it sat inside
    `except Exception as e:` and `KeyboardInterrupt` derives from `BaseException`, so `e` could never
    be bound to one and the branch was dead. What actually happened: Click converts the interrupt into
    `Abort` and, under `standalone_mode=False`, re-raises it; `main()` swallowed it with a bare
    `sys.exit(1)`. A wrapper that SIGINTs on timeout therefore got nothing on stdout, and its
    `json.loads(stdout)` failed on empty input instead of reading a structured cancellation. The dead
    branch also read as working code to anyone maintaining the handler.

    Driven at the unit level rather than by SIGINT-ing a subprocess. Sending a real SIGINT portably is
    the problem: on Windows a console interrupt needs `CREATE_NEW_PROCESS_GROUP` plus
    `CTRL_BREAK_EVENT` and is delivered to the whole group, which makes the test flaky on the
    development platform for this repository. What has to be pinned is `main()`'s handling of the
    `Abort` Click hands it, and that is exactly what this drives.
    """

    @staticmethod
    def _run_main_with_abort(monkeypatch, argv):
        import click

        import vamscli.main as main_module

        def raise_abort(*args, **kwargs):
            raise click.exceptions.Abort()

        monkeypatch.setattr(main_module, "cli", raise_abort)
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exit_info:
            main_module.main()
        return exit_info.value.code

    def test_abort_emits_a_json_payload_when_the_flag_is_present(self, monkeypatch, capsys):
        code = self._run_main_with_abort(
            monkeypatch, ["vamscli", "execution", "logs", "e1", "--json-output"])
        assert code == 1
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())  # raises if anything non-JSON was emitted
        assert payload["error_type"] == "Aborted"
        assert payload["error"] == "Operation cancelled by user"

    def test_without_the_flag_a_human_readable_line_goes_to_stderr(self, monkeypatch, capsys):
        """Negative control: the JSON path is conditional on the flag, and stdout stays clean so a
        pipeline consuming stdout is unaffected."""
        code = self._run_main_with_abort(monkeypatch, ["vamscli", "execution", "logs", "e1"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "cancelled" in captured.err.lower()

    def test_the_dead_interrupt_branch_is_gone(self):
        """The reason it was dead, asserted rather than described.

        A future edit that reinstates an `isinstance(e, KeyboardInterrupt)` test inside that
        `except Exception` handler is unreachable code again, and looks like a working guard.
        """
        import inspect

        from vamscli.utils import global_exceptions

        assert not issubclass(KeyboardInterrupt, Exception), (
            "KeyboardInterrupt is now an Exception subclass, so the reasoning above no longer holds")
        source = inspect.getsource(global_exceptions)
        assert "isinstance(e, KeyboardInterrupt)" not in source
