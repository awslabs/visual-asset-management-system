# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Referenced files: fetch the files an input names (glTF and GLB buffers and images, USD sublayers,
references, payloads and asset-valued attributes, a ``.usdz`` package's external references, COLLADA
images and external instances, OBJ material libraries and their texture maps, FBX texture files, an
XGRIDS ``.lcc`` scene's data files and the chunk files of one ``.lcc2`` level of detail) from the
input's own asset, so the loader opens the input against them.

Every reference is untrusted input. The container's job role can read every asset bucket, so a
reference must not pull another asset's content into this run (ARCC BSC1, input validation by
allowlist). A reference is accepted only when it is a relative path that, normalised against the
referring file's directory, stays inside the input's asset root; absolute paths, ``..`` escapes,
URI schemes (``s3:``, ``http(s):``, ``file:``, ``anon:``, a Windows drive), control characters and
over-long values are rejected before any Amazon S3 call, and nothing is ever fetched over HTTP (ARCC
BSC12, SSRF). References into linked assets are not followed. A COLLADA document is read with an expat
parser that refuses DTDs and entities (ARCC BSC12, secure XML parsing).

The walk is breadth first from the input, with a visited set keyed on the normalised root-relative
path, so a file referenced twice (a diamond) is fetched once and a reference cycle ends the walk; the
cycles are reported. It runs in two phases. Discovery downloads only the documents that carry further
references (USD layers and packages, glTF, OBJ, MTL, FBX, COLLADA; a GLB's JSON chunk is read with a
ranged GET) and sizes every other file with HeadObject, pinning the version each HeadObject returned;
the fetch then downloads those files at those versions. Between the two, a caller can compare the
predicted bytes with what its disk can take and send the run elsewhere. Every bound stops the walk
rather than failing it (ARCC BSC8): a file count, a lookup count, a byte total checked before each
download and against free disk, a recursion depth, the references read from one file, and a deadline.
A reference that is rejected, missing or over a bound is reported and skipped; it never fails the run.

``resolve`` is the full walk and returns a ``DependencyReport``. ``fetch_referenced_files`` is the same
walk over the formats the 3D thumbnail reads, at current versions, returning the ``FetchReport`` the
thumbnail pipeline has always read.

