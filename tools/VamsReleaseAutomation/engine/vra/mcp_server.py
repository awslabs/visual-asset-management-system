# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra mcp`: the engine as MCP tools (stdio) for Codex, Kiro, Claude Code and any other MCP client.

Same gates as the CLI, because every tool runs the CLI (``python -m vra --json --non-interactive ...``) with
``VRA_AGENT=1`` and ``VRA_MCP=1`` set by the server itself, so the engine refuses every human-only action.

Tiers (like tools/VamsMCP's enable_writes / enable_destructive):

- read tools are always registered: status, report, ledger validate/show/diff/graph, preflight, sync dry-run,
  history verify/show, home show;
- ``VRA_MCP_ENABLE_WRITES=true`` adds work that changes no approval-gated env: sync, merge, build, deploy / test /
  backup of envs without ``requiresApproval``, resume, watch;
- ``VRA_MCP_ENABLE_GATED=true`` (with writes) adds deploy / promote / rollback / restore / data copy / migrate of
  approval-gated envs. They still need an approval record (``approval``), verified by the engine.

The pipeline lane: ``pipeline_status`` (read), ``pipeline_plan`` and ``pipeline_sync_ledger`` without deploy
(write), ``pipeline_deploy`` and ``pipeline_push_source`` (gated; the adapters ask a person before them).

Never exposed: unlock, takeover, --confirm, --writes-stopped, --allow-missing-scanners, --abort-workflow-executions,
ledger import, upgrade-config --write, setup --force, home migrate, credential-process.

A non-zero engine exit becomes a tool error whose text is JSON: ``{exitCode, meaning, message, runId, status,
itRequests, result}``. ``meaning`` is BLOCKED_ON_IT (10), NEEDS_HUMAN (20), TESTS_FAILED (30), ROLLBACK_FAILED (40),
USAGE_ERROR (2) or FAILED (1), the same codes the CLI documents.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

EXIT_MEANING = {0: "OK", 1: "FAILED", 2: "USAGE_ERROR", 10: "BLOCKED_ON_IT", 20: "NEEDS_HUMAN", 30: "TESTS_FAILED",
                40: "ROLLBACK_FAILED"}

# Engine flags and commands an MCP tool never passes (vra.human.HUMAN_ONLY + the internal helper).
FORBIDDEN_ARGS = {"--confirm", "--takeover", "--writes-stopped", "--allow-missing-scanners",
                  "--abort-workflow-executions", "--force", "--write", "--include-auth-tables", "--human-approval",
                  "--emergency", "--allow-replacements", "--accept-drift"}
# Full flags the tools below pass (exact spellings, never refused as a prefix of a forbidden flag, e.g. `--all`).
TOOL_FLAGS = {"--lane", "--ref", "--suites", "--envs", "--env", "--build", "--approval", "--mode", "--run-id", "--from",
              "--to", "--checkpoint", "--source-approval", "--databases", "--lanes", "--no-notify", "--all",
              "--workflow", "--dry-run", "--to-build", "--continue", "--json", "--non-interactive", "--plan",
              "--offline"}
FORBIDDEN_COMMANDS = {"unlock", "credential-process", "setup", "mcp"}
# Subcommands that are human-only even though their group has MCP-safe members.
FORBIDDEN_SUBCOMMANDS = {("ledger", "import"), ("home", "migrate")}

READ, WRITE, GATED = "read", "write", "gated"


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    enable_writes: bool = False
    enable_gated: bool = False
    timeout_seconds: int = 6 * 3600
    extra_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        writes = _truthy(env.get("VRA_MCP_ENABLE_WRITES"))
        return cls(enable_writes=writes, enable_gated=writes and _truthy(env.get("VRA_MCP_ENABLE_GATED")),
                   timeout_seconds=int(env.get("VRA_MCP_TIMEOUT_SECONDS") or 6 * 3600))


class VraToolError(Exception):
    """A non-zero engine exit, as structured data."""

    def __init__(self, payload: dict):
        super().__init__(json.dumps(payload, default=str))
        self.payload = payload


def run_vra(argv: list[str], cfg: Config) -> tuple[int, str, str]:
    """Run the engine CLI as the agent identity. Returns (exit code, stdout, stderr)."""
    env = {**os.environ, **cfg.extra_env, "VRA_AGENT": "1", "VRA_MCP": "1"}
    p = subprocess.run([sys.executable, "-m", "vra", "--json", "--non-interactive", *argv], capture_output=True,
                       text=True, env=env, stdin=subprocess.DEVNULL, timeout=cfg.timeout_seconds)
    return p.returncode, p.stdout, p.stderr


def check_argv(argv: list[str]) -> None:
    for a in argv:
        # argparse also accepts `--flag=value` and unique prefixes (`--takeov`): refuse those spellings too
        name = a.split("=", 1)[0] if a.startswith("--") else a
        if a in FORBIDDEN_ARGS or (name.startswith("--") and len(name) > 2 and name not in TOOL_FLAGS and
                                   any(f.startswith(name) for f in FORBIDDEN_ARGS)):
            raise VraToolError({"exitCode": 20, "meaning": "NEEDS_HUMAN",
                                "message": f"{a} is human-only and never available through MCP"})
    positional = [a for a in argv if not a.startswith("-")]
    cmd = positional[0] if positional else None
    if cmd in FORBIDDEN_COMMANDS:
        raise VraToolError({"exitCode": 20, "meaning": "NEEDS_HUMAN",
                            "message": f"`vra {cmd}` is not available through MCP"})
    if tuple(positional[:2]) in FORBIDDEN_SUBCOMMANDS:
        raise VraToolError({"exitCode": 20, "meaning": "NEEDS_HUMAN",
                            "message": f"`vra {positional[0]} {positional[1]}` is not available through MCP"})


def call(argv: list[str], cfg: Config, runner: Callable[[list[str], Config], tuple[int, str, str]] = run_vra) -> dict:
    """Run one engine command; return its JSON envelope, or raise VraToolError for a non-zero exit."""
    check_argv(argv)
    code, out, err = runner(argv, cfg)
    try:
        doc = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        doc = {"message": out.strip()[-2000:]}
    if code == 0:
        return doc
    payload = {"exitCode": code, "meaning": EXIT_MEANING.get(code, "FAILED"), "message": doc.get("message"),
               "runId": doc.get("runId"), "status": doc.get("status"), "itRequests": doc.get("itRequests"),
               "result": doc.get("result")}
    if not payload["message"] and err:
        payload["message"] = err.strip().splitlines()[-1][:500]
    raise VraToolError(payload)


def env_is_gated(env: str) -> bool:
    """Whether ``env`` is approval-gated, read from the ledger the server's engine resolves (VRA_HOME / VRA_LEDGER)."""
    from vra import home as home_mod
    from vra.ledger import Ledger
    h, _work = home_mod.resolve(None, None, None)
    e = Ledger(h.ledger_dir).env(env)
    return bool(e.get("requiresApproval"))


@dataclass
class Tool:
    name: str
    tier: str
    fn: Callable[..., dict]
    description: str


def _opt(flag: str, value: Any) -> list[str]:
    if value is None or value == "" or value is False:
        return []
    if value is True:
        return [flag]
    if isinstance(value, (list, tuple)):
        return [flag, ",".join(str(v) for v in value)]
    return [flag, str(value)]


def tools(cfg: Config, runner: Callable[[list[str], Config], tuple[int, str, str]] = run_vra,
          gated_check: Callable[[str], bool] = env_is_gated) -> list[Tool]:
    """Every tool this configuration registers (read always; writes and gated behind their flags)."""

    def c(*argv: str) -> dict:
        return call([a for a in argv if a is not None], cfg, runner)

    def ungated(env: str, what: str) -> None:
        if gated_check(env) and not cfg.enable_gated:
            raise VraToolError({"exitCode": 20, "meaning": "NEEDS_HUMAN",
                                "message": f"{env} is approval-gated; {what} on it needs VRA_MCP_ENABLE_GATED=true on "
                                           f"the server (and an approval record), or a human at the CLI"})

    def need_approval(approval: str | None, what: str) -> None:
        if not approval:
            raise VraToolError({"exitCode": 20, "meaning": "NEEDS_HUMAN",
                                "message": f"{what} on an approval-gated env needs `approval` (an approval record id "
                                           f"or path written by the approver)"})

    # ---------------------------------------------------------------- read
    def status(run_id: str | None = None, all_runs: bool = False) -> dict:
        """Run state: one run, the latest runs on this home, or every run and lock of every machine (all_runs)."""
        return c("status", *([run_id] if run_id else []), *(["--all"] if all_runs else []))

    def report(run_id: str | None = None) -> dict:
        """Write and return the Markdown + JSON report of a run (default: the latest)."""
        return c("report", *([run_id] if run_id else []))

    def ledger_validate() -> dict:
        """Schema and semantic checks of the ledger (exit 2 as a tool error when it is invalid)."""
        return c("ledger", "validate")

    def ledger_show(env: str | None = None) -> dict:
        """Project settings, or one env with its tier, gate, required suites and current deploy."""
        return c("ledger", "show", *_opt("--env", env))

    def ledger_diff(env: str) -> dict:
        """The env overlay now vs the overlay of its last successful deploy."""
        return c("ledger", "diff", "--env", env)

    def ledger_graph() -> dict:
        """Promotion topology: every env with its parent, gate and tier."""
        return c("ledger", "graph")

    def preflight(env: str | None = None, workflow: str | None = None, all_envs: bool = False) -> dict:
        """IAM simulation of what a workflow needs; writes an IT request for gaps (exit 10)."""
        return c("preflight", *_opt("--env", env), *_opt("--workflow", workflow), *(["--all"] if all_envs else []))

    def sync_dry_run() -> dict:
        """Fetch upstream and report new releases / preview commits without changing anything."""
        return c("--dry-run", "sync")

    def history_verify(env: str | None = None) -> dict:
        """Check the env history (malformed, edited, duplicate, unsynced or local-only records)."""
        return c("history", "verify", *_opt("--env", env))

    def history_show(env: str) -> dict:
        """The env history records in sequence order."""
        return c("history", "show", "--env", env)

    def home_show() -> dict:
        """The shared home: path, sync type, lease, registered hosts."""
        return c("home", "show")

    def iam_verify(env: str) -> dict:
        """Read-only check of the roles IT created against the env's applied IT role package (exit 10 = gaps)."""
        return c("iam", "verify", "--env", env)

    def pipeline_status() -> dict:
        """The vra pipeline stack: stage states, pending approvals, topology in step with the ledger."""
        return c("pipeline", "status")

    out = [Tool(f.__name__, READ, f, f.__doc__ or "") for f in (
        status, report, ledger_validate, ledger_show, ledger_diff, ledger_graph, preflight, sync_dry_run,
        history_verify, history_show, home_show, iam_verify, pipeline_status)]
    if not cfg.enable_writes:
        return out

    # ---------------------------------------------------------------- write (no approval-gated env)
    def sync() -> dict:
        """Fetch upstream, fast-forward the vendor mirrors, detect new releases and preview commits."""
        return c("sync")

    def merge(lane: str, ref: str | None = None) -> dict:
        """Merge an upstream ref into sync/<lane>-<ref> (parks with exit 20 on conflicts)."""
        return c("merge", "--lane", lane, *_opt("--ref", ref))

    def merge_continue(run_id: str | None = None) -> dict:
        """Continue a merge run after its conflicts were resolved and staged."""
        return c(*_opt("--run-id", run_id), "merge", "--continue")

    def build(lane: str, ref: str, suites: list[str] | None = None, envs: list[str] | None = None) -> dict:
        """Unit suites, per-env synth, bundle, tag. Scanners are never waived through MCP."""
        return c("build", "--lane", lane, "--ref", ref, *_opt("--suites", suites), *_opt("--envs", envs))

    def deploy(env: str, build: str, approval: str | None = None, mode: str | None = None) -> dict:
        """Deploy a build to an env. Approval-gated envs need VRA_MCP_ENABLE_GATED and an approval record."""
        ungated(env, "deploy")
        if gated_check(env):
            need_approval(approval, "deploy")
        return c("deploy", "--env", env, "--build", build, *_opt("--approval", approval), *_opt("--mode", mode))

    def test(env: str, suites: list[str] | None = None) -> dict:
        """Run test suites against an env."""
        ungated(env, "testing")
        return c("test", "--env", env, *_opt("--suites", suites))

    def backup(env: str) -> dict:
        """Checkpoint an env (tables + buckets)."""
        ungated(env, "backup")
        return c("backup", "--env", env)

    def resume(run_id: str) -> dict:
        """Continue a blocked or failed run (never --confirm or --takeover: those are human-only)."""
        return c("resume", run_id)

    def watch(lanes: list[str] | None = None) -> dict:
        """One cron cycle: sync, merge, build, deploy and test the auto-deploy envs, report."""
        return c("watch", *_opt("--lanes", lanes), "--no-notify")

    def iam_plan(env: str, mode: str | None = None, ref: str | None = None) -> dict:
        """Write the IT role package of an env (report-only synth, no AWS writes). `vra iam apply` stays human-only."""
        return c("iam", "plan", "--env", env, *_opt("--mode", mode), *_opt("--ref", ref))

    def pipeline_plan(offline: bool = False) -> dict:
        """Render and synthesize the vra pipeline, diff it against the deployed stack, write its IT package."""
        return c("pipeline", "plan", *(["--offline"] if offline else []))

    def pipeline_sync_ledger() -> dict:
        """Plan the pipeline again when the ledger's topology moved (never deploys; exit 20 until a person does)."""
        return c("pipeline", "sync-ledger")

    out += [Tool(f.__name__, WRITE, f, f.__doc__ or "") for f in (
        sync, merge, merge_continue, build, deploy, test, backup, resume, watch, iam_plan, pipeline_plan,
        pipeline_sync_ledger)]
    if not cfg.enable_gated:
        return out

    # ---------------------------------------------------------------- gated (approval records still required)
    def promote(from_env: str, to_env: str, build: str, approval: str, mode: str | None = None) -> dict:
        """Promote the same build to the next env (approval record required, verified by the engine)."""
        need_approval(approval, "promote")
        return c("promote", "--from", from_env, "--to", to_env, "--build", build, "--approval", approval,
                 *_opt("--mode", mode))

    def rollback(env: str, approval: str | None = None, to_build: str | None = None) -> dict:
        """Redeploy the previous good build (gated envs: approval record)."""
        if gated_check(env):
            need_approval(approval, "rollback")
        return c("rollback", "--env", env, *_opt("--approval", approval), *_opt("--to-build", to_build))

    def restore(env: str, checkpoint: str, approval: str | None = None) -> dict:
        """Restore an env to a checkpoint (access tables stay as they are). Gated envs: approval record."""
        if gated_check(env):
            need_approval(approval, "restore")
        return c("restore", "--env", env, "--checkpoint", checkpoint, *_opt("--approval", approval))

    def data_copy(from_env: str, to_env: str, approval: str, source_approval: str | None = None,
                  databases: list[str] | None = None) -> dict:
        """Copy data between envs (target approval and source data-owner approval)."""
        need_approval(approval, "data copy")
        return c("data-copy", "--from", from_env, "--to", to_env, "--approval", approval,
                 *_opt("--source-approval", source_approval), *_opt("--databases", databases))

    def migrate(env: str, build: str | None = None, from_version: str | None = None,
                to_version: str | None = None, approval: str | None = None) -> dict:
        """Chain the VAMS data migrations between two versions (gated envs: approval record, written by a
        person; the engine verifies it)."""
        if gated_check(env):
            need_approval(approval, "migrate")
        return c("migrate", "--env", env, *_opt("--build", build), *_opt("--from", from_version),
                 *_opt("--to", to_version), *_opt("--approval", approval))

    def pipeline_deploy(plan: str | None = None, approval: str | None = None) -> dict:
        """Deploy a planned pipeline stack. The engine refuses it for an agent when the plan is human-only (first
        deploy, IAM changes, removed approvals), recomputed against the live stack."""
        return c("pipeline", "deploy", *_opt("--plan", plan), *_opt("--approval", approval))

    def pipeline_push_source(lane: str = "release") -> dict:
        """Upload the fork branch to the pipeline's S3 source when it changed and start the pipeline."""
        return c("pipeline", "push-source", "--lane", lane)

    out += [Tool(f.__name__, GATED, f, f.__doc__ or "") for f in (promote, rollback, restore, data_copy, migrate,
                                                                  pipeline_deploy, pipeline_push_source)]
    return out


def create_server(cfg: Config | None = None, **kw):
    """The MCP server object with this configuration's tools (needs the ``mcp`` package: ``pip install
    'vams-release-automation[mcp]'``)."""
    cfg = cfg or Config.from_env()
    try:
        from mcp.server.mcpserver import MCPServer as McpServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as McpServer  # type: ignore[no-redef]
        from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]
    import functools

    server = McpServer("vra", instructions=__doc__)
    for t in tools(cfg, **kw):
        def wrap(fn=t.fn):
            @functools.wraps(fn)
            def tool(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except VraToolError as exc:
                    raise ToolError(str(exc)) from exc
            return tool
        server.add_tool(wrap(), name=t.name, description=f"[{t.tier}] {t.description}")
    return server


def main() -> None:
    # stdout is the JSON-RPC channel: every log line goes to stderr
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    os.environ["VRA_AGENT"] = "1"
    os.environ["VRA_MCP"] = "1"
    create_server().run()


__all__ = ["Config", "VraToolError", "call", "tools", "create_server", "main", "EXIT_MEANING", "FORBIDDEN_ARGS"]
