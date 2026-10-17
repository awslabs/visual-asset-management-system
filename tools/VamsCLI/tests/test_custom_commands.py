"""Commands in `vamscli.commands.custom.CUSTOM_COMMANDS` are registered on the root group.

`commands/custom/__init__.py` is a fork extension point that ships with an empty list, so the shipped
file is never edited here: the command is appended to the list in a SUBPROCESS before `vamscli.main`
is imported, which is the order a fork's own module-level definition takes effect in. The subprocess
also keeps the appended command out of every other test's `cli` group.

A custom command never replaces a core one: `main.py` refuses a custom name that is already
registered, so importing the CLI fails with an error naming the command.
"""

import os
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    '''
    import click
    import vamscli.commands.custom as custom

    @click.command(name=PROBE_NAME)
    def probe():
        """Probe command registered through CUSTOM_COMMANDS."""

    if ADD_PROBE:
        custom.CUSTOM_COMMANDS.append(probe)

    from click.testing import CliRunner
    from vamscli.main import cli

    runner = CliRunner()
    print(runner.invoke(cli, ["--help"]).output)
    if ADD_PROBE:
        print("registered:", cli.commands.get(PROBE_NAME) is probe)
    '''
)


def _run(tmp_path, add_probe: bool, name: str = "custom-probe") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # A throwaway config home, so nothing the CLI writes at import lands in the developer's.
    for var in ("HOME", "USERPROFILE", "APPDATA"):
        env[var] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", f"ADD_PROBE = {add_probe}\nPROBE_NAME = {name!r}\n" + PROBE],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace", env=env,
    )


def _output(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_a_custom_command_is_registered_and_listed(tmp_path):
    output = _output(_run(tmp_path, add_probe=True))
    assert "custom-probe" in output
    assert "Probe command registered through CUSTOM_COMMANDS." in output
    assert "registered: True" in output


def test_without_the_custom_command_the_help_does_not_list_it(tmp_path):
    # Control: the listing above comes from the appended command, not from the probe's own text.
    output = _output(_run(tmp_path, add_probe=False))
    assert "Commands:" in output
    assert "custom-probe" not in output


def test_a_custom_command_named_like_a_core_group_is_refused(tmp_path):
    proc = _run(tmp_path, add_probe=True, name="database")
    assert proc.returncode != 0
    assert "custom command 'database'" in proc.stderr
    assert "core commands take precedence" in proc.stderr


def test_a_custom_command_named_like_the_core_version_command_is_refused(tmp_path):
    # `version` is defined with @cli.command() rather than add_command(); the custom commands are
    # registered after it, so it is covered too.
    proc = _run(tmp_path, add_probe=True, name="version")
    assert proc.returncode != 0
    assert "custom command 'version'" in proc.stderr


def test_the_core_group_name_is_not_refused_without_a_custom_command(tmp_path):
    # Control for the two refusals: the same probe script, not appended, imports cleanly and the
    # core `database` group is listed, so the failures above come from the clash check alone.
    output = _output(_run(tmp_path, add_probe=False, name="database"))
    assert "database" in output
