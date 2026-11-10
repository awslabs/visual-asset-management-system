# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra iam plan|apply|verify` and the IT-package gates of preflight and build, against real VAMS v2.6.6
iam-policy-reports (commercial and GovCloud templates; fixtures/iam/README.md)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from vra import build as build_mod, cli, errors, iam, runner, util
from vra.context import Context
from vra.errors import BlockedOnIT, NeedsHuman, UsageError
from vra.state import Run

import iam_fixtures as fx
from conftest import FakeAws, env_doc, fake_shell_cls, write_ledger
from test_gates import approve

PROD_IAM = {"mode": "precreated+bootstrap", "rolePrefix": "ITVAMS-",
            "boundaryArn": "arn:aws:iam::111111111111:policy/it-vams-boundary"}


def envs(prod_iam=None, gov_iam=None):
    return {
        "dev": env_doc("dev", baseStackName="dev"),
        "prod": env_doc("prod", account="111111111111", baseStackName="prod", promotesFrom=None, rootEnv=True,
                        deployRoleArn="arn:aws:iam::111111111111:role/vra-deploy-prod",
                        **({"iam": prod_iam} if prod_iam else {})),
        "gov": env_doc("prod", account="111111111111", region="us-gov-west-1", baseStackName="prod",
                       promotesFrom=None, rootEnv=True, requiresApproval=False, approvers=[], templateBase="govcloud",
                       **({"iam": gov_iam} if gov_iam else {})),
    }


def make_ctx(tmp_path, repos=None, prod_iam=PROD_IAM, gov_iam=None, calls=None, handler=None):
    root = tmp_path / "ledger"
    fork = str(repos["fork"]) if repos else "file:///nonexistent-fork"
    up = str(repos["upstream"]) if repos else "file:///nonexistent-upstream"
    led = write_ledger(root, fork, up, envs(prod_iam, gov_iam))
    c = Context(ledger=led, work_dir=tmp_path / "work", initiator="dev-operator@example.com")
    c.aws = FakeAws(led)
    c.shell_cls = fake_shell_cls(calls if calls is not None else [], handler)
    return c


@pytest.fixture
def ictx(tmp_path):
    return make_ctx(tmp_path)


def plan_from(ctx, env, name, tmp_path, mutate=None, modes=None):
    out = fx.write(name, tmp_path / f"asm-{name}-{len(list(tmp_path.glob('asm-*')))}", mutate)
    o = runner.start(ctx, "iam-plan", {"env": env, "report": str(out / iam.REPORT), "modes": modes}, env=env)
    ctx.run_id = None
    assert o.exit_code == 0, o.message
    return o.run.state["result"]


def response_for(ctx, env, n=None, edit=None) -> Path:
    pkg = iam.load_package(ctx, env, n)
    doc = yaml.safe_load((Path(pkg["dir"]) / "it-response.template.yaml").read_text())
    if edit:
        edit(doc)
    p = Path(pkg["dir"]).parent / f"response-{pkg['n']}.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


def new_run(ctx, workflow="preflight", env="prod"):
    return Run.create(ctx.state_dir, workflow, {}, ctx.initiator, env=env, home=ctx.home)


# ================================================================ settings
def test_parse_modes_combinations_and_errors():
    assert iam.parse_modes(None) == ["standard"]
    assert iam.parse_modes("bootstrap+boundary") == ["boundary", "bootstrap"]
    assert iam.parse_modes(["precreated", "boundary"]) == ["boundary", "precreated"]
    assert iam.parse_modes("all") == ["boundary", "precreated", "bootstrap"]
    with pytest.raises(errors.ConfigError):
        iam.parse_modes("standard+boundary")
    with pytest.raises(errors.ConfigError):
        iam.parse_modes("strict")


def test_ledger_validate_checks_iam_settings(tmp_path):
    bad = {"mode": "boundary", "boundaryArn": "arn:aws-us-gov:iam::111111111111:policy/b",
           "bootstrap": {"deployRoleArn": "arn:aws:iam::555555555555:role/x", "qualifier": "way-too-long-q"}}
    c = make_ctx(tmp_path, prod_iam=bad)
    res = c.ledger.validate()
    text = "\n".join(res["errors"])
    assert "iam.boundaryArn" in text and "partition aws-us-gov" in text
    assert "iam.bootstrap.deployRoleArn" in text and "account 555555555555" in text
    assert "qualifier" in text
    c2 = make_ctx(tmp_path / "b", prod_iam={"mode": "strict"})
    assert any("iam" in e for e in c2.ledger.validate()["errors"])


# ================================================================ report -> roles (real VAMS v2.6.6 synth)
@pytest.mark.parametrize("name,part,region,count", [("commercial-report", "aws", "us-east-1", 88),
                                                     ("govcloud-report", "aws-us-gov", "us-gov-west-1", 92)])
def test_roles_from_the_real_report(name, part, region, count):
    asm = fx.load(name)
    info = {"partition": part, "region": region, "account": fx.ACCOUNT, "coreStack": fx.STACK[name.split("-")[0]],
            "rolePattern": "prod-*"}
    roles, refs = iam.role_rows(asm, info)
    assert len(roles) == count                      # every role VAMS v2.6.6 creates with this template
    # the 53 references are one existing service-linked role (Role.fromRoleName), not roles to create
    assert [r["roleName"] for r in refs] == ["AWSServiceRoleForApplicationAutoScaling_DynamoDBTable"]
    assert len(refs[0]["constructPaths"]) == 53
    deploy = [r for r in roles if r["phase"] == "deploy"]
    assert 10 <= len(deploy) <= 20 and all(r["phase"] in ("deploy", "runtime") for r in roles)
    assert any(r["purpose"] == "CDK auto-delete-objects handler" and r["phase"] == "deploy" for r in roles)
    # the identity-pool role's trust names a pool created later: PENDING, never widened
    unauth = next(r for r in roles if r["constructPath"].endswith("DefaultUnauthenticatedRole"))
    assert unauth["trustPending"] and iam.PENDING in json.dumps(unauth["trust"])
    fed = "cognito-identity-us-gov.amazonaws.com" if part == "aws-us-gov" else "cognito-identity.amazonaws.com"
    assert f"Federated:{fed}" in unauth["trustPrincipals"]
    text = json.dumps([r["policy"] for r in roles] + [r["managedPolicyArns"] for r in roles])
    assert "(PARTITION)" not in text and "${Token[" not in text
    if part == "aws-us-gov":
        assert "arn:aws:" not in text
    # every IAM inline policy fits (or it-roles.yaml splits it into managed policies)
    assert max(len(json.dumps(r["policy"], separators=(",", ":"))) for r in roles) < iam.INLINE_LIMIT


