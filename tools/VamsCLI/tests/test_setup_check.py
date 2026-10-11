"""The setup gate lets a user recover from a missing profile, and every hint names a real command.

`check_setup_required` runs on every invocation and raises SetupRequired when the active profile
has no configuration. Two things have to hold for the message it prints to be useful:

1. The commands the message sends the user to must themselves pass the gate. `vamscli setup` and
   `vamscli profile list` / `profile switch` are how a user finds or creates a configured profile,
   so gating them leaves no way out.
2. The hint must be a command Click accepts. `--profile` is a root-group option, so it goes
   before the subcommand: `vamscli --profile <name> setup <url>`. Written after the subcommand,
   Click exits 2 with "No such option" and the user cannot tell the hint from a product error.

The gate cases run the CLI as a SUBPROCESS: `check_setup_required` skips itself when pytest is
loaded, so only a child process exercises the live gate. Each subprocess gets an EMPTY config home,
which is the state the hint is printed in.

The source scan is the durable half of (2): it reads every `vamscli --<opt> ...` suggestion in the
CLI source, the MCP server and the connectors and fails on an option the root group does not have.
"""

import glob
import json
import os
import re
import subprocess
import sys

import pytest

from vamscli.main import cli, invoked_command

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_TOOLS = os.path.join(_REPO_ROOT, "tools")


# ---------------------------------------------------------------------------
# Reading the invoked command off argv
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv, expected", [
    (["vamscli", "profile", "list"], "profile"),
    (["vamscli", "--profile", "staging", "profile", "list"], "profile"),
    (["vamscli", "--profile=staging", "profile", "switch", "prod"], "profile"),
    (["vamscli", "--verbose", "--profile", "staging", "setup", "https://x"], "setup"),
    (["vamscli", "auth", "status"], "auth"),
    (["vamscli", "--profile", "profile", "auth", "status"], "auth"),
    (["vamscli", "--version"], None),
    (["vamscli"], None),
])
def test_invoked_command_skips_root_options_and_their_values(argv, expected):
    assert invoked_command(argv) == expected


def test_the_value_of_profile_is_not_read_as_the_command():
    """A profile named like a command must not be mistaken for one."""
    assert invoked_command(["vamscli", "--profile", "setup", "auth", "status"]) == "auth"


# ---------------------------------------------------------------------------
# The live gate, in a subprocess with no configured profile
# ---------------------------------------------------------------------------


