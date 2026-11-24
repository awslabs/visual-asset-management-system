#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""
Claim-key derivation and release for the Miris upload dedup gate.

The gate writes a claim, and several later steps have to delete the *same* object when the run
does not succeed. Both halves live here so the key can only ever be derived one way.

The key is deterministic on the asset and the content of its USD layers and sits at the auxiliary
bucket ROOT: ``locks/miris-upload/{databaseId}/{assetId}/{fingerprint}.claim``. An assetId is unique
only within its database, so the database scopes the key. The fingerprint is a digest of
the asset-relative key and ETag of every file under the asset root whose extension can trigger the
pipeline, so every execution fanned out from one upload computes the same key, and a changed or
added layer computes a new one. Only the BUCKET is taken from the auxiliary URI: its prefix is
scoped to the workflow execution, so folding the prefix into the key would give every fanned-out
execution a different key and defeat the dedup entirely.

A claim is ``pending`` while its run is in flight and ``completed`` once the run succeeded. A
completed claim keeps collapsing repeat triggers on the same content; a pending claim older than the
inner execution's time limit plus a margin is stale (its run ended without reporting) and the next
triggered run replaces it. A successful upload retires the asset's claims on any other content, so
the only claim left is the one for the content Miris streams.
"""
import hashlib
import json
import os

from assetNesting import held_by_nested

# Matches the IAM grants on the auxiliary bucket (MIRIS_CLAIM_PREFIX in mirisUploadFunctions.ts).
CLAIM_PREFIX = "locks/miris-upload"
CLAIM_PENDING = "pending"
CLAIM_COMPLETED = "completed"


def parse_extensions(value):
    """The exact members of a comma-separated extension list, lower-cased."""
    return [ext.strip().lower() for ext in (value or "").split(",") if ext.strip()]


def asset_root_prefix(root_key):
    """The asset root as a listing prefix ending in '/', or '' when there is no root."""
    root = (root_key or "").lstrip("/")
    if not root:
        return ""
    return root if root.endswith("/") else root + "/"


def list_trigger_files(s3_client, bucket, root_prefix, extensions, nested_locations=()):
    """(asset-relative key, ETag) of every file under the asset root with a listed extension, except
    the files of the assets stored in `nested_locations` inside it."""
    files = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=root_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if os.path.splitext(key)[1].lower() not in extensions:
                continue
            if held_by_nested(nested_locations, key):
                continue
            files.append((key[len(root_prefix):], obj.get("ETag", "")))
    return files


def content_fingerprint(files):
    """sha256 of the sorted (asset-relative key, ETag) pairs; '' when there are none."""
    pairs = sorted([rel, etag] for rel, etag in files)
    if not pairs:
        return ""
    return hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode("utf-8")).hexdigest()


def _asset_claim_prefix(database_id, asset_id):
    """The prefix, ending in '/', under which one asset's claims sit."""
    return f"{CLAIM_PREFIX}/{database_id}/{asset_id}/"


def claim_location(aux_uri, database_id, asset_id, fingerprint):
    """(bucket, key) for this asset's content claim, or (None, None) when there is nothing to key on."""
    if not aux_uri or not database_id or not asset_id or not fingerprint:
        return None, None
    bucket = aux_uri.replace("s3://", "").partition("/")[0]
    if not bucket:
        return None, None
    return bucket, f"{_asset_claim_prefix(database_id, asset_id)}{fingerprint}.claim"


def retire_superseded_claims(s3_client, logger, aux_uri, database_id, asset_id, fingerprint):
    """Delete the asset's claims on any content other than `fingerprint` after a successful upload.

    Miris now streams this content, so a later trigger on earlier content (a reverted version, or
    re-uploaded bytes) must upload again rather than meet its old claim. A run without a
    fingerprint (a manual run) keeps none. A deleted claim that an in-flight run still holds only
    costs that run its dedup: its release finds nothing, and a repeat trigger uploads again.

    Best-effort: a successful upload is reported whatever happens here. Returns the keys deleted.
    """
    bucket = (aux_uri or "").replace("s3://", "").partition("/")[0]
    if not bucket or not database_id or not asset_id:
        return []
    _, keep = claim_location(aux_uri, database_id, asset_id, fingerprint)
    deleted = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=_asset_claim_prefix(database_id, asset_id)):
            for obj in page.get("Contents", []):
                if obj["Key"] != keep:
                    s3_client.delete_object(Bucket=bucket, Key=obj["Key"])
                    deleted.append(obj["Key"])
    except Exception as e:
        logger.warning(f"Could not retire superseded claims of asset {database_id}/{asset_id}: {e}")
    if deleted:
        logger.info(f"Retired {len(deleted)} superseded claim(s) of asset {database_id}/{asset_id}")
    return deleted