def test_feature_and_config_option_per_role():
    roles, _ = iam.role_rows(fx.load("govcloud-report"), {"partition": "aws-us-gov", "region": "us-gov-west-1",
                                                           "account": fx.ACCOUNT, "coreStack": "x"})
    by = {r["constructPath"].split("/", 1)[1]: r for r in roles}
    assert by["VPCBuilder/Vpc/vpc-logs/IAMRole"]["configOption"] == "app.useGlobalVpc.enabled"
    conv = next(r for p, r in by.items() if p.startswith("PipelineBuilder/Conversion3dBasicNestedStack"))
    assert conv["configOption"] == "app.pipelines.useConversion3dBasic.enabled"
    assert by["CloudTrail-VAMS/LogsRole"]["configOption"] == "app.addStackCloudTrailLogs"


def test_report_and_precreated_build_give_the_same_digests():
    """The precreated build's report names the IT roles (and passes their ARNs to iam:PassRole); the package
    digest must not change because of that, or every build would look stale."""
    info = {"partition": "aws", "region": "us-east-1", "account": fx.ACCOUNT, "coreStack": "x", "rolePattern": "p-*"}
    a, _ = iam.role_rows(fx.load("commercial-report"), info)
    b, _ = iam.role_rows(fx.load("commercial-precreated"), info)
    assert iam.role_set_digest(a) == iam.role_set_digest(b) and iam.policy_digest(a) == iam.policy_digest(b)
    assert all(r["assignedRoleName"] for r in b) and not any(r["assignedRoleName"] for r in a)
    pipe = next(r for r in a if r["constructPath"].endswith("pipelineService/ServiceRole"))
    assert "arn:aws:iam::111111111111:role/p-*" in json.dumps(pipe["policy"])  # PassRole on the env's VAMS roles


# ================================================================ plan
def test_plan_writes_the_it_package(ictx, tmp_path):
    res = plan_from(ictx, "prod", "commercial-report", tmp_path)
    d = Path(res["dir"])
    assert res["packageId"] == "prod-1" and d == ictx.state_dir / "iam" / "prod" / "1"
    assert {"IT-ROLES.md", "roles.json", "roles.csv", "delta.json", "it-roles.yaml", "it-response.template.yaml",
            "package.json"} <= set(res["files"])
    assert res["counts"]["vamsRoles"] == 88 and res["counts"]["bootstrapRoles"] == 5
    meta = util.read_json(d / "package.json")
    assert all(util.sha256_file(d / f) == h for f, h in meta["files"].items())
    roles = util.read_json(d / "roles.json")
    assert len(list((d / "policies").glob("*.json"))) >= 88 + 5
    assert {v["id"] for v in roles["vraRoles"]} >= {"vra-deploy-prod", "vra-synth-prod", "vra-tools"}
    md = (d / "IT-ROLES.md").read_text()
    assert "Create the **88** roles" in md and "First package for this env" in md
    assert "AWSServiceRoleForApplicationAutoScaling_DynamoDBTable" in md and "CDK bootstrap roles" in md
    assert "iam-apply:prod-1" in md                       # prod is approval-gated
    rows = (d / "roles.csv").read_text().splitlines()
    assert rows[0].startswith("kind,id,suggested_role_name") and len(rows) == 1 + 88 + 5 + len(roles["vraRoles"])
    tpl = iam.load_cfn_yaml((d / "it-roles.yaml").read_text())
    kinds = [r["Type"] for r in tpl["Resources"].values()]
    assert kinds.count("AWS::IAM::Role") == 88 + 5
    assert tpl["Parameters"]["RolePrefix"]["Default"] == "ITVAMS-"
    assert tpl["Parameters"]["PermissionsBoundaryArn"]["Default"] == PROD_IAM["boundaryArn"]
    assert any(p.startswith("Trust") and v["Default"] == iam.PENDING for p, v in tpl["Parameters"].items())
    for r in tpl["Resources"].values():
        if r["Type"] == "AWS::IAM::Role":
            props = r["Properties"]
            assert props["RoleName"]["Fn::Sub"].startswith("${RolePrefix}")
            assert "HasBoundary" in json.dumps(props["PermissionsBoundary"])
            for pol in props.get("Policies") or []:
                assert len(json.dumps(pol["PolicyDocument"], separators=(",", ":"))) <= iam.INLINE_LIMIT
            name = props["RoleName"]["Fn::Sub"].replace("${RolePrefix}", "ITVAMS-")
            assert len(name.replace("${Qualifier}", "hnb659fds").replace("${AWS::AccountId}", fx.ACCOUNT)
                       .replace("${AWS::Region}", "us-east-1")) <= 64
    resp = yaml.safe_load((d / "it-response.template.yaml").read_text())
    assert resp["package"] == "prod-1" and len(resp["roles"]) == 88
    assert all(v.startswith("ITVAMS-") for v in resp["roles"].values())
    assert set(resp["bootstrap"]) >= set(iam.BOOTSTRAP_ROLES.values())


def test_it_roles_template_has_no_yaml_aliases(ictx, tmp_path):
    """CloudFormation rejects YAML aliases ("YAML aliases are not allowed in CloudFormation templates"). Every role
    shares one PermissionsBoundary object, which PyYAML's default dumper writes once as &id001 and then as *id001."""
    res = plan_from(ictx, "prod", "commercial-report", tmp_path)
    d = Path(res["dir"])
    for name in ("it-roles.yaml", "it-response.template.yaml"):
        events = list(yaml.parse((d / name).read_text()))
        assert not [e for e in events if isinstance(e, yaml.AliasEvent)], name
        assert not [e for e in events if getattr(e, "anchor", None)], name
    tpl = iam.load_cfn_yaml((d / "it-roles.yaml").read_text())
    bounds = [r["Properties"]["PermissionsBoundary"] for r in tpl["Resources"].values()
              if r["Type"] == "AWS::IAM::Role"]
    assert len(bounds) == 88 + 5 and all(b == bounds[0] for b in bounds)


