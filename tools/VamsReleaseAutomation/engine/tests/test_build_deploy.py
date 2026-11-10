# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""build -> deploy -> rollback flows with a fake shell (git real, npm/npx/cdk/vamscli simulated)."""

import hashlib
import json
import subprocess

import pytest
from botocore.exceptions import ClientError

from vra import build as build_mod, deploy, errors, runner, util

from conftest import FakeAws, fake_shell_cls, git
from test_permissions import GOV_CFN


class FakeCfn:
    def __init__(self, events=None):
        self.events = events or []

    def describe_stacks(self, StackName):
        raise ClientError({"Error": {"Code": "ValidationError", "Message": f"Stack with id {StackName} does not exist"}},
                          "DescribeStacks")

    def describe_stack_events(self, StackName):
        return {"StackEvents": self.events}


def _build(ctx, ref="main"):
    out = runner.start(ctx, "build", {"lane": "release", "ref": ref, "suites": []})
    ctx.run_id = None
    return out


def test_build_bundle_manifest_and_tag(ctx, repos, calls):
    out = _build(ctx)
    assert out.exit_code == 0, out.message
    m = out.run.state["result"]
    assert m["buildId"] == "cust-v2.6.5-1" and m["tag"] == "cust-v2.6.5-1" and m["lane"] == "release"
    assert sorted(m["targets"]) == ["dev", "prod", "test"]  # release-lane envs only
    bdir = build_mod.build_dir(ctx, m["buildId"])
    assert (bdir / "migrations" / "v2.5_to_v2.6").is_dir()
    assert (bdir / "overlays" / "test" / "infra/config/config.json").exists()
    for t in m["targets"]:
        build_mod.verify_bundle(ctx, m, t)
    # the tag is annotated, on the fork, and points at the built commit
    assert git(repos["fork"], "rev-parse", "cust-v2.6.5-1^{commit}") == m["commit"]
    synths = [c for c in calls if "synth" in c["argv"]]
    assert len(synths) == 3 and all(c["cwd"].endswith("/src/infra") for c in synths)
    # overlay applied at synth time and restored afterwards (scratch removed after bundling)
    assert not (ctx.scratch_dir / m["buildId"]).exists()
    # idempotent: rebuilding the same commit reuses the bundle and the tag
    again = _build(ctx)
    assert again.exit_code == 0 and again.run.state["result"]["buildId"] == "cust-v2.6.5-1"
    assert git(repos["fork"], "tag", "--list", "cust-*") == "cust-v2.6.5-1"


def test_preview_build_not_tagged(ctx, repos):
    out = runner.start(ctx, "build", {"lane": "preview", "ref": "main", "suites": []})
    assert out.exit_code == 0, out.message
    m = out.run.state["result"]
    assert m["buildId"].startswith("preview-v2.6.5-") and m["tag"] is None and m["promotable"] is False
    assert sorted(m["targets"]) == ["dev-preview"]


def test_unit_suite_failure_exits_30(ctx, repos, calls):
    def handler(argv, cwd, env):
        if argv[:2] == ["npm", "run"] and "typecheck" in argv:
            return subprocess.CompletedProcess(argv, 2, "", "TS2322 error")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": ["infra"]})
    assert out.exit_code == errors.TESTS_FAILED
    assert list((out.run.junit_dir / "unit").glob("infra.xml"))


def test_unit_suite_skips_npm_script_missing_at_this_version(ctx, repos, calls):
    """E2E finding: the suite catalog mirrors development's CI, but a v2.6.6 release tree has no `typecheck`
    script in web/ or infra/, so `npm run typecheck` failed and every release-lane build exited 30."""
    w = repos["tmp"] / "fork-noscript"
    git(repos["tmp"], "clone", "-q", str(repos["fork"]), str(w))
    (w / "infra/package.json").write_text('{"devDependencies": {"aws-cdk": "^2.1135.0"}}')
    git(w, "commit", "-q", "-am", "older tree without typecheck")
    git(w, "push", "-q", "origin", "main")
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": ["infra"]})
    assert out.exit_code == 0, out.message
    assert not any(c["argv"][:3] == ["npm", "run", "typecheck"] for c in calls)
    xml = (out.run.junit_dir / "unit" / "infra.xml").read_text()
    assert "is not defined" in xml


