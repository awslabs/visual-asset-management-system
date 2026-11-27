# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolve an archive selection into members, then check every member before streaming.

Folders and the whole asset expand to their current files (the latest version of each key that is not
a delete marker), explicit files keep their pinned version or resolve to the current one, and an
asset-version pin is already a resolved list. Preview companions (``{file}.previewFile.{ext}``) are left
out unless the run or the request sets ``includePreviewFiles``. Every member is then head-checked on its
exact version:
a delete marker, a missing version, an archive storage class, a ``vams-status`` of archived or deleted,
or a blocked extension or media type refuses the WHOLE archive (owner rule: no partial archives), as
do an entry name the archive writers would refuse and the file and size limits.
"""

import fnmatch
from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError

from .engine.zipstream import check_member_name
from .rules import ARCHIVED_IN_SELECTION, Refusal, is_archived, is_blocked, is_preview_companion


def relative_path(key, base_key):
    """The asset-relative path ('/a/b.glb') of a key under the asset's base key."""
    if not key.startswith(base_key):
        raise Refusal("A selected file is not part of this asset")
    return "/" + key[len(base_key):]


def held_by_excluded(key, exclude_locations):
    """Whether a key is held by one of ``exclude_locations``, the stored locations of other assets nested in
    the asset's location (the job manifest's ``excludeLocations``): a folder location holds every key under
    it; a single-file location its own key, every key under ``{key}/`` and its previews
    ``{key}.previewFile.{ext}``. Those keys are the nested assets' files, not the archived asset's."""
    for location in exclude_locations or ():
        if location.endswith("/"):
            if key.startswith(location):
                return True
        elif key == location or key.startswith(location + "/"):
            return True
        elif key.startswith(location + ".previewFile."):
            extension = key[len(location) + len(".previewFile."):]
            if extension and "/" not in extension:
                return True
    return False


def list_current(s3_client, bucket, prefix):
    """(key, versionId, size, lastModified epoch) of each current object under a prefix, and the
    folder marker keys (ending in '/')."""
    files, markers = [], []
    kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
    while True:
        response = s3_client.list_object_versions(**kwargs)
        for version in response.get("Versions", []) or []:
            if not version.get("IsLatest"):
                continue
            key = version["Key"]
            if key.endswith("/"):
                markers.append(key)
                continue
            modified = version.get("LastModified")
            files.append((key, version.get("VersionId") or None, int(version.get("Size", 0)),
                          modified.timestamp() if hasattr(modified, "timestamp") else 0))
        if not response.get("IsTruncated"):
            return files, markers
        kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
        kwargs["VersionIdMarker"] = response.get("NextVersionIdMarker", "")


def expand_selection(s3_client, bucket, base_key, entries, pinned_files=None, include_preview_files=False,
                     exclude_locations=None):
    """Members ({bucket, key, versionId, size, relativePath, mtime}) and empty-folder relative paths.

    Preview companions (``{file}.previewFile.{ext}``) are left out unless ``include_preview_files``: an
    explicit one, a pinned one and every one a folder or the whole asset lists, as the file list hides
    them. A folder that holds only preview companions then reads as empty. Keys ``exclude_locations`` hold
    (other assets nested in the asset's location) are left out everywhere."""
    def wanted(key):
        if held_by_excluded(key, exclude_locations):
            return False
        return include_preview_files or not is_preview_companion(key)

    members, seen, markers = [], set(), []
    for pinned in pinned_files or []:
        if pinned["key"] in seen or not wanted(pinned["key"]):
            continue
        seen.add(pinned["key"])
        members.append({"bucket": bucket, "key": pinned["key"], "versionId": pinned.get("versionId"),
                        "size": int(pinned.get("size") or 0),
                        "relativePath": relative_path(pinned["key"], base_key), "mtime": 0})
    for entry in entries or []:
        key = entry["key"]
        if entry["type"] == "file":
            if key not in seen and wanted(key):
                seen.add(key)
                members.append({"bucket": bucket, "key": key, "versionId": entry.get("versionId"),
                                "size": None, "relativePath": relative_path(key, base_key),
                                "mtime": 0})
            continue
        files, folder_markers = list_current(s3_client, bucket, key)
        files = [f for f in files if wanted(f[0])]
        folder_markers = [m for m in folder_markers if not held_by_excluded(m, exclude_locations)]
        if not files and not folder_markers:
            raise Refusal("A selected folder is empty or does not exist")
        markers.extend(folder_markers)
        for file_key, version_id, size, mtime in files:
            if file_key in seen:
                continue
            seen.add(file_key)
            members.append({"bucket": bucket, "key": file_key, "versionId": version_id, "size": size,
                            "relativePath": relative_path(file_key, base_key), "mtime": mtime})
    file_paths = [m["relativePath"] for m in members]
    empty_dirs = []
    for marker in sorted(set(markers)):
        rel = relative_path(marker, base_key)
        if rel == "/":
            continue
        if not any(p.startswith(rel) for p in file_paths):
            empty_dirs.append(rel)
    members.sort(key=lambda m: m["relativePath"])
    return members, empty_dirs


