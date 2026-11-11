# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CI lanes against the 0.4.0 engine (offline).

- CI roles (GitHub / GitLab OIDC, generic): state-store access as policy decisions: run state, create-only
  history and bundles, approval claims, no deletes, lock items limited to run leases and the role's own envs;
  kms:Verify on approval signing keys, never kms:Sign; generic CI trusts its agent role, never OIDC.
- approval_record.py: generic records bound to $VRA_RUN_REF; human-only records (subject human:<action>, scope
  [<action>]) valid against the engine's approval schema and its human-only action list; CI never adds restore
  scopes (restore-auth-tables included).
- ci_graph.py: topology from `vra ledger graph`; requires-approval and required suites rendered and checked; a
  generic step list.
- Every CI deploy waits for a busy env lock (--lock-wait), passes --approval only after asking the ledger, never
  exports a VAMS config variable, never passes a human-only flag without a human approval record; run-vra.sh
  refuses VAMS config variables and wires VRA_HUMAN_APPROVAL; the generic lane prints `vra watch --print-cron`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[2]
CICD = PKG / "cicd"
SCRIPTS = CICD / "codebuild" / "scripts"
LEDGER = PKG / "ledger-template"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfn = _load("cfn_eval", PKG / "iam" / "tests" / "cfn_eval.py")
lanes = _load("test_ci_lanes_helpers", CICD / "tests" / "test_ci_lanes.py")
writer = _load("approval_record", SCRIPTS / "approval_record.py")
ci_graph = _load("ci_graph", SCRIPTS / "ci_graph.py")

BUCKET = "vra-state-111111111111-us-east-1"
B = f"arn:aws:s3:::{BUCKET}"
TABLE = "arn:aws:dynamodb:us-east-1:111111111111:table/vra-locks"
KEY = "arn:aws:kms:us-east-1:111111111111:key/state"


# ------------------------------------------------------------------ CI roles: state store
def _state(env: str, provider: str = "github", **over) -> list[dict]:
    _, stmts = lanes._oidc_full(provider, env, StateBucketName=BUCKET, StateKeyArn=KEY, **over)
    return stmts


def _lock_keys(stmts: list[dict]) -> list[str]:
    st = next(s for s in stmts if s.get("Sid") == "StateLocks")
    return st["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"]


def test_ci_roles_take_only_their_own_locks():
    assert _lock_keys(_state("prod")) == ["run#*", "env#prod"]
    assert _lock_keys(_state("build", DeployableEnvs=["none"], AllowSynthRoles="true")) == ["run#*"]
    assert _lock_keys(_state("prod-tests", "gitlab", DeployableEnvs=["none"], TestEnvs=["prod"])) == ["run#*"]
    watch = _state("vra-watch", DeployableEnvs=["dev", "dev-preview"], AllowWatchLock="true")
    assert _lock_keys(watch) == ["run#*", "env#watch", "env#dev", "env#dev-preview"]
    S = _state("prod")
    ok = lambda key, a="dynamodb:PutItem": cfn.allowed(S, a, TABLE, {"dynamodb:LeadingKeys": [key]})  # noqa: E731
    assert ok("env#prod") and ok("run#r1") and ok("env#prod", "dynamodb:DeleteItem") and ok("run#r1", "dynamodb:GetItem")
    assert not ok("env#test") and not ok("env#watch") and not ok("config#x")
    assert not cfn.allowed(S, "dynamodb:UpdateItem", TABLE, {"dynamodb:LeadingKeys": ["env#prod"]})


@pytest.mark.parametrize("provider", ["github", "gitlab", "generic"])
def test_ci_roles_write_history_and_bundles_create_only_and_never_delete(provider):
    over = {"TrustedPrincipalArns": ["arn:aws:iam::111111111111:role/jenkins-prod"]} if provider == "generic" else {}
    S = _state("prod", provider, **over)
    put = lambda key, **h: cfn.allowed(S, "s3:PutObject", f"{B}/{key}", h)  # noqa: E731
    for key in ("vra/history/prod/000000000007-r1.json", "vra/artifacts/b1/manifest.json"):
        assert not put(key), key
        assert put(key, **{"s3:if-none-match": "*"}), key
    assert put("vra/runs/r1/state.json") and put("vra/approvals-consumed/prod/abc.json")
    assert put("vra/signatures/b1.json") and put("vra/checkpoints/c1/manifest.json")
    assert not put("other/runs/r1/state.json"), "objects outside StatePrefix"
    assert cfn.allowed(S, "s3:GetObject", f"{B}/vra/history/prod/000000000007-r1.json")
    assert cfn.allowed(S, "s3:ListBucket", B)
    for key in ("vra/history/prod/x.json", "vra/approvals-consumed/prod/x.json", "vra/artifacts/b/x", "vra/runs/r/x"):
        for a in ("s3:DeleteObject", "s3:DeleteObjectVersion"):
            assert not cfn.allowed(S, a, f"{B}/{key}"), (a, key)
            assert cfn.denied(S, a, f"{B}/{key}"), (a, key)
    assert cfn.allowed(S, "kms:Decrypt", KEY) and cfn.allowed(S, "kms:GenerateDataKey", KEY)