def test_build_ids_count_the_homes_builds_when_tags_are_not_pushed(ctx, repos, calls):
    """Live test: with build.pushTags false every CodeBuild build clones the fork afresh, without the tags of
    earlier builds, so the build of a second commit got id cust-v2.6.6-1, the id the earlier build of the first commit had; a
    rollback that excludes the failing build then excluded both. The ids the shared home recorded count now."""
    proj = util.read_yaml(ctx.ledger.root / "vra.yaml")
    proj.setdefault("build", {})["pushTags"] = False
    util.write_yaml(ctx.ledger.root / "vra.yaml", proj)
    ctx.ledger._project = None
    first = _build(ctx)
    assert first.exit_code == 0, first.message
    assert first.run.state["result"]["buildId"] == "cust-v2.6.5-1"
    git(ctx.repo_dir, "tag", "-d", "cust-v2.6.5-1")              # a fresh CI clone has no local tags
    w = repos["tmp"] / "fork-next"
    git(repos["tmp"], "clone", "-q", str(repos["fork"]), str(w))
    (w / "NOTE.md").write_text("customer note\n")
    git(w, "add", "NOTE.md")
    git(w, "commit", "-q", "-m", "comment-only change")
    git(w, "push", "-q", "origin", "main")
    second = _build(ctx)
    assert second.exit_code == 0, second.message
    m = second.run.state["result"]
    assert m["buildId"] == "cust-v2.6.5-2" and m["commit"] != first.run.state["result"]["commit"]
    git(ctx.repo_dir, "tag", "-d", "cust-v2.6.5-2")
    again = runner.start(ctx, "build", {"lane": "release", "ref": m["commit"], "suites": []})
    assert again.run.state["result"]["buildId"] == "cust-v2.6.5-2"      # the same commit keeps its id


def test_resumed_unit_step_does_not_count_stale_junit(ctx, repos, calls):
    """E2E finding: after a failed unit step was resumed with a different suite list, the summary still counted
    the failed attempt's JUnit files (6 failures reported for a passing step)."""
    state = {"fail": True}

    def handler(argv, cwd, env):
        if argv[:3] == ["npm", "run", "typecheck"] and state["fail"]:
            return subprocess.CompletedProcess(argv, 2, "", "TS2322 error")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    proj = util.read_yaml(ctx.ledger.root / "vra.yaml")
    proj.setdefault("build", {})["unitSuites"] = ["infra"]
    util.write_yaml(ctx.ledger.root / "vra.yaml", proj)
    ctx.ledger._project = None
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main"})
    assert out.exit_code == errors.TESTS_FAILED
    # the operator narrows the suites in the ledger and resumes (as in the live W6 run)
    proj["build"]["unitSuites"] = ["lint"]
    util.write_yaml(ctx.ledger.root / "vra.yaml", proj)
    ctx.ledger._project = None
    ctx.run_id = None
    again = runner.resume(ctx, out.run.run_id)
    assert again.exit_code == 0, again.message
    summ = again.run.find_step("build.unit")["result"]["summary"]
    assert summ["failures"] == 0 and summ["ok"] is True


def test_resynth_over_undeletable_partial_cdk_out(ctx, monkeypatch):
    """E2E finding: a resumed build re-ran synth for an env whose earlier attempt left Docker-bundled files owned
    by another user in cdk.out/<env>/asset.*/python; rmtree raised PermissionError and the resume failed. The
    stale tree is moved out of the bundle instead."""
    out = build_mod.build_dir(ctx, "cust-v2.6.6-2") / "cdk.out" / "test"
    (out / "asset.abc" / "python").mkdir(parents=True)
    (out / "asset.abc" / "python" / "x.py").write_text("x")
    real = build_mod.shutil.rmtree

    def rmtree(p, *a, **kw):
        if str(p) == str(out):
            raise PermissionError(13, "Permission denied", str(out / "asset.abc" / "python"))
        return real(p, *a, **kw)
    monkeypatch.setattr(build_mod.shutil, "rmtree", rmtree)
    build_mod.discard_dir(ctx, out)
    assert not out.exists()
    moved = list((ctx.scratch_dir / "discarded").iterdir())
    assert len(moved) == 1 and (moved[0] / "asset.abc" / "python" / "x.py").exists()


