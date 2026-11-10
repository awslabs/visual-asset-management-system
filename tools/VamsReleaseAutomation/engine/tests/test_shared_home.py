# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shared home folder: one ledger + state for every machine, synced by git (here: a local bare remote)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vra import cli, errors, home as home_mod, report, runner, util
from vra.context import Context
from vra.errors import NeedsHuman
from vra.home import Contention, Home
from vra.ledger import Ledger
from vra.runner import workflow
from vra.state import FileLock, FolderHistory, LeaseKeeper, Run, StaleRun

from conftest import FakeAws, env_doc, git, write_ledger


@pytest.fixture(autouse=True)
def _xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("VRA_HOME", raising=False)
    monkeypatch.delenv("VRA_LEDGER", raising=False)


def _git_home(tmp_path: Path, repos) -> tuple[Home, Home, Path]:
    """Machine A creates a home synced by git; machine B joins it by URL."""
    remote = tmp_path / "home-remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    a = Home.create(tmp_path / "machine-a" / "home", {"type": "git"}, lease_seconds=60)
    write_ledger(a.ledger_dir, str(repos["fork"]), str(repos["upstream"]))
    git(a.root, "init", "-q", "-b", "main")
    git(a.root, "remote", "add", "origin", str(remote))
    git(a.root, "add", "-A")
    git(a.root, "commit", "-q", "-m", "home")
    git(a.root, "push", "-q", "-u", "origin", "main")
    res = home_mod.join(str(remote), str(tmp_path / "machine-b" / "home"))
    assert res["cloned"] and res["validation"]["ok"], res
    b = Home.open(res["home"])
    git(b.root, "config", "user.email", "b@example.com")
    git(b.root, "config", "user.name", "B")
    git(a.root, "config", "user.email", "a@example.com")
    git(a.root, "config", "user.name", "A")
    a.pull()
    return a, b, remote


def _ctx(h: Home, tmp_path: Path, name: str) -> Context:
    c = Context(ledger=Ledger(h.ledger_dir), work_dir=tmp_path / f"work-{name}", initiator="op@example.com", home=h)
    c.aws = FakeAws(c.ledger)
    return c


def test_setup_creates_home_and_cli_finds_it(tmp_path, repos, monkeypatch):
    ans = tmp_path / "answers.yaml"
    util.write_yaml(ans, {"home": {"sync": {"type": "none"}}, "project": {"name": "x"}, "fork": {"url": str(repos["fork"])},
                          "githost": {"type": "local"},
                          "envs": {"dev": {"account": "111111111111", "region": "eusc-de-east-1",
                                           "liveTest": {"auth": {"type": "none"}}}}})
    h = tmp_path / "vams-release-home"
    rc = cli.main(["--json", "--non-interactive", "--home", str(h), "setup", "--answers", str(ans),
                   "--skip-preflight"])
    assert rc == 0
    assert (h / "vra-home.yaml").exists() and (h / "ledger/vra.yaml").exists() and (h / "state/locks").is_dir()
    env = util.read_yaml(h / "ledger/envs/dev/env.yaml")
    assert env["partition"] == "aws-eusc" and env["templateBase"] == "eusovereign" and env["tier"] == "nonprod"
    assert home_mod.read_pointer() == h.resolve()
    # the pointer written by setup is what a later command without flags uses
    monkeypatch.chdir(tmp_path)
    resolved, work = home_mod.resolve(None, None, None)
    assert resolved.root == h.resolve() and "xdg-cache" in str(work)