def claim_body(database_id, asset_id, fingerprint, miris_asset_name, owner, claimed_at,
               state=CLAIM_PENDING):
    """The bytes of a claim."""
    return json.dumps({
        "databaseId": database_id,
        "assetId": asset_id,
        "fingerprint": fingerprint,
        "mirisAssetName": miris_asset_name,
        "owner": owner,
        "state": state,
        "claimedAt": claimed_at,
    }).encode("utf-8")


def read_claim(s3_client, bucket, key):
    """(claim, ETag, claimed-at epoch seconds) of the claim at `key`; (None, None, None) when there
    is no readable claim there.

    The claimed-at time is the claim's own `claimedAt`, else the object's last write (a claim
    written before claims recorded a time), else None.
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        claim = json.loads(response["Body"].read())
    except Exception:
        return None, None, None
    if not isinstance(claim, dict):
        return None, None, None
    claimed_at = claim.get("claimedAt")
    if isinstance(claimed_at, bool) or not isinstance(claimed_at, (int, float)):
        last_modified = response.get("LastModified")
        claimed_at = last_modified.timestamp() if hasattr(last_modified, "timestamp") else None
    return claim, response.get("ETag"), claimed_at


def is_stale(claim, claimed_at, stale_after_seconds, now):
    """Whether a claim belongs to a run that ended without reporting: still pending, and taken more
    than `stale_after_seconds` ago. A completed claim, or one whose age is unknown, never is."""
    if claim.get("state") == CLAIM_COMPLETED or claimed_at is None:
        return False
    return now - claimed_at > stale_after_seconds


def complete_claim(s3_client, logger, aux_uri, database_id, asset_id, fingerprint, owner):
    """Mark this execution's claim completed after a successful run, so later triggers on the same
    content skip it whatever its age.

    Only the claim `owner` holds is changed, and only if it has not been replaced since it was read.
    Best-effort: left pending, the claim goes stale and a later trigger uploads the content again.
    Returns True when the claim was marked.
    """
    bucket, key = claim_location(aux_uri, database_id, asset_id, fingerprint)
    if not bucket:
        return False
    claim, etag, _ = read_claim(s3_client, bucket, key)
    if claim is None or not owner or claim.get("owner") != owner:
        logger.info(f"No claim of this execution to complete at s3://{bucket}/{key}")
        return False
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps({**claim, "state": CLAIM_COMPLETED}).encode("utf-8"),
            IfMatch=etag,
        )
    except Exception as e:
        logger.warning(f"Could not mark claim s3://{bucket}/{key} completed: {e}")
        return False
    logger.info(f"Marked claim s3://{bucket}/{key} completed")
    return True


def owner_id(task_token):
    """Claim owner for an execution: stable across retries of one execution, unique per fan-out.

    A digest rather than the token itself, since the token can complete the workflow task.
    """
    if not task_token:
        return ""
    return hashlib.sha256(task_token.encode("utf-8")).hexdigest()


def claim_owner(s3_client, bucket, key):
    """The owner recorded in the claim at `key`; None when there is no readable claim there."""
    try:
        existing = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception:
        return None
    if not isinstance(existing, dict):
        return None
    return existing.get("owner", "") or ""


def release_claim(s3_client, logger, aux_uri, database_id, asset_id, fingerprint, owner):
    """Delete the claim so a later trigger can upload the same content again.

    Only the execution that wrote the claim releases it: a failed run can be reported more than
    once (its failure branch, then the execution-ended rule), and by then a retried run may hold a
    new claim at the same key. `owner` is this execution's `owner_id`. A run without a fingerprint
    (a manual run, or one whose asset listed no trigger files) wrote no claim and releases nothing.

    Best-effort: a failed release must not mask the failure that prompted it. Returns True when
    a delete was issued.
    """
    bucket, key = claim_location(aux_uri, database_id, asset_id, fingerprint)
    if not bucket:
        logger.info("No claim location to release")
        return False
    held_by = claim_owner(s3_client, bucket, key)
    if held_by is None:
        logger.info(f"No claim to release at s3://{bucket}/{key}")
        return False
    if held_by != (owner or ""):
        logger.info(f"Claim s3://{bucket}/{key} is held by another execution; leaving it")
        return False
    try:
        s3_client.delete_object(Bucket=bucket, Key=key)
        logger.info(f"Released claim s3://{bucket}/{key}")
        return True
    except Exception as e:
        logger.warning(f"Could not release claim s3://{bucket}/{key}: {e}")
        return False
