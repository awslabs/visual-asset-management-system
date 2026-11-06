"""Commands in `vamscli.commands.addon.ADDON_COMMANDS` are registered on the root group.

`main.py` registers the add-on commands after every core command and before the custom commands
(`commands/custom/CUSTOM_COMMANDS`), refusing a name that is already registered. So an add-on command
never replaces a core one, and a custom command never replaces an add-on one. The probes are appended
in a SUBPROCESS before `vamscli.main` is imported, which keeps them out of every other test's `cli`
group.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from vamscli.commands.addon import ADDON_COMMANDS
from vamscli.main import cli

PROBE = textwrap.dedent(
    '''
    import click
    import vamscli.commands.addon as addon
    import vamscli.commands.custom as custom

    def _probe(name, text):
        @click.command(name=name, help=text)
        def probe():
            pass
        return probe

    for name in ADDON_NAMES:
        addon.ADDON_COMMANDS.append(_probe(name, "Probe command registered through ADDON_COMMANDS."))
    for name in CUSTOM_NAMES:
        custom.CUSTOM_COMMANDS.append(_probe(name, "Probe command registered through CUSTOM_COMMANDS."))

    from click.testing import CliRunner
    from vamscli.main import cli

    print(CliRunner().invoke(cli, ["--help"]).output)
    for name in ADDON_NAMES:
        print("registered:", name, cli.commands.get(name) in addon.ADDON_COMMANDS)
    '''
)


def _run(tmp_path, addon_names=(), custom_names=()) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # A throwaway config home, so nothing the CLI writes at import lands in the developer's.
    for var in ("HOME", "USERPROFILE", "APPDATA"):
        env[var] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    script = f"ADDON_NAMES = {list(addon_names)!r}\nCUSTOM_NAMES = {list(custom_names)!r}\n" + PROBE
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace", env=env,
    )


def _output(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_every_shipped_add_on_command_is_the_registered_command():
    for command in ADDON_COMMANDS:
        assert cli.commands[command.name] is command, command.name


def test_an_add_on_command_is_registered_and_listed(tmp_path):
    output = _output(_run(tmp_path, addon_names=["addon-probe"]))
    assert "addon-probe" in output
    assert "Probe command registered through ADDON_COMMANDS." in output
    assert "registered: addon-probe True" in output


def test_without_the_add_on_command_the_help_does_not_list_it(tmp_path):
    # Control: the listing above comes from the appended command, not from the probe's own text.
    output = _output(_run(tmp_path))
    assert "Commands:" in output
    assert "addon-probe" not in output


def test_an_add_on_command_named_like_a_core_group_is_refused(tmp_path):
    proc = _run(tmp_path, addon_names=["database"])
    assert proc.returncode != 0
    assert "add-on command 'database'" in proc.stderr
    assert "core commands take precedence" in proc.stderr


def test_an_add_on_command_named_like_the_core_version_command_is_refused(tmp_path):
    # `version` is defined with @cli.command() rather than add_command(); the add-on commands are
    # registered after it, so it is covered too.
    proc = _run(tmp_path, addon_names=["version"])
    assert proc.returncode != 0
    assert "add-on command 'version'" in proc.stderr


def test_two_add_on_commands_of_one_name_are_refused(tmp_path):
    proc = _run(tmp_path, addon_names=["addon-probe", "addon-probe"])
    assert proc.returncode != 0
    assert "add-on command 'addon-probe'" in proc.stderr


def test_a_custom_command_named_like_an_add_on_command_is_refused(tmp_path):
    # The add-on commands are registered first, so the custom one is the one refused.
    proc = _run(tmp_path, addon_names=["addon-probe"], custom_names=["addon-probe"])
    assert proc.returncode != 0
    assert "custom command 'addon-probe'" in proc.stderr


def test_a_custom_command_named_like_a_shipped_add_on_command_is_refused(tmp_path):
    if not ADDON_COMMANDS:
        pytest.skip("no add-on ships a command")
    name = ADDON_COMMANDS[0].name
    proc = _run(tmp_path, custom_names=[name])
    assert proc.returncode != 0
    assert f"custom command '{name}'" in proc.stderr


def test_distinct_add_on_and_custom_names_are_both_registered(tmp_path):
    # Control for the refusals: the same script with distinct names imports cleanly and lists both.
    output = _output(_run(tmp_path, addon_names=["addon-probe"], custom_names=["custom-probe"]))
    assert "addon-probe" in output
    assert "custom-probe" in output
