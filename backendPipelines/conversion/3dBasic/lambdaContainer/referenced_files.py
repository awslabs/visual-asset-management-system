# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Referenced files: fetch the files a model input names (glTF and GLB buffers and images, an OBJ's
material library and the ``map_Kd`` textures it names, a PLY ``TextureFile``) from the input's own asset
before the model is loaded, and give the loader those files and nothing else.

Every reference is untrusted input. This function's role can read every asset bucket, so a reference
must not pull another asset's content into the converted model (ARCC BSC1, input validation by
allowlist). A reference is accepted only when it is a relative path that, normalised against the
referring file's directory, stays inside the input's asset prefix; absolute paths, ``..`` escapes,
URI schemes (``s3:``, ``http(s):``, ``file:``, a Windows drive), control characters and over-long
values are rejected. An inline ``data:`` URI is left to the loader and never becomes an Amazon S3
request. The fetch is bounded by a file count (every name looked up counts, found or not), a byte
total (each object's size is read with HeadObject before it is downloaded, and checked against free
disk) and a recursion depth: only an OBJ's material library is read for further references, so the
chain is at most OBJ -> MTL -> texture.

What happens to a reference that is rejected, missing or over a limit depends on what it carries. A
glTF or GLB buffer holds the geometry, so without it the model cannot be loaded: the conversion FAILS
with a message naming it. A material library or a texture image only adds appearance: it is skipped
with a warning naming it, and the model converts without it.

The loader reads the fetched files through ``ConfinedResolver``, which serves the files fetched here
from the local copy of the asset and refuses any other name, so a skipped reference is not read from
anywhere.