def test_bundle_verifies_after_cdk_wrote_its_asset_cache(ctx, repos, calls):
    """E2E finding (ROLLBACK_FAILED on dev): `cdk deploy --app <bundle>/cdk.out/dev` writes
    `.cache/<assetHash>.zip` into the assembly, so the automatic rollback to the previous build failed its
    integrity check. The cache is purged before verification; real tampering is still caught."""
    m = _build(ctx).run.state["result"]
    d = build_mod.build_dir(ctx, m["buildId"]) / "cdk.out" / "dev"
    (d / ".cache").mkdir()
    (d / ".cache" / "abc.zip").write_bytes(b"zip")
    build_mod.verify_bundle(ctx, m, "dev")
    assert not (d / ".cache").exists()
    next(p for p in d.iterdir() if p.suffix == ".json").write_text("{}")
    with pytest.raises(errors.VraError, match="integrity"):
        build_mod.verify_bundle(ctx, m, "dev")


def test_bundle_verifies_with_a_leftover_cdk_read_lock(ctx, repos, calls):
    """Live test: a bundle copied to machine B while A's `cdk diff` read it carried `read.<pid>.<n>.lock`, and the
    takeover's deploy failed the integrity check. CDK's assembly lock files are not content; other files still
    count."""
    m = _build(ctx).run.state["result"]
    d = build_mod.build_dir(ctx, m["buildId"]) / "cdk.out" / "dev"
    (d / "read.4151544.1.lock").write_text("")
    (d / "synth.lock").write_text("")
    build_mod.verify_bundle(ctx, m, "dev")
    (d / "read.extra.lock").write_text("x")          # not a CDK lock name: counts as a change
    with pytest.raises(errors.VraError, match="integrity"):
        build_mod.verify_bundle(ctx, m, "dev")


def test_empty_unit_suites_in_ledger_means_none(ctx, repos, calls, ledger):
    """`build.unitSuites: []` disables unit suites (it used to fall back to the defaults, so `vra watch`, which
    has no --suites for the build, always ran the full CI catalog)."""
    proj = util.read_yaml(ledger.root / "vra.yaml")
    proj.setdefault("build", {})["unitSuites"] = []
    util.write_yaml(ledger.root / "vra.yaml", proj)
    ledger._project = None
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main"})
    assert out.exit_code == 0, out.message
    assert out.run.find_step("build.unit")["status"] == "skipped"


def test_deploy_dev_records_history_and_diff(ctx, repos, calls):
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == 0, out.message
    h = ctx.ledger.history("dev")[-1]
    assert h["action"] == "deploy" and h["result"] == "SUCCEEDED" and h["build"] == b and h["vamsVersion"] == "2.6.5"
    argvs = [c["argv"] for c in calls]
    deploy_cmd = next(a for a in argvs if "deploy" in a)
    assert deploy_cmd[:3] == ["npx", "--yes", "aws-cdk@2.1135.0"]
    assert "--app" in deploy_cmd and deploy_cmd[deploy_cmd.index("--app") + 1].endswith(f"{b}/cdk.out/dev")
    assert "--force" not in deploy_cmd
    assert (out.run.dir / "cdk-diff-dev.txt").exists()
    assert not (ctx.work_dir / "locks" / "dev.lock").exists()  # lock released


def test_deploy_blocks_data_resource_replacement(ctx, repos, calls):
    b = _build(ctx).run.state["result"]["buildId"]
    diff = "Stack vams-core-dev\nResources\n[~] AWS::DynamoDB::Table AssetStorageTable AssetStorageTable123 replace\n"

    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, 0, diff, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "data-bearing" in out.message
    assert not any("deploy" in c["argv"] and "--app" in c["argv"] and "diff" not in c["argv"] for c in calls)


def test_deploy_permission_denied_writes_it_request(ctx, repos, calls):
    b = _build(ctx).run.state["result"]["buildId"]

    def handler(argv, cwd, env):
        if "deploy" in argv and "diff" not in argv:
            return subprocess.CompletedProcess(argv, 1, "", "vams-core-dev | UPDATE_ROLLBACK_COMPLETE")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    events = [{"LogicalResourceId": "AuthRole", "ResourceStatus": "CREATE_FAILED", "ResourceType": "AWS::IAM::Role",
               "ResourceStatusReason": GOV_CFN, "StackId": "s", "PhysicalResourceId": ""}]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn(events)})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert req["actions"] == ["iam:CreateRole"] and req["step"] == "deploy.dev.cdk-deploy"


