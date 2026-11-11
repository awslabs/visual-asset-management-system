# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Git operations for the customer fork: sync (ff-only vendor mirrors), merge with per-path policy,
conflicts.json hand-off, publish (plain push + PR via the githost adapter).

Rules enforced here: never force-push, never rebase, never rewrite history. Every push goes through
:meth:`Git.push`, which rejects force flags and ``+`` refspecs.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from vra import githost, util
from vra.errors import NeedsHuman, UsageError, CommandFailed, VraError
from vra.runner import workflow

RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
STRATEGIES = ("upstream", "fork", "union", "regenerate", "agent")
_FORCE_FLAGS = {"-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "-d"}


class Git:
    def __init__(self, ctx, repo: Path, log_path: Path | None = None):
        self.ctx = ctx
        self.repo = Path(repo)
        self.sh = ctx.shell(log_path)
        name = os.environ.get("VRA_GIT_NAME", "vra-bot")
        email = os.environ.get("VRA_GIT_EMAIL", "vra-bot@example.com")
        self._ident = ["-c", f"user.name={name}", "-c", f"user.email={email}"]

    def run(self, *args: str, check: bool = True, mutating: bool = False, input_text: str | None = None):
        return self.sh.run(["git", *self._ident, "-C", str(self.repo), *args], check=check, mutating=mutating,
                           input_text=input_text)

    def out(self, *args: str, check: bool = True) -> str:
        return self.run(*args, check=check).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False).returncode == 0

    def rev(self, ref: str) -> str | None:
        p = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        return p.stdout.strip() or None if p.returncode == 0 else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.ok("merge-base", "--is-ancestor", a, b)

    def push(self, remote: str, refspec: str, *extra: str):
        for a in (refspec, *extra):
            if a in _FORCE_FLAGS or a.startswith("--force") or a.startswith("+") or a.startswith(":"):
                raise UsageError(f"refusing non fast-forward / destructive push argument {a!r}")
        return self.run("push", remote, refspec, *extra, mutating=True)

    def show(self, rev: str, path: str) -> str | None:
        p = self.run("show", f"{rev}:{path}", check=False)
        return p.stdout if p.returncode == 0 else None

    def in_merge(self) -> bool:
        return (self.repo / ".git" / "MERGE_HEAD").exists() if (self.repo / ".git").is_dir() else \
            self.ok("rev-parse", "-q", "--verify", "MERGE_HEAD")

    def unmerged(self) -> list[str]:
        o = self.out("diff", "--name-only", "--diff-filter=U")
        return [x for x in o.splitlines() if x]

    def stages(self, path: str) -> list[int]:
        o = self.out("ls-files", "-u", "--", path)
        return sorted({int(line.split()[2]) for line in o.splitlines() if line.strip()})

    def dirty(self) -> bool:
        return bool(self.out("status", "--porcelain", "--untracked-files=no"))

    def current_branch(self) -> str | None:
        p = self.run("symbolic-ref", "--short", "-q", "HEAD", check=False)
        return p.stdout.strip() or None


# ============================================================ repo setup
def ensure_repo(ctx, log_path: Path | None = None) -> Git:
    proj = ctx.ledger.project
    repo = ctx.repo_dir
    sh = ctx.shell(log_path)
    if not (repo / ".git").exists():
        fork_url = proj["fork"]["url"]
        repo.parent.mkdir(parents=True, exist_ok=True)
        # clone is a local-only operation; it runs even under --dry-run (read of remote)
        sh.run(["git", "clone", "--origin", "origin", fork_url, str(repo)], mutating=False)
    g = Git(ctx, repo, log_path)
    up = proj["upstream"]["url"]
    cur = g.run("remote", "get-url", "upstream", check=False)
    if cur.returncode != 0:
        g.run("remote", "add", "upstream", up)
    elif cur.stdout.strip() != up:
        g.run("remote", "set-url", "upstream", up)
    return g


def sync_state_path(ctx) -> Path:
    return ctx.sync_state_path