def test_engine_writes_yaml_only_through_the_alias_free_dumper():
    """A raw yaml.safe_dump / yaml.dump in engine code would bring aliases back for any reused object."""
    pkg = Path(iam.__file__).parent
    offenders = [f"{p.name}:{i}" for p in pkg.glob("*.py") if p.name != "util.py"
                 for i, line in enumerate(p.read_text().splitlines(), 1)
                 if "yaml.safe_dump(" in line or "yaml.dump(" in line]
    assert not offenders, offenders


def test_govcloud_package_is_partition_clean(tmp_path):
    c = make_ctx(tmp_path, prod_iam=None, gov_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"})
    res = plan_from(c, "gov", "govcloud-report", tmp_path)
    d = Path(res["dir"])
    assert res["counts"]["vamsRoles"] == 92 and res["counts"]["bootstrapRoles"] == 0
    for f in ["it-roles.yaml", "roles.json", *[p.relative_to(d).as_posix() for p in (d / "policies").glob("*.json")
                                                if not p.name.startswith("vra-")]]:
        assert "arn:aws:" not in (d / f).read_text(), f
    assert "cognito-identity-us-gov.amazonaws.com" in (d / "it-roles.yaml").read_text()


def test_precreated_mode_without_a_role_prefix_stops_plan_and_validate(tmp_path):
    """The scoped CDK execution policy passes IT's roles only by the env's prefix; there is no fallback."""
    from vra.ledger import Ledger
    c = make_ctx(tmp_path, prod_iam=None, gov_iam={"mode": "precreated"})
    out = tmp_path / "asm"
    fx.write("govcloud-report", out)
    o = runner.start(c, "iam-plan", {"env": "gov", "report": str(out / iam.REPORT), "modes": None}, env="gov")
    assert o.exit_code == 2 and "needs iam.rolePrefix" in o.message
    errs = Ledger(c.ledger.root).validate()["errors"]
    assert any("envs/gov" in x and "needs iam.rolePrefix" in x for x in errs), errs


def test_boundary_mode_lists_the_actions_the_boundary_must_allow(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary", "boundaryArn": PROD_IAM["boundaryArn"]})
    res = plan_from(c, "prod", "commercial-report", tmp_path)
    d = Path(res["dir"])
    assert "boundary-actions.json" in res["files"] and "it-roles.yaml" not in res["files"]
    acts = util.read_json(d / "boundary-actions.json")["policy"]["Statement"][0]["Action"]
    assert "dynamodb:GetItem" in acts and "s3:PutBucketNotification" in acts
    resp = yaml.safe_load((d / "it-response.template.yaml").read_text())
    assert resp["boundaryArn"] == PROD_IAM["boundaryArn"] and "roles" not in resp


def test_plan_refuses_a_standard_env(ictx, tmp_path):
    out = fx.write("commercial-report", tmp_path / "a")
    o = runner.start(ictx, "iam-plan", {"env": "dev", "report": str(out / iam.REPORT)}, env="dev")
    assert o.exit_code == errors.USAGE_ERROR and "iam.mode is standard" in o.message
    o = runner.start(make_ctx(tmp_path / "x"), "iam-plan",
                     {"env": "dev", "report": str(out / iam.REPORT), "modes": ["boundary"]}, env="dev")
    assert o.exit_code == 0  # --mode overrides env.yaml for planning


def test_second_package_carries_only_the_delta(ictx, tmp_path):
    plan_from(ictx, "prod", "commercial-report", tmp_path)

    def upstream_merge(asm):
        fx.add_role(asm)
        fx.add_action(asm)
        asm.report["roles"] = [r for r in asm.report["roles"] if not r["roleConstructPath"].endswith("/sendEmail/ServiceRole")]
    res = plan_from(ictx, "prod", "commercial-report", tmp_path, upstream_merge)
    assert res["packageId"] == "prod-2"
    assert res["delta"] == {"against": "prod-1", "added": 1, "removed": 1, "changed": 1, "bootstrapChanged": 0}
    d = util.read_json(Path(res["dir"]) / "delta.json")
    assert d["changed"][0]["addedActions"] == ["s3:GetObjectAcl"]
    md = (Path(res["dir"]) / "IT-ROLES.md").read_text()
    assert "Against `prod-1`: 1 added, 1 removed, 1 changed" in md and "NewUpstreamHandler" in md


# ================================================================ apply
def test_apply_writes_the_overlay_and_records_it(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary+precreated+bootstrap", **{k: v for k, v in PROD_IAM.items()
                                                                                if k != "mode"}})
    c.ledger.env("prod")["requiresApproval"] = False   # gated apply is tested below
    plan_from(c, "prod", "commercial-report", tmp_path)
    res = iam.apply(c, "prod", response_for(c, "prod"))
    od = c.ledger.overlay_dir("prod")
    cfg = util.read_json(od / iam.CONFIG_JSON)
    assert cfg["app"]["iamRoleConfig"] == {"useCustomBootstrapRoles": True, "useCustomVamsStackRoles": True}
    assert cfg["app"]["baseStackName"] == "prod"      # the rest of the overlay config is kept
    rc = util.read_json(od / iam.IAM_ROLE_CONFIG)
    assert rc["vamsStacks"]["generateReportOnly"] is False and len(rc["vamsStacks"]["precreatedRoles"]) == 88
    assert rc["bootstrap"]["deployRoleArn"].endswith(":role/ITVAMS-cdk-hnb659fds-deploy-role-111111111111-us-east-1")
    merge = util.read_json(od / iam.CDK_JSON_MERGE)
    assert merge == {"context": {"environments": {"aws": {"PermissionBoundaryArn": PROD_IAM["boundaryArn"],
                                                          "IamRoleNamePrefix": "ITVAMS-"}}}}
    applied = iam.applied_record(c, "prod")
    assert applied["packageId"] == "prod-1" and applied["overlay"][iam.IAM_ROLE_CONFIG]
    assert c.ledger.history("prod")[-1]["action"] == "iam-apply"
    assert "commit and push the ledger overlay" in res["next"]


