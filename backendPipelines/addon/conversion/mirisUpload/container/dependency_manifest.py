#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Dependency manifest of a Miris upload: the ETag of each asset file the stream was built from.

The manifest lists the root USD file and every file of its dependency tree (layers, textures and
other assets) by asset-relative path, each with the ETag of the bytes the run downloaded:

    {"files": {"scene.usda": "<etag>", "textures/wood.png": "<etag>"}, "root": "scene.usda"}

Its canonical form is UTF-8 JSON with sorted keys and no insignificant whitespace, so one tree at
one set of ETags always gives the same bytes. The SHA-256 of those bytes is the manifest id, the
object is stored in the auxiliary bucket under that id, and the stream metadata records it
(``miris_dependency_manifest``). Comparing the manifest with a listing of the asset tells whether a
file the stream was built from has changed. Kept free of boto3/pxr imports so it can be tested in
isolation.
"""
import hashlib
import json

# Auxiliary-bucket prefix of the manifests. Mirrors MIRIS_DEPENDENCY_MANIFEST_PREFIX in
# infra/lib/nestedStacks/addon/miris/upload/lambdaBuilder/mirisUploadFunctions.ts.
MANIFEST_PREFIX = "pipelines/miris-upload/manifests"


def normalize_etag(etag: str) -> str:
    """An S3 ETag without its surrounding quotes and whitespace ('"abc"' and 'abc' are one ETag)."""
    return (etag or "").strip().strip('"')


def build_manifest(root_rel: str, etags_by_path: dict) -> dict:
    """The manifest of the root `root_rel` and its tree, `etags_by_path` mapping each
    asset-relative path (the root included) to the ETag downloaded."""
    return {
        "root": root_rel.lstrip("/"),
        "files": {rel.lstrip("/"): normalize_etag(etag) for rel, etag in etags_by_path.items()},
    }


def canonical_bytes(manifest: dict) -> bytes:
    """The manifest's canonical UTF-8 JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8")


def manifest_id(data: bytes) -> str:
    """The manifest id: the hex SHA-256 of its canonical bytes."""
    return hashlib.sha256(data).hexdigest()


def manifest_key(database_id: str, asset_id: str, manifest_sha: str) -> str:
    """Auxiliary-bucket key of the manifest with id `manifest_sha` for one asset."""
    return f"{MANIFEST_PREFIX}/{database_id}/{asset_id}/{manifest_sha}.json"