def test_smoke_failure_auto_rolls_back(ctx, repos, calls, ledger):
    b1 = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    assert runner.start(ctx, "deploy", {"env": "dev", "build": b1}, env="dev").exit_code == 0
    ctx.run_id = None
    # a second build (new commit on the fork trunk)
    w = repos["tmp"] / "fork-w2"
    git(repos["tmp"], "clone", "-q", str(repos["fork"]), str(w))
    (w / "web/src/custom/plugin.ts").write_text("export const customer = 2;\n")
    git(w, "commit", "-q", "-am", "customer change 2")
    git(w, "push", "-q", "origin", "main")
    b2 = _build(ctx).run.state["result"]["buildId"]
    assert b2 == "cust-v2.6.5-2"
    env = util.read_yaml(ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com"}
    util.write_yaml(ledger.root / "envs/dev/env.yaml", env)
    ledger._envs.clear()
    state = {"features": 0}

    def handler(argv, cwd, env_):
        if "features" in argv:
            state["features"] += 1
            if state["features"] == 1:
                return subprocess.CompletedProcess(argv, 1, "", "500 Internal Server Error")
        return None
    calls.clear()
    ctx.shell_cls = fake_shell_cls(calls, handler)
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b2}, env="dev")
    assert out.exit_code == errors.TESTS_FAILED and out.run.status == "ROLLED_BACK", out.message
    deploys = [c["argv"] for c in calls if "deploy" in c["argv"] and "--app" in c["argv"]]
    assert deploys[0][deploys[0].index("--app") + 1].endswith(f"{b2}/cdk.out/dev")
    assert deploys[-1][deploys[-1].index("--app") + 1].endswith(f"{b1}/cdk.out/dev")  # previous good bundle
    hist = ledger.history("dev")
    assert [h["action"] + ":" + h["result"] for h in hist[-2:]] == ["deploy:FAILED", "rollback:SUCCEEDED"]
    assert ledger.current_deploy("dev")["build"] == b1


class LiveCfn(FakeCfn):
    """Core stack exists (an env imported with `vra ledger import`, no vra history yet)."""

    def __init__(self, description):
        super().__init__()
        self.description = description

    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackName": StackName, "StackStatus": "UPDATE_COMPLETE", "Description": self.description,
                            "Outputs": []}]}


def test_dry_run_deploy_completes(ctx, repos, calls):
    """E2E finding: `vra --dry-run deploy` crashed with KeyError 'ok' in the post-deploy smoke because the dry-run
    smoke writes no JUnit and aggregate_junit returned a summary without 'ok'."""
    b = _build(ctx).run.state["result"]["buildId"]
    env = util.read_yaml(ctx.ledger.root / "envs/dev/env.yaml")
    env["liveTest"] = {"webUrl": "https://dev.vams.example.com"}
    util.write_yaml(ctx.ledger.root / "envs/dev/env.yaml", env)
    ctx.ledger._envs.clear()
    ctx.dry_run = True
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == 0, out.message
    assert ctx.ledger.history("dev") == []  # dry-run records nothing


def test_imported_env_plans_migrations_from_deployed_stack_version(ctx, repos, calls):
    """E2E finding: an env with no vra history (imported config) was planned as a 'fresh install' even though its
    core stack was live, which would silently skip version-delta migrations. The deployed version is read from
    the core stack description instead."""
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": LiveCfn(
        "(SO9299) (VAMS-CORE) (version:2.5.3) Primary Components for the Visual Asset Management Systems")})
    ctx.ledger.env("dev")["backupBeforeDeploy"] = False
    ctx.dry_run = True  # stop short of running the chained migration scripts
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    plan = out.run.find_step("deploy.dev.migration-plan")["result"]
    assert plan["from"] == "2.5.3" and plan["fromSource"].startswith("stack description")
    assert [s["id"] for s in plan["steps"]] == ["v2.5_to_v2.6"]


def test_imported_env_without_readable_version_needs_human(ctx, repos, calls):
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": LiveCfn("some stack")})
    ctx.ledger.env("dev")["backupBeforeDeploy"] = False
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == errors.NEEDS_HUMAN and "VAMS version" in out.message
    assert not any("deploy" in c["argv"] and "--app" in c["argv"] and "diff" not in c["argv"] for c in calls)


def test_manual_rollback_to_build_records_vams_version(ctx, repos, calls, ledger):
    """E2E finding: `vra rollback --to-build B` wrote a history line with vamsVersion null, so the next deploy's
    migration plan had no 'from' version."""
    b = _build(ctx).run.state["result"]["buildId"]
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    assert runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev").exit_code == 0
    ctx.run_id = None
    out = runner.start(ctx, "rollback", {"env": "dev", "toBuild": b}, env="dev")
    assert out.exit_code == 0, out.message
    h = ledger.history("dev")[-1]
    assert h["action"] == "rollback" and h["vamsVersion"] == "2.6.5"


