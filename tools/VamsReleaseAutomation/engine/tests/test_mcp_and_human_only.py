# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra mcp` tool tiers and error mapping, and human-only actions enforced in the engine."""

from __future__ import annotations

import asyncio
import json

import pytest

from vra import cli, errors, human, mcp_server
from vra.mcp_server import Config, VraToolError


def _names(cfg, **kw):
    return {t.name: t.tier for t in mcp_server.tools(cfg, **kw)}


def test_read_tools_only_by_default_and_writes_and_gated_behind_flags():
    read = _names(Config.from_env({}))
    assert set(read.values()) == {"read"} and {"status", "ledger_validate", "preflight", "history_verify",
                                              "sync_dry_run"} <= set(read)
    writes = _names(Config.from_env({"VRA_MCP_ENABLE_WRITES": "true"}))
    assert {"sync", "build", "deploy", "test", "resume"} <= set(writes) and "promote" not in writes
    # gated needs writes too
    assert "promote" not in _names(Config.from_env({"VRA_MCP_ENABLE_GATED": "true"}))
    gated = _names(Config.from_env({"VRA_MCP_ENABLE_WRITES": "1", "VRA_MCP_ENABLE_GATED": "1"}))
    assert {"promote", "restore", "data_copy", "rollback"} <= set(gated)
    for forbidden in ("unlock", "takeover", "confirm", "ledger_import", "upgrade_config", "setup", "home_migrate",
                      "credential_process"):
        assert forbidden not in gated


def test_no_tool_passes_a_human_only_flag():
    seen = []

    def runner(argv, cfg):
        seen.append(argv)
        return 0, json.dumps({"exitCode": 0}), ""
    cfg = Config.from_env({"VRA_MCP_ENABLE_WRITES": "1", "VRA_MCP_ENABLE_GATED": "1"})
    for t in mcp_server.tools(cfg, runner=runner, gated_check=lambda env: True):
        import inspect
        kw = {p: ("x" if p not in ("suites", "envs", "lanes", "databases") else ["a"])
              for p, prm in inspect.signature(t.fn).parameters.items() if prm.default is inspect.Parameter.empty}
        if "approval" in inspect.signature(t.fn).parameters:
            kw["approval"] = "approvals/x.yaml"
        t.fn(**kw)
    flat = [a for argv in seen for a in argv]
    assert not set(flat) & mcp_server.FORBIDDEN_ARGS
    with pytest.raises(VraToolError, match="human-only"):
        mcp_server.call(["resume", "r1", "--takeover"], cfg, runner)
    with pytest.raises(VraToolError, match="not available"):
        mcp_server.call(["unlock", "--env", "dev"], cfg, runner)


def test_exit_codes_become_structured_tool_errors():
    def runner(argv, cfg):
        return 20, json.dumps({"exitCode": 20, "message": "approval required", "runId": "deploy-1",
                               "status": "NEEDS_HUMAN"}), ""
    with pytest.raises(VraToolError) as ei:
        mcp_server.call(["deploy", "--env", "dev", "--build", "b"], Config(), runner)
    p = ei.value.payload
    assert p["exitCode"] == 20 and p["meaning"] == "NEEDS_HUMAN" and p["runId"] == "deploy-1"
    assert json.loads(str(ei.value))["message"] == "approval required"
    for code, meaning in ((10, "BLOCKED_ON_IT"), (30, "TESTS_FAILED"), (40, "ROLLBACK_FAILED"), (2, "USAGE_ERROR")):
        with pytest.raises(VraToolError) as ei:
            mcp_server.call(["status"], Config(), lambda a, c, code=code: (code, "", "boom\n"))
        assert ei.value.payload["meaning"] == meaning and ei.value.payload["message"] == "boom"