@pytest.fixture
def unset_up_env(tmp_path):
    """Env pointing the CLI at a config home that holds an active-profile marker but no profile.

    Both HOME and APPDATA are set so the same fixture works on Linux/macOS (`~/.config/vamscli`)
    and Windows (`%APPDATA%/vamscli`).
    """
    config_home = tmp_path / "confighome"
    for base in (config_home / ".config" / "vamscli", config_home / "vamscli"):
        base.mkdir(parents=True, exist_ok=True)
        (base / "active_profile.json").write_text(
            json.dumps({"active_profile": "ghost"}), encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(config_home)
    env["USERPROFILE"] = str(config_home)
    env["APPDATA"] = str(config_home)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args, env):
    proc = subprocess.run([sys.executable, "-m", "vamscli.main", *args],
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace", env=env)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_the_gate_fires_for_an_ordinary_command_with_no_profile(unset_up_env):
    """CONTROL for the fixture: without it the exemptions below would pass for the wrong reason."""
    rc, combined = _run(["auth", "status"], unset_up_env)
    assert rc != 0
    assert "Setup required for profile 'ghost'" in combined, combined[:400]


def test_the_gate_hint_puts_profile_before_the_command(unset_up_env):
    rc, combined = _run(["auth", "status"], unset_up_env)
    assert rc != 0
    assert "vamscli --profile ghost setup <api-gateway-url>" in combined, combined[:400]
    assert "setup <api-gateway-url> --profile" not in combined, combined[:400]


def test_profile_list_passes_the_gate_with_no_configured_profile(unset_up_env):
    rc, combined = _run(["profile", "list"], unset_up_env)
    assert "Setup required" not in combined, combined[:400]
    assert rc == 0, combined[:400]


def test_profile_switch_passes_the_gate_with_no_configured_profile(unset_up_env):
    """`profile switch` may still refuse an unknown target, but it must not be gated first."""
    rc, combined = _run(["profile", "switch", "elsewhere"], unset_up_env)
    assert "Setup required" not in combined, combined[:400]


def test_a_named_profile_still_reaches_profile_list(unset_up_env):
    rc, combined = _run(["--profile", "other", "profile", "list"], unset_up_env)
    assert "Setup required" not in combined, combined[:400]
    assert rc == 0, combined[:400]


def test_a_profile_named_setup_does_not_exempt_the_command_that_follows(unset_up_env):
    """The exemption is decided by the invoked COMMAND, not by the token `setup` appearing anywhere
    in argv: `--profile setup auth status` is an `auth status` call against a profile that happens to
    be named `setup`, and it is gated like any other."""
    rc, combined = _run(["--profile", "setup", "auth", "status"], unset_up_env)
    assert rc != 0
    assert "Setup required for profile 'setup'" in combined, combined[:400]


def test_setup_itself_passes_the_gate(unset_up_env):
    """CONTROL for the test above: `setup` is exempt when it is the command, so the gating of a
    profile named `setup` comes from reading the command, not from `setup` having lost its exemption.
    Click then rejects the missing URL argument, which is the behaviour past the gate."""
    rc, combined = _run(["--profile", "ghost", "setup"], unset_up_env)
    assert "Setup required" not in combined, combined[:400]
    assert "Missing argument" in combined, combined[:400]


# ---------------------------------------------------------------------------
# Source scan: every suggested `vamscli --<opt>` is a root-group option
# ---------------------------------------------------------------------------

_SCANNED_TREES = (
    os.path.join(_TOOLS, "VamsCLI", "vamscli"),
    os.path.join(_TOOLS, "VamsMCP", "vams_mcp"),
    os.path.join(_TOOLS, "ExternalIntegrations", "arcgispro-connector-for-vams"),
    os.path.join(_TOOLS, "ExternalIntegrations", "isaacsim_vams_integration", "vams"),
)
_SCANNED_EXTENSIONS = (".py", ".cs")

# `vamscli --something` in any string: the option that immediately follows the executable name is
# in the root-group position, whatever the surrounding quoting.
_ROOT_POSITION_OPTION = re.compile(r"vamscli\s+(--[a-z][a-z0-9-]*)")


def _root_options():
    opts = {"--help"}
    for param in cli.params:
        opts.update(o for o in list(param.opts) + list(param.secondary_opts) if o.startswith("--"))
    return opts


def _source_files():
    files = []
    for tree in _SCANNED_TREES:
        for ext in _SCANNED_EXTENSIONS:
            files.extend(glob.glob(os.path.join(tree, "**", "*" + ext), recursive=True))
    return sorted(path for path in files if os.sep + "tests" + os.sep not in path
                  and os.sep + "bin" + os.sep not in path and os.sep + "obj" + os.sep not in path)


def _root_position_options_in(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        text = handle.read()
    return [(lineno, match.group(1))
            for lineno, line in enumerate(text.splitlines(), 1)
            for match in _ROOT_POSITION_OPTION.finditer(line)]


def test_every_suggested_root_option_exists_on_the_root_group():
    root_options = _root_options()
    assert "--profile" in root_options and "--verbose" in root_options

    files = _source_files()
    assert len(files) >= 20, f"scan found only {len(files)} source files; the walk is broken"

    seen = 0
    problems = []
    for path in files:
        for lineno, option in _root_position_options_in(path):
            seen += 1
            if option not in root_options:
                problems.append(f"{os.path.relpath(path, _TOOLS)}:{lineno}: suggests `vamscli {option}`, "
                                f"but the root group has no such option")
    # The hints this guards are the ones that put --profile first; a scan that sees none of them
    # has lost its subject.
    assert seen >= 7, f"only {seen} `vamscli --<opt>` suggestions were found across the scanned trees"
    assert not problems, "\n".join(problems)


def test_the_scan_fires_on_an_invented_root_option(tmp_path):
    """CONTROL: the matcher reads the option after `vamscli` and judges it against the root group."""
    probe = tmp_path / "probe.py"
    probe.write_text('HINT = "Run \'vamscli --token-override <token> auth status\' first."\n'
                     'OK = "Run \'vamscli --profile staging setup <url>\' first."\n', encoding="utf-8")
    found = _root_position_options_in(str(probe))
    assert found == [(1, "--token-override"), (2, "--profile")]
    assert "--token-override" not in _root_options()
    assert "--profile" in _root_options()