def test_ci_roles_without_a_state_store_get_no_state_access():
    _, S = lanes._oidc_full("github", "prod")
    assert not any(str(s.get("Sid", "")).startswith("State") for s in S)


def test_ci_roles_verify_approval_signatures_but_never_sign():
    k = "arn:aws:kms:us-east-1:111111111111:key/approvals"
    _, S = lanes._oidc_full("gitlab", "prod", ApprovalSigningKeyArns=[k])
    assert cfn.allowed(S, "kms:Verify", k) and cfn.allowed(S, "kms:DescribeKey", k)
    assert not cfn.allowed(S, "kms:Sign", k)
    _, none = lanes._oidc_full("gitlab", "prod")
    assert not cfn.allowed(none, "kms:Verify", k)


def test_generic_ci_role_trusts_its_agent_role_not_oidc():
    agent = "arn:aws:iam::111111111111:role/jenkins-agent-prod"
    trust, _ = lanes._oidc_full("generic", "prod", TrustedPrincipalArns=[agent], ExternalId="x-1")
    assert trust[0]["Principal"] == {"AWS": [agent]} and "Federated" not in json.dumps(trust)
    assert trust[0]["Condition"] == {"StringEquals": {"sts:ExternalId": "x-1"}}
    assert "sts:SetSourceIdentity" in trust[0]["Action"]
    doc = lanes.OIDC
    assert "generic" in doc["Parameters"]["Provider"]["AllowedValues"]
    assert doc["Rules"]["GenericTrustGiven"]["RuleCondition"] == {"Fn::Equals": [{"Ref": "Provider"}, "generic"]}
    gh_trust, _ = lanes._oidc_full("github", "prod")
    assert "Federated" in gh_trust[0]["Principal"]


# ------------------------------------------------------------------ approval writer
GENERIC = {"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "alice", "VRA_RUN_REF": "jenkins:release/42",
           "VRA_APPROVAL_REF": "jenkins:release/42/input-unlock"}


def test_generic_record_source_is_the_run_ref_verbatim():
    rec = writer.from_generic("bob", "2026-11-03T10:00:00Z", "", "prod", "b1",
                              {**GENERIC, "VRA_RUN_REF": "azure-devops:proj/builds/9"})
    assert rec["source"] == "azure-devops:proj/builds/9"


def test_human_only_record_format():
    rec = writer.from_generic("bob", "2026-11-03T10:00:00Z", "ticket: INC-4; notes: stuck lock", "prod", "", GENERIC,
                              hours=2, human="unlock")
    assert rec["build"] == "human:unlock" and rec["scope"] == ["unlock"] and rec["env"] == "prod"
    assert rec["source"] == "jenkins:release/42" and rec["ticket"] == "INC-4"
    import jsonschema
    jsonschema.validate(rec, json.loads((PKG / "engine/vra/data/schemas/approval.schema.json").read_text()))
    with pytest.raises(writer.Refused, match="exactly"):
        writer.from_generic("bob", "t", "scope: emergency", "prod", "", GENERIC, human="unlock")
    with pytest.raises(writer.Refused, match="--human"):
        writer.from_generic("bob", "t", "", "prod", "", GENERIC, human="deploy")
    with pytest.raises(writer.Refused, match="separation of duties"):
        writer.from_generic("alice", "t", "", "prod", "", GENERIC, human="unlock")
    with pytest.raises(writer.Refused, match="--build"):
        writer.from_generic("bob", "t", "", "prod", "", GENERIC)
    gh = writer.from_github([{"state": "approved", "environments": [{"name": "prod"}], "user": {"login": "bob"}}],
                            "prod", "", {"GITHUB_TRIGGERING_ACTOR": "alice", "GITHUB_REPOSITORY": "o/r",
                                         "GITHUB_RUN_ID": "5"}, human="takeover")
    assert gh["build"] == "human:takeover" and gh["scope"] == ["takeover"]


