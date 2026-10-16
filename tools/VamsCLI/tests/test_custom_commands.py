"""Commands in `vamscli.commands.custom.CUSTOM_COMMANDS` are registered on the root group.

`commands/custom/__init__.py` is a fork extension point that ships with an empty list, so the shipped
file is never edited here: the command is appended to the list in a SUBPROCESS before `vamscli.main`
is imported, which is the order a fork's own module-level definition takes effect in. The subprocess
also keeps the appended command out of every other test's `cli` group.
"""

import os
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    '''
    import click
    import vamscli.commands.custom as custom

    @click.command(name="custom-probe")
    def probe():
        """Probe command registered through CUSTOM_COMMANDS."""

    if ADD_PROBE:
        custom.CUSTOM_COMMANDS.append(probe)

    from click.testing import CliRunner
    from vamscli.main import cli

    runner = CliRunner()
    print(runner.invoke(cli, ["--help"]).output)
    if ADD_PROBE:
        print("registered:", cli.commands.get("custom-probe") is probe)
    '''
)


def _run(tmp_path, add_probe: bool) -> str:
    env = dict(os.environ)
    # A throwaway config home, so nothing the CLI writes at import lands in the developer's.
    for name in ("HOME", "USERPROFILE", "APPDATA"):
        env[name] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", f"ADD_PROBE = {add_probe}\n" + PROBE],
        capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace", env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_a_custom_command_is_registered_and_listed(tmp_path):
    output = _run(tmp_path, add_probe=True)
    assert "custom-probe" in output
    assert "Probe command registered through CUSTOM_COMMANDS." in output
    assert "registered: True" in output


def test_without_the_custom_command_the_help_does_not_list_it(tmp_path):
    # Control: the listing above comes from the appended command, not from the probe's own text.
    output = _run(tmp_path, add_probe=False)
    assert "Commands:" in output
    assert "custom-probe" not in output