def test_ci_rollback_fetches_the_previous_bundle_from_the_bundle_store(ctx, repos, calls, ledger, monkeypatch):
    """Live test: with a git home (pushArtifacts false) a CodeBuild job holds only its own execution's
    bundle, so the pipeline's auto-rollback stopped at "unknown build <previous>". The build now mirrors each bundle
    to VRA_BUNDLE_STORE (create-only) and a rollback fetches the previous one, bound to the manifest sha256 the
    env's deploy record holds."""
    import shutil

    import boto3
    import moto

    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-pipeline-artifacts")
        monkeypatch.setenv(build_mod.BUNDLE_STORE_ENV, "s3://vra-pipeline-artifacts/vra-bundles")
        ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()}, moto=True)
        ctx.ledger.env("dev")["backupBeforeDeploy"] = False
        first = _build(ctx)
        assert first.exit_code == 0, first.message
        b1 = first.run.state["result"]["buildId"]
        up = [s for s in first.run.state["steps"] if str(s.get("id", "")).endswith("build.upload")] \
            if isinstance(first.run.state["steps"], list) else [first.run.state["steps"]["build.upload"]]
        assert up[0]["result"]["uploaded"] is True
        assert runner.start(ctx, "deploy", {"env": "dev", "build": b1}, env="dev").exit_code == 0
        ctx.run_id = None
        assert ledger.history("dev")[-1]["manifestSha256"] == util.sha256_file(
            build_mod.build_dir(ctx, b1) / "manifest.json")
        w = repos["tmp"] / "fork-next"
        git(repos["tmp"], "clone", "-q", str(repos["fork"]), str(w))
        (w / "NOTE.md").write_text("customer note\n")
        git(w, "add", "NOTE.md")
        git(w, "commit", "-q", "-m", "comment-only change")
        git(w, "push", "-q", "origin", "main")
        second = _build(ctx)
        b2 = second.run.state["result"]["buildId"]
        assert second.exit_code == 0 and b2 != b1
        assert runner.start(ctx, "deploy", {"env": "dev", "build": b2}, env="dev").exit_code == 0
        ctx.run_id = None

        # the test job of b2's execution: only b2's bundle is on the machine
        shutil.rmtree(build_mod.build_dir(ctx, b1))
        out = runner.start(ctx, "rollback", {"env": "dev"}, env="dev")
        assert out.exit_code == 0, out.message
        h = ledger.history("dev")[-1]
        assert h["action"] == "rollback" and h["build"] == b1 and h["manifestSha256"]
        ctx.run_id = None

        # a bundle in the store that is not the one dev deployed is refused
        s3 = boto3.client("s3", region_name="us-east-1")
        key = f"vra-bundles/artifacts/{b1}/manifest.json"
        doc = json.loads(s3.get_object(Bucket="vra-pipeline-artifacts", Key=key)["Body"].read())
        doc["commit"] = "0" * 40
        body = json.dumps(doc).encode()
        s3.put_object(Bucket="vra-pipeline-artifacts", Key=key, Body=body)
        s3.put_object(Bucket="vra-pipeline-artifacts", Key=f"vra-bundles/artifacts/{b1}/manifest.sha256",
                      Body=(hashlib.sha256(body).hexdigest() + "  manifest.json\n").encode())
        shutil.rmtree(build_mod.build_dir(ctx, b1))
        bad = runner.start(ctx, "rollback", {"env": "dev", "toBuild": b1}, env="dev")
        assert bad.exit_code != 0 and "does not match the manifest dev deployed" in bad.message, bad.message


def test_ci_rollback_without_a_bundle_store_names_the_setting(ctx, repos, calls, monkeypatch):
    import shutil

    monkeypatch.delenv(build_mod.BUNDLE_STORE_ENV, raising=False)
    b = _build(ctx).run.state["result"]["buildId"]
    shutil.rmtree(build_mod.build_dir(ctx, b))
    with pytest.raises(errors.UsageError, match="VRA_BUNDLE_STORE"):
        build_mod.load_manifest(ctx, b)


def test_parse_diff():
    text = "[-] AWS::S3::Bucket AssetBucket AssetBucketABC destroy\n[~] AWS::Lambda::Function Fn Fn1\n" \
           "[+] AWS::SQS::Queue Q Q1\n[~] AWS::Cognito::UserPool Pool Pool1 may be replaced\n"
    p = deploy.parse_diff(text)
    assert p["changes"] == 4
    assert [d["type"] for d in p["destructive"]] == ["AWS::S3::Bucket", "AWS::Cognito::UserPool"]
