# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""backup / restore against moto, and data-copy remapping/filtering."""

import boto3
import pytest

from vra import backup, datacopy, errors, restore, runner, util
from vra.errors import NeedsHuman

from conftest import FakeAws, git
from test_build_deploy import FakeCfn

moto = pytest.importorskip("moto")


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        yield


def _stack(prefix: str, region="us-east-1", bucket="dev-assets"):
    ssm = boto3.client("ssm", region_name=region)
    ddb = boto3.client("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    names = {"dynamoTables/assetStorage": f"{prefix}-AssetStorage",
             "dynamoTables/s3AssetBucketsStorage": f"{prefix}-S3AssetBuckets",
             "dynamoTables/appFeatureEnabledStorage": f"{prefix}-Features"}
    for k, t in names.items():
        ddb.create_table(TableName=t, KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        ssm.put_parameter(Name=f"{prefix}/{k}", Value=t, Type="String")
    ssm.put_parameter(Name=f"{prefix}/s3Buckets/assetAuxiliary", Value=f"{bucket}-aux", Type="String")
    for b in (bucket, f"{bucket}-aux"):
        s3.create_bucket(Bucket=b)
        s3.put_bucket_versioning(Bucket=b, VersioningConfiguration={"Status": "Enabled"})
    ddb.put_item(TableName=names["dynamoTables/s3AssetBucketsStorage"],
                 Item={"id": {"S": "b1"}, "bucketId": {"S": f"{bucket}-id"}, "bucketName": {"S": bucket},
                       "baseAssetsPrefix": {"S": "/"}})
    return names


def allow_degraded(ledger, env):
    d = util.read_yaml(ledger.root / f"envs/{env}/env.yaml")
    d["backup"] = {"allowDegraded": True}
    util.write_yaml(ledger.root / f"envs/{env}/env.yaml", d)
    ledger._envs.clear()


def test_backup_checkpoint_and_restore(ctx, ledger, aws):
    names = _stack("/vams-dev-us-east-1/resourceNames")
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "orig"}})
    s3.put_object(Bucket="dev-assets", Key="a1/model.glb", Body=b"v1")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "backup", {"env": "dev"}, env="dev")
    assert out.exit_code == 0, out.message
    cid = out.run.state["checkpoint"]
    cp = backup.load_checkpoint(ctx, cid)
    assert {t["key"] for t in cp["tables"]} == set(names)
    assert all(t.get("backupArn") for t in cp["tables"])
    assert any("PITR disabled" in w for w in cp["warnings"])
    assets = next(b for b in cp["buckets"] if b["name"] == "dev-assets")
    assert assets["versioning"] == "Enabled" and assets["objects"] == 1 and assets["kind"] == "asset"
    assert ledger.history("dev")[-1]["action"] == "backup"

    # data changes after the checkpoint
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "changed"}})
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a2"}, "v": {"S": "new"}})
    s3.put_object(Bucket="dev-assets", Key="a1/model.glb", Body=b"v2")
    s3.put_object(Bucket="dev-assets", Key="a2/new.glb", Body=b"x")

    # S3 restore to recorded versions (table restore-from-backup is exercised below via copy helpers)
    res = restore.restore_bucket(ctx, "dev", backup.checkpoint_dir(ctx, cid), assets)
    assert res["restored"] == 1 and res["deleteMarkers"] == 1
    assert s3.get_object(Bucket="dev-assets", Key="a1/model.glb")["Body"].read() == b"v1"

    # table copy-back path: clear live table, copy items from a "restored" table
    tmp = "restored-tmp"
    ddb.create_table(TableName=tmp, KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
    ddb.put_item(TableName=tmp, Item={"id": {"S": "a1"}, "v": {"S": "orig"}})
    live = names["dynamoTables/assetStorage"]
    assert restore.clear_table(ddb, live) == 2
    assert restore.copy_table(ddb, tmp, live) == 1
    assert ddb.get_item(TableName=live, Key={"id": {"S": "a1"}})["Item"]["v"]["S"] == "orig"


def test_restore_gated_by_approval(ctx, ledger, aws):
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "backup", {"env": "test"}, env="test")
    cid = out.run.state["checkpoint"]
    ctx.run_id = None
    r = runner.start(ctx, "restore", {"env": "test", "checkpoint": cid}, env="test")
    assert r.exit_code == errors.NEEDS_HUMAN and f"restore:{cid}" in r.message


