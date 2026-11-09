# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from vra import errors, gitops, runner, util
from vra.errors import UsageError

from conftest import git, write


def test_sync_ff_only_and_detection(ctx, repos):
    out = runner.start(ctx, "sync", {})
    assert out.exit_code == 0, out.message
    res = out.run.state["result"]
    assert res["newRelease"] == "v2.6.6"
    assert res["newPreviewCommit"]
    # vendor mirrors pushed to the fork with plain fast-forward pushes
    up_main = git(repos["upstream"], "rev-parse", "main")
    assert git(repos["fork"], "rev-parse", "vendor/vams-main") == up_main
    assert git(repos["fork"], "rev-parse", "vendor/vams-development") == git(repos["upstream"], "rev-parse", "development")
    # once processed, the same release is not reported again
    gitops.mark_processed(ctx, "release", "v2.6.6", up_main)
    gitops.mark_processed(ctx, "preview", "x", res["lanes"]["preview"]["sha"])
    ctx.run_id = None
    out2 = runner.start(ctx, "sync", {})
    assert out2.run.state["result"]["newRelease"] is None
    assert out2.run.state["result"]["newPreviewCommit"] is None


def test_sync_refuses_diverged_vendor(ctx, repos, tmp_path):
    # someone pushed a non-upstream commit onto the fork's vendor branch
    w = tmp_path / "rogue"
    git(tmp_path, "clone", "-q", str(repos["fork"]), str(w))
    git(w, "checkout", "-q", "-b", "vendor/vams-main")
    write(w, "rogue.txt", "x")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "rogue")
    git(w, "push", "-q", "origin", "vendor/vams-main")
    out = runner.start(ctx, "sync", {})
    assert out.exit_code == errors.NEEDS_HUMAN
    assert "fast-forward only" in out.message


def test_merge_path_policy_conflicts_and_continue(ctx, repos, ledger):
    (ledger.root / "core-patches.md").write_text("| Path | Reason |\n|---|---|\n| `backend/backend/core.py` | patch |\n")
    assert runner.start(ctx, "sync", {}).exit_code == 0
    ctx.run_id = None
    out = runner.start(ctx, "merge", {"lane": "release", "ref": "v2.6.6"})
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    conflicts = util.read_json(out.run.dir / "conflicts.json")
    assert [c["path"] for c in conflicts["unresolved"]] == ["backend/backend/core.py"]
    auto = {a["path"]: a["strategy"] for a in conflicts["autoResolved"]}
    assert auto == {"CHANGELOG.md": "union", "web/src/custom/README.md": "fork",
                    "infra/package-lock.json": "regenerate"}
    repo = ctx.repo_dir
    # union kept both sides, fork lane kept the customer, lockfile took upstream
    cl = (repo / "CHANGELOG.md").read_text()
    assert "customer change" in cl and "upstream change 2.6.6" in cl
    assert (repo / "web/src/custom/README.md").read_text() == "customer custom lane notes\n"
    assert '"2.6.6"' in (repo / "infra/package-lock.json").read_text()

    # continue without resolving -> still NEEDS_HUMAN
    again = runner.resume(ctx, out.run.run_id)
    assert again.exit_code == errors.NEEDS_HUMAN

    # agent resolves
    (repo / "backend/backend/core.py").write_text("VERSION = '2.6.6'  # customer core patch\n")
    git(repo, "add", "backend/backend/core.py")
    done = runner.resume(ctx, out.run.run_id)
    assert done.exit_code == 0, done.message
    branch = "sync/release-v2.6.6"
    assert git(repos["fork"], "rev-parse", "--verify", branch)
    pr = util.read_json(ctx.work_dir / "prs" / "sync__release-v2.6.6.json")
    assert pr["base"] == "main" and "union" in pr["body"]
    # merge commit has both parents: upstream sha stays in history (no rebase)
    parents = git(repo, "rev-list", "--parents", "-n", "1", branch).split()
    assert len(parents) == 3
    assert git(repo, "merge-base", "--is-ancestor", "v2.6.6", branch) == ""


def test_merge_of_a_release_the_target_already_contains_publishes_nothing(ctx, repos, ledger, tmp_path):
    """E2E-050 O-050-4 / B-050-23: v2.6.67 was merged into test/fork-main; the next daily watch merged it again
    (alreadyUpToDate), then pushed an empty sync branch, failed without a fork write token, and the watch exited 1.
    A release the target contains is marked processed: no push, no pull request."""
    # the fork's edits outside the lanes are registered (`-s ours` below keeps the fork's tree)
    (ledger.root / "core-patches.md").write_text("- `backend/backend/core.py` customer core patch\n"
                                                 "- `infra/config/config.ts` customer config\n")
    assert runner.start(ctx, "sync", {}).exit_code == 0
    ctx.run_id = None
    w = tmp_path / "fork-merged"
    git(tmp_path, "clone", "-q", git(ctx.repo_dir, "remote", "get-url", "origin"), str(w))
    git(w, "fetch", "-q", str(repos["upstream"]), "refs/tags/v2.6.6")
    git(w, "merge", "-q", "-s", "ours", "--no-edit", "FETCH_HEAD")     # the fork took the release already
    git(w, "push", "-q", "origin", "main")
    assert runner.start(ctx, "sync", {}).exit_code == 0             # the watch: sync, then merge
    ctx.run_id = None
    out = runner.start(ctx, "merge", {"lane": "release", "ref": "v2.6.6"})
    assert out.exit_code == 0, out.message
    assert out.run.step_result("merge.prepare")["baseSha"] == git(w, "rev-parse", "HEAD")
    assert out.run.step_result("merge.merge")["alreadyUpToDate"] is True
    pub = out.run.step_result("merge.publish")
    assert pub["pushed"] is None and "already contains v2.6.6" in pub["skipped"]
    assert git(repos["fork"], "branch", "--list", "sync/*") == ""
    assert not (ctx.work_dir / "prs" / "sync__release-v2.6.6.json").exists()


