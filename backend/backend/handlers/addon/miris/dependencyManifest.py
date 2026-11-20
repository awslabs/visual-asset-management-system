# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reads the dependency manifest of a Miris upload and compares it with a listing of the asset.

The upload pipeline writes the manifest to the auxiliary bucket and records its id on the streamed
file (`miris_dependency_manifest`). It lists the root USD file and every file of its dependency tree
(sublayers, references, textures and other assets) by asset-relative path, each with the ETag of the
bytes the stream was built from:

    {"files": {"scene.usda": "<etag>", "textures/wood.png": "<etag>"}, "root": "scene.usda"}

The id is the hex SHA-256 of the manifest's canonical bytes, so a manifest whose bytes do not hash to
the recorded id is not used. The format, the key layout and the ETag normalization mirror
backendPipelines/addon/conversion/mirisUpload/container/dependency_manifest.py.
"""
import hashlib
import json
import posixpath
import re
from typing import Iterable

# Auxiliary-bucket prefix of the manifests. Mirrors MANIFEST_PREFIX in the container's
# dependency_manifest.py and MIRIS_DEPENDENCY_MANIFEST_PREFIX in
# infra/lib/nestedStacks/addon/miris/upload/lambdaBuilder/mirisUploadFunctions.ts.
MANIFEST_PREFIX = "pipelines/miris-upload/manifests"

_MANIFEST_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(Exception):
    """The manifest is not usable: a malformed id, a hash mismatch, or a malformed body."""


def normalize_etag(etag) -> str:
    """An S3 ETag without its surrounding quotes and whitespace ('"abc"' and 'abc' are one ETag)."""
    return (etag or "").strip().strip('"') if isinstance(etag, str) else ""


def is_manifest_id(value: str) -> bool:
    return isinstance(value, str) and bool(_MANIFEST_ID_PATTERN.fullmatch(value))


def manifest_key(database_id: str, asset_id: str, manifest_id: str) -> str:
    """Auxiliary-bucket key of the manifest with id `manifest_id` for one asset."""
    return f"{MANIFEST_PREFIX}/{database_id}/{asset_id}/{manifest_id}.json"


def asset_root_prefix(asset: dict) -> str:
    """The asset's root prefix in its bucket (`assetLocation.Key`, no leading slash, ending in
    '/'), or "" when the asset records none."""
    location = asset.get("assetLocation")
    key = location.get("Key") if isinstance(location, dict) else None
    if not isinstance(key, str) or not key.strip("/"):
        return ""
    key = key.lstrip("/")
    return key if key.endswith("/") else key + "/"


def parse_manifest(data: bytes, manifest_id: str) -> dict:
    """The manifest's {asset-relative path: normalized ETag}, after checking that `data` hashes to
    `manifest_id`. Raises `ManifestError` for a hash mismatch or a malformed manifest."""
    if hashlib.sha256(data).hexdigest() != manifest_id:
        raise ManifestError("The manifest does not match its recorded id")
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ManifestError("The manifest is not JSON") from e
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise ManifestError("The manifest lists no files")
    etags = {}
    for path, etag in files.items():
        if not isinstance(path, str) or not path.strip("/") or not isinstance(etag, str):
            raise ManifestError("The manifest has a malformed file entry")
        etags[path.lstrip("/")] = normalize_etag(etag)
    return etags


def listed_etags(objects: Iterable[dict], root_prefix: str) -> dict:
    """{asset-relative path: normalized ETag} of the listed objects under `root_prefix`.

    Paths are normalized the way the upload pipeline names them ('./' and 'a/../' resolved); folder
    markers, keys outside the root and keys that resolve outside it are skipped.
    """
    etags = {}
    for obj in objects:
        key = obj.get("Key")
        if not isinstance(key, str) or key.endswith("/") or not key.startswith(root_prefix):
            continue
        normalized = posixpath.normpath(key[len(root_prefix):])
        if normalized in ("", ".") or normalized.startswith("/") or normalized.split("/")[0] == "..":
            continue
        etags[normalized] = normalize_etag(obj.get("ETag"))
    return etags


def is_stale(manifest_etags: dict, current_etags: dict) -> bool:
    """True when a manifest file is missing from the listing or its ETag differs."""
    return any(current_etags.get(path) != etag for path, etag in manifest_etags.items())