This module is copied byte for byte into the SYSTEM GenAI metadata pipeline's Blender image as
``scene_dependencies.py`` (pinned by that pipeline's vendored-copies test), where it is imported as a
top-level module: it imports nothing from this package at module level there.
"""

import json
import logging
import os
import posixpath
import re
import shutil
import struct
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote
from xml.parsers import expat  # nosec B407 - hardened in _safe_expat

from boto3.exceptions import S3TransferFailedError
from botocore.exceptions import BotoCoreError, ClientError

if __package__:  # preview_pipeline.dependencies, in the 3D thumbnail and 3D render images
    from .utils import s3_utils as s3
    from .utils.logging import get_logger

    logger = get_logger()
else:  # the byte-identical top-level copy in the Blender image; its caller passes its own logger
    s3 = None
    logger = logging.getLogger()

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_TOTAL_BYTES = 50 * 1024 ** 3
DEFAULT_MAX_DEPTH = 32
# Free disk kept beyond a download, for the loader, the frames and the encoded output.
DEFAULT_DISK_HEADROOM_BYTES = 1024 ** 3
# The referenced bytes a Lambda render branch fetches: about 6 GiB of the function's 10 GiB /tmp, the
# rest held for the input (at most 2 GiB past the size gate), the renders and the loader.
LAMBDA_MAX_TOTAL_BYTES = 6 * 1024 ** 3
# S3 keys are at most 1,024 bytes; a longer reference cannot name an object.
MAX_REFERENCE_LENGTH = 1024
# References read from one file; any beyond are not followed (an FBX scan can list a great many).
MAX_REFERENCES_PER_FILE = 100_000
# A required reference missing from an input uploaded this recently may still be on its way (a
# multi-file upload lands file by file): discovery waits once and looks the missing ones up again.
LATE_ARRIVAL_WINDOW_SECONDS = 5 * 60
LATE_ARRIVAL_WAIT_SECONDS = 30
# Entries the report keeps; the counts cover the rest.
MAX_REPORTED_REJECTED = 200
MAX_REPORTED_CYCLES = 50
MAX_GRAPH_EDGES = 20_000
# The longest rejected reference quoted in the report.
_SHOWN_LENGTH = 200

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_USD_LAYER_EXTENSIONS = {".usd", ".usda", ".usdc"}
_USD_EXTENSIONS = _USD_LAYER_EXTENSIONS | {".usdz"}

# Why the walk stopped short; the first bound reached is the one reported.
TRUNCATED_FILES = "files"
TRUNCATED_LOOKUPS = "lookups"
TRUNCATED_BYTES = "bytes"
TRUNCATED_DISK = "disk"
TRUNCATED_DEPTH = "depth"
TRUNCATED_REFERENCES = "references"
TRUNCATED_DEADLINE = "deadline"

REJECTED_NOT_IN_ASSET = "not a relative path to a file inside the asset"


@dataclass
class FetchLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH
    disk_headroom_bytes: int = DEFAULT_DISK_HEADROOM_BYTES
    # Objects looked up (found or not); twice the file count when unset, so a file naming a great
    # many objects that do not exist still costs a bounded number of HeadObject calls.
    max_lookups: Optional[int] = None

    @property
    def lookup_cap(self) -> int:
        return self.max_lookups if self.max_lookups is not None else 2 * self.max_files


# Template configuration keys that may lower (never raise) a caller's limits.
_LIMIT_OVERRIDES = (("maxDependencyFiles", "max_files"), ("maxDependencyBytes", "max_total_bytes"),
                    ("maxDependencyDepth", "max_depth"))


def limits_from_config(base: FetchLimits, config) -> FetchLimits:
    """``base`` with each limit the configuration names lowered to it; a larger, negative or
    non-integer value is ignored, so a configuration can only narrow the walk."""
    limits = FetchLimits(base.max_files, base.max_total_bytes, base.max_depth, base.disk_headroom_bytes,
                         base.max_lookups)
    if not isinstance(config, dict):
        return limits
    for key, attribute in _LIMIT_OVERRIDES:
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            setattr(limits, attribute, min(number, getattr(limits, attribute)))
    return limits


@dataclass
class FetchReport:
    fetched: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    skipped_for_budget: List[str] = field(default_factory=list)
    total_bytes: int = 0


@dataclass
class ObjectInfo:
    """What HeadObject returned for one referenced object."""
    size: int
    version_id: Optional[str] = None
    last_modified: Optional[datetime] = None


# --------------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------------

def asset_root_key(input_key: str, asset_id: str = "", output_dir: str = "",
                   same_bucket_output: bool = False) -> str:
    """The S3 prefix (ending in ``/``, or empty) references are confined to.

    An original asset file sits under ``.../<assetId>/...``: the root is the key up to and including
    the asset id segment. A file an earlier workflow step wrote sits in this run's output files
    folder, keyed by its asset-relative path: the root is that folder. Otherwise the root is the
    input file's own directory, the narrowest confinement.
    """
    input_key = input_key or ""
    if same_bucket_output and output_dir and input_key.startswith(output_dir):
        return output_dir if output_dir.endswith("/") else output_dir + "/"
    parts = input_key.split("/")
    if asset_id and asset_id in parts[:-1]:
        index = parts.index(asset_id)
        return "/".join(parts[:index + 1]) + "/"
    return input_key.rsplit("/", 1)[0] + "/" if "/" in input_key else ""


def confinement_root(input_key: str, asset_id: str = "", relative_path: str = "",
                     asset_root_hint: str = "") -> str:
    """The asset root a pipeline input's references are confined to (ending in ``/``, or empty).

    In order: the execution manifest's ``assetRootS3Key`` when the caller has it and it is a proper
    prefix of the key; the key up to its asset id segment; the key with its asset-relative path (the
    manifest's ``relativePath``) removed from the end, which is the root of an asset created on an
    existing bucket folder, whose keys need not contain the asset id; else the input's own directory.
    A root is never the empty bucket-wide prefix unless the key has no directory at all.
    """
    input_key = input_key or ""
    own_directory = input_key.rsplit("/", 1)[0] + "/" if "/" in input_key else ""
    hint = asset_root_hint or ""
    if hint and not hint.endswith("/"):
        hint += "/"
    if hint and hint != "/" and input_key.startswith(hint) and len(input_key) > len(hint):
        return hint
    parts = input_key.split("/")
    if asset_id and asset_id in parts[:-1]:
        return asset_root_key(input_key, asset_id)
    relative = (relative_path or "").lstrip("/")
    if relative and resolve_reference(relative) == relative and input_key.endswith("/" + relative):
        return input_key[:len(input_key) - len(relative)]
    return own_directory


def relative_to_root(key: str, root: str) -> str:
    """``key`` relative to the confinement root (the input file's asset-relative path)."""
    if root and not key.startswith(root):
        raise ValueError(f"key {key!r} is not under {root!r}")
    return key[len(root):]


def local_path_for(local_root: str, relative_path: str) -> Optional[str]:
    """The local path mirroring ``relative_path`` under ``local_root``, or None if it would leave it."""
    base = os.path.realpath(local_root)
    target = os.path.realpath(os.path.join(base, relative_path))
    if target == base or not target.startswith(base + os.sep):
        return None
    return target


def resolve_reference(reference, referrer_dir: str = "", percent_encoded: bool = False) -> Optional[str]:
    """The root-relative path ``reference`` names, or None when it is not an accepted reference.

    ``referrer_dir`` is the referring file's directory relative to the confinement root. A
    percent-encoded reference (a glTF or COLLADA URI) is decoded first, so the check applies to the name
    that is downloaded rather than to its encoded spelling. Backslashes are read as separators (OBJ/MTL
    and FBX files written on Windows use them) before the path is checked.
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


def _shown(value) -> str:
    """A reference quoted in a report: control characters replaced, cut to a bounded length."""
    text = value if isinstance(value, str) else repr(value)
    text = "".join("?" if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in text)
    return text if len(text) <= _SHOWN_LENGTH else text[:_SHOWN_LENGTH] + "..."


# --------------------------------------------------------------------------------------------------
# Reference collectors: (local file) -> [(raw reference, percent_encoded[, required])]
#
# A required reference carries part of the scene itself (a glTF buffer, a USD sublayer, reference or
# payload, a COLLADA external instance): without it the scene is partial. Every other reference only
# adds appearance. A collector that returns pairs lists optional references only.
# --------------------------------------------------------------------------------------------------

def _gltf_document_references(document) -> List[tuple]:
    references = []
    if not isinstance(document, dict):
        return references
    for group, required in (("buffers", True), ("images", False)):
        for entry in document.get(group) or []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(uri, str) and uri and not uri.startswith("data:"):
                references.append((uri, True, required))
    return references


def _gltf_references(path: str) -> List[tuple]:
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    return _gltf_document_references(document)


_GLB_HEADER_BYTES = 20
# A JSON document (a GLB's JSON chunk, an LCC scene description) larger than this is not one read.
_JSON_SCAN_LIMIT = 64 * 1024 ** 2


def _glb_json_length(header: bytes) -> Optional[int]:
    """The length of a GLB's leading JSON chunk from its 20-byte header, or None when it is not one."""
    if len(header) < _GLB_HEADER_BYTES or header[:4] != b"glTF":
        return None
    length, kind = struct.unpack("<I4s", header[12:20])
    if kind != b"JSON" or length > _JSON_SCAN_LIMIT:
        return None
    return length


def _glb_chunk_references(chunk: bytes) -> List[tuple]:
    return _gltf_document_references(json.loads(chunk.decode("utf-8")))


def _glb_references(path: str) -> List[tuple]:
    """The ``buffers`` / ``images`` URIs of a GLB's JSON chunk: a GLB normally carries its data in its
    own binary chunk, but its JSON may still name external files, resolved as a ``.gltf``'s are."""
    with open(path, "rb") as handle:
        length = _glb_json_length(handle.read(_GLB_HEADER_BYTES))
        if length is None:
            return []
        return _glb_chunk_references(handle.read(length))


def _obj_references(path: str) -> List[tuple]:
    """``mtllib`` names: one or more file names after the keyword."""
    references = []
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.strip()
            if line[:6].lower() != b"mtllib" or (len(line) > 6 and not line[6:7].isspace()):
                continue
            names = line[6:].decode("utf-8", errors="replace").split()
            references.extend((name, False) for name in names)
    return references


# MTL texture statements and the number of values each option takes (the -o/-s/-t options take one
# to three numbers, handled separately).
_MTL_MAP_KEYWORDS = {
    "map_kd", "map_ka", "map_ks", "map_ns", "map_d", "map_bump", "bump", "disp", "decal", "refl",
    "norm", "map_pr", "map_pm", "map_ps", "map_ke", "map_rma", "map_orm",
}
_MTL_OPTION_ARGS = {
    "-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1,
    "-mm": 2, "-texres": 1, "-type": 1,
}
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _mtl_texture_name(tokens: List[str]) -> Optional[str]:
    index = 0
    while index < len(tokens):
        token = tokens[index].lower()
        if token in _MTL_OPTION_ARGS:
            index += 1 + _MTL_OPTION_ARGS[token]
        elif token in ("-o", "-s", "-t"):
            index += 1
            taken = 0
            while index < len(tokens) - 1 and taken < 3 and _NUMBER.match(tokens[index]):
                index += 1
                taken += 1
        else:
            break
    name = " ".join(tokens[index:])
    return name or None


def _mtl_references(path: str) -> List[tuple]:
    references = []
    with open(path, "rb") as handle:
        for raw in handle:
            tokens = raw.decode("utf-8", errors="replace").split()
            if len(tokens) >= 2 and tokens[0].lower() in _MTL_MAP_KEYWORDS:
                name = _mtl_texture_name(tokens[1:])
                if name:
                    references.append((name, False))
    return references


def _usd_references(path: str) -> List[tuple]:
    """Sublayers, references, payloads and asset-valued attribute paths authored in one layer.

    ``ExtractExternalReferences`` reads the layer's specs rather than a composed stage, so the
    references of every variant are listed, whichever variant is selected. A path to another USD
    layer or package is required; any other (a texture) is optional.
    """
    from pxr import UsdUtils

    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(path)
    found = []
    for value in list(sublayers) + list(references) + list(payloads):
        value = str(value)
        if "<UDIM>" in value.upper():
            logger.info(f"  Not fetching UDIM texture set: {value}")
            continue
        # A package-relative path names a file inside a .usdz: fetch the package itself.
        if "[" in value:
            value = value.split("[", 1)[0]
        required = os.path.splitext(value)[1].lower() in _USD_EXTENSIONS
        found.append((value, False, required))
    return found


def _usdz_references(path: str) -> List[tuple]:
    """The external references of a ``.usdz`` package's root layer.

    A path that names a member of the package (relative to the root layer, the first member) is
    resolved inside the package, so it is not fetched; any other is relative to the package's own
    directory in the asset, like a layer's.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    if not names:
        return []
    members = {posixpath.normpath(name) for name in names}
    root_dir = posixpath.dirname(names[0])
    external = []
    for reference in _usd_references(path):
        inside = posixpath.normpath(posixpath.join(root_dir, str(reference[0]).replace("\\", "/")))
        if inside not in members:
            external.append(reference)
    return external


_FBX_TEXTURE = re.compile(
    rb"[A-Za-z0-9_ .:/\\\-()]{1,512}\.(?:png|jpe?g|tga|bmp|tiff?|dds|exr|hdr|psd|gif)",
    re.IGNORECASE)
_FBX_SCAN_LIMIT = 512 * 1024 ** 2


def _fbx_references(path: str) -> List[tuple]:
    """Texture file names stored in an FBX file (binary or ASCII), by scanning for image names.

    FBX keeps a texture's absolute ``FileName`` from the authoring machine and its ``RelativeFilename``;
    both are offered and the resolver discards the absolute one. The scan reads at most the first
    512 MiB.
    """
    with open(path, "rb") as handle:
        data = handle.read(_FBX_SCAN_LIMIT)
    names = []
    for match in _FBX_TEXTURE.finditer(data):
        name = match.group(0).decode("utf-8", errors="replace").strip()
        if name not in names:
            names.append(name)
    references = [(name, False) for name in names]
    # An authoring machine's absolute path keeps the file name as its last segment, and a binary
    # string record's length bytes can read as text and prefix the match; the bare file name is
    # offered too, as a sibling of the FBX -- where an uploader most often puts the textures.
    for name in names:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if (base, False) not in references:
            references.append((base, False))
    return references


class UnsafeXmlError(ValueError):
    pass


def _forbid(kind):
    def handler(*_args):
        raise UnsafeXmlError(f"XML {kind} is not allowed in a 3D model file")
    return handler


def _safe_expat():
    """An expat parser that refuses DTDs, entity declarations and external entities and never reads
    parameter entities (the hardening of ``format_handlers/xml_mesh_handler.py``)."""
    parser = expat.ParserCreate()  # nosec B407 - handlers below refuse DTDs/entities
    parser.StartDoctypeDeclHandler = _forbid("document type declaration")
    parser.EntityDeclHandler = _forbid("entity declaration")
    parser.UnparsedEntityDeclHandler = _forbid("entity declaration")
    parser.ExternalEntityRefHandler = _forbid("external entity reference")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    return parser


# A COLLADA document is streamed through the parser up to this many bytes; the image library and the
# instance URLs come before the bulk of the geometry in the files authoring tools write.
_XML_SCAN_LIMIT = 512 * 1024 ** 2
_XML_CHUNK_BYTES = 4 * 1024 ** 2


def _dae_references(path: str) -> List[tuple]:
    """A COLLADA document's image files (``<image><init_from>``, 1.4 text or 1.5 ``<ref>``) and the
    documents its ``instance_*`` elements place from other files (a ``url`` that does not start with
    ``#``, up to its fragment). Both are URIs, so they are percent-decoded before they are checked; an
    external instance is required, an image is not."""
    references: List[tuple] = []
    state = {"image": 0, "init": 0, "text": []}

    def local(name):
        return name.rsplit(":", 1)[-1]

    def start(name, attributes):
        tag = local(name)
        if tag == "image":
            state["image"] += 1
        elif tag == "init_from" and state["image"]:
            state["init"] += 1
            state["text"] = []
        elif tag.startswith("instance_"):
            url = attributes.get("url")
            if isinstance(url, str) and url and not url.startswith("#"):
                target = url.split("#", 1)[0]
                if target:
                    references.append((target, True, True))

    def end(name):
        tag = local(name)
        if tag == "init_from" and state["init"]:
            state["init"] -= 1
            text = "".join(state["text"]).strip()
            if text and not text.startswith("#"):
                references.append((text, True, False))
        elif tag == "image" and state["image"]:
            state["image"] -= 1

    def data(text):
        # One name is at most MAX_REFERENCE_LENGTH characters; a longer one is rejected anyway.
        if state["init"] and sum(len(part) for part in state["text"]) <= MAX_REFERENCE_LENGTH:
            state["text"].append(text)

    parser = _safe_expat()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data
    read = 0
    with open(path, "rb") as handle:
        while read < _XML_SCAN_LIMIT:
            chunk = handle.read(min(_XML_CHUNK_BYTES, _XML_SCAN_LIMIT - read))
            if not chunk:
                parser.Parse(b"", True)
                break
            read += len(chunk)
            parser.Parse(chunk, False)
    return references


# The data files an XGRIDS LCC scene (``meta.lcc``) reads beside it; ``environment`` may name another.
_LCC_SIBLINGS = ("data.bin", "index.bin", "shcoef.bin", "environment.bin")


def _lcc_references(path: str) -> List[tuple]:
    """The data files beside an LCC scene description, plus the environment file its JSON names."""
    references = [(name, False) for name in _LCC_SIBLINGS]
    try:
        with open(path, "rb") as handle:
            meta = json.loads(handle.read(_JSON_SCAN_LIMIT).decode("utf-8", errors="replace"))
        environment = meta.get("environment") if isinstance(meta, dict) else None
        if isinstance(environment, str) and (environment, False) not in references:
            references.append((environment, False))
    except (OSError, ValueError):
        pass
    return references


def _lcc2_references(path: str, max_splats: Optional[int] = None) -> List[tuple]:
    """The chunk files of the one level of detail an ``.lcc2`` scene is read at (``lcc_meta``).

    The other levels and the environment chunk are not read for a thumbnail, so they are not fetched.
    """
    from .format_handlers import lcc_meta

    meta = lcc_meta.read_meta(path)
    _, names = lcc_meta.lcc2_level_files(meta, max_splats or lcc_meta.DEFAULT_MAX_SPLATS)
    return [(name, False) for name in names]


# The formats the 3D thumbnail reads references from (``fetch_referenced_files``).
_COLLECTORS: Dict[str, Callable[[str], List[tuple]]] = {
    ".gltf": _gltf_references,
    ".obj": _obj_references,
    ".mtl": _mtl_references,
    ".fbx": _fbx_references,
    ".lcc": _lcc_references,
    ".lcc2": _lcc2_references,
    **{ext: _usd_references for ext in _USD_LAYER_EXTENSIONS},
}
# Formats only the scene resolver (``resolve``) reads references from.
_SCENE_ONLY_COLLECTORS: Dict[str, Callable[[str], List[tuple]]] = {
    ".glb": _glb_references,
    ".dae": _dae_references,
    ".usdz": _usdz_references,
}
# Collectors whose references depend on the splat budget.
_BUDGETED_COLLECTORS = {".lcc2"}


def _scene_collectors() -> Dict[str, Callable]:
    return {**_COLLECTORS, **_SCENE_ONLY_COLLECTORS}


def has_references(ext: str) -> bool:
    """True when files of this extension can reference other files the thumbnail fetches."""
    return ext.lower() in _COLLECTORS


def has_scene_references(ext: str) -> bool:
    """True when files of this extension can reference other files ``resolve`` fetches."""
    return ext.lower() in _scene_collectors()


# --------------------------------------------------------------------------------------------------
# Object stores: HeadObject, a ranged GetObject and a download, for one bucket
# --------------------------------------------------------------------------------------------------

_STORE_ERRORS = (ClientError, BotoCoreError, S3TransferFailedError, OSError)


class ClientStore:
    """Amazon S3 reads of one bucket through a boto3 client (the client for the bucket's Region).

    A failed HeadObject (not found, denied, or a transport error after the client's retries) reads as
    a missing object, so one unreadable reference never fails the walk."""

    def __init__(self, client, bucket: str, transfer_config=None, log=None):
        self.client = client
        self.bucket = bucket
        self.transfer_config = transfer_config
        self.log = log or logger

    def head(self, key: str) -> Optional[ObjectInfo]:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except _STORE_ERRORS as e:
            code = e.response.get("Error", {}).get("Code", "") if isinstance(e, ClientError) else ""
            self.log.warning(f"HeadObject {self.bucket}/{key} failed: {code or type(e).__name__}")
            return None
        return ObjectInfo(int(response.get("ContentLength", 0) or 0), response.get("VersionId") or None,
                          response.get("LastModified"))

    def read_range(self, key: str, version_id: Optional[str], start: int, length: int) -> bytes:
        kwargs = {"Bucket": self.bucket, "Key": key, "Range": f"bytes={start}-{start + length - 1}"}
        if version_id:
            kwargs["VersionId"] = version_id
        body = self.client.get_object(**kwargs)["Body"]
        try:
            return body.read(length)
        finally:
            body.close()

    def download(self, key: str, version_id: Optional[str], path: str) -> bool:
        kwargs = {}
        if version_id:
            kwargs["ExtraArgs"] = {"VersionId": version_id}
        if self.transfer_config is not None:
            kwargs["Config"] = self.transfer_config
        try:
            self.client.download_file(self.bucket, key, path, **kwargs)
        except _STORE_ERRORS as e:
            self.log.warning(f"Download of {self.bucket}/{key} failed: {type(e).__name__}")
            return False
        return True


class _ThumbnailStore:
    """``fetch_referenced_files``' reads: the thumbnail image's S3 helpers, at current versions."""

    def __init__(self, bucket: str):
        self.bucket = bucket

    def head(self, key: str) -> Optional[ObjectInfo]:
        size = s3.head_object_size(self.bucket, key)
        return None if size is None else ObjectInfo(int(size))

    def read_range(self, key: str, version_id: Optional[str], start: int, length: int) -> bytes:
        return ClientStore(s3.client_for_bucket(self.bucket), self.bucket).read_range(key, version_id, start,
                                                                                      length)

    def download(self, key: str, version_id: Optional[str], path: str) -> bool:
        return s3.download(self.bucket, key, path) is not None


# --------------------------------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------------------------------

_PLANNED, _FETCHED, _MISSING, _SKIPPED = "planned", "fetched", "missing", "skipped"


@dataclass
class _Node:
    rel: str
    depth: int
    required: bool
    local_path: str
    info: Optional[ObjectInfo] = None
    state: str = _PLANNED


@dataclass
class DependencyReport:
    """What the walk found, in the shape the analysis manifest's ``dependencyReport`` carries."""
    input: str
    fetched: List[dict]
    missing: List[str]
    missing_required: List[str]
    rejected: List[dict]
    rejected_count: int
    cycles: List[List[str]]
    truncated: Optional[dict]
    total_bytes: int
    predicted_bytes: int
    max_depth: int
    graph: Dict[str, List[str]]
    partial: bool
    late_arrival_retry: bool = False

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "fetched": [dict(entry) for entry in self.fetched],
            "missing": list(self.missing),
            "missingRequired": list(self.missing_required),
            "rejected": [dict(entry) for entry in self.rejected],
            "rejectedCount": self.rejected_count,
            "cycles": [list(cycle) for cycle in self.cycles],
            "truncated": dict(self.truncated) if self.truncated else None,
            "totalBytes": self.total_bytes,
            "predictedBytes": self.predicted_bytes,
            "maxDepth": self.max_depth,
            "graph": {rel: list(children) for rel, children in self.graph.items()},
            "partial": self.partial,
            "lateArrivalRetry": self.late_arrival_retry,
        }

    def warnings(self, shown: int = 5) -> List[str]:
        """Short warnings for the manifest: counts and at most ``shown`` root-relative names, never a
        rejected reference's text (it comes from the file and is untrusted)."""
        lines = []
        if self.missing:
            names = ", ".join(name[:120] for name in self.missing[:shown])
            more = f" and {len(self.missing) - shown} more" if len(self.missing) > shown else ""
            lines.append(f"{len(self.missing)} referenced files are not in the asset: {names}{more}")
        if self.rejected_count:
            lines.append(f"{self.rejected_count} references were not followed: not a relative path to a "
                         f"file inside the asset")
        if self.cycles:
            lines.append(f"{len(self.cycles)} reference cycles were found and not followed again")
        if self.truncated:
            lines.append(f"the referenced-file walk stopped at its {self.truncated['reason']} limit "
                         f"(at {str(self.truncated.get('at') or '')[:120]})")
        return lines


class DependencyResolver:
    """The two-phase walk over one input's references (see the module docstring).

    ``store`` serves ``head``, ``read_range`` and ``download`` for the input's bucket (``ClientStore``).
    ``deadline`` is a ``clock()`` value after which nothing further is looked up or downloaded.
    ``collectors`` maps an extension to the function listing a file's references; ``child_collectors``
    those followed below the input (both default to every format ``resolve`` reads).
    """

    def __init__(self, store, root_key: str, local_root: str, limits: Optional[FetchLimits] = None, *,
                 deadline: Optional[float] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, now: Optional[Callable[[], datetime]] = None,
                 max_splats: Optional[int] = None, collectors: Optional[Dict[str, Callable]] = None,
                 child_collectors: Optional[Dict[str, Callable]] = None, late_arrival: bool = True,
                 log=None):
        self.store = store
        self.root_key = root_key or ""
        self.local_root = local_root
        self.limits = limits or FetchLimits()
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_splats = max_splats
        self.collectors = collectors if collectors is not None else _scene_collectors()
        self.child_collectors = child_collectors if child_collectors is not None else self.collectors
        self.late_arrival = late_arrival
        self.log = log or logger

        self.input_rel = ""
        self.nodes: Dict[str, _Node] = {}
        self.order: List[str] = []
        self.visited = set()
        self.graph: Dict[str, List[str]] = {}
        self.graph_edges = 0
        self.fetched: List[str] = []
        self.missing: List[str] = []
        self.missing_required: List[str] = []
        self.skipped: List[str] = []
        self.rejected: List[dict] = []
        self.rejected_raw: List[str] = []
        self.rejected_count = 0
        self.rejected_required = False
        self.truncated: Optional[dict] = None
        self.lookups = 0
        self.accepted = 0
        self.predicted_bytes = 0
        self.fetched_bytes = 0
        self.late_arrival_retry = False

    # -- bounds -----------------------------------------------------------------------------------

    def _expired(self) -> bool:
        return self.deadline is not None and self.clock() >= self.deadline

    def _truncate(self, reason: str, at: str):
        if self.truncated is None:
            self.truncated = {"reason": reason, "at": at}
            self.log.warning(f"  Referenced files: stopped at the {reason} limit (at {_shown(at)})")

    def _skip(self, node: _Node, reason: str):
        node.state = _SKIPPED
        self.skipped.append(node.rel)
        self._truncate(reason, node.rel)

    # -- discovery --------------------------------------------------------------------------------

    def discover(self, input_rel: str, local_input_path: str, ext: str):
        """Read the references of the input (already downloaded at ``input_rel`` under the local root)
        and, breadth first, of every document they name; size every other file."""
        self.input_rel = input_rel
        self.visited.add(input_rel)
        self.graph.setdefault(input_rel, [])
        queue = deque([(input_rel, local_input_path, ext.lower(), 0, None)])
        self._drain(queue)
        if self.late_arrival and self.missing_required and self._recently_uploaded():
            if self.deadline is None or self.deadline - self.clock() > 2 * LATE_ARRIVAL_WAIT_SECONDS:
                self.late_arrival_retry = True
                self.log.info(f"  {len(self.missing_required)} required referenced files are not in the "
                              f"asset yet; looking again in {LATE_ARRIVAL_WAIT_SECONDS} s")
                self.sleep(LATE_ARRIVAL_WAIT_SECONDS)
                retry = list(self.missing_required)
                self.missing = [rel for rel in self.missing if rel not in retry]
                self.missing_required = []
                for rel in retry:
                    self._lookup(self.nodes[rel], queue)
                self._drain(queue)

    def _recently_uploaded(self) -> bool:
        info = self.store.head(self.root_key + self.input_rel)
        modified = info.last_modified if info is not None else None
        if not isinstance(modified, datetime):
            return False
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        return (self.now() - modified).total_seconds() < LATE_ARRIVAL_WINDOW_SECONDS

    def _drain(self, queue: deque):
        while queue:
            rel, local_path, ext, depth, references = queue.popleft()
            if self._expired():
                self._truncate(TRUNCATED_DEADLINE, rel)
                return
            if references is None:
                collector = (self.collectors if rel == self.input_rel else self.child_collectors).get(ext)
                if collector is None:
                    continue
                try:
                    if ext in _BUDGETED_COLLECTORS:
                        references = collector(local_path, max_splats=self.max_splats)
                    else:
                        references = collector(local_path)
                except Exception as e:  # a file whose references cannot be read is read by the loader alone
                    self.log.warning(f"Could not read the references in {rel}: {type(e).__name__}: {e}")
                    continue
            referrer_dir = posixpath.dirname(rel)
            for index, reference in enumerate(references):
                if index >= MAX_REFERENCES_PER_FILE:
                    self._truncate(TRUNCATED_REFERENCES, rel)
                    break
                raw, percent_encoded = reference[0], bool(reference[1])
                required = bool(reference[2]) if len(reference) > 2 else False
                self._consider(raw, percent_encoded, required, rel, referrer_dir, depth + 1, queue)

    def _consider(self, raw, percent_encoded: bool, required: bool, referrer: str, referrer_dir: str,
                  depth: int, queue: deque):
        relative = resolve_reference(raw, referrer_dir, percent_encoded=percent_encoded)
        target = local_path_for(self.local_root, relative) if relative else None
        if relative is None or target is None:
            self.rejected_count += 1
            self.rejected_required = self.rejected_required or required
            if len(self.rejected) < MAX_REPORTED_REJECTED:
                self.rejected.append({"rel": _shown(raw), "reason": REJECTED_NOT_IN_ASSET, "referrer": referrer})
            if len(self.rejected_raw) < 1000:
                self.rejected_raw.append(raw if isinstance(raw, str) else repr(raw))
            self.log.warning(f"  Skipping reference in {referrer}: {REJECTED_NOT_IN_ASSET}: {_shown(raw)!r}")
            return
        edges = self.graph.setdefault(referrer, [])
        if relative not in edges and self.graph_edges < MAX_GRAPH_EDGES:
            edges.append(relative)
            self.graph_edges += 1
        if relative in self.visited:
            node = self.nodes.get(relative)
            if required and node is not None and not node.required:
                node.required = True
                if node.state == _MISSING:
                    self.missing_required.append(relative)
            return
        self.visited.add(relative)
        node = _Node(rel=relative, depth=depth, required=required, local_path=target)
        self.nodes[relative] = node
        self.order.append(relative)
        self._lookup(node, queue)

    def _lookup(self, node: _Node, queue: deque):
        if self._expired():
            self._skip(node, TRUNCATED_DEADLINE)
            return
        if self.lookups >= self.limits.lookup_cap:
            self._skip(node, TRUNCATED_LOOKUPS)
            return
        if self.accepted >= self.limits.max_files:
            self._skip(node, TRUNCATED_FILES)
            return
        self.lookups += 1
        info = self.store.head(self.root_key + node.rel)
        if info is None:
            node.state = _MISSING
            self.missing.append(node.rel)
            if node.required:
                self.missing_required.append(node.rel)
            self.log.warning(f"  Referenced file not found in the asset: {node.rel}")
            return
        node.info = info
        node.state = _PLANNED
        self.accepted += 1
        self.predicted_bytes += info.size
        ext = os.path.splitext(node.rel)[1].lower()
        if ext not in self.child_collectors:
            return
        if node.depth > self.limits.max_depth:
            self._truncate(TRUNCATED_DEPTH, node.rel)
            self.log.warning(f"  Not following references in {node.rel}: depth limit "
                             f"{self.limits.max_depth} reached")
            return
        if ext == ".glb":
            # Only the JSON chunk is read now; the whole file is fetched with the other files.
            references = self._glb_chunk(node)
            if references:
                queue.append((node.rel, None, ext, node.depth, references))
            return
        if self._download(node):
            queue.append((node.rel, node.local_path, ext, node.depth, None))

    def _glb_chunk(self, node: _Node) -> List[tuple]:
        key = self.root_key + node.rel
        version_id = node.info.version_id if node.info else None
        try:
            length = _glb_json_length(self.store.read_range(key, version_id, 0, _GLB_HEADER_BYTES))
            if length is None:
                return []
            return _glb_chunk_references(self.store.read_range(key, version_id, _GLB_HEADER_BYTES, length))
        except Exception as e:  # an unreadable chunk leaves the GLB to the loader
            self.log.warning(f"Could not read the references in {node.rel}: {type(e).__name__}: {e}")
            return []

    def _download(self, node: _Node) -> bool:
        size = node.info.size if node.info else 0
        if self.fetched_bytes + size > self.limits.max_total_bytes:
            self.log.warning(f"  Skipping {node.rel} ({size} bytes): referenced files would exceed "
                             f"{self.limits.max_total_bytes} bytes")
            self._skip(node, TRUNCATED_BYTES)
            return False
        free = shutil.disk_usage(self.local_root).free
        if size + self.limits.disk_headroom_bytes > free:
            self.log.warning(f"  Skipping {node.rel} ({size} bytes): {free} bytes of disk free")
            self._skip(node, TRUNCATED_DISK)
            return False
        os.makedirs(os.path.dirname(node.local_path), exist_ok=True)
        version_id = node.info.version_id if node.info else None
        if not self.store.download(self.root_key + node.rel, version_id, node.local_path):
            node.state = _MISSING
            self.missing.append(node.rel)
            if node.required:
                self.missing_required.append(node.rel)
            return False
        node.state = _FETCHED
        self.fetched.append(node.rel)
        self.fetched_bytes += size
        return True

    # -- fetch ------------------------------------------------------------------------------------

    def fetch(self):
        """Download every file discovery sized and did not download, at the version it pinned, in
        discovery order, until a bound is reached."""
        for rel in self.order:
            node = self.nodes[rel]
            if node.state != _PLANNED:
                continue
            if self._expired():
                self._skip(node, TRUNCATED_DEADLINE)
                continue
            self._download(node)
        self.log.info(f"Referenced files: {len(self.fetched)} fetched ({self.fetched_bytes} bytes), "
                      f"{self.rejected_count} rejected, {len(self.missing)} missing, "
                      f"{len(self.skipped)} skipped for a limit")

    # -- results ----------------------------------------------------------------------------------

    def cycles(self, limit: int = MAX_REPORTED_CYCLES) -> List[List[str]]:
        """Each reference back to a file on the current path of a depth-first walk of the reference
        graph, as that path (``[a, b, a]``). A file reached twice along different paths (a diamond) is
        not a cycle."""
        found: List[List[str]] = []
        if not self.input_rel:
            return found
        done = set()
        path = [self.input_rel]
        on_path = {self.input_rel}
        stack = [iter(self.graph.get(self.input_rel, []))]
        while stack:
            advanced = False
            for child in stack[-1]:
                if child in on_path:
                    found.append(path[path.index(child):] + [child])
                    if len(found) >= limit:
                        return found
                elif child not in done:
                    path.append(child)
                    on_path.add(child)
                    stack.append(iter(self.graph.get(child, [])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                finished = path.pop()
                on_path.discard(finished)
                done.add(finished)
        return found

    def report(self) -> DependencyReport:
        fetched = []
        for rel in self.fetched:
            node = self.nodes[rel]
            fetched.append({"rel": rel, "versionId": node.info.version_id if node.info else None,
                            "bytes": node.info.size if node.info else 0, "depth": node.depth})
        skipped_required = any(self.nodes[rel].required for rel in self.skipped)
        return DependencyReport(
            input=self.input_rel,
            fetched=fetched,
            missing=list(self.missing),
            missing_required=list(self.missing_required),
            rejected=list(self.rejected),
            rejected_count=self.rejected_count,
            cycles=self.cycles(),
            truncated=dict(self.truncated) if self.truncated else None,
            total_bytes=self.fetched_bytes,
            predicted_bytes=self.predicted_bytes,
            max_depth=max((entry["depth"] for entry in fetched), default=0),
            graph={rel: list(children) for rel, children in self.graph.items()},
            partial=bool(self.missing_required) or self.rejected_required or skipped_required,
            late_arrival_retry=self.late_arrival_retry,
        )

    def thumbnail_report(self) -> FetchReport:
        return FetchReport(fetched=list(self.fetched), rejected=list(self.rejected_raw),
                           missing=list(self.missing), skipped_for_budget=list(self.skipped),
                           total_bytes=self.fetched_bytes)


# --------------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------------

def resolve(store, root_key: str, input_key: str, local_input_path: str, local_root: str, ext: str,
            limits: Optional[FetchLimits] = None, *, deadline: Optional[float] = None,
            max_splats: Optional[int] = None, log=None, late_arrival: bool = True,
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
            now: Optional[Callable[[], datetime]] = None) -> DependencyReport:
    """Discover and fetch every accepted file the input references, transitively, into ``local_root``.

    ``local_input_path`` is the input already downloaded at its root-relative path under
    ``local_root``; each fetched file lands at its own root-relative path beside it, so relative
    references resolve locally exactly as they do in the asset. ``store`` reads the input's bucket
    (``ClientStore``)."""
    resolver = DependencyResolver(store, root_key, local_root, limits, deadline=deadline, clock=clock,
                                  sleep=sleep, now=now, max_splats=max_splats, late_arrival=late_arrival,
                                  log=log)
    resolver.discover(relative_to_root(input_key, root_key), local_input_path, ext)
    resolver.fetch()
    return resolver.report()


def fetch_referenced_files(bucket: str, input_key: str, local_input_path: str, local_root: str,
                           root_key: str, ext: str, limits: Optional[FetchLimits] = None,
                           max_splats: Optional[int] = None) -> FetchReport:
    """Fetch every accepted file the input references, transitively, into ``local_root``: the walk of
    ``resolve`` over the formats the 3D thumbnail reads (a child ``.gltf`` is fetched but not read),
    at current versions and without the late-arrival retry.

    ``local_input_path`` is the input already downloaded at its root-relative path under
    ``local_root``; each fetched file lands at its own root-relative path beside it, so relative
    references resolve locally exactly as they do in the asset. ``max_splats`` is the splat budget an
    ``.lcc2`` scene's level of detail is chosen for.
    """
    if not has_references(ext):
        return FetchReport()
    collectors = dict(_COLLECTORS)
    children = {ext_: collector for ext_, collector in collectors.items() if ext_ != ".gltf"}
    resolver = DependencyResolver(_ThumbnailStore(bucket), root_key, local_root, limits or FetchLimits(),
                                  max_splats=max_splats, collectors=collectors, child_collectors=children,
                                  late_arrival=False)
    resolver.discover(relative_to_root(input_key, root_key), local_input_path, ext)
    resolver.fetch()
    return resolver.thumbnail_report()