def test_gated_env_needs_the_gated_flag_and_an_approval():
    calls = []
    runner = lambda argv, cfg: (calls.append(argv), (0, "{}", ""))[1]  # noqa: E731
    writes = {t.name: t for t in mcp_server.tools(Config(enable_writes=True), runner=runner,
                                                   gated_check=lambda env: env == "prod")}
    writes["deploy"].fn(env="dev", build="b1")
    with pytest.raises(VraToolError, match="VRA_MCP_ENABLE_GATED"):
        writes["deploy"].fn(env="prod", build="b1", approval="a")
    gated = {t.name: t for t in mcp_server.tools(Config(enable_writes=True, enable_gated=True), runner=runner,
                                                  gated_check=lambda env: env == "prod")}
    with pytest.raises(VraToolError, match="needs `approval`"):
        gated["deploy"].fn(env="prod", build="b1")
    gated["deploy"].fn(env="prod", build="b1", approval="approvals/prod/b1.yaml")
    assert calls[-1][-2:] == ["--approval", "approvals/prod/b1.yaml"]


def test_server_registers_the_tools_with_the_sdk():
    pytest.importorskip("mcp")
    srv = mcp_server.create_server(Config(enable_writes=True))
    listed = asyncio.run(srv.list_tools())
    names = {t.name for t in listed}
    assert {"status", "deploy", "resume"} <= names and "promote" not in names
    assert all(t.description.startswith("[") for t in listed)


def test_server_tool_runs_the_engine_as_an_agent(ledger, tmp_path, monkeypatch):
    """End to end through a real engine subprocess: read tools work, a human-only action is refused."""
    monkeypatch.setenv("VRA_LEDGER", str(ledger.root))
    monkeypatch.setenv("VRA_WORK_DIR", str(tmp_path / "w"))
    cfg = Config()
    out = mcp_server.call(["ledger", "show", "--env", "prod"], cfg)
    assert out["result"]["tier"] == "prod" and out["result"]["requiresApproval"] is True
    code, stdout, _ = mcp_server.run_vra(["unlock", "--env", "dev", "--reason", "x"], cfg)
    assert code == errors.NEEDS_HUMAN and "human-only" in json.loads(stdout)["message"]


# ------------------------------------------------------------------ human-only actions in the engine
@pytest.mark.parametrize("argv,action", [
    (["unlock", "--env", "dev", "--reason", "x"], "unlock"),
    (["ledger", "import", "--env", "dev", "--from", "/tmp/x.json"], "ledger-import"),
    (["ledger", "upgrade-config", "--env", "dev", "--to-ref", "v2.6.6", "--write"], "upgrade-config-write"),
    (["deploy", "--env", "dev", "--build", "b", "--abort-workflow-executions"], "abort-workflow-executions"),
    (["resume", "r1", "--takeover"], "takeover"),
    (["resume", "r1", "--confirm"], "confirm"),
    (["setup", "--force"], "setup-force"),
    (["home", "migrate", "--from", "/tmp/old"], "home-migrate"),
    (["build", "--lane", "release", "--ref", "v1", "--allow-missing-scanners"], "allow-missing-scanners"),
    (["restore", "--env", "dev", "--checkpoint", "c", "--writes-stopped"], "writes-stopped"),
])
def test_agents_are_refused_every_human_only_action(argv, action, ledger, monkeypatch, capsys):
    for var in ("VRA_AGENT", "VRA_MCP"):
        monkeypatch.setenv(var, "1")
        rc = cli.main(["--json", "--ledger", str(ledger.root), *argv])
        out = json.loads(capsys.readouterr().out)
        assert rc == errors.NEEDS_HUMAN and out["result"]["humanOnly"] == action, (var, out)
        monkeypatch.delenv(var)


