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