@pytest.mark.parametrize("edit,needle", [
    (lambda d: d["roles"].pop(next(iter(d["roles"]))), "have no name"),
    (lambda d: d["roles"].update({"vams-core-prod-us-east-1/Nope/Role": "ITVAMS-x"}), "not in the package"),
    (lambda d: d.update(package="prod-9"), "expected 'prod-1'"),
    (lambda d: d["roles"].update({next(iter(d["roles"])): "arn:aws-us-gov:iam::111111111111:role/ITVAMS-a"}),
     "is not in aws account"),
    (lambda d: d["roles"].update({next(iter(d["roles"])): "arn:aws:iam::111111111111:role/team/ITVAMS-a"}),
     "has a role path"),
    (lambda d: d["roles"].update({next(iter(d["roles"])): "other-name"}), "does not start with iam.rolePrefix"),
    (lambda d: d["roles"].update({k: "ITVAMS-same" for k in list(d["roles"])[:2]}), "more than one construct"),
    (lambda d: d["bootstrap"].pop("lookupRoleArn"), "bootstrap.lookupRoleArn is required"),
    (lambda d: d["bootstrap"].update(deployRoleArn="arn:aws:iam::555555555555:role/x"), "account 555555555555"),
])
def test_apply_rejects_a_response_that_does_not_answer_the_package(tmp_path, edit, needle):
    c = make_ctx(tmp_path)
    c.ledger.env("prod")["requiresApproval"] = False
    plan_from(c, "prod", "commercial-report", tmp_path)
    with pytest.raises(UsageError) as ei:
        iam.apply(c, "prod", response_for(c, "prod", edit=edit))
    assert needle in ei.value.message
    assert iam.applied_record(c, "prod") is None
    assert not (c.ledger.overlay_dir("prod") / iam.IAM_ROLE_CONFIG).exists()


def test_apply_accepts_role_arns_and_reduces_them_to_names(tmp_path):
    c = make_ctx(tmp_path)
    c.ledger.env("prod")["requiresApproval"] = False
    plan_from(c, "prod", "commercial-report", tmp_path)

    def arns(d):
        d["roles"] = {k: f"arn:aws:iam::111111111111:role/{v}" for k, v in d["roles"].items()}
    iam.apply(c, "prod", response_for(c, "prod", edit=arns))
    rc = util.read_json(c.ledger.overlay_dir("prod") / iam.IAM_ROLE_CONFIG)
    assert all(v.startswith("ITVAMS-") and ":" not in v for v in rc["vamsStacks"]["precreatedRoles"].values())


def test_apply_dry_run_shows_the_overlay_and_writes_nothing(tmp_path):
    c = make_ctx(tmp_path)              # gated: the dry run needs no approval yet
    plan_from(c, "prod", "commercial-report", tmp_path)
    c.dry_run = True
    res = iam.apply(c, "prod", response_for(c, "prod"))
    assert res["dryRun"] and len(res["proposed"][iam.IAM_ROLE_CONFIG]["vamsStacks"]["precreatedRoles"]) == 88
    assert iam.applied_record(c, "prod") is None
    assert not (c.ledger.overlay_dir("prod") / iam.IAM_ROLE_CONFIG).exists()


def test_apply_on_a_gated_env_needs_a_single_use_approval(tmp_path):
    c = make_ctx(tmp_path)
    plan_from(c, "prod", "commercial-report", tmp_path)
    resp = response_for(c, "prod")
    with pytest.raises(NeedsHuman):
        iam.apply(c, "prod", resp)
    subject = iam.approval_subject("prod-1", util.sha256_file(resp))   # package AND response
    approve(c.ledger, "prod", subject, "release-manager@example.com", scope=["iam-apply"])
    res = iam.apply(c, "prod", resp)
    assert res["packageId"] == "prod-1" and iam.applied_record(c, "prod")["approval"]
    with pytest.raises(NeedsHuman) as ei:
        iam.apply(c, "prod", resp)
    assert "already used" in ei.value.message


def test_apply_answers_the_latest_package_only(ictx, tmp_path):
    ictx.ledger.env("prod")["requiresApproval"] = False
    plan_from(ictx, "prod", "commercial-report", tmp_path)
    old = response_for(ictx, "prod")
    plan_from(ictx, "prod", "commercial-report", tmp_path, fx.add_role)
    with pytest.raises(UsageError):
        iam.apply(ictx, "prod", old, package=1)
    with pytest.raises(UsageError) as ei:
        iam.apply(ictx, "prod", old)
    assert "expected 'prod-2'" in ei.value.message


def test_iam_apply_without_a_terminal_takes_the_documented_single_bound_approval(tmp_path, monkeypatch):
    """Live test: `vra iam apply --env test --from it-response.yaml --approval <record bound to the response>` (the
    documented form) was refused outside a terminal: the human gate wanted a second record, subject
    human:iam-apply, through the global --human-approval, and its message named --approval. The bound record is
    now the person's authorization, required on every env and recorded."""
    from vra import human
    monkeypatch.setattr(human, "_tty", lambda: False)
    monkeypatch.delenv("VRA_AGENT", raising=False)
    c = make_ctx(tmp_path)
    plan_from(c, "prod", "commercial-report", tmp_path)
    resp = response_for(c, "prod")
    subject = iam.approval_subject("prod-1", util.sha256_file(resp))
    rec = approve(c.ledger, "prod", subject, "release-manager@example.com", scope=["iam-apply"])
    p = cli.build_parser()
    ns = p.parse_args(["--non-interactive", "iam", "apply", "--env", "prod", "--from", str(resp)])
    with pytest.raises(NeedsHuman):             # no record at all: still refused
        cli.human_gate(c, ns)
    ns = p.parse_args(["--non-interactive", "iam", "apply", "--env", "prod", "--from", str(resp),
                       "--approval", str(rec)])
    assert cli.human_gate(c, ns) == []
    res = iam.apply(c, "prod", resp, ns.approval, authorization=runner.human_authorization(c, "iam-apply"))
    assert res["packageId"] == "prod-1"
    auth = iam.applied_record(c, "prod")["authorization"]
    assert auth and auth[0]["how"] == "approval" and auth[0]["approver"] == "release-manager@example.com"