def test_human_actions_and_scopes_mirror_the_engine():
    from vra import human
    assert set(writer.HUMAN_ACTIONS) == set(human.HUMAN_ONLY)
    import inspect
    assert 'f"human:{action}"' in inspect.getsource(human.require)   # the subject the engine checks
    enum = json.loads((PKG / "engine/vra/data/schemas/approval.schema.json").read_text())
    enum = enum["properties"]["scope"]["items"]["enum"]
    assert set(writer.ALL_SCOPES) == set(enum)


def test_ci_never_adds_restore_scopes_including_auth_tables():
    for scope in ("restore-auth-tables", "restore", "data-copy", "migrate", "rollback"):
        with pytest.raises(writer.Refused, match="operator workflows"):
            writer.from_generic("bob", "t", f"scope: {scope}", "prod", "b", GENERIC)
    files = [p for p in CICD.rglob("*") if p.is_file() and p.suffix in (".yml", ".yaml", ".sh", "")
             and "node_modules" not in p.parts and p.name != "approval_record.py"]
    assert not [p for p in files if "--include-auth-tables" in p.read_text(errors="ignore")]


def test_writer_cli_human_record(tmp_path):
    out = tmp_path / "human-takeover.yaml"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("VRA_")}, **GENERIC}
    r = subprocess.run([sys.executable, str(SCRIPTS / "approval_record.py"), "generic", "--human", "takeover",
                        "--approver", "bob", "--env", "prod", "--out", str(out)], env=env, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    rec = json.loads(out.read_text())
    assert rec["build"] == "human:takeover" and rec["scope"] == ["takeover"]
    import datetime as dt
    exp = dt.datetime.fromisoformat(rec["expiresAt"].replace("Z", "+00:00"))
    assert exp - dt.datetime.now(dt.timezone.utc) <= dt.timedelta(hours=2, minutes=1)


# ------------------------------------------------------------------ topology from the engine
def test_ci_graph_takes_the_topology_from_the_engine():
    src = (SCRIPTS / "ci_graph.py").read_text()
    assert "glob(" not in src and "ledger graph" in src, "ci_graph must not re-read env.yaml"
    g = ci_graph.load_graph(LEDGER)
    eng = subprocess.run([sys.executable, "-m", "vra", "--json", "--ledger", str(LEDGER), "ledger", "graph"],
                         capture_output=True, text=True, check=True, env={k: v for k, v in os.environ.items()
                                                                           if k not in ("VRA_HOME", "VRA_LEDGER")})
    envs = json.loads(eng.stdout)["result"]["envs"]
    for name, e in g.items():
        assert e["requiresApproval"] == envs[name]["requiresApproval"] and e["tier"] == envs[name]["tier"]
        assert e["requiredSuites"] == envs[name]["requiredSuites"]


def test_check_flags_a_rendered_gate_or_missing_suite_that_disagrees(tmp_path):
    graph = ci_graph.load_graph(graph_file=lanes._fanout_graph(tmp_path))
    gh = {"jobs": yaml.safe_load(ci_graph.render_github(graph))}
    gh["jobs"]["deploy-prod-eu"]["with"]["requires-approval"] = False
    gh["jobs"]["deploy-int"]["with"]["test-suites"] = "live-cli,live-web"
    problems = ci_graph.check_github(graph, gh)
    assert any("requires-approval" in p for p in problems) and any("smoke" in p for p in problems)
    gl = yaml.safe_load(ci_graph.render_gitlab(graph))
    gl["deploy-int"]["variables"]["VRA_TEST_SUITES"] = "live-cli"
    assert any("smoke" in p for p in ci_graph.check_gitlab(graph, gl))
    cp = {"release": {"envs": [{"name": n, "account": e["account"], "promotesFrom": e["promotesFrom"],
                                "requiresApproval": e["requiresApproval"], "requiredSuites": e["requiredSuites"]}
                               for n, e in graph.items()]}}
    assert ci_graph.check_codepipeline(graph, cp) == []
    cp["release"]["envs"][0]["requiredSuites"] = []
    assert ci_graph.check_codepipeline(graph, cp)


def test_generic_render_lists_the_steps_and_approvals_only_for_gated_envs(tmp_path):
    graph = ci_graph.load_graph(graph_file=lanes._fanout_graph(tmp_path))
    text = ci_graph.render_generic(graph)
    deploys = [ln for ln in text.splitlines() if ln.startswith("vra-ci.sh deploy")]
    assert [re.search(r"--env (\S+)", ln).group(1) for ln in deploys] == ["dev", "int", "prod-eu", "prod-us"]
    assert all(("--approver" in ln) == ("prod-" in ln) for ln in deploys)
    assert "vra-ci.sh test --env int --suites live-cli,live-web,customer,smoke" in text
    assert "--env prod-us --suites" not in text   # a leaf runs no suites


def test_shipped_workflows_render_requires_approval_from_the_ledger():
    graph = ci_graph.load_graph(LEDGER)
    gh = yaml.safe_load((CICD / "github-actions/deploy.yml").read_text())
    for name, e in graph.items():
        assert gh["jobs"][f"deploy-{name}"]["with"]["requires-approval"] is e["requiresApproval"]
    deploy_env = yaml.safe_load((CICD / "github-actions/vra-deploy-env.yml").read_text())
    assert deploy_env[True]["workflow_call"]["inputs"]["requires-approval"]["required"] is True
    gl = yaml.safe_load((CICD / "gitlab-ci/.gitlab-ci.yml").read_text())
    assert gl[".deploy-gated"]["variables"]["VRA_REQUIRES_APPROVAL"] == "true"
    assert gl[".deploy-auto"]["variables"]["VRA_REQUIRES_APPROVAL"] == "false"


# ------------------------------------------------------------------ CI files
def _ci_files() -> list[Path]:
    out = []
    for p in sorted(CICD.rglob("*")):
        if not p.is_file() or "node_modules" in p.parts or "__pycache__" in p.parts or "tests" in p.parts:
            continue
        if p.suffix in (".yml", ".yaml", ".sh") or p.name in ("Jenkinsfile", ".gitlab-ci.yml"):
            out.append(p)
    return out


def _code_lines(p: Path) -> str:
    """The file with comment lines dropped and backslash continuations joined."""
    lines = [ln for ln in p.read_text().splitlines() if not ln.strip().startswith(("#", "//"))]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


DEPLOYERS = ["codebuild/buildspec-deploy.yml", "github-actions/vra-deploy-env.yml", "gitlab-ci/.gitlab-ci.yml",
             "generic/vra-ci.sh"]


@pytest.mark.parametrize("rel", DEPLOYERS)
def test_every_ci_deploy_waits_for_a_busy_env_lock_and_asks_the_ledger_about_approval(rel):
    text = _code_lines(CICD / rel)
    calls = re.findall(r'(?:run-vra\.sh"?|\brun)\s+"(?:deploy|promote)-\$\{[A-Z_a-z]+\}"\s+(?:deploy|promote)\b[^\n]*',
                       text)
    assert len(calls) == 2, (rel, calls)
    for c in calls:
        assert "--lock-wait" in c, (rel, c)
    assert "ci_graph.py requires-approval" in text and "--approval" in text


def test_no_ci_file_exports_a_vams_config_variable():
    from vra.ledger import VAMS_CONFIG_ENV_VARS
    names = "|".join(VAMS_CONFIG_ENV_VARS)
    pat = re.compile(rf"(?<![A-Za-z0-9_$!{{])({names})\s*(=|:\s)")
    hits = [(p.relative_to(CICD), m.group(0)) for p in _ci_files() for m in pat.finditer(_code_lines(p))]
    assert hits == []
    for cfg in (CICD / "pipeline/config").glob("*.json"):
        env = (json.loads(cfg.read_text()).get("codebuild") or {}).get("environment") or {}
        assert not set(env) & set(VAMS_CONFIG_ENV_VARS), cfg.name
    run_vra = (SCRIPTS / "run-vra.sh").read_text()
    listed = re.search(r"vams_config_vars=\(([^)]*)\)", run_vra).group(1).split()
    assert listed == list(VAMS_CONFIG_ENV_VARS)
    ts = (CICD / "pipeline/lib/config.ts").read_text()
    ts_list = re.findall(r'"([A-Z_]+)"', re.search(r"VAMS_CONFIG_ENV_VARS = \[([^\]]*)\]", ts).group(1))
    assert ts_list == list(VAMS_CONFIG_ENV_VARS)


def test_human_only_flags_only_run_with_a_human_approval_record():
    for p in _ci_files():
        text = _code_lines(p)
        for flag in ("--writes-stopped", "--abort-workflow-executions", "--allow-missing-scanners"):
            assert flag not in text, (p.name, flag)
        if re.search(r"\bunlock --env\b|--takeover\b|--confirm\b|\"--\$\{(ACTION|VRA_OPERATOR_ACTION)\}\"", text):
            assert "VRA_HUMAN_APPROVAL=" in text and "--human" in text, p.name


def test_ci_watch_jobs_leave_lanes_to_the_engine():
    for p in _ci_files():
        assert "--lanes" not in _code_lines(p), p.name


def test_operator_workflow_passes_inputs_through_the_environment_only():
    doc = yaml.safe_load((CICD / "github-actions/vra-operator.yml").read_text())
    opts = doc[True]["workflow_dispatch"]["inputs"]["action"]["options"]
    assert set(opts) <= set(writer.HUMAN_ACTIONS)
    for p in sorted((CICD / "github-actions").glob("*.yml")):
        for job in (yaml.safe_load(p.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                assert "${{ inputs." not in str(step.get("run", "")), (p.name, step.get("name"))


# ------------------------------------------------------------------ run-vra.sh, generic lane
def _fake_vra(tmp_path: Path, stdout: str = '{"exitCode": 0, "runId": "r1"}') -> tuple[dict, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    argv = tmp_path / "argv.txt"
    (bin_dir / "vra").write_text(f"#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > {argv}\ncat <<'J'\n{stdout}\nJ\n")
    (bin_dir / "vra").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    env.update({"PATH": f"{bin_dir}:{os.environ['PATH']}", "VRA_OUT_DIR": str(tmp_path / "out"), "HOME": str(tmp_path)})
    from vra.ledger import VAMS_CONFIG_ENV_VARS
    for v in VAMS_CONFIG_ENV_VARS:
        env.pop(v, None)
    return env, argv


def test_run_vra_refuses_vams_config_variables(tmp_path):
    env, argv = _fake_vra(tmp_path)
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), "build", "build", "--lane", "release"],
                       env={**env, "REGION": "us-east-1", "AWS_USE_WAF": "false"}, capture_output=True, text=True)
    assert r.returncode == 2 and not argv.exists(), "vra must not run"
    data = json.loads((tmp_path / "out" / "build.json").read_text())
    assert "REGION" in data["message"] and "AWS_USE_WAF" in data["message"] and data["exitCode"] == 2
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), "build", "build"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and argv.exists()


