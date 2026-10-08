# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Normalization + placement of an execution's output base path extension.

The extension is the prefix an execution's output files are written under, relative to the output
asset. It is inserted immediately BEFORE the final filename rather than at the start of the path, so
each pipeline's own output folder structure survives and the extension names the leaf folder:

    /path1/path2/file.txt  +  /YOLO/  ->  /path1/path2/YOLO/file.txt
    /path1/path2/file.txt  +  YOLO    ->  /path1/path2/YOLOfile.txt

The trailing slash is therefore MEANINGFUL — with one the extension is a folder, without one it is
glued onto the filename — so normalization preserves it and only canonicalizes the leading slash and
duplicate separators. `"/"`, `""` and `None` all mean "no extension".

This module is pure (no boto3, no os.environ) so the execute handler, the end-state output lambda and
the interim tracking lambda place the extension identically.
"""

# Canonical "no extension" value: outputs land directly at the output asset's relative path.
NO_EXTENSION = "/"


def normalize_output_path_extension(raw):
    """Canonicalize an authored extension to a single leading `/` plus its authored trailing `/`.

    Returns `NO_EXTENSION` for None/empty/`"/"`. Any `{{tag}}` placeholders pass through untouched —
    they are substituted once the launch has a manifest, and the result is normalized again. Traversal
    and backslashes are rejected by the request model, not here.
    """
    if raw is None:
        return NO_EXTENSION
    text = str(raw).strip()
    if text in ("", "/"):
        return NO_EXTENSION
    is_folder = text.endswith("/")
    segments = [segment for segment in text.split("/") if segment]
    if not segments:
        return NO_EXTENSION
    body = "/".join(segments)
    return f"/{body}/" if is_folder else f"/{body}"


def apply_output_path_extension(relative_path, extension):
    """Insert `extension` into `relative_path` immediately before the final filename.

    `relative_path` is asset-relative (a leading `/` is tolerated and dropped, matching the
    bucket-relative keys the output lambdas build). Returns the path unchanged when there is no
    extension, and returns an empty/only-slash path unchanged (there is no filename to place before).
    """
    relative = (relative_path or "").lstrip("/")
    normalized = normalize_output_path_extension(extension)
    if not relative or normalized == NO_EXTENSION:
        return relative
    insert = normalized.lstrip("/")
    head, separator, filename = relative.rpartition("/")
    return f"{head}{separator}{insert}{filename}"