def load_sync_state(ctx) -> dict:
    """Last seen upstream refs, shared by every machine (`vra watch` holds the ``watch`` lock while it uses them)."""
    p = sync_state_path(ctx)
    ctx.home.pull()
    if ctx.store is not None:  # the store is authoritative; a stale local copy never wins
        try:
            ctx.store.pull_prefix("sync-state.json")
        except Exception:  # noqa: BLE001
            pass
    return util.read_json(p) if p.exists() else {"lanes": {}}


def save_sync_state(ctx, st: dict) -> None:
    if ctx.dry_run:
        return
    util.write_json(sync_state_path(ctx), st)
    if ctx.store is not None:
        ctx.store.push_file(sync_state_path(ctx))
    ctx.home.publish("sync state")


def mark_processed(ctx, lane: str, ref: str, sha: str) -> None:
    st = load_sync_state(ctx)
    ln = st.setdefault("lanes", {}).setdefault(lane, {})
    ln["lastProcessed"] = ref
    ln["lastProcessedSha"] = sha
    ln["processedAt"] = util.now_iso()
    save_sync_state(ctx, st)


def release_tags(g: Git, ref: str) -> list[str]:
    o = g.out("tag", "--merged", ref, "--list", "v*")
    tags = [t for t in o.splitlines() if RELEASE_TAG_RE.match(t)]
    return sorted(tags, key=util.version_tuple)


# ============================================================ sync
def do_sync(ctx, run, prefix: str = "") -> dict:
    def _sync(sc):
        g = ensure_repo(ctx, sc.log_path)
        g.run("fetch", "upstream", "--prune", "--tags")
        g.run("fetch", "origin", "--prune")
        proj = ctx.ledger.project
        st = load_sync_state(ctx)
        out: dict[str, Any] = {"newRelease": None, "newPreviewCommit": None, "lanes": {}}
        for lane in ("release", "preview"):
            lcfg = proj["lanes"][lane]
            src = f"refs/remotes/upstream/{lcfg['source']}"
            sha = g.rev(src)
            if not sha:
                raise UsageError(f"upstream has no branch {lcfg['source']!r}")
            vb = lcfg["vendorBranch"]
            local_old = g.rev(f"refs/heads/{vb}")
            remote_old = g.rev(f"refs/remotes/origin/{vb}")
            for old, where in ((local_old, "local"), (remote_old, "origin")):
                if old and not g.is_ancestor(old, sha):
                    raise NeedsHuman(f"{where} {vb} ({old[:12]}) is not an ancestor of upstream {lcfg['source']} "
                                     f"({sha[:12]}); vendor mirrors are fast-forward only. Investigate before syncing.",
                                     {"lane": lane, "branch": vb})
            if local_old != sha:
                # compare-and-swap ref update (fast-forward verified above)
                g.run("update-ref", f"refs/heads/{vb}", sha, local_old or "0" * 40, mutating=True)
            pushed = False
            if proj["githost"].get("pushVendorBranches", True) and remote_old != sha:
                g.push("origin", f"refs/heads/{vb}:refs/heads/{vb}")
                pushed = True
            ln = st.setdefault("lanes", {}).setdefault(lane, {})
            ln["vendorSha"] = sha
            ln["seenAt"] = util.now_iso()
            info = {"vendorBranch": vb, "sha": sha, "advanced": local_old != sha, "pushed": pushed}
            if lane == "release":
                tags = release_tags(g, src)
                latest = tags[-1] if tags else None
                info["latestTag"] = latest
                last = ln.get("lastProcessed")
                if latest and (not last or util.version_tuple(latest) > util.version_tuple(last)):
                    out["newRelease"] = latest
            else:
                if sha != ln.get("lastProcessedSha"):
                    out["newPreviewCommit"] = sha
            out["lanes"][lane] = info
        save_sync_state(ctx, st)
        return out

    return run.step(f"{prefix}sync", _sync, "sync upstream")


@workflow("sync")
def wf_sync(ctx, run, args):
    return do_sync(ctx, run)


