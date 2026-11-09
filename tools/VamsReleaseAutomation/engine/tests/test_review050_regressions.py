# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the REVIEW-050 findings on `vra iam` (the reviewer's reproductions, inverted: each one
failed on vra 0.5.0 before the fix wave and passes after it)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import boto3
import moto
import pytest

import iam_fixtures as fx
from vra import iam, runner, util
from vra.context import Context
from vra.errors import NeedsHuman
from vra.ledger import Ledger

from conftest import FakeAws
from test_gates import approve
from test_iam_package import _synth_handler, _ungate, make_ctx, new_run, plan_from, response_for

BOUNDARY = "arn:aws:iam::111111111111:policy/it-vams-boundary"


def _add_boundary(d):
    d["boundaryArn"] = BOUNDARY


# ================================================================ R50-1: the #431 check is never skipped
def test_boundary_arn_only_in_it_response_still_runs_the_431_check(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod", edit=_add_boundary))
    unpatched = fx.write("commercial-boundary", tmp_path / "unpatched")
    with pytest.raises(NeedsHuman) as ei:      # 88 roles with AWS::NoValue: refused (exit 20)
        iam.check_assembly(c, new_run(c, "build"), "prod", unpatched, "build.synth.prod")
    assert "88 IAM role(s)" in ei.value.message
    fixed = fx.write("commercial-boundary-fixed", tmp_path / "fixed")
    res = iam.check_assembly(c, new_run(c, "build"), "prod", fixed, "build.synth.prod")
    assert res["rolesWithoutBoundary"] == 0


def test_boundary_arn_from_the_applied_overlay_when_applied_json_lacks_it(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary"})
    util.write_json(c.ledger.overlay_dir("prod") / iam.CDK_JSON_MERGE,
                    {"context": {"environments": {"aws": {"PermissionBoundaryArn": BOUNDARY}}}})
    assert iam.boundary_arn(c, "prod") == BOUNDARY


def test_boundary_mode_without_any_arn_fails_closed(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary"})
    out = fx.write("commercial-boundary-fixed", tmp_path / "a")
    with pytest.raises(NeedsHuman) as ei:
        iam.check_assembly(c, new_run(c, "build"), "prod", out, "build.synth.prod")
    assert "no boundary ARN is known" in ei.value.message
    assert any("without iam.boundaryArn" in w for w in c.ledger.validate()["warnings"])


# ================================================================ R50-5: the approval names the response
def test_gated_iam_apply_approval_is_bound_to_the_response(tmp_path):
    c = make_ctx(tmp_path)
    plan_from(c, "prod", "commercial-report", tmp_path)
    good = response_for(c, "prod")
    approve(c.ledger, "prod", iam.approval_subject("prod-1", util.sha256_file(good)), "release-manager@example.com",
            scope=["iam-apply"])

    def swap(d):
        first = next(iter(d["roles"]))
        d["roles"][first] = "ITVAMS-AdministratorAccessRole"   # not what the approver saw
    with pytest.raises(NeedsHuman) as ei:
        iam.apply(c, "prod", response_for(c, "prod", edit=swap))
    assert "iam-apply:prod-1:" in ei.value.message
    assert iam.applied_record(c, "prod") is None
    # the old package-only subject is not accepted either
    approve(c.ledger, "prod", "iam-apply:prod-1", "release-manager@example.com", scope=["iam-apply"])
    with pytest.raises(NeedsHuman):
        iam.apply(c, "prod", response_for(c, "prod", edit=swap))


def test_apply_dry_run_shows_the_response_sha_and_records_the_it_ticket(tmp_path):
    c = make_ctx(tmp_path)
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    resp = response_for(c, "prod", edit=lambda d: d.update(itTicket="CHG-1234"))
    c.dry_run = True
    dry = iam.apply(c, "prod", resp)
    sha = util.sha256_file(resp)
    assert dry["responseSha256"] == sha and dry["approvalSubject"] == f"iam-apply:prod-1:{sha[:16]}"
    c.dry_run = False
    iam.apply(c, "prod", resp)
    rec = c.ledger.history("prod")[-1]
    assert rec["action"] == "iam-apply" and rec["itTicket"] == "CHG-1234" and rec["responseSha256"] == sha


# ================================================================ R50-4: applied.json is bound to history
def test_gate_refuses_an_applied_json_no_iam_apply_recorded(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    assert iam.gate(c, new_run(c), "prod", "s")["package"] == "prod-1"
    # an agent rewrites the overlay and recomputes applied.json: self-consistent, but not what was applied
    p = c.ledger.overlay_dir("prod") / iam.IAM_ROLE_CONFIG
    rc = util.read_json(p)
    first = next(iter(rc["vamsStacks"]["precreatedRoles"]))
    rc["vamsStacks"]["precreatedRoles"][first] = "ITVAMS-AdministratorAccessRole"
    util.write_json(p, rc)
    ap = iam.package_root(c, "prod") / "applied.json"
    forged = util.read_json(ap)
    forged["overlay"] = iam.overlay_hashes(c, "prod")
    forged["roles"][first] = "ITVAMS-AdministratorAccessRole"
    forged["appliedSha256"] = iam.applied_digest(forged)
    util.write_json(ap, forged)
    with pytest.raises(NeedsHuman) as ei:
        iam.gate(c, new_run(c), "prod", "s")
    assert "is not the record of a `vra iam apply`" in ei.value.message


def test_gate_refuses_an_applied_json_written_by_hand(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    pkg = iam.load_package(c, "prod")
    util.write_json(iam.package_root(c, "prod") / "applied.json",
                    {"packageId": pkg["packageId"], "n": pkg["n"], "overlay": iam.overlay_hashes(c, "prod")})
    with pytest.raises(NeedsHuman):
        iam.gate(c, new_run(c), "prod", "s")


# ================================================================ R50-2: no bundle reuse across IAM changes
def test_complete_bundle_is_rebuilt_after_iam_apply_and_the_iam_gate_runs(tmp_path, repos):
    state = {"name": "commercial-report"}
    c = make_ctx(tmp_path, repos, prod_iam=None, handler=_synth_handler(state))
    _ungate(c)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    first = out.run.state["build"]
    c.run_id = None
    c.ledger.env("prod")["iam"] = {"mode": "precreated", "rolePrefix": "ITVAMS-"}
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    state["name"] = "commercial-precreated"
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    assert out.run.state["build"] == first                         # same commit, same build id
    assert out.run.find_step("build.reuse") is None
    assert out.run.find_step("build.iam.prod")["status"] == "done"  # the gate ran
    assert state["synths"] == ["commercial-report", "commercial-precreated"]
    manifest = util.read_json(Path(c.home.artifacts_dir) / "builds" / first / "manifest.json") \
        if (Path(c.home.artifacts_dir) / "builds" / first).exists() else out.run.state["result"]
    assert manifest["iam"]["prod"]["package"] == "prod-1"


def test_deploy_gate_refuses_a_bundle_built_with_other_iam_settings(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    good = {"buildId": "b-1", "iam": {"prod": iam.build_identity(c, "prod")}}
    assert iam.deploy_gate(c, new_run(c, "deploy"), "prod", good, "deploy.prod.iam")["bundleIam"] == "matches"
    for stale in ({"buildId": "b-0"}, {"buildId": "b-0", "iam": {"prod": {"modes": ["standard"]}}}):
        with pytest.raises(NeedsHuman) as ei:
            iam.deploy_gate(c, new_run(c, "deploy"), "prod", stale, "deploy.prod.iam")
        assert "rebuild" in ei.value.message
    # a standard env deploys a standard bundle without an IAM package
    assert iam.deploy_gate(c, new_run(c, "deploy", "dev"), "dev", {"buildId": "b"}, "x") == \
        {"env": "dev", "modes": ["standard"]}


# ================================================================ R50-3: IT packages through the S3 state store
def test_it_package_is_shared_through_the_s3_state_store(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-state-iam")
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="vra-locks", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "lockId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "lockId", "AttributeType": "S"}])
        a = make_ctx(tmp_path / "a", prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
        _ungate(a)
        doc = util.read_yaml(a.ledger.root / "vra.yaml")
        doc["stateStore"] = {"type": "s3", "bucket": "vra-state-iam", "lockTable": "vra-locks",
                             "region": "us-east-1"}
        util.write_yaml(a.ledger.root / "vra.yaml", doc)

        def machine(root: Path, name: str) -> Context:
            led = Ledger(root)
            led.env("prod")["requiresApproval"] = False
            led.env("prod")["iam"] = {"mode": "precreated", "rolePrefix": "ITVAMS-"}
            c = Context(ledger=led, work_dir=tmp_path / f"work-{name}", initiator="ci@example.com")
            c.aws = FakeAws(led, moto=True)
            return c
        m1 = machine(a.ledger.root, "1")
        plan_from(m1, "prod", "commercial-report", tmp_path)
        keys = [o["Key"] for o in boto3.client("s3", region_name="us-east-1")
                .list_objects_v2(Bucket="vra-state-iam", Prefix="vra/iam/prod/")["Contents"]]
        assert any(k.endswith("/1/package.json") for k in keys)
        iam.apply(m1, "prod", response_for(m1, "prod"))
        # another machine (a CI job with a fresh work dir and checkout) sees package and apply
        root2 = tmp_path / "checkout-2"
        shutil.copytree(a.ledger.root, root2)
        m2 = machine(root2, "2")
        assert iam.load_package(m2, "prod")["packageId"] == "prod-1"
        assert iam.gate(m2, new_run(m2), "prod", "s")["package"] == "prod-1"


# ================================================================ R50-6: PassRole matches the names in use
def test_precreated_passrole_matches_exactly_the_passed_roles(tmp_path):
    import fnmatch
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "APP-"})
    plan_from(c, "prod", "commercial-report", tmp_path)
    roles = iam.load_package(c, "prod")["roles"]["roles"]
    arn = "arn:aws:iam::111111111111:role/"
    names = {r["constructPath"]: r["suggestedRoleName"] for r in roles}
    grants = {}
    for r in roles:
        for st in r["policy"]["Statement"]:
            acts = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
            if "iam:PassRole" in acts:
                grants.setdefault(r["constructPath"], []).extend(
                    st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]])
    assert grants, "VAMS v2.6.6 has PassRole grants"
    for path, pats in grants.items():
        hits = {p for p, n in names.items() for pat in pats if fnmatch.fnmatchcase(arn + n, pat)}
        targets = set((next(r for r in roles if r["constructPath"] == path).get("passRoleTargets") or []))
        assert targets, f"{path}: PassRole targets not mapped"
        assert hits == targets, f"{path}: grant matches {len(hits)} roles, VAMS passes {len(targets)}"
        assert len(hits) < 10


# ================================================================ R50-7: bootstrap mode with vra's deploy role
def _deploy_policy(params: dict) -> list[dict]:
    from test_freeze_restore_signing import _conditions
    from test_iam_and_ci_roles import load_cfn, render
    t = load_cfn("iam/cloudformation/vra-deploy-role.yaml")
    conds = _conditions(t, params)
    base = {"AWS::Partition": "aws", "AWS::AccountId": "111111111111", "AWS::Region": "us-east-1",
            "ConfigName": "vams", "BaseStackName": "prod", "VamsRegion": "", "EnvName": "prod", **params}
    pol = render(t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"], base, conds, {})["Statement"]
    role = render(t["Resources"]["DeployRole"]["Properties"], base, conds, {})
    return pol, role


def test_deploy_role_assumes_the_bootstrap_roles_it_returned():
    it = ["arn:aws:iam::111111111111:role/ITVAMS-cdk-q-deploy-role-us-east-1",
          "arn:aws:iam::111111111111:role/ITVAMS-cdk-q-lookup-role-us-east-1"]
    pol, _ = _deploy_policy({"BootstrapRoleArns": it})
    cdk = next(s for s in pol if s.get("Sid") == "CdkBootstrapRolesAssume")
    assert cdk["Resource"] == it
    pol, _ = _deploy_policy({})
    cdk = next(s for s in pol if s.get("Sid") == "CdkBootstrapRolesAssume")
    assert all(":role/cdk-*-" in r for r in cdk["Resource"])


def test_cli_credentials_deploy_role_gets_the_execution_policy_and_staging():
    pol, role = _deploy_policy({"CliCredentialsDeploy": "true", "CdkExecutionPolicyArn":
                                "arn:aws:iam::111111111111:policy/vra-cdk-execution", "StagingBucketName": "stage-b",
                                "StagingRepositoryName": "stage-r"})
    sids = {s.get("Sid") for s in pol}
    assert "CdkBootstrapRolesAssume" not in sids
    assert {"CliCredentialsStagingBucket", "CliCredentialsStagingRepository"} <= sids
    assert role["ManagedPolicyArns"] == ["arn:aws:iam::111111111111:policy/vra-cdk-execution"]


def test_package_vra_deploy_policy_names_the_bootstrap_arns(tmp_path):
    arn = "arn:aws:iam::111111111111:role/IT-custom-deploy"
    c = make_ctx(tmp_path, prod_iam={"mode": "bootstrap", "rolePrefix": "ITVAMS-",
                                     "bootstrap": {"deployRoleArn": arn, "qualifier": "q"}})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    vra_deploy = next(r for r in iam.load_package(c, "prod")["roles"]["vraRoles"] if r["id"] == "vra-deploy-prod")
    cdk = next(s for s in vra_deploy["statements"] if s["Sid"] == "CdkBootstrapRolesAssume")
    assert arn in cdk["Resource"] and not any(":role/cdk-*-" in r for r in cdk["Resource"])
    assert any("ITVAMS-cdk-q-lookup-role" in r for r in cdk["Resource"])
    assert any("/cdk-bootstrap/q/version" in n for n in vra_deploy["notes"])
    # cliCredentialsSynthesizer: no bootstrap role to assume at all
    c2 = make_ctx(tmp_path / "cli", prod_iam={"mode": "bootstrap", "cliCredentialsSynthesizer": True})
    assert iam.bootstrap_assume_arns(c2, "prod") == []


# ================================================================ R50-14: pipeline deploy recomputes human-only
def test_pipeline_deploy_recomputes_iam_and_gate_changes_against_the_live_stack(repos, tmp_path, monkeypatch):
    from vra import pipeline
    from test_pipeline import FakeCfn, automation_block, make_ctx as pl_ctx, make_ledger, template
    L = make_ledger(repos, tmp_path, automation=automation_block())
    cfn = FakeCfn({"StackStatus": "UPDATE_COMPLETE"}, template())
    ctx = pl_ctx(L, tmp_path, state={"template": template()}, cfn=cfn)
    rec = pipeline.plan(ctx)
    assert rec["humanOnly"] == []
    monkeypatch.setenv("VRA_AGENT", "1")
    # another deploy changed the stack after the plan: the plan's "no IAM change" no longer holds
    cfn.template = template(approvals=("Approval-test", "FinalApproval-prod", "Approval-uat"), role_policy="old")
    with pytest.raises(NeedsHuman, match="changed since plan"):
        pipeline.deploy(ctx, rec["planId"])
    # an edited plan record cannot turn a human-only deploy into an agent deploy
    cfn.template = template(role_policy="old")
    p = ctx.state_dir / "pipeline" / "plans" / f"{rec['planId']}.json"
    forged = util.read_json(p)
    forged.update(iamChanged=["BuildRole"], humanOnly=[])
    util.write_json(p, forged)
    with pytest.raises(NeedsHuman, match="human-only and an agent cannot run it"):
        pipeline.deploy(ctx, rec["planId"])


# ================================================================ R50-19: verify reports broader roles
def test_verify_reports_roles_broader_than_the_package(tmp_path):
    from test_iam_package import FakeIam, _live_roles, _verify_ctx

    class BroadIam(FakeIam):
        def list_attached_role_policies(self, RoleName):
            extra = [{"PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}] if RoleName == victim else []
            return {"AttachedPolicies": extra + [{"PolicyArn": "arn:aws:iam::aws:policy/service-role/"
                                                               "AWSLambdaBasicExecutionRole"}]}

        def list_role_policies(self, RoleName):
            return {"PolicyNames": ["it-extra"] if RoleName == victim else []}

        def get_role_policy(self, RoleName, PolicyName):
            return {"PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}}
    c = _verify_ctx(tmp_path)
    live = _live_roles(c)
    victim = sorted(live)[0]
    live[victim]["AssumeRolePolicyDocument"]["Statement"].append(
        {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::555555555555:root"}, "Action": "sts:AssumeRole"})
    c.aws.clients[("prod", "iam")] = BroadIam(live)
    res = iam.verify(c, "prod")
    text = "\n".join(res["problems"])
    assert f"role {victim} also trusts" in text and "555555555555" in text
    assert "AdministratorAccess" in text and "allows *" in text
    assert any("AWSLambdaBasicExecutionRole" in w for w in res["warnings"])
    assert not res["ok"]


# ================================================================ R50-22 / R50-24 / R50-26
def test_bootstrap_check_reads_the_asset_publishing_roles(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "bootstrap", "rolePrefix": "ITVAMS-"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    applied = iam.applied_record(c, "prod")["bootstrap"]
    out = fx.write("commercial-report", tmp_path / "a")
    stacks = iam.Assembly.load(out).bootstrap_arns()
    man = util.read_json(out / "manifest.json")
    for art in man["artifacts"].values():   # the stacks name the IT roles (overlay applied)
        if art.get("type") == "aws:cloudformation:stack":
            art["properties"]["assumeRoleArn"] = applied["deployRoleArn"]
            art["properties"]["cloudFormationExecutionRoleArn"] = applied["cloudFormationExecutionRoleArn"]
            art["properties"].setdefault("lookupRole", {})["arn"] = applied["lookupRoleArn"]
    util.write_json(out / "manifest.json", man)
    assert stacks
    ok = {"files": {"a": {"destinations": {"d": {"assumeRoleArn": applied["fileAssetPublishingRoleArn"]}}}}}
    util.write_json(out / "core.assets.json", ok)
    assert iam.check_assembly(c, new_run(c, "build"), "prod", out, "s")["bootstrap"] == "matches"
    bad = {"files": {"a": {"destinations": {"d": {"assumeRoleArn":
                                                  "arn:${AWS::Partition}:iam::111111111111:role/cdk-hnb659fds-"
                                                  "file-publishing-role-111111111111-us-east-1"}}}}}
    util.write_json(out / "core.assets.json", bad)
    with pytest.raises(NeedsHuman, match="file-publishing"):
        iam.check_assembly(c, new_run(c, "build"), "prod", out, "s")


def test_it_roles_md_lists_broad_grants(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    plan_from(c, "prod", "commercial-report", tmp_path)
    md = (Path(iam.load_package(c, "prod")["dir"]) / "IT-ROLES.md").read_text()
    assert "## Broad grants" in md and "`*`" in md
    assert "## iam:PassRole between VAMS roles" in md
    assert "iam-apply:prod-1:<first 16 hex" in md


def test_assembly_load_reads_nested_cloud_assemblies(tmp_path):
    out = fx.write("commercial-boundary-fixed", tmp_path / "stage")
    top = tmp_path / "top"
    (top / "assembly-Prod").mkdir(parents=True)
    for f in out.iterdir():
        shutil.copy(f, top / "assembly-Prod" / f.name)
    util.write_json(top / "manifest.json", {"version": "36.0.0", "artifacts": {"assembly-Prod": {
        "type": "cdk:cloud-assembly", "properties": {"directoryName": "assembly-Prod"}}}})
    flat = iam.Assembly.load(out)
    nested = iam.Assembly.load(top)
    assert len(nested.template_roles()) == len(flat.template_roles()) > 0
    assert sorted(map(json.dumps, nested.bootstrap_arns().values())) == \
        sorted(map(json.dumps, flat.bootstrap_arns().values())) and flat.bootstrap_arns()


# ================================================================ R50-9: typed confirmations only at a person's TTY
def _clean_env(monkeypatch):
    from vra import human
    for var in human.AGENT_MARKERS + human.CI_MARKERS:
        monkeypatch.delenv(var, raising=False)


def _chain(monkeypatch, *procs):
    """Fake parent process chain: procs = [(name, argv...)] from this process up; the last parent is init."""
    import os
    from vra import human
    pids = [os.getpid()] + [10_000 + i for i in range(1, len(procs))]
    table = {pid: (pids[i + 1] if i + 1 < len(pids) else 1, list(p)) for i, (pid, p) in enumerate(zip(pids, procs))}
    monkeypatch.setattr(human, "_proc_info", lambda pid: table.get(pid))


def test_typed_confirmation_needs_a_person_at_a_login_tty(monkeypatch):
    from vra import human
    _clean_env(monkeypatch)
    monkeypatch.setattr(human, "_tty", lambda: True)
    _chain(monkeypatch, ("python3", "python3", "-m", "vra"), ("bash", "-bash"), ("sshd", "sshd: me@pts/0"))
    assert human._typed_confirmation_blocker() is None
    # VRA_AGENT cleared with `env -u`, but the agent's own marker stays
    monkeypatch.setenv("CLAUDECODE", "1")
    assert "CLAUDECODE" in human._typed_confirmation_blocker()
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("CI", "true")
    assert "CI marker" in human._typed_confirmation_blocker()
    monkeypatch.delenv("CI")
    # `printf prod | script -qec 'env -u VRA_AGENT vra unlock prod'` under an agent CLI
    _chain(monkeypatch, ("python3", "python3", "-m", "vra"), ("sh", "sh", "-c", "vra unlock"),
           ("script", "script", "-qec", "vra unlock", "/dev/null"), ("node", "node", "/usr/lib/claude-code/cli.js"))
    assert "script" in human._typed_confirmation_blocker()
    _chain(monkeypatch, ("python3", "python3", "-m", "vra"), ("bash", "bash"), ("kiro-cli", "kiro-cli", "chat"))
    assert "kiro-cli" in human._typed_confirmation_blocker()
    _chain(monkeypatch, ("python3", "python3", "-m", "vra"), ("node", "node", "/opt/x/node_modules/@anthropic-ai/"
                                                                            "claude-code/cli.js"))
    assert "claude" in human._typed_confirmation_blocker()
    monkeypatch.setattr(human, "_proc_info", lambda pid: None)      # unreadable chain: fail closed
    assert "cannot be read" in human._typed_confirmation_blocker()


def test_human_require_takes_only_an_approval_record_when_a_typed_confirmation_is_blocked(ctx, monkeypatch):
    from vra import human
    monkeypatch.delenv("VRA_AGENT", raising=False)
    monkeypatch.setattr(human, "_typed_confirmation_blocker", lambda: "agent marker CLAUDECODE is set")
    with pytest.raises(NeedsHuman, match="typed confirmation is not accepted"):
        human.require(ctx, "unlock", "dev", tty=True, ask=lambda q: "dev")
