#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The views of the subject an Image to 3D run reads, carried as one list of S3 objects.

Each view is ``{"bucket": ..., "key": ..., "versionId": ...}``, the field names of the workflow manifest's
``inputFiles`` entry it is built from. A view's object and the version it is read at therefore travel as
one value: they cannot fall out of step the way a list of paths and a parallel list of versions can, and
the key is never formatted into a URI and parsed back between hops. ``vamsExecute`` builds the list
(``views_from_manifest``) and sends it as ``inputViews``; ``openPipeline`` gates it and
``constructPipeline`` writes it onto the container definition's ``inputFiles``, both through
``views_from_event``, so the two hops always agree on which list a run reads.
"""

import manifestHelper

INPUT_VIEWS_FIELD = "inputViews"
# The path list that a development build before the view list carries, alone or beside a parallel
# `inputVersionIds` this module does not read.
LEGACY_INPUT_PATHS_FIELD = "inputS3AssetFilePaths"


class MalformedInputView(ValueError):
    """A view that names no readable S3 object. The run fails rather than skipping the view, padding its
    version or reading something else in its place."""


def _is_text(value):
    return isinstance(value, str) and value != ""


def view_uri(view):
    """The ``s3://bucket/key`` location of one view, for messages and the legacy path list."""
    return f"s3://{view['bucket']}/{view['key']}"


def views_from_manifest(input_files):
    """Every manifest input file as a view, in manifest order, built in one pass: the object and its launch
    version come from the same entry. An entry without a bucket or a key raises rather than being dropped,
    since a reconstruction from fewer views than were selected looks like a right answer."""
    views = []
    for index, entry in enumerate(input_files):
        entry = entry if isinstance(entry, dict) else {}
        if not (_is_text(entry.get("bucket")) and _is_text(entry.get("key"))):
            raise MalformedInputView(
                f"Input file {index + 1} of {len(input_files)} in the workflow manifest has no bucket or no "
                f"key, so it cannot be read as a view.")
        views.append({
            "bucket": entry["bucket"],
            "key": entry["key"],
            "versionId": manifestHelper.input_version_id(entry),
        })
    return views


def _checked_view(view, index, count):
    """One ``inputViews`` entry, checked: a bucket and a key, and its version as a string ("" reads the
    current object). A missing or non-string version is malformed too, because reading the current object in
    its place is the silent substitution the version exists to prevent."""
    where = f"Input view {index + 1} of {count}"
    if not isinstance(view, dict):
        raise MalformedInputView(f"{where} is not an object with a bucket, a key and a versionId.")
    if not _is_text(view.get("bucket")):
        raise MalformedInputView(f"{where} names no bucket.")
    if not _is_text(view.get("key")):
        raise MalformedInputView(f"{where} names no key.")
    if not isinstance(view.get("versionId"), str):
        raise MalformedInputView(f"{where} carries no versionId string; use an empty one for the current object.")
    return {"bucket": view["bucket"], "key": view["key"], "versionId": view["versionId"]}


def views_from_event(event):
    """The views a run reads, in order, each as ``{"bucket", "key", "versionId"}``.

    - ``inputViews``, when present and not empty, is the only source; every entry is checked and a
      malformed one raises ``MalformedInputView``. ``inputS3AssetFilePaths`` is then ignored.
    - Otherwise ``inputS3AssetFilePaths``, the list a development build before the view list carries. This
      is the path a run takes while the three functions run different builds, during a stack update or a
      rollback: every path that equals the primary ``inputS3AssetFilePath`` is read at ``inputVersionId``,
      and every other path at the current object. A file selected at two versions is listed twice with one
      version on both copies, so the container reads it once. A path that is not ``s3://bucket/key`` raises.
    - Otherwise the primary ``inputS3AssetFilePath`` at ``inputVersionId`` (a run without a manifest), or no
      view when the primary does not parse.
    """
    event = event or {}
    views = event.get(INPUT_VIEWS_FIELD)
    if views:
        if not isinstance(views, list):
            raise MalformedInputView(
                f"{INPUT_VIEWS_FIELD} must be a list of views, each with a bucket, a key and a versionId.")
        return [_checked_view(view, index, len(views)) for index, view in enumerate(views)]

    legacy_paths = event.get(LEGACY_INPUT_PATHS_FIELD) or []
    if legacy_paths:
        primary_uri = event.get("inputS3AssetFilePath") or ""
        primary_version = event.get("inputVersionId") or ""
        legacy_views = []
        for index, uri in enumerate(legacy_paths):
            bucket, key = manifestHelper.parse_s3_uri(uri if isinstance(uri, str) else "")
            if not (bucket and key):
                raise MalformedInputView(
                    f"Input view {index + 1} of {len(legacy_paths)} is not an s3://bucket/key location.")
            version_id = primary_version if primary_uri and uri == primary_uri else ""
            legacy_views.append({"bucket": bucket, "key": key, "versionId": version_id})
        return legacy_views

    bucket, key = manifestHelper.parse_s3_uri(event.get("inputS3AssetFilePath") or "")
    if not (bucket and key):
        return []
    return [{"bucket": bucket, "key": key, "versionId": event.get("inputVersionId") or ""}]