# ------------------------------------------------------------------ data copy
def _side(env, account, region, prefix, bucket, bucket_id, part="aws"):
    return {"env": env, "account": account, "region": region, "partition": part, "role": "dataCopy",
            "names": {"prefix": prefix, "all": {"dynamoTables/assetStorage": f"{env}-AssetStorage",
                                                "s3Buckets/assetAuxiliary": f"{bucket}-aux"},
                      "tables": {"dynamoTables/assetStorage": f"{env}-AssetStorage"},
                      "buckets": {"s3Buckets/assetAuxiliary": f"{bucket}-aux"}},
            "buckets": [{"name": f"{bucket}-aux", "key": "s3Buckets/assetAuxiliary", "kind": "ssm"},
                        {"name": bucket, "kind": "asset", "bucketId": bucket_id, "baseAssetsPrefix": "/"}]}


def test_remap_ids_buckets_and_arns():
    src = _side("prod", "333333333333", "us-gov-west-1", "/vams-prod", "prod-assets", "11111111-aaaa", "aws-us-gov")
    dst = _side("test", "222222222222", "us-gov-east-1", "/vams-test", "test-assets", "22222222-bbbb", "aws-us-gov")
    remap, info = datacopy.build_remap(src, dst)
    assert ("prod-assets", "test-assets") in info["bucketPairs"]
    assert ("prod-assets-aux", "test-assets-aux") in info["bucketPairs"]
    item = {"assetId": {"S": "a1"}, "bucketId": {"S": "11111111-aaaa"},
            "assetLocation": {"M": {"Key": {"S": "a1/"}, "Bucket": {"S": "prod-assets"}}},
            "roleArn": {"S": "arn:aws-us-gov:iam::333333333333:role/x"},
            "fnArn": {"S": "arn:aws-us-gov:lambda:us-gov-west-1:333333333333:function:prod-AssetStorage-fn"},
            "foreign": {"S": "arn:aws-us-gov:s3:::shared-bucket"},
            "other": {"S": "arn:aws-us-gov:sns:us-gov-west-1:555555555555:external"},
            "tags": {"SS": ["prod-assets", "t"]}, "n": {"N": "3"}}
    out = remap.item(item)
    assert out["bucketId"]["S"] == "22222222-bbbb"
    assert out["assetLocation"]["M"]["Bucket"]["S"] == "test-assets"
    assert out["roleArn"]["S"] == "arn:aws-us-gov:iam::222222222222:role/x"
    assert out["fnArn"]["S"] == "arn:aws-us-gov:lambda:us-gov-east-1:222222222222:function:test-AssetStorage-fn"
    assert out["foreign"]["S"] == "arn:aws-us-gov:s3:::shared-bucket"
    assert out["other"]["S"].endswith(":555555555555:external")  # other accounts untouched
    assert out["tags"]["SS"] == ["t", "test-assets"] and out["n"] == {"N": "3"}


def test_remap_requires_bucket_map_for_multiple_asset_buckets():
    src = _side("prod", "333333333333", "us-east-1", "/p", "prod-assets", "s1")
    src["buckets"].append({"name": "prod-assets-2", "kind": "asset", "bucketId": "s2", "baseAssetsPrefix": "/"})
    dst = _side("test", "222222222222", "us-east-1", "/t", "test-assets", "d1")
    with pytest.raises(NeedsHuman, match="bucketMap"):
        datacopy.build_remap(src, dst)


def test_database_subset_filter():
    ids = {"db1"}
    assert datacopy.item_in_databases({"databaseId": {"S": "db1"}}, ids)
    assert not datacopy.item_in_databases({"databaseId": {"S": "db2"}}, ids)
    assert datacopy.item_in_databases({"databaseId:assetId": {"S": "db1:a"}}, ids)
    assert not datacopy.item_in_databases({"databaseId:assetId": {"S": "db2:a"}}, ids)
    assert datacopy.item_in_databases({"roleName": {"S": "admin"}}, ids)  # unscoped config rows kept