def test_iam_apply_without_an_approval_names_the_bound_record_not_a_human_record(tmp_path, monkeypatch):
    """Live test: outside a terminal and without --approval, the refusal told the person to write a record
    with subject human:iam-apply, which iam apply then refuses; it names the record bound to the response."""
    from vra import human
    monkeypatch.setattr(human, "_tty", lambda: False)
    monkeypatch.delenv("VRA_AGENT", raising=False)
    monkeypatch.delenv("VRA_MCP", raising=False)
    c = make_ctx(tmp_path)
    ns = cli.build_parser().parse_args(["--non-interactive", "iam", "apply", "--env", "prod", "--from", "r.yaml"])
    with pytest.raises(NeedsHuman) as ei:
        cli.human_gate(c, ns)
    msg = str(ei.value)
    assert "iam-apply:<packageId>:" in msg and "approvalSubject" in msg
    assert "human:iam-apply" not in msg


def test_iam_apply_is_human_only_for_agents(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VRA_AGENT", "1")
    code = cli.main(["--ledger", str(tmp_path / "l"), "--json", "iam", "apply", "--env", "prod", "--from", "r.yaml"])
    assert code == errors.NEEDS_HUMAN
    assert "human-only" in json.loads(capsys.readouterr().out)["message"]


def test_overlay_merge_file_keeps_the_forks_cdk_json(ictx, tmp_path):
    src = tmp_path / "src"
    util.write_json(src / "infra/cdk.json", {"app": "npx ts-node bin/infra.ts", "context": {
        "@aws-cdk/core:stackRelativeExports": True,
        "environments": {"common": {"SolutionName": "AWSVisualAssetManagementSystem"},
                         "aws": {"PermissionBoundaryArn": "", "IamRoleNamePrefix": ""}}}})
    original = (src / "infra/cdk.json").read_bytes()
    util.write_json(ictx.ledger.overlay_dir("prod") / iam.CDK_JSON_MERGE,
                    {"context": {"environments": {"aws": {"PermissionBoundaryArn": "arn:b", "IamRoleNamePrefix": "P-"}}}})
    with build_mod.overlay_applied(ictx, src, "prod") as applied:
        merged = util.read_json(src / "infra/cdk.json")
        assert iam.CDK_JSON_MERGE in applied
    assert merged["context"]["@aws-cdk/core:stackRelativeExports"] is True
    assert merged["context"]["environments"]["common"]["SolutionName"] == "AWSVisualAssetManagementSystem"
    assert merged["context"]["environments"]["aws"] == {"PermissionBoundaryArn": "arn:b", "IamRoleNamePrefix": "P-"}
    assert (src / "infra/cdk.json").read_bytes() == original   # restored after synth


# ================================================================ gates: preflight + resume
def _ungate(c):
    c.ledger.env("prod")["requiresApproval"] = False


def test_gate_blocks_until_the_package_is_applied(ictx, tmp_path):
    _ungate(ictx)
    run = new_run(ictx)
    with pytest.raises(BlockedOnIT) as ei:
        iam.gate(ictx, run, "prod", "preflight.prod.iam")
    assert "no IT role package yet" in ei.value.message
    req = run.state["itRequests"][-1]
    assert req["kind"] == "iam-package" and req["status"] == "open" and Path(req["path"]).exists()
    assert "vra iam plan --env prod" in Path(req["path"]).read_text()
    plan_from(ictx, "prod", "commercial-report", tmp_path)
    with pytest.raises(BlockedOnIT) as ei:
        iam.gate(ictx, run, "prod", "preflight.prod.iam")
    assert "prod-1" in ei.value.message and "not applied" in ei.value.message
    assert "IT-ROLES.md" in Path(run.state["itRequests"][-1]["path"]).read_text()
    iam.apply(ictx, "prod", response_for(ictx, "prod"))
    assert iam.gate(ictx, run, "prod", "preflight.prod.iam")["package"] == "prod-1"
    # an overlay edited after apply is a person's change to look at, not IT's
    p = ictx.ledger.overlay_dir("prod") / iam.IAM_ROLE_CONFIG
    p.write_text(p.read_text().replace("ITVAMS-", "XX-", 1))
    with pytest.raises(NeedsHuman):
        iam.gate(ictx, run, "prod", "preflight.prod.iam")


def test_gate_blocks_when_env_yaml_changed_the_mode(ictx, tmp_path):
    _ungate(ictx)
    plan_from(ictx, "prod", "commercial-report", tmp_path)
    iam.apply(ictx, "prod", response_for(ictx, "prod"))
    ictx.ledger.env("prod")["iam"] = {**PROD_IAM, "mode": "precreated"}
    with pytest.raises(BlockedOnIT) as ei:
        iam.gate(ictx, new_run(ictx), "prod", "s")
    assert "planned for iam mode precreated+bootstrap" in ei.value.message


def test_preflight_exits_10_and_resumes_after_iam_apply(ictx, tmp_path):
    _ungate(ictx)
    out = runner.start(ictx, "preflight", {"env": "prod", "workflow": "deploy"}, env="prod")
    assert out.exit_code == errors.BLOCKED_ON_IT
    run_id = out.run.run_id
    assert [r["kind"] for r in out.to_dict()["itRequests"]] == ["iam-package"]
    ictx.run_id = None
    plan_from(ictx, "prod", "commercial-report", tmp_path)
    iam.apply(ictx, "prod", response_for(ictx, "prod"))
    res = runner.resume(ictx, run_id)
    assert res.exit_code == 0, res.message
    assert res.run.state["result"]["preflight.prod.iam"]["package"] == "prod-1"


def test_standard_env_preflight_has_no_iam_step(ictx):
    out = runner.start(ictx, "preflight", {"env": "dev", "workflow": "deploy"}, env="dev")
    assert out.exit_code == 0 and "preflight.dev.iam" not in out.run.state["result"]


# ================================================================ gates: build
def _synth_handler(state):
    """Fake `cdk synth`: lays out the fixture assembly ``state['name']``; exit 1 for the partial synth (VAMS
    fails synth on an unmapped precreated role but still writes the report)."""
    def h(argv, cwd, env):
        if "synth" in argv and "-o" in argv:
            out = Path(argv[argv.index("-o") + 1])
            fx.write(state["name"], out, state.get("mutate"))
            state.setdefault("synths", []).append(state["name"])
            return subprocess.CompletedProcess(argv, 1 if state.get("fail") else 0, "", "")
        return None
    return h


def test_build_blocks_on_new_upstream_roles_and_resumes(tmp_path, repos):
    state = {"name": "commercial-precreated"}
    c = make_ctx(tmp_path, repos, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-"},
                 handler=_synth_handler(state))
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    assert out.run.find_step("build.iam.prod")["status"] == "done"
    assert out.run.step_result("build.synth.prod")["iam"]["roles"] == 88
    c.run_id = None
    # upstream merge: a new handler role IT has not created; VAMS fails synth and names it in the report
    state.update(mutate=lambda a: fx.add_role(a, missing=True), fail=True)
    git_tip = repos["fork_work"]
    (git_tip / "infra/new.txt").write_text("upstream change\n")
    from conftest import git
    git(git_tip, "add", "-A")
    git(git_tip, "commit", "-q", "-m", "upstream merge")
    git(git_tip, "push", "-q", str(repos["fork"]), "HEAD:main")
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert "prod-2" in out.message and "1 new" in out.message
    pkg = iam.load_package(c, "prod")
    assert pkg["delta"]["added"] == 1 and pkg["source"]["build"] == out.run.state["build"]
    c.run_id = None
    iam.apply(c, "prod", response_for(c, "prod"))
    # IT created it: the next synth maps every role
    state.update(mutate=lambda a: fx.add_role(a, missing=False), fail=False)
    res = runner.resume(c, out.run.run_id)
    assert res.exit_code == 0, res.message
    assert res.run.step_result("build.synth.prod")["iam"]["unmapped"] == 0


def test_build_refuses_a_boundary_vams_drops(tmp_path, repos):
    state = {"name": "commercial-boundary"}
    c = make_ctx(tmp_path, repos, prod_iam={"mode": "boundary", "boundaryArn":
                                            "arn:aws:iam::111111111111:policy/it-vams-boundary",
                                            "rolePrefix": "ITVAMS-"}, handler=_synth_handler(state))
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == errors.NEEDS_HUMAN
    assert "88 IAM role(s)" in out.message and "IamRoleTransform" in out.message
    c.run_id = None
    state["name"] = "commercial-boundary-fixed"      # a fork-side fix of the aspect
    res = runner.resume(c, out.run.run_id)
    assert res.exit_code == 0, res.message
    assert res.run.step_result("build.synth.prod")["iam"]["rolesWithoutBoundary"] == 0


def test_cdk_permissions_boundary_name_is_checked_too(tmp_path):
    """env.yaml permissionsBoundaryName (vra 0.4) is passed as @aws-cdk/core:permissionsBoundary; VAMS 2.6.6
    removes it again, so a build that relied on it deployed unbounded roles."""
    c = make_ctx(tmp_path, prod_iam=None)
    c.ledger.env("prod")["permissionsBoundaryName"] = "it-vams-boundary"
    out = fx.write("commercial-boundary", tmp_path / "a")
    with pytest.raises(NeedsHuman):
        iam.check_assembly(c, new_run(c, "build"), "prod", out, "build.synth.prod")
    ok = fx.write("commercial-boundary-fixed", tmp_path / "b")
    assert iam.check_assembly(c, new_run(c, "build"), "prod", ok, "build.synth.prod")["rolesWithoutBoundary"] == 0


def test_boundary_mode_build_blocks_on_roles_the_package_does_not_list(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary", "boundaryArn":
                                     "arn:aws:iam::111111111111:policy/it-vams-boundary"})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path,
              lambda a: a.report["roles"].pop(next(i for i, r in enumerate(a.report["roles"]) if r.get("missing"))))
    iam.apply(c, "prod", response_for(c, "prod"))
    out = fx.write("commercial-boundary-fixed", tmp_path / "b")
    with pytest.raises(BlockedOnIT) as ei:
        iam.check_assembly(c, new_run(c, "build"), "prod", out, "build.synth.prod")
    assert "1 IAM role(s) the applied IT package" in ei.value.message