# ============================================================ path policy
def glob_to_regex(glob: str) -> re.Pattern:
    i, out = 0, ""
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out += r"(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += r".*"
            i += 2
        elif c == "*":
            out += r"[^/]*"
            i += 1
        elif c == "?":
            out += r"[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile(rf"^{out}$")


class PathPolicy:
    def __init__(self, policy: dict, core_patches: list[str] | None = None):
        self.default = policy.get("default", "agent")
        self.rules = [(r, glob_to_regex(r["glob"])) for r in policy.get("rules", [])]
        self.core = set(core_patches or [])
        self.unregistered = policy.get("unregisteredCoreEdits", "refuse")

    def rule_for(self, path: str) -> dict:
        if path in self.core:
            return {"glob": path, "strategy": "agent", "note": "listed in core-patches.md"}
        for rule, rx in self.rules:
            if rx.match(path):
                return rule
        return {"glob": "<default>", "strategy": self.default}

    def registered(self, path: str) -> bool:
        """A fork edit is registered when core-patches.md lists it or an explicit rule (not the default)
        decides it. A catch-all ``**/*`` rule counts too: it is an explicit, reviewed choice in the ledger."""
        return path in self.core or any(rx.match(path) for _, rx in self.rules)


def core_audit(ctx, prep: dict, sc) -> dict:
    """Before merging: list files the fork changed since its last upstream merge that no path rule and no
    core-patches.md entry covers. Unregistered edits stop the merge (NeedsHuman): resolving them by a default
    strategy would silently drop or keep a customer change nobody reviewed (``unregisteredCoreEdits: warn``
    in path-policy.yaml reports them instead). Only edits to existing files (modified / deleted / renamed)."""
    g = Git(ctx, ctx.repo_dir, sc.log_path)
    base = g.out("merge-base", prep["baseSha"], prep["refSha"], check=False).strip()
    if not base:
        return {"skipped": "no merge base between the fork trunk and the upstream ref"}
    changed = [p for p in g.out("diff", "--name-only", "--diff-filter=MDR", base, prep["baseSha"]).splitlines() if p]
    policy = PathPolicy(ctx.ledger.path_policy(), ctx.ledger.core_patches())
    unregistered = sorted(p for p in changed if not policy.registered(p))
    res = {"mergeBase": base, "forkEdits": len(changed), "unregistered": unregistered, "mode": policy.unregistered}
    if unregistered and policy.unregistered != "warn" and not ctx.dry_run:
        raise NeedsHuman(f"{len(unregistered)} fork edit(s) outside the fork lanes are not registered: "
                         f"{', '.join(unregistered[:10])}{' ...' if len(unregistered) > 10 else ''}. List each in "
                         f"core-patches.md (or give it a path-policy rule), then `vra merge --continue`",
                         {"unregistered": unregistered})
    return res


def _write_union(g: Git, path: str) -> bool:
    tmp = g.ctx.work_dir / "tmp" / "union"
    tmp.mkdir(parents=True, exist_ok=True)
    files = {}
    for n, name in ((1, "base"), (2, "ours"), (3, "theirs")):
        content = g.run("show", f":{n}:{path}", check=False)
        if content.returncode != 0:
            if n == 1:
                content_text = ""
            else:
                return False
        else:
            content_text = content.stdout
        f = tmp / f"{name}"
        f.write_text(content_text, encoding="utf-8")
        files[name] = f
    merged = g.sh.run(["git", "merge-file", "-p", "--union", str(files["ours"]), str(files["base"]),
                       str(files["theirs"])], check=False, mutating=False).stdout
    (g.repo / path).write_text(merged, encoding="utf-8")
    return True


