# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""apply_template.py reports a step that failed: it prints `Status: Incomplete` naming the step and
exits non-zero, so a script that applies a template can tell it left a role without its constraints.

Run with: python -m pytest tools/permissionsSetup/tests
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE = TOOL_DIR.parents[1] / "documentation" / "permissionsTemplates" / "database-admin.json"
_spec = importlib.util.spec_from_file_location("apply_template", TOOL_DIR / "apply_template.py")
apply_template = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_template)


class _Vamscli:
    """A `subprocess.run` stand-in that answers each vamscli command by its first two words."""

    def __init__(self, failing=(), timing_out=()):
        self.failing, self.timing_out = set(failing), set(timing_out)
        self.commands = []

    def run(self, cmd, **kwargs):
        words = " ".join(cmd[1:3])
        self.commands.append(words)
        if words in self.timing_out:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        if words in self.failing:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"{words} refused")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"constraintsCreated": 1}', stderr="")


def _apply(monkeypatch, vamscli, **kwargs):
    monkeypatch.setattr(apply_template.subprocess, "run", vamscli.run)
    apply_template.apply_template(
        template_path=str(TEMPLATE), var_values={"ROLE_NAME": "r1", "DATABASE_ID": "db1"},
        profile=None, dry_run=False, delete=kwargs.get("delete", False),
        assign_user=kwargs.get("assign_user"), role_description=None, mfa_required=False)


def test_every_step_succeeding_is_complete_and_exits_0(monkeypatch, capsys):
    _apply(monkeypatch, _Vamscli(), assign_user="u1")
    assert "Status: Complete" in capsys.readouterr().out


@pytest.mark.parametrize("vamscli, failed", [
    (_Vamscli(failing={"role constraint"}), "constraint import"),
    (_Vamscli(timing_out={"role constraint"}), "constraint import"),
    (_Vamscli(failing={"role user"}), "user assignment"),
    (_Vamscli(failing={"role constraint", "role user"}), "constraint import, user assignment"),
], ids=["import-refused", "import-timed-out", "assignment-refused", "both"])
def test_a_failed_step_is_incomplete_and_exits_1(monkeypatch, capsys, vamscli, failed):
    with pytest.raises(SystemExit) as excinfo:
        _apply(monkeypatch, vamscli, assign_user="u1")
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert f"Status: Incomplete (failed: {failed})" in out
    assert "Status: Complete" not in out
    # Each step was attempted: a failed import does not skip the user assignment.
    assert vamscli.commands == ["role create", "role constraint", "role user"]


def test_a_timeout_is_reported_not_raised(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _apply(monkeypatch, _Vamscli(timing_out={"role constraint"}))
    assert "did not finish within 60 seconds" in capsys.readouterr().out


def test_a_failed_deletion_is_incomplete_and_exits_1(monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        _apply(monkeypatch, _Vamscli(failing={"role delete"}), delete=True)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Status: Incomplete (failed: role deletion)" in out
    assert "Deletion complete." not in out
