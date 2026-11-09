#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate FILES.md: every file in the package, grouped by area, with a one-line purpose.

Usage: gen_index.py <package-root> [--check]   (--check fails if FILES.md is out of date)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_PARTS = {"node_modules", ".pytest_cache", "__pycache__", ".git", "cdk.out", ".venv", "build", "dist", ".vra"}

GROUPS = [
    ("Top level", "", "Package metadata, development guide, self-check"),
    ("Engine", "engine/", "The `vra` CLI (package `vra`), its data files and tests"),
    ("Skill", "skill/", "Agent-portable skill and one SOP per workflow"),
    ("Adapters", "adapters/", "Codex, Claude Code and Kiro front-ends (MCP wiring, permissions, guard hook), the shared command guard and cron script"),
    ("IAM", "iam/", "Action manifests for preflight, CloudFormation role and state-store templates"),
    ("CI/CD", "cicd/", "Optional CodeBuild, CodePipeline, OIDC, GitHub Actions and GitLab CI lanes"),
    ("Ledger template", "ledger-template/", "Skeleton for the customer's private ledger repository"),
    ("Scripts", "scripts/", "Self-check helpers"),
]

DESCRIPTIONS = {
    "README.md": "Package overview and links to the documentation",
    "CLAUDE.md": "Component development guide",
    "FILES.md": "This index",
    ".gitignore": "Keeps installed adapter copies, build output and caches out of the repository",
    "Makefile": "`make check`, `make test`, `make install-dev`, `make index`",
    "pyproject.toml": "Python packaging; install with `pip install .` from this directory",
    "scripts/check.sh": "Offline self-check run by `make check`",
    "scripts/check_files.py": "Parse, link, ledger, identical-copy and neutrality checks",
    "scripts/check_doc_commands.py": "Checks every `vra ...` command in the Markdown against the CLI",
    "scripts/gen_index.py": "Generates this index (`--check` verifies it)",
    "scripts/tests/test_check_scripts.py": "Tests for the self-check scripts (scan scope, links, neutrality)",
    "scripts/check_agent_policy.py": "Checks the human-only set across engine, skill, MCP server, guard and adapters",
    "scripts/tests/test_check_agent_policy.py": "Tests for the agent policy check",
    "engine/tests/test_review050_regressions.py": "REVIEW-050 regressions: IAM gates, IT apply binding, PassRole, TTY rule",
    "cicd/github-actions/vra-home-publish.yml": "Reusable workflow: the only job that pushes the home (no fork code)",
    "cicd/oidc/github_trust.py": "One source for GitHub OIDC trust: issuer hosts (GHES, GHEC), subjects, rendered template",
    "cicd/tests/test_review050_ci.py": "REVIEW-050 regressions: home token, hand-off gate, ASH, test isolation, trust",
    "scripts/tests/test_check_doc_commands.py": "Tests for the doc-command check (command groups and flags)",
    "adapters/install.sh": "Installs one adapter into the operator workspace",
    "adapters/shared/vra-guard.py": "Pre-tool-use command guard for every adapter (exit 2 blocks)",
    "adapters/shared/test_vra_guard.py": "Guard tests: bypass spellings and event shapes",
    "adapters/shared/vra-cron.sh": "One cron entry point for every agent system (watch, then triage by exit code)",
    "adapters/claude/mcp.json.example": "Claude Code `.mcp.json`: the vra MCP server (gated tier from the environment)",
    "adapters/claude/mcp.cron.json": "MCP configuration for headless Claude Code runs (gated tier off)",
    "adapters/claude/settings.json.example": "Claude Code settings: VRA_AGENT, MCP server, allow/ask/deny, guard hook",
    "adapters/codex/config.toml.example": "Codex project config: MCP server, VRA_AGENT, sandbox, hooks, cron profile",
    "adapters/codex/hooks.json": "Codex PreToolUse hook running the shared guard",
    "adapters/codex/rules/vra.rules": "Codex command rules for the shell fallback",
    "adapters/kiro/generate_agents.py": "Source of the Kiro agent files (run, then kiro-cli agent upgrade)",
    "cicd/pipeline/.npmrc": "npm policy: engine-strict, min-release-age = 7 days",
    "adapters/claude/CLAUDE.md.example": "Claude Code project memory, installed as `.claude/CLAUDE.md`",
    "engine/vra/cli.py": "Command line parser and dispatch",
    "engine/vra/runner.py": "Workflow runner, resume, locks, `--json` output",
    "engine/vra/state.py": "Run state (compare-before-write), owner-token locks with heartbeat, history, consumed approvals; folder and S3 backends",
    "engine/vra/home.py": "Shared home folder: layout, vra-home.yaml, sync (none/git/command), join, migrate",
    "engine/vra/human.py": "Human-only actions enforced in the engine (TTY or approval record; never agents)",
    "engine/vra/mcp_server.py": "`vra mcp`: stdio MCP server with read, write and gated tool tiers",
    "engine/tests/test_shared_home.py": "Shared home across machines: locks, takeover, stale writes, history, join, migrate",
    "engine/tests/test_mcp_and_human_only.py": "MCP tool tiers and error mapping; human-only actions refused for agents",
    "engine/tests/test_config_and_topology.py": "Config identity, partitions, synth isolation, FIPS, live sign-in, A/B, tiers, generic CI",
    "engine/vra/ledger.py": "Ledger loading, validation, import, upgrade-config",
    "engine/vra/aws.py": "Partition-generic sessions, refreshable credentials, credential_process",
    "engine/vra/gitops.py": "sync, merge, path policy, conflicts.json, push guard",
    "engine/vra/githost.py": "Pull requests on github, gitlab, bitbucket, codecommit, local",
    "engine/vra/build.py": "Unit suites, per-env synth, bundle, (KMS-signed) tag",
    "engine/vra/deploy.py": "deploy, promote, rollback, cdk diff / change-set gate",
    "engine/vra/freeze.py": "Write freeze (maintenance-mode / manual backends), workflow executions",
    "engine/vra/sshsig.py": "KMS-signed git tags in SSH signature format",
    "iam/scripts/patch-bootstrap-template.py": "Adds sts:SetSourceIdentity to the CDK bootstrap role trust",
    "engine/vra/migrate.py": "Migration discovery, chaining, manual-step gate",
    "engine/vra/backup.py": "Checkpoints",
    "engine/vra/restore.py": "Restore to a checkpoint (parallel temp restores, frozen reload, verification)",
    "engine/vra/datacopy.py": "Data copy between envs, target protection, remapping",
    "engine/vra/permissions.py": "Denial classification, IAM simulation, IT requests",
    "engine/vra/approvals.py": "Approval records and separation of duties",
    "engine/vra/report.py": "Reports, metrics, notifications",
    "engine/vra/watch.py": "Cron entry point",
    "engine/vra/setup.py": "First-run wizard",
    "engine/vra/tests_runner.py": "Unit, live and customer suites",
    "engine/vra/data/defaults/path-policy.yaml": "Default merge policy (same as ledger-template/path-policy.yaml)",
    "engine/vra/data/upgrade-matrix.yaml": "Per-transition migration notes (A/B, manual steps)",
    "engine/vra/data/suites.yaml": "VAMS unit suite commands",
    "ledger-template/path-policy.yaml": "Merge policy (identical to the engine default)",
    "engine/vra/iam.py": "`vra iam` plan, apply and verify: IT role package, overlay wiring, preflight/build IAM gates",
    "engine/vra/data/cdk-bootstrap-roles.json": "CDK bootstrap roles for bootstrap mode (same as iam/it-roles/)",
    "engine/tests/test_iam_package.py": "IT role package, apply, verify and the BLOCKED_ON_IT loop on real VAMS reports",
    "engine/tests/iam_fixtures.py": "Loads the real VAMS v2.6.6 assemblies in fixtures/iam/ as cdk.out folders",
    "engine/tests/fixtures/iam/README.md": "How the VAMS v2.6.6 IAM fixtures were synthesized, per variant",
    "iam/actions/iam-verify.yaml": "Read-only `vra iam verify` (role reads, simulation)",
    "iam/it-roles/README.md": "Inputs of the IT role package and how it is generated",
    "iam/it-roles/cdk-bootstrap-roles.json": "CDK bootstrap role trust + policies of the pinned CDK CLI",
    "iam/scripts/extract_cdk_bootstrap_roles.py": "Refreshes cdk-bootstrap-roles.json from a CDK CLI",
    "scripts/make_iam_fixture.py": "Reduces a real VAMS cdk.out to an IAM test fixture",
    "engine/vra/core_patches.py": "Ledger core patches (`core-patches/*.patch`) applied to every build and IAM plan source",
    "engine/tests/test_b050_29_generated_lambda_names.py":
        "B-050-29: generated Lambda names keep 25 characters of the stack name (IAM package and vra role grants)",
    "engine/tests/test_b050_35_ref_is_a_name.py":
        "B-050-35: `Ref` of a Lambda function or DynamoDB table resolves to its name inside an IAM package ARN",
    "engine/tests/test_b050_36_rollback_failed.py":
        "B-050-36: a cdk deploy that leaves a stack ROLLBACK_FAILED reports exit 40 with the first failure, "
        "not an IT block",
    "engine/tests/test_b050_37_held_lock_note.py":
        "B-050-37: a run that parks more than once lists its held-lock note once, naming the unlock and takeover "
        "paths for a resume from another machine",
    "engine/tests/test_b050_38_held_lock_note_cleared.py":
        "B-050-38: a resumed run that succeeds and releases its env drops the earlier \"stays locked\" note",
    "cicd/tests/test_b050_39_report_run.py":
        "B-050-39: a CI `report` step without a run id reports the run of the job's own vra step",
    "engine/tests/test_b050_40_asset_topic.py":
        "B-050-40: roles that use the runtime per-asset SNS topics are granted `AssetTopic*`, not the core stack "
        "pattern",
    "engine/tests/test_cloudfront_quota.py":
        "O-050-15 (0.5.1): the CloudFront quota check before `cdk deploy`; the demo11 full cache-policy quota case",
    "engine/tests/fixtures/b050_40_prod4_sns_statements.json":
        "SNS statements of the 7 AssetTopic roles from the E2E-050 prod-4 IAM report (B-050-40)",
    "engine/tests/test_core_patches.py": "Ledger core patches; the GitHub issue #431 and #1210 patches on real VAMS v2.6.6 / development input",
    "engine/tests/fixtures/iam/vams-2.6.6-iam-role-transform.aspect.ts":
        "VAMS v2.6.6 IamRoleTransform aspect source (the boundary core patch applies to it)",
    "engine/tests/fixtures/iam/vams-development-2fb310762-iam-role-transform.aspect.ts":
        "VAMS development 2fb310762 IamRoleTransform aspect with the upstream #431 fix (the boundary core patch "
        "reports `present` through its marker)",
    "engine/tests/fixtures/iam/vams-2.6.6-rest-api-gateway-construct.ts":
        "VAMS v2.6.6 REST API construct source (the #1210 API deployment core patch applies to it)",
    "engine/tests/fixtures/iam/vams-development-a9ac094c4-rest-api-gateway-construct.ts":
        "VAMS development a9ac094c4 REST API construct source (the #1210 core patch applies to it)",
    "ledger-template/core-patches/api-deployment-role-name.patch":
        "Core patch for GitHub issue #1210: new API Gateway deployment when the role-name prefix changes",
    "ledger-template/core-patches.md": "Core patch register (fork paths and ledger patch files, template entries)",
    "ledger-template/core-patches/iam-role-transform-boundary.patch":
        "Core patch for GitHub issue #431: IamRoleTransform applies the permission boundary",
    "engine/vra/pipeline.py": "`automation.mode` tiers: validation, pipeline config rendering, plan/deploy/status",
    "engine/vra/pipeline_cli.py": "`vra pipeline` plan, deploy, status, sync-ledger, push-source",
    "engine/vra/pipeline_iam.py": "IT package for the pipeline's own roles (CDK policy report of cicd/pipeline)",
    "adapters/cursor/gen_cli_json.py": "Generates the Cursor CLI permissions from the Claude Code settings (`--check`)",
}
for _v in ("commercial-report", "govcloud-report", "commercial-precreated", "commercial-precreated-partial",
           "commercial-boundary", "commercial-boundary-fixed", "commercial-cli", "commercial-cdkflag-patched",
           "commercial-cdkboundary-patched"):
    DESCRIPTIONS[f"engine/tests/fixtures/iam/vams-2.6.6-{_v}.json.gz"] = \
        f"Real VAMS v2.6.6 synth, {_v.replace('-', ' ')} (fixtures/iam/README.md)"