def resolve_conflicts(ctx, g: Git, policy: PathPolicy) -> tuple[list[dict], list[dict]]:
    auto, unresolved = [], []
    for path in g.unmerged():
        rule = policy.rule_for(path)
        strat = rule["strategy"]
        stages = g.stages(path)
        ctype = "both-modified" if stages == [1, 2, 3] else ("add/add" if stages == [2, 3] else
                                                               ("deleted-by-fork" if 2 not in stages else
                                                                ("deleted-upstream" if 3 not in stages else "other")))
        rec = {"path": path, "strategy": strat, "glob": rule.get("glob"), "stages": stages, "conflictType": ctype}
        try:
            if strat == "upstream" or strat == "regenerate":
                if 3 not in stages:
                    g.run("rm", "-q", "--", path, mutating=True)
                    rec["action"] = "removed (deleted upstream)"
                else:
                    g.run("checkout", "--theirs", "--", path, mutating=True)
                    g.run("add", "--", path, mutating=True)
                    rec["action"] = "took upstream"
                if strat == "regenerate":
                    rec["regenerate"] = _regenerate(ctx, g, path, rule)
                auto.append(rec)
            elif strat == "fork":
                if 2 not in stages:
                    g.run("rm", "-q", "--", path, mutating=True)
                    rec["action"] = "removed (deleted in fork)"
                else:
                    g.run("checkout", "--ours", "--", path, mutating=True)
                    g.run("add", "--", path, mutating=True)
                    rec["action"] = "kept fork"
                auto.append(rec)
            elif strat == "union":
                if not ctx.dry_run and _write_union(g, path):
                    g.run("add", "--", path, mutating=True)
                    rec["action"] = "union merge"
                    auto.append(rec)
                else:
                    rec["reason"] = "union not possible (file deleted on one side)"
                    unresolved.append(rec)
            else:
                rec["reason"] = rule.get("note") or "path policy: agent"
                unresolved.append(rec)
        except CommandFailed as exc:
            rec["reason"] = f"auto-resolution failed: {exc.message}"
            unresolved.append(rec)
    return auto, unresolved


def _regenerate(ctx, g: Git, path: str, rule: dict) -> dict:
    cmd = rule.get("regenerate")
    if not cmd:
        return {"status": "needed", "note": "no regenerate command configured"}
    import shlex
    argv = shlex.split(cmd)
    if ctx.dry_run or not shutil.which(argv[0]):
        return {"status": "needed", "command": cmd, "note": "tool unavailable or dry-run; regenerate before release"}
    cwd = (g.repo / path).parent
    p = g.sh.run(argv, cwd=cwd, check=False)
    if p.returncode == 0:
        g.run("add", "--", path, mutating=True)
        return {"status": "done", "command": cmd}
    return {"status": "failed", "command": cmd, "note": "kept upstream lockfile; regenerate manually"}


_MARKER_RE = re.compile(r"^(<{7}|>{7})( |$)", re.MULTILINE)


def has_markers(path: Path) -> bool:
    try:
        return bool(_MARKER_RE.search(path.read_text(encoding="utf-8", errors="replace")))
    except FileNotFoundError:
        return False