def test_bootstrap_roles_must_be_in_the_synthesized_assembly(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "bootstrap", "cliCredentialsSynthesizer": True})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    assert iam.check_assembly(c, new_run(c, "build"), "prod", fx.write("commercial-cli", tmp_path / "a"),
                              "s")["bootstrap"] == "matches"
    # the default synthesizer (overlay not applied): the stacks still name the cdk-hnb659fds-* roles
    with pytest.raises(NeedsHuman):
        iam.check_assembly(c, new_run(c, "build"), "prod", fx.write("commercial-report", tmp_path / "b"), "s")


# ================================================================ verify
class FakeIam:
    """GetRole / GetPolicy / SimulatePrincipalPolicy over a dict of roles."""

    def __init__(self, roles: dict, policies=(), deny=(), get_denied=False):
        self.roles, self.policies, self.deny, self.get_denied = roles, set(policies), set(deny), get_denied

    def _err(self, code):
        from botocore.exceptions import ClientError
        return ClientError({"Error": {"Code": code, "Message": code}}, "x")

    def get_role(self, RoleName):
        if self.get_denied:
            raise self._err("AccessDenied")
        if RoleName not in self.roles:
            raise self._err("NoSuchEntity")
        return {"Role": self.roles[RoleName]}

    def get_policy(self, PolicyArn):
        if PolicyArn not in self.policies:
            raise self._err("NoSuchEntity")
        return {"Policy": {"Arn": PolicyArn}}

    def simulate_principal_policy(self, **kw):
        return {"EvaluationResults": [{"EvalActionName": a, "EvalResourceName": r,
                                       "EvalDecision": "implicitDeny" if a in self.deny else "allowed"}
                                      for a in kw["ActionNames"] for r in kw.get("ResourceArns") or ["*"]],
                "IsTruncated": False}


