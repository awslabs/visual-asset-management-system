# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""(live test Phase B10g): the Deploy-prod of 5437d5c2 parked with prod dirty (smoke secret missing), a person
released the lock, and the CodePipeline retry resumed the run, which SUCCEEDED and released prod. Its report still
said "prod stays locked by this run ... `vra unlock` releases it", sending the reader to unlock an env nobody held.
Releasing the lock now drops that env's held-lock note."""

from __future__ import annotations

from pathlib import Path

import pytest

from vra import errors, report, runner
from vra.context import Context
from vra.home import Home
from vra.ledger import Ledger
from vra.runner import workflow
from vra.state import FileLock

from conftest import FakeAws, write_ledger


@pytest.fixture(autouse=True)
def _xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("VRA_HOME", raising=False)
    monkeypatch.delenv("VRA_LEDGER", raising=False)


@workflow("_test_held_lock_park_then_pass")
def _wf(ctx, run, args):
    with runner.env_lock(ctx, run, "dev"):
        runner.mark_dirty(run, "dev", "cdk deploy of b1 to dev")
        attempt = int(run.state.get("_attempt") or 0) + 1
        run.state["_attempt"] = attempt
        if attempt == 1:
            raise errors.VraError("post-deploy smoke: live-test secret missing")
        runner.mark_clean(run, "dev")


def _ctx(tmp_path: Path, repos) -> Context:
    h = Home.create(tmp_path / "home", {"type": "none"})
    write_ledger(h.ledger_dir, str(repos["fork"]), str(repos["upstream"]))
    c = Context(ledger=Ledger(h.ledger_dir), work_dir=tmp_path / "work", initiator="op@example.com", home=h)
    c.aws = FakeAws(c.ledger)
    return c


def test_a_resumed_run_that_succeeds_drops_its_held_lock_note(tmp_path, repos):
    ctx = _ctx(tmp_path, repos)
    out = runner.start(ctx, "_test_held_lock_park_then_pass", {})
    assert out.exit_code != 0
    assert any("stays locked by this run" in n for n in out.run.state.get("notes") or [])
    res = runner.resume(ctx, out.run.run_id)
    assert res.exit_code == 0, res.message
    assert FileLock(ctx.home).holder("dev") is None
    assert not any("stays locked by this run" in n for n in res.run.state.get("notes") or [])
    md = report.render_markdown(report.build_report(ctx, res.run))
    assert "stays locked by this run" not in md


def test_other_notes_survive_the_release(tmp_path, repos):
    ctx = _ctx(tmp_path, repos)
    out = runner.start(ctx, "_test_held_lock_park_then_pass", {})
    out.run.state.setdefault("notes", []).append("IT-REQUEST-2 is pending (deploy of dev did not need bucket access)")
    out.run.save()
    res = runner.resume(ctx, out.run.run_id)
    assert res.exit_code == 0, res.message
    assert any(n.startswith("IT-REQUEST-2 is pending") for n in res.run.state.get("notes") or [])