The path rules (``resolve_reference``, ``local_path_for``) and the glTF, OBJ and MTL collectors are
vendored from the 3D Preview Thumbnail container's ``preview_pipeline/dependencies.py`` (PR #474);
keep the two in step. The thumbnail skips every unusable reference; this copy fails on a buffer.
"""

import json
import os
import posixpath
import re
import shutil
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import unquote

from botocore.exceptions import ClientError

DEFAULT_MAX_FILES = 1000
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 ** 3
# Levels of referencing followed below the input: the input names an MTL (1), the MTL names a
# texture (2). Nothing this pipeline loads needs more.
DEFAULT_MAX_DEPTH = 2
# Free disk kept beyond a download for the export, which is staged on the same /tmp.
DEFAULT_DISK_HEADROOM_BYTES = 512 * 1024 ** 2
# S3 keys are at most 1,024 bytes; a longer reference cannot name an object.
MAX_REFERENCE_LENGTH = 1024
# Skipped references logged one by one; any beyond this are counted in one closing warning.
MAX_SKIPPED_LOGGED = 50
# A GLB's JSON chunk, or a PLY header, larger than this is not one this pipeline reads references from.
_HEADER_SCAN_LIMIT = 64 * 1024 ** 2
# The longest reference quoted back in an error message.
_SHOWN_LENGTH = 200

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


class ReferencedFileError(ValueError):
    """A buffer the input model references cannot be used: rejected, missing, or over a limit."""


class Reference(NamedTuple):
    """A file name as the referring file spells it."""
    raw: object
    # A glTF URI, which is percent-decoded before it is checked.
    percent_encoded: bool
    # A glTF/GLB buffer: the model cannot be loaded without it, so a reference that cannot be used
    # fails the conversion. Material libraries and texture images are optional and are skipped.
    required: bool


@dataclass
class FetchLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH
    disk_headroom_bytes: int = DEFAULT_DISK_HEADROOM_BYTES


@dataclass
class FetchReport:
    fetched: List[str] = field(default_factory=list)
    total_bytes: int = 0
    # Each accepted reference, spelled as the referring file spells it, mapped to the local file it
    # names. The loader asks for a reference by that spelling (a glTF URI still percent-encoded, an
    # MTL texture name relative to the MTL rather than to the OBJ), so this is what the resolver
    # looks it up by.
    by_reference: Dict[str, str] = field(default_factory=dict)
    # The warning for each optional reference that was skipped (the first MAX_SKIPPED_LOGGED), and
    # how many were skipped in all.
    skipped: List[str] = field(default_factory=list)
    skipped_count: int = 0


# --------------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------------

def resolve_reference(reference, referrer_dir: str = "", percent_encoded: bool = False) -> Optional[str]:
    """The root-relative path ``reference`` names, or None when it is not an accepted reference.

    ``referrer_dir`` is the referring file's directory relative to the confinement root. A
    percent-encoded reference (a glTF URI) is decoded first, so the check applies to the name that is
    downloaded rather than to its encoded spelling. Backslashes are read as separators (OBJ/MTL files
    written on Windows use them) before the path is checked.
    """
    if not isinstance(reference, str):
        return None
    value = unquote(reference) if percent_encoded else reference
    value = value.strip()
    if not value or len(value) > MAX_REFERENCE_LENGTH:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return None
    if value.startswith("`") or _SCHEME.match(value):
        return None
    value = value.replace("\\", "/")
    if value.startswith("/"):
        return None
    joined = posixpath.normpath(posixpath.join(referrer_dir or "", value))
    if joined in (".", "..") or joined.startswith(("/", "../")):
        return None
    return joined


def local_path_for(local_root: str, relative_path: str) -> Optional[str]:
    """The local path mirroring ``relative_path`` under ``local_root``, or None if it would leave it."""
    base = os.path.realpath(local_root)
    target = os.path.realpath(os.path.join(base, relative_path))
    if target == base or not target.startswith(base + os.sep):
        return None
    return target


def confinement_root(input_key: str, relative_subdir: str = "") -> Tuple[str, str]:
    """``(root_key, input_relative_path)``: the S3 prefix references are confined to, and the input's
    path below it.

    The root is the input's asset prefix: the input key with its asset-relative path (the manifest's
    ``relativePath``, which ``relative_subdir`` and the file name rebuild) removed from the end. That
    holds for an original asset file, for an asset created on an existing bucket folder, and for a
    file an earlier workflow step wrote, which is keyed by its asset-relative path under the run's
    output files folder. When the key does not end with that path -- a direct invocation with no
    manifest, for one -- the root is the input file's own directory, the narrowest confinement.
    """
    input_key = input_key or ""
    name = posixpath.basename(input_key)
    own_directory = input_key[:len(input_key) - len(name)]
    subdir = (relative_subdir or "").strip("/")
    if subdir:
        suffix = f"{subdir}/{name}"
        if (input_key == suffix or input_key.endswith("/" + suffix)) and resolve_reference(suffix) == suffix:
            return input_key[:len(input_key) - len(suffix)], suffix
    return own_directory, name


def _shown(value) -> str:
    """A reference quoted for an error message: escaped, and cut to a bounded length."""
    text = repr(value)
    return text if len(text) <= _SHOWN_LENGTH else text[:_SHOWN_LENGTH] + "..."


# --------------------------------------------------------------------------------------------------
# Reference collectors: (local file) -> [Reference]
# --------------------------------------------------------------------------------------------------

def _gltf_document_references(document) -> List[Reference]:
    references = []
    if not isinstance(document, dict):
        return references
    for group, required in (("buffers", True), ("images", False)):
        for entry in document.get(group) or []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                references.append(Reference(uri, True, required))
    return references


def _gltf_references(path: str) -> List[Reference]:
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    return _gltf_document_references(document)


def _glb_references(path: str) -> List[Reference]:
    """The same ``buffers`` / ``images`` URIs, read from a GLB's JSON chunk. A GLB normally carries
    its data in its own binary chunk, but its JSON may still name external files, and the loader
    resolves those exactly as it does for a ``.gltf``."""
    with open(path, "rb") as handle:
        header = handle.read(20)
        if len(header) < 20 or header[:4] != b"glTF":
            return []
        chunk_length, chunk_type = struct.unpack("<I4s", header[12:20])
        if chunk_type != b"JSON" or chunk_length > _HEADER_SCAN_LIMIT:
            return []
        document = json.loads(handle.read(chunk_length).decode("utf-8"))
    return _gltf_document_references(document)


# The OBJ and MTL collectors return exactly the names trimesh's OBJ loader asks its resolver for
# (trimesh.exchange.obj ``load_obj`` and ``parse_mtl``, trimesh 4.8.3), so the fetch requests, and its
# warnings name, only files the loader reads. Both read the text as the loader does: UTF-8, whole.
# trimesh decodes other encodings only with charset_normalizer, which the image does not install, so
# it reads no name from a file that is not UTF-8; here such a file raises, and nothing is fetched
# for it.
# ``tests/test_3dbasic_real_conversions.py`` compares these names with the ones the installed trimesh
# asks for.

def _obj_references(path: str) -> List[Reference]:
    """The OBJ's material library: the rest of the line after the first ``mtllib`` in the text, as
    one name (spaces included), after the loader's line-ending and line-continuation handling. The
    loader reads one material library per OBJ and finds ``mtllib`` anywhere in the text."""
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8")
    text = "\n{}\n".format(text.strip().replace("\r\n", "\n")).replace("\\\n", "")
    position = text.find("mtllib")
    if position < 0:
        return []
    name = text[position + 6:text.find("\n", position)].strip()
    return [Reference(name, False, False)] if name else []


def _mtl_references(path: str) -> List[Reference]:
    """Texture names: for each ``map_Kd`` statement, the rest of the line after the keyword, as one
    name. The loader reads no other texture statement, and reads options such as ``-s`` as part of
    the name."""
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8")
    references = []
    for line in str.splitlines(text.strip()):
        tokens = line.strip().split()
        if len(tokens) <= 1 or tokens[0].lower() != "map_kd":
            continue
        name = line[line.lower().index("map_kd") + 6:].strip()
        if name:
            references.append(Reference(name, False, False))
    return references


def _ply_references(path: str) -> List[Reference]:
    """The texture a PLY header names (``comment TextureFile fuze_uv.jpg``), read the way trimesh's
    PLY loader reads it: the rest of any header line after ``TextureFile``."""
    references = []
    with open(path, "rb") as handle:
        if handle.readline(16).strip().lower() != b"ply":
            return []
        scanned = 0
        for raw in handle:
            scanned += len(raw)
            if scanned > _HEADER_SCAN_LIMIT:
                break
            line = raw.decode("utf-8", errors="replace")
            if line.strip().lower() == "end_header":
                break
            lowered = line.lower()
            if "texturefile" in lowered:
                name = line[lowered.index("texturefile") + len("texturefile"):].strip()
                if name:
                    references.append(Reference(name, False, False))
    return references


_COLLECTORS: Dict[str, Callable[[str], List[Reference]]] = {
    ".gltf": _gltf_references,
    ".glb": _glb_references,
    ".obj": _obj_references,
    ".mtl": _mtl_references,
    ".ply": _ply_references,
}

# How a fetched file is read for further references, by the extension of the file that referenced
# it: an OBJ names only its material library, which the loader reads as an MTL whatever its name. A
# buffer or image a glTF names is data, never a document, and a fetched model is not loaded.
_FOLLOWED = {".obj": ".mtl"}


def has_references(ext: str) -> bool:
    """True when files of this extension can reference other files the container fetches."""
    return ext.lower() in _COLLECTORS


# --------------------------------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------------------------------

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound", "403", "AccessDenied", "Forbidden"}


def _object_size(client, bucket: str, key: str):
    """``(size, version_id)`` of an object, or None when it does not exist or cannot be read."""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        status = str(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        if code in _NOT_FOUND_CODES or status in ("403", "404"):
            return None
        raise
    return int(response.get("ContentLength", 0)), response.get("VersionId")


def fetch_referenced_files(client, bucket: str, root_key: str, input_relative_path: str,
                           local_input_path: str, local_root: str,
                           limits: Optional[FetchLimits] = None, logger=None) -> FetchReport:
    """Fetch every file the input references, transitively, into ``local_root``.

    ``local_input_path`` is the input already downloaded at ``input_relative_path`` under
    ``local_root``; each fetched file lands at its own root-relative path beside it, so relative
    references resolve locally exactly as they do in the asset. ``client`` is the Amazon S3 client
    for ``bucket``'s Region. A buffer reference that is rejected, names no object, or would pass a
    limit raises ``ReferencedFileError``; any other such reference is skipped, with a warning on
    ``logger`` and in ``FetchReport.skipped``.
    """
    limits = limits or FetchLimits()
    report = FetchReport()
    ext = os.path.splitext(input_relative_path)[1].lower()
    if not has_references(ext):
        return report

    # Root-relative path -> local file, for every file the loader may be served; and root-relative
    # path -> the failure message, for every name already looked up and not fetched.
    available = {input_relative_path: local_input_path}
    unavailable: Dict[str, str] = {}
    looked_up = 0
    queue = deque([(local_input_path, input_relative_path, ext, 0)])

    def refuse(reference: Reference, rel_path: str, name: str, failure: str, reason: str):
        """Fail on a required reference; skip an optional one with a warning naming it."""
        if reference.required:
            raise ReferencedFileError(failure)
        report.skipped_count += 1
        if report.skipped_count <= MAX_SKIPPED_LOGGED:
            warning = (f"Skipped {name}, referenced by {rel_path}: {reason}. The model is converted "
                       f"without it.")
            report.skipped.append(warning)
            if logger is not None:
                logger.warning(warning)

    while queue:
        local_path, rel_path, file_ext, depth = queue.popleft()
        try:
            references = _COLLECTORS[file_ext](local_path)
        except Exception as e:
            # A file whose references cannot be read is not one the loader can read either, so the
            # load reports it in its own terms. Nothing is fetched for it, and the resolver serves
            # only fetched files, so an unread reference cannot reach anything.
            if logger is not None:
                logger.warning(f"Could not read the references in {rel_path}: {e}")
            continue
        too_deep = depth + 1 > limits.max_depth
        referrer_dir = posixpath.dirname(rel_path)
        for reference in references:
            raw = reference.raw
            if too_deep:
                refuse(reference, rel_path, _shown(raw),
                       f"{rel_path} references further files {depth + 1} levels below the input; "
                       f"this pipeline follows at most {limits.max_depth}.",
                       f"it is {depth + 1} levels below the input, and this pipeline follows at most "
                       f"{limits.max_depth}")
                continue
            relative = resolve_reference(raw, referrer_dir, percent_encoded=reference.percent_encoded)
            target = local_path_for(local_root, relative) if relative else None
            if relative is None or target is None:
                refuse(reference, rel_path, _shown(raw),
                       f"{rel_path} references {_shown(raw)}, which is not a relative path to a file "
                       f"in the same asset. Only files inside the input's own asset are fetched: "
                       f"change the reference to a path relative to {rel_path}, or embed the data, "
                       f"and run the conversion again.",
                       "it is not a relative path to a file in the same asset")
                continue
            if relative in available:
                report.by_reference.setdefault(raw.strip(), available[relative])
                continue
            if relative in unavailable:
                # Already looked up and not fetched, and already reported once if it was skipped.
                if reference.required:
                    raise ReferencedFileError(unavailable[relative])
                continue
            if looked_up >= limits.max_files:
                failure = (f"The input references more than {limits.max_files} files; this "
                           f"pipeline fetches at most {limits.max_files}.")
                unavailable[relative] = failure
                refuse(reference, rel_path, relative, failure,
                       f"the input references more than {limits.max_files} files, and this "
                       f"pipeline fetches at most {limits.max_files}")
                continue
            looked_up += 1
            key = root_key + relative
            measured = _object_size(client, bucket, key)
            if measured is None:
                failure = (f"{rel_path} references {relative}, which is not in the asset. Upload it "
                           f"to the asset at /{relative} and run the conversion again.")
                unavailable[relative] = failure
                refuse(reference, rel_path, relative, failure,
                       f"it is not in the asset (upload it at /{relative} and run the conversion "
                       f"again to include it)")
                continue
            size, version_id = measured
            if report.total_bytes + size > limits.max_total_bytes:
                failure = (f"The files the input references exceed {limits.max_total_bytes} bytes "
                           f"(reached at {relative}, {size} bytes).")
                unavailable[relative] = failure
                refuse(reference, rel_path, relative, failure,
                       f"its {size} bytes take the referenced files past "
                       f"{limits.max_total_bytes} bytes")
                continue
            free = shutil.disk_usage(local_root).free
            if size + limits.disk_headroom_bytes > free:
                failure = (f"{relative} ({size} bytes) does not fit in the {free} bytes of working "
                           f"disk left.")
                unavailable[relative] = failure
                refuse(reference, rel_path, relative, failure,
                       f"its {size} bytes exceed the {free} bytes of working disk left")
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            extra = {"VersionId": version_id} if version_id else None
            with open(target, "wb") as handle:
                client.download_fileobj(Bucket=bucket, Key=key, Fileobj=handle, ExtraArgs=extra)
            report.fetched.append(relative)
            report.total_bytes += size
            available[relative] = target
            report.by_reference.setdefault(raw.strip(), target)
            child_kind = _FOLLOWED.get(file_ext)
            if child_kind is not None:
                queue.append((target, relative, child_kind, depth + 1))

    if logger is not None:
        if report.skipped_count > MAX_SKIPPED_LOGGED:
            logger.warning(f"Skipped {report.skipped_count - MAX_SKIPPED_LOGGED} further referenced "
                           f"files.")
        logger.info(f"Referenced files: {len(report.fetched)} fetched ({report.total_bytes} bytes), "
                    f"{report.skipped_count} skipped")
    return report


# --------------------------------------------------------------------------------------------------
# The loader's view of the asset
# --------------------------------------------------------------------------------------------------

class ConfinedResolver:
    """The resolver the model loader is given in place of its default file-path resolver.

    It serves the files ``fetch_referenced_files`` fetched -- looked up by the spelling the referring
    file uses, then by the name resolved against the input's directory -- and raises
    ``FileNotFoundError`` for every other name, so the loader reads nothing outside this conversion's
    local copy of the asset.
    """

    def __init__(self, local_root: str, input_relative_path: str, by_reference: Dict[str, str]):
        self._root = os.path.realpath(local_root)
        self._referrer_dir = posixpath.dirname(input_relative_path)
        self._by_reference = dict(by_reference or {})
        self._served = set(self._by_reference.values())
        # trimesh reads these from a file-path resolver: `parent` to record where a texture came
        # from, `file_name` to name the geometry after the file it was loaded from.
        self.parent = os.path.join(self._root, *[p for p in self._referrer_dir.split("/") if p])
        self.file_name = posixpath.basename(input_relative_path)

    def _path(self, name) -> Optional[str]:
        if not isinstance(name, str):
            return None
        path = self._by_reference.get(name.strip())
        if path is None:
            relative = resolve_reference(name, self._referrer_dir)
            candidate = local_path_for(self._root, relative) if relative else None
            path = candidate if candidate in self._served else None
        return path if path is not None and os.path.isfile(path) else None

    def get(self, name):
        path = self._path(name)
        if path is None:
            raise FileNotFoundError(f"{_shown(name)} is not a file fetched for this conversion")
        with open(path, "rb") as handle:
            return handle.read()

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name) -> bool:
        return self._path(name) is not None

    def keys(self):
        return iter(sorted(self._by_reference))