def test_human_only_needs_a_terminal_or_an_approval_record(ctx, ledger, monkeypatch):
    monkeypatch.setattr(human, "_typed_confirmation_blocker", lambda: None)   # a person at their own terminal
    # no terminal, no approval: refused
    with pytest.raises(errors.NeedsHuman, match="needs --approval"):
        human.require(ctx, "unlock", "dev", tty=False)
    # at a terminal: the person types the word
    assert human.require(ctx, "unlock", "dev", tty=True, ask=lambda q: "dev")["how"] == "tty"
    with pytest.raises(errors.NeedsHuman, match="not confirmed"):
        human.require(ctx, "unlock", "dev", tty=True, ask=lambda q: "yes")
    # in CI: an approval record with subject human:<action> and that scope, used once
    from vra import util
    util.write_yaml(ledger.root / "approvals/dev/human_unlock.yaml",
                    {"build": "human:unlock", "env": "dev", "approver": "lead@example.com",
                     "approvedAt": util.now_iso(), "scope": ["unlock"]})
    ledger.raw_env("dev")
    import yaml
    doc = yaml.safe_load((ledger.root / "envs/dev/env.yaml").read_text())
    doc["approvers"] = ["lead@example.com"]
    (ledger.root / "envs/dev/env.yaml").write_text(yaml.safe_dump(doc))
    ledger._envs.clear()
    got = human.require(ctx, "unlock", "dev", approval="approvals/dev/human_unlock.yaml", tty=False)
    assert got["how"] == "approval" and got["approver"] == "lead@example.com"
    with pytest.raises(errors.NeedsHuman, match="already used"):
        human.require(ctx, "unlock", "dev", approval="approvals/dev/human_unlock.yaml", tty=False)


def test_unhandled_errors_still_print_a_json_envelope(ledger, tmp_path, capsys, monkeypatch):
    from vra import runner
    monkeypatch.setattr(runner, "unlock", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store unreachable")))
    monkeypatch.setattr(human, "_tty", lambda: True)
    monkeypatch.setattr(human, "_typed_confirmation_blocker", lambda: None)
    monkeypatch.setattr(human, "_default_ask", lambda q: "dev")
    rc = cli.main(["--json", "--ledger", str(ledger.root), "--work-dir", str(tmp_path / "w"), "unlock", "--env", "dev",
                   "--reason", "x"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["exitCode"] == 1 and "store unreachable" in out["message"]


@pytest.mark.parametrize("argv", [
    ["deploy", "--env", "prod", "--build", "b", "--emergency"],
    ["deploy", "--env", "dev", "--build", "b", "--allow-replacements"],
    ["deploy", "--env", "dev", "--build", "b", "--accept-drift"],
    ["ledger", "import", "--env", "dev", "--from", "config.json"],
    ["--json", "home", "migrate", "--from", "old"],
])
def test_mcp_refuses_gate_waivers_and_human_only_subcommands(argv):
    with pytest.raises(VraToolError) as exc:
        mcp_server.check_argv(argv)
    assert exc.value.payload["exitCode"] == 20


def test_mcp_allows_safe_members_of_the_same_groups():
    mcp_server.check_argv(["ledger", "validate"])
    mcp_server.check_argv(["home", "sync"])


@pytest.mark.parametrize("argv", [
    ["resume", "--takeov"],                                  # argparse expands a unique prefix (E2E-040)
    ["resume", "r1", "--takeover=1"],
    ["status", "--human-approval=approvals/dev/x.yaml"],
    ["deploy", "--env", "dev", "--build", "b", "--allow-repl"],
])
def test_mcp_refuses_prefix_and_equals_spellings_of_human_only_flags(argv):
    with pytest.raises(VraToolError) as exc:
        mcp_server.check_argv(argv)
    assert exc.value.payload["exitCode"] == 20


def test_every_flag_the_mcp_tools_pass_survives_the_prefix_check():
    for flag in ("--lane", "--ref", "--suites", "--envs", "--env", "--build", "--approval", "--mode", "--run-id",
                 "--from", "--to", "--checkpoint", "--source-approval", "--databases", "--lanes", "--no-notify",
                 "--all", "--workflow", "--dry-run", "--to-build", "--continue"):
        mcp_server.check_argv(["x", flag])
