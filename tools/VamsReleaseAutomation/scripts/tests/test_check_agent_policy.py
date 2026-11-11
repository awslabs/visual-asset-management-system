# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The agent policy is the same in the engine, the skill, the MCP server, the guard and every adapter."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("check_agent_policy", ROOT / "scripts/check_agent_policy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_agent_policy"] = mod
    spec.loader.exec_module(mod)
    return mod


policy = _load()


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "pkg"
    for part in ("skill", "adapters"):
        shutil.copytree(ROOT / part, dst / part)
    return dst


def test_the_shipped_package_is_consistent():
    assert policy.check(ROOT) == []


def test_a_kiro_agent_that_allows_a_human_only_command_is_reported(tmp_path):
    pkg = _copy(tmp_path)
    p = pkg / "adapters/kiro/.kiro/agents/vra-release-operator.json"
    cfg = json.loads(p.read_text())
    cfg["toolsSettings"]["shell"]["deniedCommands"] = [
        d for d in cfg["toolsSettings"]["shell"]["deniedCommands"] if "unlock" not in d]
    p.write_text(json.dumps(cfg))
    assert any("vra-release-operator.json does not deny 'vra unlock" in x for x in policy.check(pkg))


def test_a_workflow_step_the_guard_blocks_must_be_handed_to_the_human(tmp_path):
    pkg = _copy(tmp_path)
    wf = pkg / "skill/workflows/W0-setup.md"
    wf.write_text(wf.read_text() + "\n99. Run `vra ledger import --env dev --from c.json`.\n")
    assert any("W0-setup.md" in x and "vra ledger import" in x for x in policy.check(pkg))


def test_the_skill_must_name_every_human_only_action(tmp_path):
    pkg = _copy(tmp_path)
    sk = pkg / "skill/SKILL.md"
    sk.write_text("\n".join(x for x in sk.read_text().splitlines() if "--takeover" not in x))
    assert any("--takeover" in x for x in policy.check(pkg))


def test_every_engine_human_only_action_has_a_sample():
    from vra import human
    assert set(human.HUMAN_ONLY) <= set(policy.SAMPLES) | set(policy.AWAITING)
    assert not set(policy.SAMPLES) & set(policy.AWAITING)


def test_iam_apply_is_human_only_in_every_adapter(tmp_path):
    """vra 0.5.0: `vra iam apply` is a SAMPLES entry; dropping it from any adapter is reported."""
    assert "iam-apply" in policy.SAMPLES and not policy.AWAITING
    pkg = _copy(tmp_path)
    s = pkg / "adapters/claude/settings.json.example"
    cfg = json.loads(s.read_text())
    cfg["permissions"]["deny"].remove("Bash(vra iam apply:*)")
    s.write_text(json.dumps(cfg))
    k = pkg / "adapters/kiro/.kiro/agents/vra-cron-operator.json"
    agent = json.loads(k.read_text())
    agent["toolsSettings"]["shell"]["deniedCommands"].remove(".*vra iam apply.*")
    k.write_text(json.dumps(agent))
    problems = policy.check(pkg)
    assert any("Claude settings do not deny 'vra iam apply" in p for p in problems)
    assert any("Kiro vra-cron-operator.json does not deny 'vra iam apply" in p for p in problems)


def test_awaiting_only_downgrades_the_named_action(monkeypatch):
    policy.check(ROOT)
    awaited = {a: list(g) for a, g in policy.AWAITED.items()}
    moved = {a: (w, s) for a, (w, s, _who) in policy.AWAITING.items()}
    monkeypatch.setattr(policy, "AWAITING", {})
    monkeypatch.setattr(policy, "SAMPLES", {**policy.SAMPLES, **moved})
    assert sorted(policy.check(ROOT)) == sorted(g for gs in awaited.values() for g in gs)


def test_cursor_cli_permissions_and_hooks_are_part_of_the_policy(tmp_path):
    pkg = _copy(tmp_path)
    p = pkg / "adapters/cursor/.cursor/cli.json"
    cfg = json.loads(p.read_text())
    cfg["permissions"]["deny"] = [d for d in cfg["permissions"]["deny"] if "unlock" not in d]
    p.write_text(json.dumps(cfg))
    h = pkg / "adapters/cursor/.cursor/hooks.json"
    hooks = json.loads(h.read_text())
    for x in hooks["hooks"]["beforeMCPExecution"]:
        x["failClosed"] = False
    h.write_text(json.dumps(hooks))
    got = policy.check(pkg)
    assert any("Cursor cli.json does not deny 'vra unlock" in x for x in got)
    assert any("beforeMCPExecution does not run vra-guard.py fail-closed" in x for x in got)


def test_agent_writes_to_iam_pipeline_state_and_overlays_must_be_denied(tmp_path):
    """an adapter that lets an agent write state/iam, state/pipeline or an env overlay is reported."""
    pkg = _copy(tmp_path)
    p = pkg / "adapters/claude/settings.json.example"
    cfg = json.loads(p.read_text())
    cfg["permissions"]["deny"] = [d for d in cfg["permissions"]["deny"] if "state/iam" not in d]
    p.write_text(json.dumps(cfg))
    k = pkg / "adapters/kiro/.kiro/agents/vra-release-operator.json"
    kc = json.loads(k.read_text())
    kc["toolsSettings"]["write"]["deniedPaths"] = [d for d in kc["toolsSettings"]["write"]["deniedPaths"]
                                                   if "overlay" not in d]
    k.write_text(json.dumps(kc))
    got = policy.check(pkg)
    assert any("Claude settings do not deny agent edits of /srv/vra-home/state/iam/prod/applied.json" in x
               for x in got)
    assert any("Kiro vra-release-operator.json does not deny writes of" in x and "overlay" in x for x in got)


def test_a_hook_that_fails_open_is_reported(tmp_path):
    """a hook command without `|| exit 2` is non-blocking when the guard cannot run."""
    pkg = _copy(tmp_path)
    p = pkg / "adapters/codex/hooks.json"
    p.write_text(p.read_text().replace(" || exit 2", ""))
    assert any(x.startswith("Codex hook does not fail closed") for x in policy.check(pkg))


def test_pipeline_deploy_must_ask_a_person_everywhere(tmp_path):
    """plan / status / sync-ledger for agents; deploy and push-source ask."""
    pkg = _copy(tmp_path)
    k = pkg / "adapters/kiro/.kiro/agents/vra-release-operator.json"
    kc = json.loads(k.read_text())
    kc["toolsSettings"]["shell"]["allowedCommands"].append("vra pipeline deploy .*")
    k.write_text(json.dumps(kc))
    p = pkg / "adapters/claude/settings.json.example"
    cfg = json.loads(p.read_text())
    cfg["permissions"]["allow"] = [a for a in cfg["permissions"]["allow"] if "pipeline plan" not in a]
    p.write_text(json.dumps(cfg))
    got = policy.check(pkg)
    assert any("Kiro vra-release-operator.json runs `vra pipeline deploy --plan pl-1` without asking" in x
               for x in got)
    assert any("Claude settings do not allow `vra pipeline plan`" in x for x in got)