def _live_roles(ctx, boundary=PROD_IAM["boundaryArn"], drop=None, trust_drop=None):
    pkg = iam.load_package(ctx, "prod")
    applied = iam.applied_record(ctx, "prod")
    by = {r["constructPath"]: r for r in pkg["roles"]["roles"]}
    out = {}
    for path, name in applied["roles"].items():
        if name == drop:
            continue
        trust = by[path]["trust"] if name != trust_drop else []
        out[name] = {"RoleName": name, "Arn": f"arn:aws:iam::111111111111:role/{name}",
                     "PermissionsBoundary": {"PermissionsBoundaryArn": boundary},
                     "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": trust}}
    return out


def _verify_ctx(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "precreated", "rolePrefix": "ITVAMS-",
                                     "boundaryArn": PROD_IAM["boundaryArn"]})
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    return c


def test_verify_passes_for_the_roles_it_created(tmp_path):
    c = _verify_ctx(tmp_path)
    c.aws.clients[("prod", "iam")] = FakeIam(_live_roles(c))
    res = iam.verify(c, "prod")
    assert res["ok"] and not res["problems"] and len(res["checked"]) == 88


def test_verify_reports_missing_unbounded_untrusted_and_denied(tmp_path):
    c = _verify_ctx(tmp_path)
    names = sorted(iam.applied_record(c, "prod")["roles"].values())
    live = _live_roles(c, drop=names[0], trust_drop=names[1])
    live[names[2]]["PermissionsBoundary"] = {}
    c.aws.clients[("prod", "iam")] = FakeIam(live, deny={"dynamodb:GetItem"})
    res = iam.verify(c, "prod")
    text = "\n".join(res["problems"])
    assert f"role {names[0]} does not exist" in text
    assert f"role {names[1]} does not trust" in text
    assert f"role {names[2]} does not carry the boundary" in text
    assert "is not allowed dynamodb:GetItem" in text and res["ok"] is False


def test_verify_labels_grants_vams_never_makes_effective_and_keeps_real_gaps(tmp_path):
    """Live test: on prodfr, `vra iam verify` reported 136 problems for IT's 87 roles: `kms:ListKeys` /
    `kms:ListAliases` granted on `key/*` (they take only `*`) and SNS subscription actions granted on the topic
    pattern. VAMS's own policy never makes these effective, so they are listed under `notEffective` with that label
    instead of as IT gaps; a real denial stays a problem."""
    from vra import permissions
    c = _verify_ctx(tmp_path)
    c.aws.clients[("prod", "iam")] = FakeIam(_live_roles(c))
    key, topic = "arn:aws:kms:us-east-1:111111111111:key/*", "arn:aws:sns:us-east-1:111111111111:vams-core-x*"
    denied = [{"action": "kms:ListKeys", "resource": key}, {"action": "kms:ListAliases", "resource": key},
              {"action": "sns:Unsubscribe", "resource": topic},
              {"action": "dynamodb:GetItem", "resource": "arn:aws:dynamodb:us-east-1:111111111111:table/t"}]
    orig = permissions.simulate
    permissions.simulate = lambda iam_, arn, stmts: {"denied": denied, "allowed": []}
    try:
        res = iam.verify(c, "prod")
    finally:
        permissions.simulate = orig
    n = len(iam.applied_record(c, "prod")["roles"])
    assert len(res["notEffective"]) == 3 * n and all(iam.NOT_EFFECTIVE in x for x in res["notEffective"])
    assert len(res["problems"]) == n and all("is not allowed dynamodb:GetItem" in p for p in res["problems"])
    assert res["ok"] is False
    assert iam.ineffective_grant("kms:ListKeys", "*") is False      # granted on `*` and denied: a real gap
    assert iam.ineffective_grant("sns:Unsubscribe", "arn:aws:sns:us-east-1:111111111111:vams-core-x*")
    assert not iam.ineffective_grant("sns:Unsubscribe", "arn:aws:sns:us-east-1:111111111111:vams-core-x:1a2b")
    assert not iam.ineffective_grant("sns:Publish", "arn:aws:sns:us-east-1:111111111111:vams-core-x")


def test_verify_reports_what_it_could_not_check(tmp_path):
    c = _verify_ctx(tmp_path)
    c.aws.clients[("prod", "iam")] = FakeIam({}, get_denied=True)
    res = iam.verify(c, "prod")
    assert not res["problems"] and len(res["notChecked"]) == 88
    assert all("denied" in x for x in res["notChecked"])


def test_verify_cli_exits_10_on_gaps(tmp_path, monkeypatch, capsys):
    c = _verify_ctx(tmp_path)
    c.aws.clients[("prod", "iam")] = FakeIam({})
    monkeypatch.setattr(cli, "make_context", lambda ns: c)
    code = cli.main(["--json", "iam", "verify", "--env", "prod"])
    assert code == errors.BLOCKED_ON_IT
    assert "problem(s) for IT" in json.loads(capsys.readouterr().out)["message"]


# ================================================================ bootstrap roles, setup, MCP
def test_packaged_bootstrap_roles_come_from_the_cdk_template():
    from vra import data as vra_data
    d = json.loads(vra_data.text("cdk-bootstrap-roles.json"))
    assert d["bootstrapVersion"] == "32" and "aws-cdk 2.1135.0" in d["source"]
    assert set(d["roles"]) == set(iam.BOOTSTRAP_ROLES)
    deploy = json.dumps(d["roles"]["deploy"]["policy"])
    assert "cloudformation:CreateChangeSet" in deploy and "${CloudFormationExecutionRole.Arn}" not in deploy
    assert d["roles"]["cfn-exec"]["trustPrincipals"] == ["Service:cloudformation.amazonaws.com"]
    assert "Fn::If" not in json.dumps(d)


