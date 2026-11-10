# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Migration audit 2026-11-10 (#1244 D18, D19): the post-deploy reindex decision and a reindex whose result is
confirmed (Lambda RequestResponse, direct mode)."""

import io
import json

import pytest
from botocore.exceptions import ReadTimeoutError

from vra import build as build_mod, errors, migrate, reindex, runner, util
from vra.errors import NeedsHuman

from conftest import FakeAws

# ----------------------------------------------------------------- D18: when a deploy reindexes
SERVERLESS = {"app": {"openSearch": {"useServerless": {"enabled": True}, "useProvisioned": {"enabled": False}}}}
PROVISIONED = {"app": {"openSearch": {"useServerless": {"enabled": False}, "useProvisioned": {"enabled": True}}}}
OFF = {"app": {"openSearch": {"useServerless": {"enabled": False}, "useProvisioned": {"enabled": False}}}}


def _on_cdk(cfg):
    out = json.loads(json.dumps(cfg))
    out["app"]["openSearch"]["reindexOnCdkDeploy"] = True
    return out


def test_matrix_names_2_6_required_and_2_7_recommended():
    vs = migrate.matrix()["versions"]
    assert vs["2.6.0"]["reindex"] == "required" and vs["2.6.0"]["coveredBy"] == "v2.5_to_v2.6"
    assert vs["2.7.0"]["reindex"] == "recommended" and "#606" in vs["2.7.0"]["reason"]


@pytest.mark.parametrize("frm,to,old,new,applied,decision,skipped", [
    ("2.6.6", "2.6.7", SERVERLESS, SERVERLESS, [], "none", None),
    ("2.6.6", "2.7.0", SERVERLESS, SERVERLESS, [], "recommended", None),
    ("2.5.3", "2.6.6", SERVERLESS, SERVERLESS, ["v2.5_to_v2.6"], "none", None),   # covered by the migration
    ("2.5.3", "2.6.6", SERVERLESS, SERVERLESS, [], "required", None),             # migration skipped
    ("2.5.3", "2.7.0", SERVERLESS, SERVERLESS, ["v2.5_to_v2.6"], "recommended", "reindexed in this deploy"),
    ("2.6.6", "2.6.7", SERVERLESS, PROVISIONED, [], "required", None),            # config switch
    ("2.6.6", "2.7.0", SERVERLESS, OFF, [], "required", "search is off"),
    ("2.6.6", "2.7.0", SERVERLESS, _on_cdk(SERVERLESS), [], "recommended", "reindexOnCdkDeploy"),
    ("2.6.6", "2.6.7", SERVERLESS, _on_cdk(PROVISIONED), [], "required", "reindexOnCdkDeploy"),
    (None, "2.7.0", None, SERVERLESS, [], "none", None),                          # first install
])
def test_decide(frm, to, old, new, applied, decision, skipped):
    d = reindex.decide(frm, to, old, new, applied=applied)
    assert d["decision"] == decision, d
    if skipped:
        assert skipped in (d["skip"] or ""), d
    else:
        assert d["skip"] is None, d


def test_decide_index_name_change_is_required():
    d = reindex.decide("2.6.6", "2.6.7", SERVERLESS, SERVERLESS, {"assetIndexName": "vams-assets-v3"},
                       {"assetIndexName": "vams-assets-v4"})
    assert d["decision"] == "required" and d["reasons"][0]["source"] == "index-names"


def test_decide_without_the_deployed_config_notes_it():
    d = reindex.decide("2.6.6", "2.6.7", None, SERVERLESS)
    assert d["decision"] == "none" and any("not known" in n for n in d["notes"])


def test_index_names_from_the_assembly(tmp_path):
    tpl = {"Resources": {
        "A": {"Type": "AWS::SSM::Parameter", "Properties": {"Name": "/vams-dev/aos/assetIndexName",
                                                             "Value": "vams-assets-v3"}},
        "F": {"Type": "AWS::SSM::Parameter", "Properties": {"Name": "/vams-dev/aos/fileIndexName",
                                                             "Value": "vams-files-v3"}},
        "X": {"Type": "AWS::SSM::Parameter", "Properties": {"Name": "/vams-dev/other", "Value": "x"}}}}
    (tmp_path / "core.template.json").write_text(json.dumps(tpl))
    assert reindex.index_names(tmp_path) == {"assetIndexName": "vams-assets-v3", "fileIndexName": "vams-files-v3"}


class _Run:
    def __init__(self):
        self.state = {}

    def save(self):
        pass


def _bundle_cfg(ctx, build_id, target, cfg):
    p = build_mod.build_dir(ctx, build_id) / "effective-config" / f"{target}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    util.write_json(p, cfg)


def test_post_deploy_runs_a_required_reindex(ctx, monkeypatch):
    _bundle_cfg(ctx, "old", "dev", SERVERLESS)
    _bundle_cfg(ctx, "new", "dev", PROVISIONED)
    ran = []
    monkeypatch.setattr(reindex, "run", lambda *a, **kw: ran.append(kw) or {"status": "succeeded"})
    r = _Run()
    out = reindex.post_deploy(ctx, r, "dev", None, "2.6.6", "2.6.7", "new", "dev", "old", "dev", [])
    assert out["ran"] is True and ran and ran[0]["clear_indexes"] is True


def test_post_deploy_skips_with_reindex_on_cdk_deploy(ctx, monkeypatch):
    _bundle_cfg(ctx, "old", "dev", SERVERLESS)
    _bundle_cfg(ctx, "new", "dev", _on_cdk(PROVISIONED))
    monkeypatch.setattr(reindex, "run", lambda *a, **kw: pytest.fail("must not reindex"))
    r = _Run()
    out = reindex.post_deploy(ctx, r, "dev", None, "2.6.6", "2.6.7", "new", "dev", "old", "dev", [])
    assert out["ran"] is False and "reindexOnCdkDeploy" in r.state["notes"][0]


def test_post_deploy_recommended_is_a_note(ctx, monkeypatch):
    _bundle_cfg(ctx, "old", "dev", SERVERLESS)
    _bundle_cfg(ctx, "new", "dev", SERVERLESS)
    monkeypatch.setattr(reindex, "run", lambda *a, **kw: pytest.fail("must not reindex"))
    r = _Run()
    out = reindex.post_deploy(ctx, r, "dev", None, "2.6.6", "2.7.0", "new", "dev", "old", "dev", [])
    assert out["ran"] is False and "vra reindex --env dev" in r.state["notes"][0] and "#606" in r.state["notes"][0]


def test_post_deploy_skips_after_the_ab_copy_reindexed(ctx, monkeypatch):
    _bundle_cfg(ctx, "old", "dev", SERVERLESS)
    _bundle_cfg(ctx, "new", "dev", PROVISIONED)
    monkeypatch.setattr(reindex, "run", lambda *a, **kw: pytest.fail("must not reindex"))
    out = reindex.post_deploy(ctx, _Run(), "dev", None, "2.6.6", "2.6.7", "new", "dev", "old", "dev", [], None,
                              "the A/B slot copy reindexed the new slot")
    assert out["ran"] is False and out["skip"].startswith("the A/B slot copy")


def test_deploy_runs_the_reindex_step_between_migrations_and_smoke(ctx, repos, calls):
    from test_build_deploy import FakeCfn, _build
    out = _build(ctx)
    b = out.run.state["result"]["buildId"]
    bdir = build_mod.build_dir(ctx, b)
    assert (bdir / "effective-config" / "dev.json").is_file()                 # the build keeps the config
    assert (bdir / "reindexer" / "backend" / "backend").is_dir()               # and the direct-mode source
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    dep = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev")
    assert dep.exit_code == 0, dep.message
    ids = [s["id"] for s in dep.run.state["steps"]] if isinstance(dep.run.state["steps"], list) \
        else list(dep.run.state["steps"])
    assert ids.index("deploy.dev.reindex") < ids.index("deploy.dev.smoke")
    assert dep.run.find_step("deploy.dev.reindex")["result"]["decision"] == "none"   # first install


# ----------------------------------------------------------------- D19: a reindex result is confirmed
def _resp(payload, function_error=None):
    r = {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(payload).encode())}
    if function_error:
        r["FunctionError"] = function_error
    return r


def test_lambda_result_parsing():
    ok = reindex._parse_lambda_result("fn", _resp({"statusCode": 200, "body": json.dumps(
        {"message": "done", "failures": [], "results": {"assets": {"indexed": 3, "name": "x"}}})}))
    assert ok["status"] == "succeeded" and ok["counts"] == {"assets": {"indexed": 3}}
    fe = reindex._parse_lambda_result("fn", _resp({"errorMessage": "Task timed out after 900.00 seconds"},
                                                  "Unhandled"))
    assert fe["status"] == "failed" and "Unhandled" in fe["error"]
    fail = reindex._parse_lambda_result("fn", _resp({"statusCode": 200, "body": json.dumps(
        {"failures": ["asset a1: mapping error"]})}))
    assert fail["status"] == "failed" and "mapping error" in fail["error"]
    code = reindex._parse_lambda_result("fn", _resp({"statusCode": 500, "body": json.dumps({"error": "boom"})}))
    assert code["status"] == "failed" and "boom" in code["error"]
    empty = reindex._parse_lambda_result("fn", {"StatusCode": 200, "Payload": io.BytesIO(b"")})
    assert empty["status"] == "unverified"
    garbage = reindex._parse_lambda_result("fn", {"StatusCode": 200, "Payload": io.BytesIO(b"<html>")})
    assert garbage["status"] == "unverified"


class FakeLambda:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def invoke(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.resp


def _names(monkeypatch):
    from vra import vams_resources
    monkeypatch.setattr(vams_resources, "discover", lambda *a, **kw: {
        "prefix": "/vams-dev-us-east-1/resourceNames", "lambdas": {vams_resources.REINDEXER_KEY: "dev-reindexer"},
        "tables": {}, "buckets": {}, "all": {}})


def test_lambda_reindex_waits_for_the_answer(ctx, monkeypatch):
    _names(monkeypatch)
    lam = FakeLambda(_resp({"statusCode": 200, "body": json.dumps({"failures": [], "results": {}})}))
    got = {}

    class Aws(FakeAws):
        def client(self, env, service, role="deploy", region=None, config=None):
            got["config"] = config
            return lam
    ctx.aws = Aws(ctx.ledger)
    res = reindex.lambda_reindex(ctx, "dev")
    assert res["status"] == "succeeded"
    assert lam.calls[0]["InvocationType"] == "RequestResponse"
    assert got["config"].read_timeout > 15 * 60 and got["config"].retries["max_attempts"] == 0


def test_lambda_reindex_timeout_is_unverified(ctx, monkeypatch):
    _names(monkeypatch)
    lam = FakeLambda(exc=ReadTimeoutError(endpoint_url="https://lambda"))
    ctx.aws = FakeAws(ctx.ledger, {"lambda": lam})
    res = reindex.lambda_reindex(ctx, "dev")
    assert res["status"] == "unverified" and "--mode direct" in res["error"]


def test_restore_reindex_reports_a_failure_instead_of_success(ctx, monkeypatch):
    from vra import restore
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(_resp({"errorMessage": "x"}, "Unhandled"))})
    r = _Run()
    res = restore.trigger_reindex(ctx, "dev", run=r, what="the restore")
    assert res["status"] == "failed"
    assert "the restore reindex failed" in r.state["notes"][0] and "vra reindex --env dev" in r.state["notes"][0]


def test_vra_reindex_fails_on_a_function_error(ctx, monkeypatch):
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(_resp({"errorMessage": "x"}, "Unhandled"))})
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    out = runner.start(ctx, "reindex", {"env": "dev"}, env="dev")
    assert out.exit_code not in (0, errors.NEEDS_HUMAN) and "failed" in out.message


def test_vra_reindex_unconfirmed_needs_a_human(ctx, monkeypatch):
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(exc=ReadTimeoutError(endpoint_url="https://lambda"))})
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    out = runner.start(ctx, "reindex", {"env": "dev"}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "could not be confirmed" in out.message


def test_vra_reindex_succeeds_and_records_history(ctx, ledger, monkeypatch):
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(_resp({"statusCode": 200, "body": json.dumps(
        {"failures": [], "results": {}})}))})
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    out = runner.start(ctx, "reindex", {"env": "dev", "clearIndexes": True}, env="dev")
    assert out.exit_code == 0, out.message
    h = ledger.history("dev")[-1]
    assert h["action"] == "reindex" and h["result"] == "SUCCEEDED" and h["mode"] == "lambda"


def test_vra_reindex_on_a_gated_env_needs_an_approval(ctx, monkeypatch):
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(_resp({"statusCode": 200, "body": "{}"}))})
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    out = runner.start(ctx, "reindex", {"env": "prod"}, env="prod")
    assert out.exit_code == errors.NEEDS_HUMAN and "approval" in out.message.lower()


def test_direct_mode_refuses_a_bundle_without_the_reindexer(ctx, tmp_path):
    (build_mod.build_dir(ctx, "old-bundle")).mkdir(parents=True, exist_ok=True)
    with pytest.raises(NeedsHuman, match="no reindex utility"):
        reindex.direct_reindex(ctx, "dev", "old-bundle", None)


def test_direct_inputs_from_resource_names(ctx):
    names = {"prefix": "/vams-dev-us-east-1/resourceNames", "tables": {
        "dynamoTables/assetStorage": "A", "dynamoTables/s3AssetBucketsStorage": "B",
        "dynamoTables/assetFileMetadataStorage": "C"}}
    argv = reindex.direct_inputs(ctx, "dev", names, PROVISIONED)
    kv = dict(zip(argv[::2], argv[1::2]))
    assert kv["--asset-storage-table-name"] == "A" and kv["--asset-file-metadata-storage-table-name"] == "C"
    assert kv["--opensearch-asset-index-ssm-param"] == "/vams-dev-us-east-1/aos/assetIndexName"
    assert kv["--opensearch-endpoint-ssm-param"] == "/vams-dev-us-east-1/aos/endPoint"
    assert kv["--opensearch-type"] == "provisioned" and kv["--region"] == "us-east-1"


def test_reindex_mode_setting_and_direct_permissions(ctx, ledger):
    from vra import permissions
    assert reindex.settings(ledger.env("dev")) == {"mode": "lambda"}
    assert "reindexDirect" not in permissions.env_features(ctx, "dev")
    ledger.env("dev")["reindex"] = {"mode": "direct"}
    assert permissions.env_features(ctx, "dev")["reindexDirect"] == "on"
    assert permissions.placeholders(ctx, "dev")["SsmRoot"] == "/vams-dev-us-east-1"


# ----------------------------------------------------------------- D20: the dry run resolves what the run would do
def _deployed(ledger, build_id):
    ledger.append_history("dev", {"action": "deploy", "build": build_id, "result": "SUCCEEDED",
                                  "vamsVersion": "2.6.6"})


def test_dry_run_direct_stops_on_a_bundle_without_the_reindexer(ctx, ledger, monkeypatch):
    """Reindex smoke: `vra --dry-run reindex --mode direct` reported SUCCEEDED on a bundle made by an older engine,
    which a real run refuses."""
    _names(monkeypatch)
    build_mod.build_dir(ctx, "cust-v2.6.6-11").mkdir(parents=True, exist_ok=True)
    _deployed(ledger, "cust-v2.6.6-11")
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    ctx.dry_run = True
    out = runner.start(ctx, "reindex", {"env": "dev", "mode": "direct"}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "no reindex utility" in out.message


def test_dry_run_direct_resolves_the_inputs(ctx, ledger, monkeypatch):
    from vra import vams_resources
    monkeypatch.setattr(vams_resources, "discover", lambda *a, **kw: {
        "prefix": "/vams-dev-us-east-1/resourceNames", "lambdas": {}, "buckets": {}, "all": {}, "tables": {
            "dynamoTables/assetStorage": "A", "dynamoTables/s3AssetBucketsStorage": "B",
            "dynamoTables/assetFileMetadataStorage": "C"}})
    bdir = build_mod.build_dir(ctx, "cust-v2.7.0-1")
    (bdir / "migrations" / "tools").mkdir(parents=True)
    (bdir / "migrations" / "tools" / "reindex_utility.py").write_text("")
    (bdir / "reindexer" / "backend" / "backend").mkdir(parents=True)
    _deployed(ledger, "cust-v2.7.0-1")
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    ctx.dry_run = True
    out = runner.start(ctx, "reindex", {"env": "dev", "mode": "direct"}, env="dev")
    assert out.exit_code == 0, out.message
    res = out.run.find_step("reindex.run")["result"]
    assert res["dryRun"] is True and res["build"] == "cust-v2.7.0-1"
    assert res["argv"][res["argv"].index("--asset-storage-table-name") + 1] == "A"


def test_dry_run_lambda_names_the_reindexer(ctx, monkeypatch):
    _names(monkeypatch)
    ctx.aws = FakeAws(ctx.ledger, {"lambda": FakeLambda(exc=AssertionError("a dry run must not invoke"))})
    monkeypatch.setattr("vra.permissions.preflight", lambda *a, **kw: {"ok": True})
    ctx.dry_run = True
    out = runner.start(ctx, "reindex", {"env": "dev"}, env="dev")
    assert out.exit_code == 0, out.message
    assert out.run.find_step("reindex.run")["result"]["function"] == "dev-reindexer"
    assert ctx.ledger.history("dev") == []


# ----------------------------------------------------------------- D32: direct mode reads the reported failures
DIRECT_OK = """\
INFO DIRECT REINDEXER RUN SUCCESSFUL
INFO Asset Reindexing Results:
INFO   Total: 22
INFO   Success: 22
INFO   Failed: 0
INFO File Reindexing Results:
INFO   Buckets Processed: 1
INFO   Objects Scanned: 91
INFO   Total: 60
INFO   Success: 60
INFO   Failed: 0
INFO Reindexing completed successfully
"""
# the handler's error line, as its JSON logger writes it, then the utility still exits 0 (upstream #1296)
DIRECT_FAILED_RECORDS = DIRECT_OK.replace("Success: 60\nINFO   Failed: 0", "Success: 57\nINFO   Failed: 3") \
    .replace("INFO DIRECT", '{"level":"ERROR","message":"Reindexing did not complete: 3 files failed to reindex",'
                            '"service":"CrReindexer"}\nINFO DIRECT')


def _direct_bundle(ctx, monkeypatch, tmp_path, output, rc=0):
    import subprocess
    from types import SimpleNamespace

    from vra import vams_resources
    from conftest import fake_shell_cls
    monkeypatch.setattr(vams_resources, "discover", lambda *a, **kw: {
        "prefix": "/vams-dev-us-east-1/resourceNames", "lambdas": {}, "buckets": {}, "all": {}, "tables": {
            "dynamoTables/assetStorage": "A", "dynamoTables/s3AssetBucketsStorage": "B",
            "dynamoTables/assetFileMetadataStorage": "C"}})
    bdir = build_mod.build_dir(ctx, "cust-v2.7.0-1")
    (bdir / "migrations" / "tools").mkdir(parents=True)
    (bdir / "migrations" / "tools" / "reindex_utility.py").write_text("")
    (bdir / "reindexer" / "backend" / "backend").mkdir(parents=True)
    monkeypatch.setenv("VRA_MIGRATION_PYTHON", "utility-python")
    calls: list = []
    ctx.shell_cls = fake_shell_cls(calls, lambda argv, cwd, env: subprocess.CompletedProcess(argv, rc, output, ""))
    return SimpleNamespace(log_path=tmp_path / "direct.log"), calls


def test_direct_mode_reports_the_counts(ctx, monkeypatch, tmp_path):
    sc, calls = _direct_bundle(ctx, monkeypatch, tmp_path, DIRECT_OK)
    res = reindex.direct_reindex(ctx, "dev", "cust-v2.7.0-1", sc)
    assert res["status"] == "succeeded", res
    assert res["counts"]["files"] == {"total_count": 60, "success_count": 60, "failed_count": 0}
    assert calls[0]["argv"][2:4] == ["--mode", "direct"]


def test_direct_mode_exit_0_with_reported_failures_is_failed(ctx, monkeypatch, tmp_path):
    sc, _ = _direct_bundle(ctx, monkeypatch, tmp_path, DIRECT_FAILED_RECORDS)
    res = reindex.direct_reindex(ctx, "dev", "cust-v2.7.0-1", sc)
    assert res["status"] == "failed" and "3 files failed to reindex" in res["error"], res


def test_direct_mode_failed_counts_without_the_error_line_are_failed(ctx, monkeypatch, tmp_path):
    out = DIRECT_OK.replace("Success: 22\nINFO   Failed: 0", "Success: 20\nINFO   Failed: 2")
    sc, _ = _direct_bundle(ctx, monkeypatch, tmp_path, out)
    res = reindex.direct_reindex(ctx, "dev", "cust-v2.7.0-1", sc)
    assert res["status"] == "failed" and "2 assets failed" in res["error"], res


def test_direct_mode_index_clearing_failure_is_failed(ctx, monkeypatch, tmp_path):
    out = '{"message":"Reindexing did not complete: index clearing did not complete for asset_index"}\n' + DIRECT_OK
    sc, _ = _direct_bundle(ctx, monkeypatch, tmp_path, out)
    res = reindex.direct_reindex(ctx, "dev", "cust-v2.7.0-1", sc, clear_indexes=True)
    assert res["status"] == "failed" and "index clearing" in res["error"], res
