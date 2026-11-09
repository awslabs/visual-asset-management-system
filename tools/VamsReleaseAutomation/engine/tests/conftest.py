# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a fake upstream VAMS repo + customer fork (local bare repos), a ledger,
a fake AWS factory (Stubber/moto clients), and a fake shell that runs git for real but simulates
npm/npx/cdk.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import boto3
import pytest
import yaml

from vra import util
from vra.aws import AwsFactory
from vra.context import Context
from vra.ledger import Ledger

ACCT = {"dev": "111111111111", "dev-preview": "111111111111", "test": "222222222222", "prod": "333333333333"}


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n[protocol \"file\"]\n\tallow = always\n")
    monkeypatch.setenv("VRA_INITIATOR", "dev-operator@example.com")
    monkeypatch.setenv("VRA_POLL_SECONDS", "0")
    # identity is read from CI variables (vra.identity); tests must not inherit the runner's own CI context
    for k in ("GITHUB_ACTIONS", "GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR", "GITHUB_RUN_ID", "GITHUB_REPOSITORY",
              "GITLAB_CI", "GITLAB_USER_LOGIN", "CI_PIPELINE_ID", "CI_PROJECT_PATH", "CODEBUILD_BUILD_ID",
              "CODEBUILD_INITIATOR", "VRA_PIPELINE_NAME", "VRA_PIPELINE_EXECUTION_ID", "VRA_AGENT", "VRA_CDK_CLI",
              "VRA_MCP", "VRA_CI_PROVIDER", "VRA_RUN_REF", "VRA_APPROVAL_REF", "VRA_HOME", "VRA_LEDGER",
              "VRA_WORK_DIR", "VRA_CREDPROC_NONCE", "MCP_TEST", "STACK_NAME", "ADMIN_EMAIL_ADDRESS", "ADMIN_USER_ID",
              "AWS_USE_FIPS_ENDPOINT", "AWS_USE_WAF", "DEFAULT_USER_ROLE_NAME", "REGION"):
        monkeypatch.delenv(k, raising=False)
    # the per-user pointer to a shared home, host id and caches stay inside the test
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    # belt and braces: any un-mocked botocore call goes to a closed local port, never to AWS
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))


def git(cwd: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args} failed: {p.stderr}")
    return p.stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


MIGRATION_SCRIPT = textwrap.dedent('''\
    import argparse, json, os, sys
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--region")
    {extra}
    a = p.parse_args()
    out = os.environ.get("MIGRATION_LOG")
    if out:
        with open(out, "a") as fh:
            fh.write(json.dumps({{"script": os.path.basename(__file__), "argv": sys.argv[1:]}}) + "\\n")
    print("ok")
''')


def vams_tree(root: Path, version: str) -> None:
    write(root, "infra/config/config.ts", f'export const VAMS_VERSION = "{version}";\n')
    write(root, "infra/config/config.template.commercial.json", json.dumps(
        {"name": "vams", "env": {"account": None, "region": None}, "app": {"baseStackName": "prod",
                                                                          "useWaf": True, "newFeature": {"on": False}}},
        indent=2))
    write(root, "infra/config/config.template.govcloud.json", json.dumps(
        {"name": "vams", "env": {"account": None, "region": None}, "app": {"baseStackName": "prod", "useFips": True}}))
    write(root, "infra/package.json", json.dumps({"scripts": {"typecheck": "tsc --noEmit"},
                                                 "devDependencies": {"aws-cdk": "^2.1135.0"}}))
    write(root, "infra/package-lock.json", '{"lockfileVersion": 3, "v": "' + version + '"}\n')
    write(root, "CHANGELOG.md", f"# Changelog\n\n## {version}\n\n- upstream change {version}\n")
    write(root, "backend/backend/core.py", f"VERSION = '{version}'\n")
    write(root, "web/src/custom/README.md", "custom lane\n")
    for frm, to, extra in (("2.3", "2.4", ""), ("2.4", "2.5", ""),
                           ("2.5", "2.6", 'p.add_argument("--confirm-account"); p.add_argument("--yes", action="store_true"); p.add_argument("--steps")')):
        d = f"infra/deploymentDataMigration/v{frm}_to_v{to}/upgrade"
        write(root, f"{d}/v{frm}_to_v{to}_migration.py", MIGRATION_SCRIPT.format(extra=extra))
        cfg = {"comments": "x", "aws_region": None, "dry_run": False}
        if to == "2.6":
            cfg["resource_names_ssm_param_prefix"] = "<RESOURCE_NAMES_SSM_PARAM_PREFIX>"
            cfg["workflow_storage_table_name"] = "YOUR-STACK-WorkflowStorageTable-XXXXX"
        else:
            cfg["asset_storage_table_name"] = "YOUR-STACK-AssetStorageTable-XXXXX"
        write(root, f"{d}/v{frm}_to_v{to}_migration_config.json", json.dumps(cfg))