def listing(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or SKIP_PARTS & set(rel.parts) or any(x.endswith(".egg-info") for x in rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def table(rows: list[tuple[str, str]]) -> list[str]:
    """A two-column Markdown table laid out the way Prettier formats it (cells padded to the column width,
    separator dashes as wide as the column, at least 3), so the repository's `prettier-check` accepts
    FILES.md as generated. Widths are character counts; the index is ASCII."""
    header = ("File", "Purpose")
    widths = [max(3, len(header[i]), *(len(r[i]) for r in rows)) for i in range(2)]

    def line(cells) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    return [line(header), line(["-" * w for w in widths]), *(line(r) for r in rows)]


def render(root: Path) -> str:
    files = listing(root)
    lines = ["# File index", "",
             "Every file in `tools/VamsReleaseAutomation/`, grouped by area. Generated by",
             "`scripts/gen_index.py`; `scripts/check.sh` fails when it is out of date.", ""]
    used = set()
    for title, prefix, blurb in GROUPS:
        group = [f for f in files if (f.startswith(prefix) if prefix else "/" not in f) and f not in used]
        if not group:
            continue
        used.update(group)
        lines += [f"## {title}", "", blurb + ".", ""]
        lines += table([(f"[`{f}`]({f})", DESCRIPTIONS.get(f, "")) for f in group])
        lines.append("")
    rest = [f for f in files if f not in used]
    if rest:
        lines += ["## Other", ""]
        lines += table([(f"[`{f}`]({f})", "") for f in rest])
        lines.append("")
    return "\n".join(lines)


def normalize(text: str) -> list[str]:
    """Layout-insensitive form, so the check accepts the file after Prettier aligns its tables."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"-{3,}", "---", re.sub(r"\s*\|\s*", "|", re.sub(r"\s+", " ", line))).strip()
        if line:
            out.append(line)
    return out


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    text = render(root)
    target = root / "FILES.md"
    if "--check" in sys.argv:
        if not target.exists() or normalize(target.read_text(encoding="utf-8")) != normalize(text):
            print("FILES.md is out of date; run scripts/gen_index.py <package-root>")
            sys.exit(1)
        print("FILES.md up to date")
        sys.exit(0)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target} ({text.count(chr(10))} lines)")