def test_merge_dry_run_predicts_without_touching(ctx, repos):
    runner.start(ctx, "sync", {})
    ctx.run_id = None
    ctx.dry_run = True
    out = runner.start(ctx, "merge", {"lane": "release", "ref": "v2.6.6"})
    assert out.exit_code == 0, out.message
    res = out.run.step_result("merge.merge")
    paths = {c["path"]: c["strategy"] for c in res["predictedConflicts"]}
    assert paths.get("CHANGELOG.md") == "union"
    assert git(ctx.repo_dir, "branch", "--list", "sync/*") == ""


def core_upstream_rule(ledger, path="backend/backend/core.py"):
    """Register the fixture's customer core edit with an explicit path rule (upstream wins on conflict)."""
    pol = util.read_yaml(__import__("vra.data", fromlist=["path"]).path("defaults", "path-policy.yaml"))
    pol["rules"].insert(0, {"glob": path, "strategy": "upstream", "note": "reviewed: take upstream"})
    util.write_yaml(ledger.root / "path-policy.yaml", pol)


def test_merge_refuses_unregistered_fork_core_edit(ctx, repos, ledger):
    """The fork edited backend/backend/core.py (outside the fork lanes) without registering it. A conflict
    there stops before merging instead of taking upstream's side and dropping the customer change."""
    runner.start(ctx, "sync", {})
    ctx.run_id = None
    out = runner.start(ctx, "merge", {"lane": "release", "ref": "v2.6.6"})
    assert out.exit_code == errors.NEEDS_HUMAN
    assert "not registered: backend/backend/core.py" in out.message
    assert git(ctx.repo_dir, "branch", "--list", "sync/*") == ""      # nothing merged yet
    # registering it (core-patches.md) lets the merge proceed (and the conflict then goes to the agent)
    (ledger.root / "core-patches.md").write_text("- `backend/backend/core.py` customer core patch\n")
    again = runner.resume(ctx, out.run.run_id)
    assert again.exit_code == errors.NEEDS_HUMAN and "conflict" in again.message


def test_preview_lane_fast_forwards_preview_trunk(ctx, repos, ledger):
    core_upstream_rule(ledger)
    runner.start(ctx, "sync", {})
    ctx.run_id = None
    sha = git(ctx.repo_dir, "rev-parse", "vendor/vams-development")[:12]
    out = runner.start(ctx, "merge", {"lane": "preview", "ref": sha})
    # preview has the same conflicts; the core edit has an explicit (reviewed) upstream rule
    assert out.exit_code in (0, errors.NEEDS_HUMAN), out.message
    if out.exit_code == errors.NEEDS_HUMAN:
        pytest.fail(f"unexpected unresolved: {util.read_json(out.run.dir / 'conflicts.json')['unresolved']}")
    assert git(repos["fork"], "rev-parse", "preview/main") == git(ctx.repo_dir, "rev-parse", f"sync/preview-{sha}")


def test_push_guard_rejects_force(ctx, repos):
    g = gitops.ensure_repo(ctx)
    for bad in ("+refs/heads/main:refs/heads/main", "--force", "-f", ":refs/heads/main"):
        with pytest.raises(UsageError):
            g.push("origin", bad)
    with pytest.raises(UsageError):
        g.push("origin", "main", "--force-with-lease")


def test_glob_policy():
    pol = gitops.PathPolicy(util.read_yaml(__import__("vra.data", fromlist=["path"]).path("defaults", "path-policy.yaml")),
                            ["infra/lib/core-stack.ts"])
    assert pol.rule_for("web/src/custom/a/b.tsx")["strategy"] == "fork"
    assert pol.rule_for("web/package-lock.json")["strategy"] == "regenerate"
    assert pol.rule_for("documentation/docusaurus-site/docs/additional/revisions.md")["strategy"] == "union"
    assert pol.rule_for("infra/lib/core-stack.ts")["strategy"] == "agent"
    # no catch-all upstream rule: an unregistered core conflict goes to review
    assert pol.rule_for("backend/backend/handlers/assets/x.py")["strategy"] == "agent"
    assert not pol.registered("backend/backend/handlers/assets/x.py")
    assert pol.registered("web/src/custom/a/b.tsx") and pol.registered("infra/lib/core-stack.ts")
    assert pol.rule_for("web/src/visualizerPlugin/viewers/x/customViewer.tsx")["strategy"] == "fork"
