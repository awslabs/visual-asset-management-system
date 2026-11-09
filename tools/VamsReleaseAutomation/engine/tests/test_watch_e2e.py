# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end `vra watch` against the fake upstream/fork: sync -> merge -> build -> deploy -> test, both lanes."""

from vra import errors, runner, util

from conftest import FakeAws, git
from test_build_deploy import FakeCfn


def test_watch_both_lanes(ctx, repos, calls, ledger):
    from test_gitops import core_upstream_rule
    core_upstream_rule(ledger)  # the fixture's core edit, registered with an explicit rule
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "watch", {"notify": False})
    assert out.exit_code == 0, (out.message, out.run.state["result"])
    res = out.run.state["result"]
    assert res["lanes"]["release"]["deployed"] == ["dev"]
    assert res["lanes"]["preview"]["deployed"] == ["dev-preview"]
    assert set(res["lanes"]["release"]["refusedEnvs"]) == {"test", "prod"}
    # release lane: sync PR opened, build from the sync branch tagged as the new VAMS version
    rel_build = res["lanes"]["release"]["build"]
    assert rel_build == "cust-v2.6.6-1"
    assert (ctx.work_dir / "prs" / "sync__release-v2.6.6.json").exists()
    assert ledger.current_deploy("dev")["build"] == rel_build
    assert ledger.current_deploy("dev-preview")["build"].startswith("preview-v2.6.6-")
    # gated envs untouched
    assert ledger.history("test") == [] and ledger.history("prod") == []
    # tests recorded -> promotion candidate on dev
    assert any(h["action"] == "test" and h["result"] == "SUCCEEDED" for h in ledger.history("dev"))
    assert (out.run.dir / "report.md").read_text().startswith("# vra watch run")

    # next day: nothing new -> no-op
    ctx.run_id = None
    again = runner.start(ctx, "watch", {"notify": False})
    assert again.exit_code == 0 and again.run.state["result"]["nothingNew"]


def test_watch_lane_conflict_needs_human_other_lane_continues(ctx, repos, calls, ledger):
    (ledger.root / "core-patches.md").write_text("- `backend/backend/core.py` customer core patch\n")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "watch", {"notify": False})
    assert out.exit_code == errors.NEEDS_HUMAN
    lanes = out.run.state["result"]["lanes"]
    assert lanes["release"]["status"] == "NEEDS_HUMAN" and lanes["preview"]["status"] == "NEEDS_HUMAN"
    conflicts = util.read_json(out.run.dir / "conflicts.json")
    assert conflicts["unresolved"][0]["path"] == "backend/backend/core.py"