def test_second_machine_sees_locks_runs_and_history_of_the_first(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    run = Run.create(ca.state_dir, "x", {}, "op", run_id="r-a", home=a)
    keeper = LeaseKeeper(ca.backend.lock, "token-a")
    keeper.acquire("dev", "r-a", env="dev")
    ca.ledger.append_history("dev", {"action": "deploy", "build": "b1", "result": "SUCCEEDED", "runId": "r-a"})
    # machine B: the lock, the run and the history arrive by pull
    with pytest.raises(NeedsHuman, match="locked by run r-a"):
        cb.backend.lock.acquire("dev", "r-b", token="token-b")
    assert Run.load(cb.state_dir, "r-a", home=b).state["runId"] == run.run_id
    rec = cb.ledger.history("dev")[0]
    assert rec["seq"] == 1 and rec["build"] == "b1" and rec["ledger"]["commit"]
    keeper.release("dev", "r-a")
    cb.backend.lock.acquire("dev", "r-b", token="token-b")
    a.pull()                                       # A sees B's lock after a pull
    assert FileLock(a).holder("dev")["runId"] == "r-b"


def test_standalone_test_run_waits_for_the_env_lock_of_another_machines_deploy(tmp_path, repos):
    """Live test: `vra test --env dev` on machine B ran (and recorded a pass for dev's current build) while machine A
    held dev's lock mid-deploy. A test run takes the env lock like every other env workflow."""
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    Run.create(ca.state_dir, "deploy", {}, "op", run_id="r-deploy-a", home=a)
    keeper = LeaseKeeper(ca.backend.lock, "token-a")
    keeper.acquire("dev", "r-deploy-a", env="dev")
    out = runner.start(cb, "test", {"env": "dev", "suites": ["live-cli"]}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "locked by run r-deploy-a" in out.message
    assert not [r for r in cb.ledger.history("dev") if r.get("action") == "test"]   # nothing recorded
    keeper.release("dev", "r-deploy-a")


def test_pulls_and_pushes_of_one_machine_never_interleave(tmp_path, repos, monkeypatch):
    """Live test: a second vra process on machine A pulled while the deploy's heartbeat pushed, and failed on
    .git/index.lock. Pull now holds the same machine mutex as publish; the mutex is reentrant per thread and
    exclusive across threads (the heartbeat) and processes."""
    import threading
    import time

    a, _b, _ = _git_home(tmp_path, repos)
    inside, overlaps = [], []
    real_pull = a.sync.pull

    def slow_pull():
        if inside:
            overlaps.append("pull during another git operation")
        inside.append(1)
        time.sleep(0.2)
        real_pull()
        inside.pop()
    monkeypatch.setattr(a.sync, "pull", slow_pull)
    with a.mutex("sync"):
        with a.mutex("sync"):           # reentrant in one thread
            pass
        t = threading.Thread(target=a.pull)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()             # another thread waits for the holder
    t.join(5)
    threads = [threading.Thread(target=a.pull) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not overlaps


def test_rejected_push_is_contention_and_drops_the_local_lock(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    # both machines write the same lock file without having seen the other's: the second push loses
    la, lb = FileLock(a), FileLock(b)
    la.acquire("prod", "run-a", token="ta")
    b.sync.pull = lambda: None  # B does not pull before writing (simulates the race window)
    with pytest.raises(NeedsHuman):
        lb.acquire("prod", "run-b", token="tb")
    del b.sync.pull
    b.pull()
    assert lb.holder("prod")["runId"] == "run-a"  # B's attempt was dropped, A's lock is what everyone sees


def test_stale_run_state_is_never_written_over(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    ra = Run.create(a.runs_dir.parent, "x", {}, "op", run_id="r1", home=a)
    rb = Run.load(b.state_dir, "r1", home=b)
    ra.set_status("SUCCEEDED")                       # A finishes the run and pushes
    rb.state["message"] = "B's stale view"
    with pytest.raises(StaleRun):
        b.sync.pull = lambda: None                    # B writes without pulling first
        rb.save()
    del b.sync.pull
    b.pull()
    assert util.read_json(b.runs_dir / "r1/state.json")["status"] == "SUCCEEDED"
    # same machine, two processes: the older copy is refused too
    r1 = Run.load(a.state_dir, "r1", home=a)
    r2 = Run.load(a.state_dir, "r1", home=a)
    r1.save()
    with pytest.raises(StaleRun):
        r2.save()


def test_explicit_run_id_never_overwrites_another_machines_run(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    Run.create(a.state_dir, "build", {}, "op", run_id="ci-42", home=a)
    with pytest.raises(errors.UsageError, match="already exists"):
        Run.create(b.state_dir, "build", {}, "op", run_id="ci-42", home=b)


@workflow("_test_dirty_crash")
def _wf_dirty(ctx, run, args):
    with runner.env_lock(ctx, run, "dev"):
        runner.mark_dirty(run, "dev", "cdk deploy")
        raise errors.VraError("host died mid-deploy")


def test_dirty_lock_survives_expiry_and_needs_takeover_from_another_machine(tmp_path, repos, monkeypatch):
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    out = runner.start(ca, "_test_dirty_crash", {})
    rid = out.run.run_id
    held = FileLock(a).holder("dev")
    assert held["dirty"] == "cdk deploy" and held["held"]
    # long after the lease: another run is still refused
    monkeypatch.setattr("vra.state.time.time", lambda: held["expiresAt"] + 10_000)
    with pytest.raises(NeedsHuman, match="never released by expiry"):
        FileLock(b).acquire("dev", "other-run", token="x")
    # the same run from machine B: only with --takeover
    res = runner.resume(cb, rid)
    assert res.exit_code == errors.NEEDS_HUMAN and "--takeover" in res.message
    cb.takeover = True
    # live test: the human authorization of the takeover (here an approval record) was never recorded anywhere
    cb.human_authorizations = [{"action": "takeover", "by": "op-b@example.com", "how": "approval",
                                "approval": "approvals/dev/human_takeover.yaml", "approver": "lead@example.com"}]
    res = runner.resume(cb, rid)  # the workflow fails again, but it ran: the takeover was accepted and recorded
    st = Run.load(cb.state_dir, rid, home=b).state
    assert any(t["lock"] == "dev" for t in st["takeovers"])
    rec = next(h for h in cb.ledger.history("dev") if h["action"] == "takeover")
    assert rec["authorization"][0]["approver"] == "lead@example.com"
    assert st["humanAuthorizations"][0]["approval"] == "approvals/dev/human_takeover.yaml"


def test_resume_refuses_a_run_live_on_another_machine(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    run = Run.create(ca.state_dir, "_test_dirty_crash", {}, "op", run_id="live-1", home=a)
    run.set_status("NEEDS_HUMAN")
    LeaseKeeper(ca.backend.lock, "ta").acquire("run-live-1", "live-1")   # A still holds the run lease
    cb.takeover = True
    with pytest.raises(NeedsHuman, match="still live"):
        runner.resume(cb, "live-1")


def test_heartbeat_renews_and_a_lost_lock_is_reported(tmp_path):
    h = Home.create(tmp_path / "h", lease_seconds=60)
    now = [1000.0]
    lock = FileLock(h, clock=lambda: now[0])
    k = LeaseKeeper(lock, "tok", interval=3600)
    k.acquire("dev", "r1", env="dev")
    now[0] = 1050.0
    k.beat()
    assert lock.holder("dev")["expiresAt"] == 1110.0
    lock.release("dev", "r1", force=True)
    k.beat()
    assert k.lost == ["dev"]
    k.stop()


def test_history_sequence_is_per_env_and_write_once(tmp_path):
    h = Home.create(tmp_path / "h")
    hist = FolderHistory(h)
    r1 = hist.append("dev", {"action": "deploy", "ts": "2026-11-03T10:00:00Z", "runId": "a"})
    r2 = hist.append("dev", {"action": "test", "ts": "2026-11-03T10:00:00Z", "runId": "a"})  # same second
    r3 = hist.append("test", {"action": "deploy", "ts": "2026-11-03T09:00:00Z", "runId": "b"})
    assert (r1["seq"], r2["seq"], r3["seq"]) == (1, 2, 1)
    assert [r["action"] for r in hist.read("dev")] == ["deploy", "test"]  # write order, not clock or hash
    assert sorted(p.name for p in hist.files("dev")) == ["000000000001-a.json", "000000000002-a.json"]


def test_history_verify_reports_edits_gaps_and_legacy_files(tmp_path, repos):
    h = Home.create(tmp_path / "h")
    write_ledger(h.ledger_dir, str(repos["fork"]), str(repos["upstream"]))
    c = _ctx(h, tmp_path, "v")
    c.ledger.append_history("dev", {"action": "deploy", "build": "b1", "result": "SUCCEEDED", "runId": "r"})
    c.ledger.append_history("dev", {"action": "test", "build": "b1", "result": "SUCCEEDED", "runId": "r"})
    assert report.history_verify(c, "dev")["ok"]
    f = FolderHistory(h).files("dev")[0]
    doc = util.read_json(f)
    doc["result"] = "FAILED"            # an edit after the fact
    util.write_json(f, doc)
    (c.ledger.root / "envs/dev/history.jsonl").write_text('{"action": "deploy"}\n')
    res = report.history_verify(c, "dev")
    assert not res["ok"]
    assert any("edited after it was written" in p for p in res["problems"])
    assert any("history.jsonl" in p for p in res["problems"])


def test_history_verify_flags_records_not_pushed(tmp_path, repos):
    a, _b, _ = _git_home(tmp_path, repos)
    c = _ctx(a, tmp_path, "a")
    a.sync.push = lambda message, paths=None: False  # the push never happened
    c.ledger.append_history("dev", {"action": "deploy", "runId": "r", "result": "SUCCEEDED"})
    res = report.history_verify(c, "dev")
    assert any("only on this machine" in p for p in res["problems"])


def test_status_all_lists_locks_and_runs_of_every_machine(tmp_path, repos):
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    Run.create(ca.state_dir, "deploy", {}, "op", run_id="from-a", env="dev", home=a)
    FileLock(a).acquire("dev", "from-a", token="t")
    res = report.status_all(cb)
    assert [l["runId"] for l in res["locks"]] == ["from-a"] and res["locks"][0]["live"]
    assert "from-a" in [r["runId"] for r in res["activeRuns"]]


def test_gated_commands_need_a_clean_home_at_the_remote_head(tmp_path, repos):
    a, _b, _ = _git_home(tmp_path, repos)
    assert a.sync.gate_check()["head"]
    (a.ledger_dir / "envs/prod/env.yaml").write_text((a.ledger_dir / "envs/prod/env.yaml").read_text() + "\n# x\n")
    with pytest.raises(NeedsHuman, match="uncommitted ledger changes"):
        a.sync.gate_check()
    git(a.root, "commit", "-q", "-am", "local only")
    with pytest.raises(NeedsHuman, match="only runs on the pushed ledger"):
        a.sync.gate_check()


def test_approval_is_consumed_once_across_machines(tmp_path, repos):
    from vra import approvals
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    rec = {"env": "test", "sha256": "f" * 64, "path": "x", "action": "promote", "build": "b1"}
    ra = Run.create(ca.state_dir, "promote", {}, "op", run_id="ra", home=a)
    ra.backend = ca.backend
    assert approvals.claim(ra, rec)["runId"] == "ra"
    rb = Run.create(cb.state_dir, "promote", {}, "op", run_id="rb", home=b)
    rb.backend = cb.backend
    with pytest.raises(NeedsHuman, match="already used by run ra"):
        approvals.claim(rb, rec)


def test_migrate_from_a_0_3_ledger(tmp_path, repos):
    old = tmp_path / "old-ledger"
    write_ledger(old, str(repos["fork"]), str(repos["upstream"]))
    util.append_jsonl(old / "envs/dev/history.jsonl", {"ts": "2026-10-01T00:00:00Z", "id": "b", "action": "deploy",
                                                        "runId": "r2", "result": "SUCCEEDED"})
    util.append_jsonl(old / "envs/dev/history.jsonl", {"ts": "2026-09-01T00:00:00Z", "id": "a", "action": "deploy",
                                                        "runId": "r1", "result": "SUCCEEDED"})
    work = tmp_path / "old-work"
    util.write_json(work / "runs/r1/state.json", {"runId": "r1", "status": "SUCCEEDED", "steps": []})
    util.write_json(work / "sync-state.json", {"lanes": {"release": {"lastSeen": "v2.6.6"}}})
    res = home_mod.migrate(old, work, tmp_path / "new-home")
    h = Home.open(res["created"])
    recs = FolderHistory(h).read("dev")
    assert [(r["seq"], r["runId"]) for r in recs] == [(1, "r1"), (2, "r2")]   # ordered by time, renumbered
    assert not (h.ledger_dir / "envs/dev/history.jsonl").exists()
    assert (h.runs_dir / "r1/state.json").exists() and h.sync_state_path.exists()
    assert Ledger(h.ledger_dir).validate()["ok"]


def test_migrate_refuses_while_the_old_work_dir_holds_a_lock(tmp_path, repos):
    old = tmp_path / "old-ledger"
    write_ledger(old, str(repos["fork"]), str(repos["upstream"]))
    util.write_json(tmp_path / "old-work/locks/prod.lock", {"runId": "r9", "held": True})
    with pytest.raises(NeedsHuman, match="holds lock"):
        home_mod.migrate(old, tmp_path / "old-work", tmp_path / "new-home")
    assert not (tmp_path / "new-home").exists()


def test_cli_home_sync_and_status_all(tmp_path, repos, capsys):
    a, _b, _ = _git_home(tmp_path, repos)
    assert cli.main(["--json", "--home", str(a.root), "home", "sync"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["sync"] == "git"
    assert cli.main(["--json", "--home", str(a.root), "status", "--all"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["backend"] == "folder" and out["result"]["home"]["sync"] == "git"


def test_push_rejection_with_command_sync_is_contention(tmp_path):
    h = Home.create(tmp_path / "h", {"type": "command", "pull": "true", "push": "false"})
    with pytest.raises(Contention):
        h.publish("x")
    lock = FileLock(h)
    with pytest.raises(NeedsHuman):
        lock.acquire("dev", "r1", token="t")  # every attempt is rejected: never reported as acquired
    assert lock.holder("dev") is None


def test_engine_pushes_only_state_and_never_ledger_changes(tmp_path, repos, monkeypatch):
    a, _b, _ = _git_home(tmp_path, repos)
    env_yaml = a.ledger_dir / "envs/dev/env.yaml"
    env_yaml.write_text(env_yaml.read_text() + "\n# local edit\n")
    FileLock(a).acquire("dev", "r1", token="t")          # publishes the lock
    status = subprocess.run(["git", "-C", str(a.root), "status", "--porcelain"], capture_output=True,
                            text=True).stdout
    assert "ledger/envs/dev/env.yaml" in status and "state/locks" not in status  # lock pushed, edit left alone
    # in CI, fork code ran in this job: a changed ledger stops the publish instead of riding along
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(NeedsHuman, match="outside the engine's scope"):
        FileLock(a).acquire("test", "r2", token="t2")


def test_a_ci_build_is_not_refused_for_its_own_unpushed_bundle(tmp_path, repos, monkeypatch):
    """Live test: `vra build` writes artifacts/<buildId>/ into the home; with sync.pushArtifacts off that is
    outside the push scope, so the first checkpoint push of a CodeBuild build stopped with "changes outside the
    engine's scope" (exit 20). The engine's own bundle is never pushed and never refused; the ledger still is."""
    a, _b, _ = _git_home(tmp_path, repos)
    monkeypatch.setenv("CODEBUILD_BUILD_ID", "vra-build:1")
    bundle = a.root / "artifacts/cust-v1-1/cdk.out/dev/AwsSolutions-NagReport.csv"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("rule,status\n")
    FileLock(a).acquire("dev", "r1", token="t")          # publishes the lock, not the bundle
    tracked = subprocess.run(["git", "-C", str(a.root), "ls-files", "artifacts"], capture_output=True,
                             text=True).stdout
    assert "cust-v1-1" not in tracked and bundle.exists()
    env_yaml = a.ledger_dir / "envs/dev/env.yaml"
    env_yaml.write_text(env_yaml.read_text() + "\n# fork code edit\n")
    with pytest.raises(NeedsHuman, match="outside the engine's scope"):
        FileLock(a).acquire("test", "r2", token="t2")


def test_a_push_the_git_host_refuses_for_a_moment_is_retried_with_its_error_kept(tmp_path, repos, monkeypatch):
    """Live test: GitHub refused a push right after the previous one (a re-pull showed nothing new); three
    instant tries ended `vra deploy` in CodeBuild with exit 20 and git's error thrown away. The push backs off and
    retries, and a final failure names git's error."""
    from vra import home as home_mod
    a, _b, _ = _git_home(tmp_path, repos)
    real = home_mod._git
    fails = {"n": 2}
    sleeps = []

    def flaky(root, *args, **kw):
        if args and args[0] == "push" and fails["n"] > 0:
            fails["n"] -= 1
            return subprocess.CompletedProcess(["git", *args], 1, "", "remote: cannot lock ref 'refs/heads/main'")
        return real(root, *args, **kw)

    monkeypatch.setattr(home_mod, "_git", flaky)
    monkeypatch.setattr(home_mod, "_push_sleep", sleeps.append)
    FileLock(a).acquire("dev", "r1", token="t")          # published after two refusals
    assert fails["n"] == 0 and sleeps == [1, 2]
    fails["n"] = 99
    (a.root / "state/runs/r9").mkdir(parents=True)
    (a.root / "state/runs/r9/state.json").write_text("{}")
    with pytest.raises(Contention, match="cannot lock ref"):
        a.publish("run r9")


def test_a_push_racing_a_busy_writer_is_replayed_until_it_wins(tmp_path, repos, monkeypatch):
    """Live test: the watch job and the dev test job published the home about once a second each; the dev
    test's push lost six times in a row (each try slept 1-8 s before pushing, the other writer pushed meanwhile)
    and the Test-dev action ended NEEDS_HUMAN. A push that loses to new remote work is replayed and retried at
    once; the backoff is only for a host that refuses with nothing new upstream."""
    from vra import home as home_mod
    a, b, _ = _git_home(tmp_path, repos)
    real = home_mod._git
    race = {"n": 9}
    sleeps = []

    def busy_writer(root, *args, **kw):
        if args and args[0] == "push" and Path(root) == a.root and race["n"] > 0:
            race["n"] -= 1                                   # the other job pushes first, every time
            (b.root / f"state/runs/w{race['n']}").mkdir(parents=True)
            (b.root / f"state/runs/w{race['n']}/state.json").write_text("{}")
            real(b.root, "add", "-A", "--", "state")
            real(b.root, "commit", "-q", "-m", f"writer {race['n']}")
            real(b.root, "push", "-q")
        return real(root, *args, **kw)

    monkeypatch.setattr(home_mod, "_git", busy_writer)
    monkeypatch.setattr(home_mod, "_push_sleep", sleeps.append)
    (a.root / "state/runs/r1").mkdir(parents=True)
    (a.root / "state/runs/r1/state.json").write_text("{}")
    assert a.publish("run r1") is True
    assert race["n"] == 0 and len(sleeps) == 9 and all(s <= 0.5 for s in sleeps)
    log = real(a.root, "log", "--format=%s", "-11", "@{u}").stdout
    assert "vra: run r1" in log.splitlines()[0] and log.count("writer") == 9


def test_s3_state_store_never_pushes_the_home(tmp_path, repos):
    a, _b, _ = _git_home(tmp_path, repos)
    doc = util.read_yaml(a.ledger_dir / "vra.yaml")
    doc["stateStore"] = {"type": "s3", "bucket": "vra-state-test", "lockTable": "vra-locks", "region": "us-east-1"}
    util.write_yaml(a.ledger_dir / "vra.yaml", doc)
    c = _ctx(a, tmp_path, "s3")
    c.aws = FakeAws(c.ledger, {("tools", "s3"): object(), ("tools", "dynamodb"): object()})
    assert c.backend.kind == "s3" and a.sync.read_only
    assert a.publish("anything") is False


def test_manifest_placeholders_for_live_test_secret_and_external_table_key(ctx, ledger):
    from vra import permissions
    d = util.read_yaml(ledger.root / "envs/test/env.yaml")
    d["liveTest"] = {"auth": {"type": "password", "secret": "vra/test/smoke"}}
    util.write_yaml(ledger.root / "envs/test/env.yaml", d)
    cfg = util.read_json(ledger.overlay_dir("test") / "infra/config/config.json")
    cfg["app"]["useKmsCmkEncryption"] = {"enabled": True,
                                         "optionalExternalCmkArn": "arn:aws:kms:us-east-1:555555555555:key/k1"}
    util.write_json(ledger.overlay_dir("test") / "infra/config/config.json", cfg)
    ledger._envs.clear()
    vals = permissions.placeholders(ctx, "test")
    assert vals["LiveTestSecret"] == "vra/test/smoke" and vals["ExternalTableKmsKey"].endswith(":key/k1")
    assert permissions.env_features(ctx, "test")["externalTableKey"] == "on"
    assert "externalTableKey" not in permissions.env_features(ctx, "dev")


def test_takeover_without_a_terminal_is_authorized_by_an_approval_of_the_runs_env(tmp_path, repos, monkeypatch):
    """Live test: `vra --human-approval <rec> resume <run> --takeover` without a TTY always failed with "(and an env)":
    resume has no --env, so no approval could ever authorize a takeover from CI or a non-interactive shell. The env
    now comes from the run being resumed."""
    from vra import human
    a, b, _ = _git_home(tmp_path, repos)
    Run.create(a.runs_dir.parent, "deploy", {"env": "dev"}, "op", run_id="r-dirty", home=a, env="dev")
    cb = _ctx(b, tmp_path, "b")
    seen = []
    monkeypatch.setattr(human, "require", lambda ctx, action, env, approval, tty=None: seen.append((action, env,
                                                                                                    approval)) or {})
    ns = cli.build_parser().parse_args(["--non-interactive", "--human-approval", "approvals/dev/takeover.yaml",
                                        "resume", "r-dirty", "--takeover"])
    cli.human_gate(cb, ns)
    assert seen == [("takeover", "dev", "approvals/dev/takeover.yaml")]


def test_takeover_of_a_run_of_no_env_is_authorized_by_the_records_env(tmp_path, repos, monkeypatch):
    """Live test: a CodeBuild build run (no env) that stopped left its run lock behind; `vra
    --human-approval <rec> resume <build-run> --takeover` failed with "(and an env)" whatever record was passed.
    The env of the record authorizes it, and only an env of this ledger does."""
    from vra import human
    a, b, _ = _git_home(tmp_path, repos)
    Run.create(a.runs_dir.parent, "build", {"lane": "release"}, "op", run_id="r-build", home=a)
    cb = _ctx(b, tmp_path, "b")
    rec = {"build": "human:takeover", "env": "dev", "approver": "approver@example.com",
           "approvedAt": "2026-01-01T00:00:00Z", "expiresAt": "2099-01-01T00:00:00Z", "scope": ["takeover"]}
    util.write_yaml(cb.ledger.root / "approvals/dev/takeover-build.yaml", rec)
    util.write_yaml(cb.ledger.root / "approvals/dev/takeover-other.yaml", {**rec, "env": "nosuchenv"})
    seen = []
    monkeypatch.setattr(human, "require", lambda ctx, action, env, approval, tty=None: seen.append((action, env)) or {})
    for name in ("takeover-build", "takeover-other"):
        ns = cli.build_parser().parse_args(["--non-interactive", "--human-approval", f"approvals/dev/{name}.yaml",
                                            "resume", "r-build", "--takeover"])
        cli.human_gate(cb, ns)
    assert seen == [("takeover", "dev"), ("takeover", None)]


def test_pull_moves_aside_unpublished_files_of_a_dead_process_that_another_machine_committed(tmp_path, repos):
    """Live test: machine A was killed mid-smoke with an uncommitted run log; machine B took the run over and pushed
    the same log; every later command on A failed `cannot fast-forward ... untracked working tree files would be
    overwritten`. The engine keeps A's copy under .vra-local/displaced/ and takes the committed one. Files outside
    the engine's scope are never touched."""
    a, b, _ = _git_home(tmp_path, repos)
    rel = "state/runs/r-killed/logs/deploy.dev.smoke.log"
    (a.root / rel).parent.mkdir(parents=True)
    (a.root / rel).write_text("A's partial log\n")
    (b.root / rel).parent.mkdir(parents=True)
    (b.root / rel).write_text("B's complete log\n")
    assert b.publish("run r-killed taken over")
    a.pull()
    assert (a.root / rel).read_text() == "B's complete log\n"
    kept = list((a.local_dir / "displaced").rglob("deploy.dev.smoke.log"))
    assert len(kept) == 1 and kept[0].read_text() == "A's partial log\n"
    # outside the engine's scope: still a human's call
    (a.root / "ledger" / "notes.md").write_text("A\n")
    (b.root / "ledger" / "notes.md").write_text("B\n")
    git(b.root, "add", "ledger/notes.md")
    git(b.root, "commit", "-q", "-m", "ledger note")
    git(b.root, "push", "-q")
    with pytest.raises(NeedsHuman, match="cannot fast-forward"):
        a.pull()
    assert (a.root / "ledger" / "notes.md").read_text() == "A\n"


def test_engine_never_pushes_a_persons_unpushed_ledger_commit(tmp_path, repos):
    """Live test: an approval committed on machine B but not pushed reached the remote with the run's first state
    push (`git push` publishes the whole branch), and the gated dry-run then accepted it. The engine refuses to
    publish while a local commit outside its scope is unpushed, and the gate refuses the approval."""
    a, b, remote = _git_home(tmp_path, repos)
    appr = b.ledger_dir / "approvals" / "test" / "b1.yaml"
    appr.parent.mkdir(parents=True, exist_ok=True)
    appr.write_text("build: b1\n")
    git(b.root, "add", str(appr))
    git(b.root, "commit", "-q", "-m", "approval, not pushed")
    (b.state_dir / "runs" / "r1").mkdir(parents=True)
    (b.state_dir / "runs" / "r1" / "state.json").write_text("{}")
    with pytest.raises(NeedsHuman, match="never publishes a person's change"):
        b.publish("run r1 RUNNING")
    shown = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "-5"], capture_output=True, text=True)
    assert "approval, not pushed" not in shown.stdout
    with pytest.raises(NeedsHuman, match="remote"):
        b.sync.gate_check()
    git(b.root, "push", "-q")                         # the person pushes it (review happens on the server)
    b.sync.gate_check()                               # now the pushed record may authorize
    (b.state_dir / "runs" / "r1" / "state.json").write_text('{"rev": 2}')
    assert b.publish("run r1 SUCCEEDED")


# --- a refused first publish, and runs nothing owns any more (live test O3) -------------------------------------------

@workflow("_test_noop")
def _wf_noop(ctx, run, args):
    return {"ok": True}


def _committed_status(h: Home, run_id: str) -> str:
    rel = (h.runs_dir / run_id / "state.json").relative_to(h.root)
    shown = subprocess.run(["git", "-C", str(h.root), "show", f"HEAD:{rel}"], capture_output=True, text=True)
    return json.loads(shown.stdout)["status"] if shown.returncode == 0 else "<not committed>"


def test_run_whose_first_publish_is_refused_is_parked_not_left_running(tmp_path, repos):
    """Live test O3: a person's unpushed ledger commit made the run's first publish refuse, and the run stayed RUNNING
    in the home (committed locally, published with the person's push) with no owner. It is parked NEEDS_HUMAN with
    the reason, in the working copy and in the local commit that a later push publishes."""
    _a, b, _remote = _git_home(tmp_path, repos)
    note = b.ledger_dir / "notes.md"
    note.write_text("a person's change\n")
    git(b.root, "add", str(note))
    git(b.root, "commit", "-q", "-m", "person, not pushed")
    cb = _ctx(b, tmp_path, "b")
    out = runner.start(cb, "_test_noop", {})
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    assert out.run is not None and out.run.status == "NEEDS_HUMAN"
    assert "first publish to the shared home was refused" in out.message and "vra home sync" in out.message
    assert "never publishes a person's change" in out.run.state["result"]["publishRefused"]["reason"]
    rid = out.run.run_id
    assert util.read_json(b.runs_dir / rid / "state.json")["status"] == "NEEDS_HUMAN"
    assert _committed_status(b, rid) == "NEEDS_HUMAN"
    assert not report.orphaned_runs(cb)


def test_run_whose_first_push_is_rejected_is_parked_not_left_running(tmp_path, repos, monkeypatch):
    _a, b, _remote = _git_home(tmp_path, repos)
    cb = _ctx(b, tmp_path, "b")

    def rejected(message, paths=None):
        raise Contention("push of the shared home was rejected (test): another machine changed the same files")
    monkeypatch.setattr(b.sync, "push", rejected)
    out = runner.start(cb, "_test_noop", {})
    assert out.exit_code == errors.NEEDS_HUMAN and out.run.status == "NEEDS_HUMAN", out.message
    assert "was rejected" in out.run.state["result"]["publishRefused"]["reason"]
    assert util.read_json(b.runs_dir / out.run.run_id / "state.json")["status"] == "NEEDS_HUMAN"


def test_status_all_and_history_verify_flag_running_runs_whose_lease_expired(tmp_path, repos, monkeypatch):
    a, b, _ = _git_home(tmp_path, repos)
    ca, cb = _ctx(a, tmp_path, "a"), _ctx(b, tmp_path, "b")
    dead = Run.create(ca.state_dir, "deploy", {}, "op", run_id="dead-1", env="dev", home=a)
    FileLock(a).acquire("run-dead-1", "dead-1", token="ta")
    Run.create(ca.state_dir, "deploy", {}, "op", run_id="nolease-1", env="dev", home=a)   # killed before its lease
    expires = FileLock(a).holder("run-dead-1")["expiresAt"]
    later = expires + 10
    live = Run.create(ca.state_dir, "deploy", {}, "op", run_id="live-2", env="dev", home=a)
    monkeypatch.setattr(report.time, "time", lambda: later)
    lock_b = FileLock(a, clock=lambda: later)
    lock_b.acquire("run-live-2", "live-2", token="tl")                                     # renewed: still live
    res = report.status_all(cb)
    orphans = {o["runId"]: o for o in res["orphanedRuns"]}
    assert sorted(orphans) == ["dead-1", "nolease-1"], res["orphanedRuns"]
    assert orphans["dead-1"]["fix"] == "vra resume dead-1 --takeover" and "expired" in orphans["dead-1"]["lease"]
    assert orphans["nolease-1"]["lease"] == "none held"
    assert {r["runId"]: r.get("orphaned") for r in res["activeRuns"]} == {"dead-1": True, "nolease-1": True,
                                                                         "live-2": None}
    ver = report.history_verify(cb)
    assert any("orphaned run" in w and "vra resume dead-1 --takeover" in w for w in ver["warnings"])
    assert ver["ok"]  # not a history defect: the exit code is unchanged
    # nothing was changed or deleted
    assert util.read_json(b.runs_dir / "dead-1" / "state.json")["status"] == "RUNNING"
    assert dead.status == "RUNNING" and live.status == "RUNNING"


# --- `vra setup` reports whether the home really reached its remote (live test O4) -----------------------------------

def _setup_git_home(tmp_path, repos, capsys, remote: str | None, sync_type: str = "git") -> dict:
    ans = tmp_path / f"answers-{sync_type}.yaml"
    sync = {"type": sync_type, **({"remote": remote} if remote else {})}
    if sync_type == "command":
        sync = {"type": "command", "pull": "true", "push": "false"}
    util.write_yaml(ans, {"home": {"sync": sync}, "project": {"name": "x"}, "fork": {"url": str(repos["fork"])},
                          "githost": {"type": "local"},
                          "envs": {"dev": {"account": "111111111111", "region": "us-east-1",
                                           "liveTest": {"auth": {"type": "none"}}}}})
    h = tmp_path / f"home-{sync_type}-{bool(remote)}"
    capsys.readouterr()
    cli.main(["--json", "--non-interactive", "--home", str(h), "setup", "--answers", str(ans), "--skip-preflight"])
    out = json.loads(capsys.readouterr().out)
    return {"root": h, **(out.get("result") or out)["home"]}


def test_setup_reports_published_false_until_the_first_push_happened(tmp_path, repos, capsys):
    """Live test O4: setup with sync.type git reported published: true while nothing was pushed (no upstream yet)."""
    remote = tmp_path / "setup-remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    res = _setup_git_home(tmp_path, repos, capsys, str(remote))
    assert res["published"] is False and "first push has not happened" in res["reason"], res
    assert res["publishCommand"] == f"git -C {res['root']} push -u origin HEAD"
    shown = subprocess.run(["git", "-C", str(remote), "log", "--oneline", "--all"], capture_output=True, text=True)
    assert shown.stdout.strip() == ""  # really nothing on the remote
    git(res["root"], "push", "-q", "-u", "origin", "HEAD")   # the person's first push
    assert Home.open(res["root"]).sync.publish_state() == {"published": True}


def test_setup_reports_why_a_home_is_not_published(tmp_path, repos, capsys):
    no_remote = _setup_git_home(tmp_path, repos, capsys, None)
    assert no_remote["published"] is False and "no git remote" in no_remote["reason"]
    assert "remote add origin" in no_remote["publishCommand"]
    unsynced = _setup_git_home(tmp_path, repos, capsys, None, sync_type="none")
    assert unsynced["published"] is False and "sync.type none" in unsynced["reason"]
    refused = _setup_git_home(tmp_path, repos, capsys, None, sync_type="command")   # sync.push is `false`
    assert refused["published"] is False and "push refused" in refused["reason"], refused
    assert refused["publishCommand"] == "vra home sync"