def test_run_vra_passes_the_human_approval_record(tmp_path):
    env, argv = _fake_vra(tmp_path)
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), "op", "unlock", "--env", "prod", "--reason", "x"],
                       env={**env, "VRA_HUMAN_APPROVAL": "vra-out/human-unlock.yaml"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    args = argv.read_text().split("\n")
    i = args.index("--human-approval")
    assert args[i + 1] == "vra-out/human-unlock.yaml" and args[:4] == ["unlock", "--env", "prod", "--reason"]
    r = subprocess.run(["bash", str(SCRIPTS / "run-vra.sh"), "op", "status"], env=env, capture_output=True, text=True)
    assert "--human-approval" not in argv.read_text()


def test_generic_cron_prints_the_engines_cron_line(tmp_path):
    line = f"17 6 * * * vra --home {tmp_path}/home --non-interactive --json watch >> /x/watch.log 2>&1"
    env, argv = _fake_vra(tmp_path, json.dumps({"exitCode": 0, "result": {"cron": line}}))
    r = subprocess.run(["bash", str(CICD / "generic/vra-ci.sh"), "cron"], env={**env, "VRA_HOME": str(tmp_path)},
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == line
    assert argv.read_text().split("\n")[:2] == ["watch", "--print-cron"]


def test_generic_operator_step_writes_the_human_record_and_runs_vra(tmp_path):
    env, argv = _fake_vra(tmp_path)
    env.update({"VRA_CI_PROVIDER": "generic", "VRA_INITIATOR": "alice", "VRA_RUN_REF": "jenkins:ops/7",
                "VRA_HOME": str(tmp_path)})
    r = subprocess.run(["bash", str(CICD / "generic/vra-ci.sh"), "operator", "--action", "unlock", "--env", "prod",
                        "--approver", "bob", "--", "unlock", "--env", "prod", "--reason", "parked run"], env=env,
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    rec = json.loads((tmp_path / "vra-out" / "human-unlock.yaml").read_text())
    assert rec["build"] == "human:unlock" and rec["source"] == "jenkins:ops/7"
    assert "--human-approval" in argv.read_text()
    r = subprocess.run(["bash", str(CICD / "generic/vra-ci.sh"), "operator", "--action", "unlock", "--env", "prod",
                        "--", "unlock", "--env", "prod", "--reason", "x"], env=env, capture_output=True, text=True,
                       cwd=tmp_path)
    assert r.returncode == 20, "no approver from the CI's approval step"


def test_pipeline_package_version_follows_the_engine():
    import tomllib
    py = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]["version"]
    pkg = json.loads((CICD / "pipeline/package.json").read_text())
    lock = json.loads((CICD / "pipeline/package-lock.json").read_text())
    assert pkg["version"] == lock["version"] == lock["packages"][""]["version"] == py
    from vra import __version__
    assert __version__ == py