def filter_patterns(members, include_patterns=None, exclude_patterns=None):
    """Keep members whose relative path (without the leading '/') matches an include pattern (all when
    none) and no exclude pattern."""
    def matches(path, patterns):
        return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], p)
                   for p in patterns)
    kept = []
    for member in members:
        path = member["relativePath"].lstrip("/")
        if include_patterns and not matches(path, include_patterns):
            continue
        if exclude_patterns and matches(path, exclude_patterns):
            continue
        kept.append(member)
    return kept


def _is_delete_marker(error):
    """Whether a HeadObject error answered for a delete marker (x-amz-delete-marker: true)."""
    headers = (error.response.get("ResponseMetadata") or {}).get("HTTPHeaders") or {}
    return str(headers.get("x-amz-delete-marker", "")).lower() == "true"


def _head(s3_client, member):
    params = {"Bucket": member["bucket"], "Key": member["key"]}
    if member.get("versionId"):
        params["VersionId"] = member["versionId"]
    try:
        return s3_client.head_object(**params)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("405", "MethodNotAllowed"):
            raise Refusal(f"{member['relativePath']} is a deleted version")
        if code in ("404", "NoSuchKey", "NotFound", "NoSuchVersion", "400"):
            # A file archived (or deleted) after the create: its current version is a delete marker.
            if _is_delete_marker(e):
                raise Refusal(ARCHIVED_IN_SELECTION)
            raise Refusal(f"{member['relativePath']} was not found")
        raise


def check_names(members, empty_dirs):
    """Refuse a selection holding an entry name the archive writers would refuse: a name that breaks
    their member-name rule, or two entries that would carry the same name.

    A member is named by its path without the leading slashes, because an archive has no portable way to
    keep an empty path segment: '/x' and '//x' (the key with an empty segment after the asset's folder,
    #1977) are both 'x'. Such a pair is refused, naming the two paths the caller selected, rather than
    renamed under a placeholder folder that could match a real one."""
    seen = {}
    entries = [(m["relativePath"], m["relativePath"].lstrip("/")) for m in members]
    entries += [(d, d.strip("/") + "/") for d in empty_dirs]
    for rel, name in entries:
        try:
            check_member_name(name)
        except ValueError:
            raise Refusal(f"{rel!r} cannot be a name in the archive") from None
        if name in seen:
            raise Refusal(f"{seen[name]!r} and {rel!r} would have the same name in the archive; "
                          "download them separately")
        seen[name] = rel


def check_members(get_client, members, blocked_extensions, blocked_content_types, empty_dirs=(),
                  workers=64):
    """Check every entry name, then head-check every member on its exact version and fill in size,
    version and mtime. The first refusal refuses the archive."""
    check_names(members, empty_dirs)

    def check(member):
        head = _head(get_client(member["bucket"]), member)
        if is_archived(head):
            raise Refusal(ARCHIVED_IN_SELECTION)
        if is_blocked(member["key"], head.get("ContentType", ""), blocked_extensions,
                      blocked_content_types):
            raise Refusal(f"{member['relativePath']} has a file type that cannot be downloaded")
        size = int(head.get("ContentLength", 0))
        if member.get("size") is not None and int(member["size"]) != size:
            raise Refusal(f"{member['relativePath']} changed while the archive was being prepared")
        member["size"] = size
        member["versionId"] = member.get("versionId") or head.get("VersionId") or None
        modified = head.get("LastModified")
        if hasattr(modified, "timestamp"):
            member["mtime"] = modified.timestamp()
        return member

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check, m) for m in members]
        try:
            for future in futures:
                future.result()
        finally:
            for future in futures:
                future.cancel()
    return members


def enforce_limits(members, max_files, max_total_bytes):
    if max_files and len(members) > int(max_files):
        raise Refusal(f"The selection has more than {int(max_files)} files")
    total = sum(int(m["size"]) for m in members)
    if max_total_bytes and total > int(max_total_bytes):
        raise Refusal(f"The selection is larger than {int(max_total_bytes)} bytes")
    if not members:
        raise Refusal("The selection has no files")
    return total