@pytest.fixture
def repos(tmp_path):
    """upstream (bare) with main@v2.6.6 + development; fork (bare) = main + customer commit."""
    work = tmp_path / "src-upstream"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    vams_tree(work, "2.6.5")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "v2.6.5")
    git(work, "tag", "v2.6.5")
    upstream = tmp_path / "upstream.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(upstream))
    # fork: starts from upstream main, adds customer code
    fork_work = tmp_path / "src-fork"
    git(tmp_path, "clone", "-q", str(upstream), str(fork_work))
    write(fork_work, "web/src/custom/plugin.ts", "export const customer = 1;\n")
    write(fork_work, "web/src/custom/README.md", "customer custom lane notes\n")
    write(fork_work, "CHANGELOG.md", "# Changelog\n\n## fork\n\n- customer change\n\n## 2.6.5\n\n- upstream change 2.6.5\n")
    write(fork_work, "infra/package-lock.json", '{"lockfileVersion": 3, "v": "fork"}\n')
    write(fork_work, "backend/backend/core.py", "VERSION = '2.6.5'  # customer core patch\n")
    git(fork_work, "add", "-A")
    git(fork_work, "commit", "-q", "-m", "customer code")
    fork = tmp_path / "fork.git"
    git(tmp_path, "clone", "-q", "--bare", str(fork_work), str(fork))
    # upstream moves on: v2.6.6 release with conflicting edits; development further ahead
    git(work, "pull", "-q", str(upstream), "main", check=False)
    vams_tree(work, "2.6.6")
    write(work, "web/src/custom/README.md", "upstream custom lane notes 2.6.6\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "v2.6.6")
    git(work, "tag", "v2.6.6")
    git(work, "branch", "development")
    git(work, "checkout", "-q", "development")
    write(work, "backend/backend/core.py", "VERSION = '2.7.0-dev'\n")
    git(work, "commit", "-q", "-am", "dev work")
    git(work, "checkout", "-q", "main")
    git(work, "push", "-q", "--tags", str(upstream), "main", "development")
    return {"upstream": upstream, "fork": fork, "upstream_work": work, "fork_work": fork_work, "tmp": tmp_path}


def env_doc(name: str, **over) -> dict:
    base = {
        "dev": {"lane": "release", "autoDeploy": True, "requiresApproval": False, "rollbackPolicy": "auto"},
        "dev-preview": {"lane": "preview", "autoDeploy": True, "requiresApproval": False},
        "test": {"lane": "release", "promotesFrom": "dev", "autoDeploy": False, "requiresApproval": True,
                 "approvers": ["qa-lead@example.com"], "rollbackPolicy": "ask",
                 "dataCopy": {"allowedSources": ["prod"], "stagingBucket": "vra-staging-example"}},
        "prod": {"lane": "release", "promotesFrom": "test", "autoDeploy": False, "requiresApproval": True,
                 "approvers": ["release-manager@example.com"], "rollbackPolicy": "ask"},
    }[name]
    region = over.pop("region", "us-east-1")
    acct = ACCT[name]
    part = "aws-us-gov" if region.startswith("us-gov-") else "aws"
    d = {"account": acct, "region": region, "partition": part,
         "deployRoleArn": f"arn:{part}:iam::{acct}:role/vra-deploy", "templateBase": "commercial",
         "baseStackName": name.replace("-", ""), "liveTest": {"webUrl": f"https://{name}.vams.example.com"},
         # the moto fixtures use short bucket names (not under the core stack): record them as granted;
         # tests of the registered-bucket checks (test_registered_buckets.py) override this
         "registeredBuckets": {"granted": True, "bucketArns": [f"arn:{part}:s3:::*"]},
         **base}
    d.update(over)
    return d


def write_ledger(root: Path, fork_url: str, upstream_url: str, envs: dict | None = None, **proj_over) -> Ledger:
    proj = {"project": {"name": "example"}, "upstream": {"url": upstream_url}, "fork": {"url": fork_url},
            "githost": {"type": "local"}, "trunk": "main", "tagPrefix": "cust-",
            "lanes": {"release": {"source": "main"}, "preview": {"source": "development", "targetBranch": "preview/main"}},
            "notifications": {"type": "none"}, "stateStore": {"type": "local"},
            "build": {"sbom": False, "ash": False, "unitSuites": []}}
    proj.update(proj_over)
    util.write_yaml(root / "vra.yaml", proj)
    for n, d in (envs or {k: env_doc(k) for k in ACCT}).items():
        util.write_yaml(root / "envs" / n / "env.yaml", d)
        util.write_json(root / "envs" / n / "overlay" / "infra/config/config.json",
                        {"name": "vams", "env": {"account": d["account"], "region": d["region"]},
                         "app": {"baseStackName": d.get("baseStackName"), "useWaf": True}})
    return Ledger(root)


class AllowAllIam:
    def simulate_principal_policy(self, **kw):
        return {"EvaluationResults": [{"EvalActionName": a, "EvalDecision": "allowed", "EvalResourceName": "*"}
                                      for a in kw["ActionNames"]], "IsTruncated": False}


class FakeStsRole:
    def __init__(self, account, role):
        self.arn = f"arn:aws:sts::{account}:assumed-role/vra-{role}/vra-test"

    def get_caller_identity(self):
        return {"Arn": self.arn}


class FakeAws:
    """Never reaches AWS: explicit clients, an allow-all IAM simulator, or moto-backed boto3 clients when
    ``moto=True`` (inside a ``mock_aws`` context)."""

    def __init__(self, ledger, clients: dict | None = None, moto: bool = False):
        self.ledger = ledger
        self.clients = clients or {}
        self.moto = moto
        self.run_id = None

    def client(self, env, service, role="deploy", region=None):
        for key in ((env, service, role), (env, service), service):
            if key in self.clients:
                return self.clients[key]
        if service == "iam":
            return AllowAllIam()
        if service == "sts" and role in ("synth", "readonly"):
            return FakeStsRole(self.ledger.env(env)["account"], role)   # build.prove_synth_role
        if self.moto:
            return boto3.client(service, region_name=region or self.ledger.env(env)["region"])
        raise AssertionError(f"test made an unexpected AWS call: {service} in {env}")

    def tools_client(self, service, region=None):
        if ("tools", service) in self.clients:
            return self.clients[("tools", service)]
        if self.moto:
            return boto3.client(service, region_name=region or "us-east-1")
        raise AssertionError(f"test made an unexpected AWS tools call: {service}")

    role_arn = AwsFactory.role_arn

    def set_via(self, env, role, via):
        self.via = getattr(self, "via", {})
        self.via[(env, role)] = via

    def identity(self, env, role="deploy"):
        e = self.ledger.env(env)
        return {"Arn": f"arn:{e.get('partition') or 'aws'}:sts::{e['account']}:assumed-role/vra-deploy/test",
                "Account": e["account"]}

    def partition(self, env):
        return self.ledger.env(env).get("partition") or "aws"

    def subprocess_env(self, env, role="deploy"):
        return {}


def fake_shell_cls(calls: list, handler=None):
    """Shell subclass: git and real python scripts run for real; npm/npx/vamscli/etc are simulated."""

    class FakeShell(util.Shell):
        def run(self, argv, cwd=None, env=None, check=True, input_text=None, mutating=True, timeout=None):
            argv = [str(a) for a in argv]
            if argv[0] in ("git", sys.executable) or argv[0].endswith("/python"):
                return super().run(argv, cwd, env, check, input_text, mutating, timeout)
            calls.append({"argv": argv, "cwd": str(cwd) if cwd else None, "env": dict(env or {}),
                          "stdin": input_text})
            if self.dry_run and mutating:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if handler:
                r = handler(argv, cwd, env)
                if r is not None:
                    if check and r.returncode != 0:
                        from vra.errors import CommandFailed
                        raise CommandFailed(f"fake command failed: {argv[:3]}", r.returncode,
                                            (r.stdout or "") + (r.stderr or ""))
                    return r
            if "synth" in argv and "-o" in argv:
                out = Path(argv[argv.index("-o") + 1])
                out.mkdir(parents=True, exist_ok=True)
                cdk_ctx = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-c" and "=" in argv[i + 1])
                # like VAMS bin/infra.ts: <name>-core-<baseStackName>-<region>
                stack = f"vams-core-{cdk_ctx['stack-name']}-{cdk_ctx['region']}" if "stack-name" in cdk_ctx \
                    else "vams-core-x"
                (out / "manifest.json").write_text(json.dumps({"artifacts": {stack: {
                    "type": "aws:cloudformation:stack", "properties": {"stackName": stack}}}}))
                (out / f"{stack}.template.json").write_text(json.dumps({"Resources": {}, "cwd": str(cwd)}))
            return subprocess.CompletedProcess(argv, 0, "", "")

    return FakeShell


def stub_inventory(monkeypatch, buckets: list[dict] | None = None):
    """Replace the registered-bucket inventory (SSM + s3AssetBucketsStorage + GetBucketEncryption) for tests that
    do not run moto. Default: one asset bucket named after the env's core stack, SSE-S3."""
    from vra import buckets as bmod

    def fake(ctx, env, role="deploy", names=None, base_override=None):
        e = ctx.ledger.env(env)
        part = e.get("partition") or "aws"
        stack = ctx.ledger.core_stack_name(env, base_override)
        rows = buckets if buckets is not None else [{"name": f"{stack}-assetbucket", "kind": "asset"}]
        out = []
        for b in rows:
            r = {"kind": "asset", "kmsKeyArn": None, "sse": "AES256", **b}
            r.setdefault("arn", f"arn:{part}:s3:::{r['name']}")
            r.setdefault("stackOwned", r["name"].lower().startswith(stack.lower()))
            out.append(r)
        return {"env": env, "stack": stack, "partition": part, "region": e["region"], "account": e["account"],
                "buckets": out}

    monkeypatch.setattr(bmod, "inventory", fake)
    return fake


@pytest.fixture
def ledger(repos, tmp_path):
    return write_ledger(tmp_path / "ledger", str(repos["fork"]), str(repos["upstream"]))


@pytest.fixture
def calls():
    return []


@pytest.fixture
def ctx(ledger, tmp_path, calls):
    c = Context(ledger=ledger, work_dir=tmp_path / "work", initiator="dev-operator@example.com")
    c.aws = FakeAws(ledger)
    c.shell_cls = fake_shell_cls(calls)
    return c