# ============================================================ merge
def sanitize_ref(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", ref).strip("-")


def default_ref(ctx, g: Git, lane: str) -> str:
    lcfg = ctx.ledger.project["lanes"][lane]
    if lane == "release":
        tags = release_tags(g, f"refs/heads/{lcfg['vendorBranch']}")
        if not tags:
            raise UsageError("no release tag found on the vendor branch; run `vra sync` first")
        return tags[-1]
    sha = g.rev(f"refs/heads/{lcfg['vendorBranch']}")
    if not sha:
        raise UsageError("vendor development branch missing; run `vra sync` first")
    return sha[:12]


def merge_branch_name(lane: str, ref: str) -> str:
    return f"sync/{lane}-{sanitize_ref(ref)}"


def do_merge(ctx, run, lane: str, ref: str | None = None, prefix: str = "") -> dict:
    if lane not in ("release", "preview"):
        raise UsageError("--lane must be release or preview")
    proj = ctx.ledger.project
    lcfg = proj["lanes"][lane]

    def _prepare(sc):
        g = ensure_repo(ctx, sc.log_path)
        r = ref or default_ref(ctx, g, lane)
        ref_sha = g.rev(r) or g.rev(f"refs/tags/{r}") or g.rev(f"refs/remotes/upstream/{r}")
        if not ref_sha:
            raise UsageError(f"cannot resolve ref {r!r}")
        if lane == "release" and RELEASE_TAG_RE.match(r):
            vb = g.rev(f"refs/heads/{lcfg['vendorBranch']}")
            if vb and not g.is_ancestor(ref_sha, vb):
                raise NeedsHuman(f"release tag {r} is not on {lcfg['vendorBranch']}")
        target = lcfg.get("targetBranch") or proj["trunk"]
        base_ref = f"refs/remotes/origin/{target}" if g.rev(f"refs/remotes/origin/{target}") else None
        if base_ref is None:
            base_ref = f"refs/remotes/origin/{proj['trunk']}"
            if not g.rev(base_ref):
                raise UsageError(f"fork has no branch {target!r} or trunk {proj['trunk']!r}")
        branch = merge_branch_name(lane, r)
        return {"lane": lane, "ref": r, "refSha": ref_sha, "branch": branch, "target": target,
                "baseRef": base_ref, "baseSha": g.rev(base_ref)}

    prep = run.step(f"{prefix}merge.prepare", _prepare, f"prepare {lane} merge")
    run.step(f"{prefix}merge.core-audit", lambda sc: core_audit(ctx, prep, sc), "audit fork core edits",
             revalidate=True)

    def _merge(sc):
        g = Git(ctx, ctx.repo_dir, sc.log_path)
        branch = prep["branch"]
        if ctx.dry_run:
            return _dry_run_merge(ctx, g, prep)
        cur = g.current_branch()
        if g.in_merge():
            if cur != branch:
                raise NeedsHuman(f"a merge is in progress on {cur}, not {branch}; finish or abort it first")
            return _finish_merge(ctx, run, g, prep, sc)
        if g.rev(f"refs/heads/{branch}"):
            if cur != branch:
                if g.dirty():
                    raise NeedsHuman("fork working tree is dirty; commit or stash before merging")
                g.run("switch", branch, mutating=True)
        else:
            if g.dirty():
                raise NeedsHuman("fork working tree is dirty; commit or stash before merging")
            g.run("switch", "-c", branch, prep["baseSha"], mutating=True)
        head = g.rev("HEAD")
        if g.is_ancestor(prep["refSha"], head):
            return {"branch": branch, "head": head, "merged": True, "alreadyUpToDate": True, "autoResolved": [],
                    "unresolved": []}
        msg = f"Merge {prep['ref']} ({lane} lane) into {branch}\n\nvra run {run.run_id}"
        p = g.run("merge", "--no-ff", "--no-commit", "-m", msg, prep["refSha"], check=False, mutating=True)
        if p.returncode != 0 and not g.unmerged() and not g.in_merge():
            raise CommandFailed(f"git merge failed: {(p.stderr or p.stdout).strip()[:500]}", p.returncode)
        policy = PathPolicy(ctx.ledger.path_policy(), ctx.ledger.core_patches())
        auto, unresolved = resolve_conflicts(ctx, g, policy)
        sc.step.setdefault("autoResolved", auto)
        run.state.setdefault("merge", {})["autoResolved"] = auto
        run.save()
        if unresolved:
            write_conflicts(ctx, run, prep, auto, unresolved)
            raise NeedsHuman(f"{len(unresolved)} conflict(s) need resolution; see conflicts.json",
                             {"conflicts": str(run.dir / "conflicts.json")})
        return _commit_merge(ctx, run, g, prep, auto)

    merged = run.step(f"{prefix}merge.merge", _merge, f"merge {lane}")

    def _publish(sc):
        g = Git(ctx, ctx.repo_dir, sc.log_path)
        branch = prep["branch"]
        if merged.get("alreadyUpToDate") and prep.get("baseSha") and g.is_ancestor(prep["refSha"], prep["baseSha"]):
            # the target already contains this release: nothing to push, no pull request (live test:
            # the daily watch re-merged v2.6.67 and failed pushing an empty sync branch)
            if not ctx.dry_run:
                mark_processed(ctx, lane, prep["ref"], prep["refSha"])
            return {"skipped": f"{prep['target']} already contains {prep['ref']}", "pushed": None}
        if ctx.dry_run:
            return {"dryRun": True, "wouldPush": branch, "target": prep["target"]}
        g.push("origin", f"refs/heads/{branch}:refs/heads/{branch}")
        out = {"pushed": branch}
        if lane == "preview" and lcfg.get("autoMerge", True):
            # fast-forward the preview trunk to the merge result (never forced)
            g.run("fetch", "origin", "--prune")
            tgt = g.rev(f"refs/remotes/origin/{prep['target']}")
            head = g.rev(f"refs/heads/{branch}")
            if tgt and not g.is_ancestor(tgt, head):
                raise NeedsHuman(f"origin/{prep['target']} moved and is not an ancestor of {branch}; re-run merge")
            g.push("origin", f"refs/heads/{branch}:refs/heads/{prep['target']}")
            out["fastForwarded"] = prep["target"]
        else:
            host = githost.make(ctx)
            body = pr_body(ctx, run, prep, merged)
            out["pr"] = host.ensure_pr(branch, prep["target"], f"Sync VAMS {prep['ref']} ({lane} lane)", body)
        mark_processed(ctx, lane, prep["ref"], prep["refSha"])
        return out

    pub = run.step(f"{prefix}merge.publish", _publish, "push + pull request")
    return {"prepare": prep, "merge": merged, "publish": pub, "branch": prep["branch"]}


def _dry_run_merge(ctx, g: Git, prep: dict) -> dict:
    """Predict conflicts with `git merge-tree --write-tree` without touching the work tree."""
    p = g.run("merge-tree", "--write-tree", "--name-only", "--no-messages", prep["baseSha"], prep["refSha"],
              check=False)
    lines = [x for x in p.stdout.splitlines() if x.strip()]
    conflicted = lines[1:] if p.returncode == 1 else []
    policy = PathPolicy(ctx.ledger.path_policy(), ctx.ledger.core_patches())
    plan = [{"path": c, "strategy": policy.rule_for(c)["strategy"]} for c in conflicted]
    return {"dryRun": True, "branch": prep["branch"], "predictedConflicts": plan,
            "wouldNeedAgent": [x["path"] for x in plan if x["strategy"] == "agent"]}


def _commit_merge(ctx, run, g: Git, prep: dict, auto: list) -> dict:
    g.run("commit", "--no-edit", "--quiet", mutating=True)
    head = g.rev("HEAD")
    return {"branch": prep["branch"], "head": head, "merged": True, "autoResolved": auto, "unresolved": [],
            "regenerateNeeded": [a["path"] for a in auto if (a.get("regenerate") or {}).get("status") != "done"
                                 and a["strategy"] == "regenerate"]}


def _finish_merge(ctx, run, g: Git, prep: dict, sc) -> dict:
    """Called on resume / merge --continue while MERGE_HEAD exists."""
    remaining = g.unmerged()
    prev = []
    cpath = run.dir / "conflicts.json"
    if cpath.exists():
        prev = [c["path"] for c in util.read_json(cpath).get("unresolved", [])]
    marked = [p for p in prev if has_markers(g.repo / p)]
    if remaining or marked:
        unresolved = [{"path": p, "strategy": "agent", "reason": "still unmerged" if p in remaining else
                       "conflict markers left in file"} for p in sorted(set(remaining) | set(marked))]
        write_conflicts(ctx, run, prep, run.state.get("merge", {}).get("autoResolved", []), unresolved)
        raise NeedsHuman(f"{len(unresolved)} conflict(s) still unresolved", {"conflicts": str(cpath)})
    return _commit_merge(ctx, run, g, prep, run.state.get("merge", {}).get("autoResolved", []))


def write_conflicts(ctx, run, prep: dict, auto: list, unresolved: list) -> Path:
    doc = {"runId": run.run_id, "lane": prep["lane"], "ref": prep["ref"], "refSha": prep["refSha"],
           "branch": prep["branch"], "target": prep["target"], "baseSha": prep["baseSha"],
           "repo": str(ctx.repo_dir), "createdAt": util.now_iso(), "unresolved": unresolved,
           "autoResolved": auto,
           "instructions": [
               f"cd {ctx.repo_dir}  (branch {prep['branch']}, merge in progress)",
               "Resolve each unresolved path; keep customer behaviour inside the fork lanes and upstream behaviour "
               "elsewhere; record any lasting core edit in the ledger's core-patches.md.",
               "Run the affected unit suites, then `git add <path>` for each file (do not commit).",
               f"Then run `vra merge --continue --run-id {run.run_id}` (or `vra resume {run.run_id}`).",
               f"To give up: `vra merge --abort --run-id {run.run_id}`."]}
    p = run.dir / "conflicts.json"
    util.write_json(p, doc)
    return p


def pr_body(ctx, run, prep: dict, merged: dict) -> str:
    lines = [f"Automated sync of upstream VAMS `{prep['ref']}` ({prep['lane']} lane) into `{prep['target']}`.", "",
             f"- upstream commit: `{prep['refSha']}`", f"- merge branch: `{prep['branch']}`",
             f"- vra run: `{run.run_id}`", ""]
    auto = merged.get("autoResolved") or []
    if auto:
        lines += ["Conflicts auto-resolved by path policy:", ""]
        lines += [f"- `{a['path']}`: {a['strategy']} ({a.get('action', '')})" for a in auto]
        lines.append("")
    regen = merged.get("regenerateNeeded") or []
    if regen:
        lines += ["Lockfiles to regenerate before release: " + ", ".join(f"`{r}`" for r in regen), ""]
    lines += ["Merge with a merge commit (not squash) so the tested build commit stays an ancestor of the trunk; "
              "`vra promote` checks this."]
    return "\n".join(lines)


def abort_merge(ctx, run) -> dict:
    g = Git(ctx, ctx.repo_dir, run.log_path("merge.abort"))
    aborted = False
    if g.in_merge():
        g.run("merge", "--abort", mutating=True)
        aborted = True
    for s in run.state["steps"]:
        if s["status"] in ("blocked", "running", "pending"):
            s["status"] = "skipped"
            s["detail"] = "merge aborted"
    run.set_status("FAILED", "merge aborted by operator")
    return {"aborted": aborted, "runId": run.run_id}


@workflow("merge")
def wf_merge(ctx, run, args):
    return do_merge(ctx, run, args["lane"], args.get("ref"))


# ============================================================ helpers used by build/promote
def resolve_commit(g: Git, ref: str) -> str:
    # remote branch first (the local clone's branches can be stale), then tags, then a sha / local ref
    for cand in (f"refs/remotes/origin/{ref}", f"refs/tags/{ref}", ref):
        sha = g.rev(cand)
        if sha:
            return sha
    raise UsageError(f"cannot resolve {ref!r} in the fork")


def on_trunk(ctx, g: Git, commit: str) -> bool:
    trunk = ctx.ledger.project["trunk"]
    for ref in (f"refs/remotes/origin/{trunk}", f"refs/heads/{trunk}"):
        if g.rev(ref) and g.is_ancestor(commit, ref):
            return True
    return False


def archive_to(g: Git, commit: str, dest: Path, paths: list[str] | None = None) -> None:
    """Extract ``commit`` (optionally a subset of paths) into ``dest`` without touching the work tree."""
    import io
    import subprocess
    import tarfile
    dest.mkdir(parents=True, exist_ok=True)
    argv = ["git", "-C", str(g.repo), "archive", "--format=tar", commit] + (paths or [])
    proc = subprocess.run(argv, capture_output=True)
    if proc.returncode != 0:
        raise VraError(f"git archive failed: {proc.stderr.decode(errors='replace')[:300]}")
    # the archive is `git archive` of the fork's own commit, and filter="data" refuses absolute paths, ".."
    # members and links that leave dest
    # nosemgrep: trailofbits.python.tarfile-extractall-traversal.tarfile-extractall-traversal
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        tf.extractall(dest, filter="data")