def test_data_copy_gates(ctx, ledger):
    out = runner.start(ctx, "data-copy", {"from": "dev", "to": "test", "approval": "x"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "allowedSources" in out.message
    ctx.run_id = None
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "x"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "approval required" in out.message


def test_data_copy_scan_end_to_end_same_partition(ctx, ledger, aws):
    """prod -> test via the scan method on moto (export needs real PITR); checks skip-list, remap, S3 copy."""
    p = _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    t = _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    ddb.put_item(TableName=p["dynamoTables/assetStorage"], Item={
        "id": {"S": "a1"}, "databaseId": {"S": "db1"}, "bucketId": {"S": "prod-assets-id"},
        "assetLocation": {"M": {"Key": {"S": "a1/"}}}})
    ddb.put_item(TableName=p["dynamoTables/appFeatureEnabledStorage"], Item={"id": {"S": "prod-flag"}})
    ddb.put_item(TableName=t["dynamoTables/assetStorage"], Item={"id": {"S": "old-test-row"}})
    s3.put_object(Bucket="prod-assets", Key="a1/model.glb", Body=b"m")
    s3.create_bucket(Bucket="vra-staging-example")  # envs/test dataCopy.stagingBucket: assets go through staging
    from test_gates import approve
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")  # source data owner
    allow_degraded(ledger, "test")  # moto tables have PITR off; a gated target needs a complete checkpoint
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == 0, out.message
    staged = s3.list_objects_v2(Bucket="vra-staging-example", Prefix=f"vra/{out.run.run_id}/assets/prod-assets/")
    assert staged["KeyCount"] == 1
    rows = ddb.scan(TableName=t["dynamoTables/assetStorage"])["Items"]
    assert len(rows) == 1 and rows[0]["bucketId"]["S"] == "test-assets-id"
    assert ddb.scan(TableName=t["dynamoTables/appFeatureEnabledStorage"])["Items"] == []  # never copied
    assert s3.get_object(Bucket="test-assets", Key="a1/model.glb")["Body"].read() == b"m"
    rec = ledger.history("test")[-1]
    assert rec["action"] == "data-copy" and rec["cognitoUsersCopied"] is False
    assert ledger.history("prod")[-1]["action"] == "data-copy-source"


def test_data_copy_dry_run_reports_counts_and_changes_nothing(ctx, ledger, aws):
    """E2E finding (W8): `vra --dry-run data-copy` returned only {"dryRun": true}, although the docs and the W8 SOP
    say the dry run reports item and object counts per table and bucket before the destructive replace."""
    p = _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    t = _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    s3 = boto3.client("s3", region_name="us-east-1")
    for i in range(3):
        ddb.put_item(TableName=p["dynamoTables/assetStorage"], Item={"id": {"S": f"a{i}"}})
    ddb.put_item(TableName=t["dynamoTables/assetStorage"], Item={"id": {"S": "old-test-row"}})
    s3.put_object(Bucket="prod-assets", Key="a1/model.glb", Body=b"m")
    s3.put_object(Bucket="prod-assets", Key="a2/model.glb", Body=b"m")
    from test_gates import approve
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")  # source data owner
    allow_degraded(ledger, "test")  # moto tables have PITR off; a gated target needs a complete checkpoint
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    ctx.dry_run = True
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == 0, out.message
    plan = out.run.find_step("data-copy.plan")["result"]
    row = plan["tables"]["dynamoTables/assetStorage"]
    assert (row["sourceItems"], row["targetItems"]) == (3, 1)
    assert plan["tables"]["dynamoTables/appFeatureEnabledStorage"]["skipped"] is True
    assets = next(b for b in plan["buckets"] if b["source"] == "prod-assets")
    assert (assets["target"], assets["sourceObjects"], assets["targetObjects"]) == ("test-assets", 2, 0)
    # nothing changed
    assert ddb.scan(TableName=t["dynamoTables/assetStorage"])["Items"] == [{"id": {"S": "old-test-row"}}]
    assert s3.list_objects_v2(Bucket="test-assets").get("KeyCount", 0) == 0