def test_extract_bootstrap_roles_from_template_text():
    text = """
Resources:
  CdkBootstrapVersion: {Type: AWS::SSM::Parameter, Properties: {Value: "99"}}
  DeploymentActionRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Statement:
          - {Action: sts:AssumeRole, Effect: Allow, Principal: {AWS: !Ref AWS::AccountId}}
          - !If [HasTrustedAccounts, {Action: sts:AssumeRole, Effect: Allow, Principal: {AWS: !Ref TrustedAccounts}}, !Ref AWS::NoValue]
      Policies:
        - PolicyName: default
          PolicyDocument:
            Statement:
              - {Action: iam:PassRole, Effect: Allow, Resource: !Sub "${CloudFormationExecutionRole.Arn}"}
"""
    for lid in ("CloudFormationExecutionRole", "LookupRole", "FilePublishingRole", "ImagePublishingRole"):
        text += f"  {lid}: {{Type: AWS::IAM::Role, Properties: {{}}}}\n"
    d = iam.extract_bootstrap_roles(iam.load_cfn_yaml(text))
    assert d["bootstrapVersion"] == "99"
    assert len(d["roles"]["deploy"]["trust"]) == 1          # no trusted accounts by default
    assert "${RolePrefix}cdk-${Qualifier}-cfn-exec-role" in json.dumps(d["roles"]["deploy"]["policy"])


def test_setup_asks_the_iam_mode_per_env(ctx, monkeypatch):
    from vra import setup
    answers = {"project": "x", "upstream": "u", "fork": "f", "githost": {"type": "local"},
               "envs": {"dev": {"account": "111111111111", "region": "us-east-1"},
                        "prod": {"account": "222222222222", "region": "us-east-1", "approvers": ["a@example.com"],
                                 "promotesFrom": "dev",
                                 "iam": {"mode": "precreated+bootstrap", "rolePrefix": "ITVAMS-",
                                         "boundaryArn": "arn:aws:iam::222222222222:policy/b",
                                         "bootstrap": {"qualifier": "abc"}}}}}
    data = setup.collect(ctx, answers, interactive=False)
    assert "iam" not in data["envs"]["dev"] or data["envs"]["dev"]["iam"] is None
    assert data["envs"]["prod"]["iam"] == {"mode": "precreated+bootstrap", "boundaryArn":
                                           "arn:aws:iam::222222222222:policy/b", "rolePrefix": "ITVAMS-",
                                           "cliCredentialsSynthesizer": False, "bootstrap": {"qualifier": "abc"}}
    with pytest.raises(UsageError):
        setup.collect(ctx, {**answers, "envs": {"dev": {"account": "1" * 12, "iam": {"mode": "bogus"}}}}, False)


def test_mcp_exposes_plan_and_verify_but_never_apply():
    from vra import mcp_server
    cfg = mcp_server.Config(enable_writes=True, enable_gated=True)
    tools = {t.name: t.tier for t in mcp_server.tools(cfg, runner=lambda argv, c: (0, "{}", ""),
                                                      gated_check=lambda env: True)}
    assert tools["iam_verify"] == mcp_server.READ and tools["iam_plan"] == mcp_server.WRITE
    assert not any("apply" in n for n in tools if n.startswith("iam"))


# ================================================================ write_package (other role sets: the pipeline)
def _pipeline_roles():
    return [
        {"roleConstructPath": "vra-pipeline/Pipeline/Role", "roleName": "missing role", "missing": True,
         "assumeRolePolicy": [{"Action": "sts:AssumeRole", "Effect": "Allow",
                               "Principal": {"Service": "codepipeline.amazonaws.com"}}],
         "managedPolicyArns": [], "managedPolicyStatements": [],
         "identityPolicyStatements": [{"Action": "s3:GetObject", "Effect": "Allow",
                                       "Resource": "arn:(PARTITION):s3:::vra-artifacts-(ACCOUNT)-(REGION)/*"},
                                      {"Action": "sts:AssumeRole", "Effect": "Allow",
                                       "Resource": "arn:(PARTITION):iam::(ACCOUNT):role/${aws:username}"}]},
        {"roleConstructPath": "vra-pipeline/Build/Role", "roleName": "it-pl-build", "missing": False,
         "assumeRolePolicy": [{"Action": "sts:AssumeRole", "Effect": "Allow",
                               "Principal": {"Service": "codebuild.amazonaws.com"}}],
         "managedPolicyArns": ["arn:(PARTITION):iam::aws:policy/AWSCodeBuildDeveloperAccess"],
         "managedPolicyStatements": [], "identityPolicyStatements": []},
    ]


def test_write_package_for_the_pipeline_roles(tmp_path):
    root = tmp_path / "state" / "iam" / "pipeline"
    res = iam.write_package(_pipeline_roles(), root, scope="pipeline", title="vra pipeline roles",
                            name_prefix="ITVRA-", purposes={"vra-pipeline/Build/Role": "CodeBuild for vra"},
                            unresolved=["arn:aws:s3:::generated-*"])
    assert res["n"] == 1 and res["roles"] == 2 and res["dir"] == str(root / "1")
    assert res["diff"] == {"added": ["vra-pipeline/Build/Role", "vra-pipeline/Pipeline/Role"], "removed": [],
                           "changed": []}
    d = Path(res["dir"])
    tpl = iam.load_cfn_yaml((d / "it-roles.yaml").read_text())
    text = json.dumps(tpl)
    assert "(ACCOUNT)" not in text and "(PARTITION)" not in text
    assert "arn:${AWS::Partition}:s3:::vra-artifacts-${AWS::AccountId}-${AWS::Region}/*" in text   # Fn::Sub
    assert "${!aws:username}" in text                                       # IAM variable escaped in Fn::Sub
    names = sorted(json.dumps(r["Properties"]["RoleName"]) for r in tpl["Resources"].values()
                   if r["Type"] == "AWS::IAM::Role")
    assert '"it-pl-build"' in names                      # a name the stack already has is kept as is
    assert any(n.startswith('{"Fn::Sub": "${RolePrefix}vra-pl-') for n in names)   # vra.pipeline_iam naming
    assert util.read_json(d / "diff.json")["previous"] is None
    assert util.read_json(d / "roles.json")["unresolved"] == ["arn:aws:s3:::generated-*"]
    md = (d / "IT-ROLES.md").read_text()
    assert "vra pipeline roles" in md and "CodeBuild for vra" in md and "generated-*" in md
    rows = util.read_json(d / "roles.json")["roles"]
    assert {r["suggestedRoleName"] for r in rows} >= {"it-pl-build"}
    again = iam.write_package(_pipeline_roles()[:1], root, scope="pipeline", title="vra pipeline roles")
    assert again["n"] == 2 and again["diff"]["removed"] == ["vra-pipeline/Build/Role"]
