# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""B-050-37 (E2E-050 Phase B10f): a run that parks mid-change more than once (each CodePipeline retry of the
Deploy-prod of 5437d5c2 resumed it and it parked again) listed the same "stays locked by this run" note once per park,
and the note only said "`vra resume` continues", although a resume from another machine (every CI retry runs on a new
CodeBuild host) is refused at the held lock (O-050-16). The note is now written once per env and names both ways on."""

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


@workflow("_test_b050_37_park")
def _wf_park(ctx, run, args):
    with runner.env_lock(ctx, run, "dev"):
        runner.mark_dirty(run, "dev", "cdk deploy of b1 to dev")
        raise errors.VraError("post-deploy smoke: live-test secret missing")


def _ctx(tmp_path: Path, repos) -> Context:
    h = Home.create(tmp_path / "home", {"type": "none"})
    write_ledger(h.ledger_dir, str(repos["fork"]), str(repos["upstream"]))
    c = Context(ledger=Ledger(h.ledger_dir), work_dir=tmp_path / "work", initiator="op@example.com", home=h)
    c.aws = FakeAws(c.ledger)
    return c


def _held_notes(st: dict) -> list[str]:
    return [n for n in st.get("notes") or [] if "stays locked by this run" in n]


def test_parking_twice_writes_the_held_lock_note_once(tmp_path, repos):
    ctx = _ctx(tmp_path, repos)
    out = runner.start(ctx, "_test_b050_37_park", {})
    rid = out.run.run_id
    assert FileLock(ctx.home).holder("dev")["held"]
    res = runner.resume(ctx, rid)            # same machine: renews the lock, fails again, parks again
    assert res.exit_code != 0
    st = res.run.state
    assert len(_held_notes(st)) == 1, st.get("notes")
    md = report.render_markdown(report.build_report(ctx, res.run))
    assert md.count("stays locked by this run") == 1


def test_held_lock_note_names_the_resume_from_another_machine(tmp_path, repos):
    ctx = _ctx(tmp_path, repos)
    out = runner.start(ctx, "_test_b050_37_park", {})
    note = _held_notes(out.run.state)[0]
    rid = out.run.run_id
    assert f"vra resume {rid}" in note and f"vra unlock --env dev --run {rid}" in note
    # O-050-16: from another machine (a CI retry runs on a new host) the resume is refused at this lock
    assert "--takeover" in note and "another machine" in note
