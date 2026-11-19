# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Vendored from backendPipelines/preview/3dThumbnail/container/preview_pipeline/dependencies.py (the USD,
# COLLADA and FBX reference collectors and the result-document name sanitiser): a container's build
# context holds only its own directory, so the copy cannot be imported across pipelines.
# tests/test_3dbasic_reference_collectors.py keeps every definition below identical to its canonical one.

"""The reference collectors of the formats whose references need a format library or an XML parse:
USD layers and packages (usd-core), COLLADA documents (the hardened expat parser) and FBX files (a scan
for image names). Each returns ``(value, percent_encoded, required, kind)`` tuples.

They run only in the worker child (``conversion_worker.scan_references``), never in the function
process that holds the AWS credentials: a USD crate file and an XML document are parsed by native code,
and the parent keeps only the Amazon S3 traffic.
"""

import os
import posixpath
import re
import zipfile
from typing import List, Optional
from urllib.parse import unquote
from xml.parsers import expat  # nosec B407 - hardened in _safe_expat

from common.logger import safeLogger

logger = safeLogger(service="conversionTrimeshPipeline")

# S3 keys are at most 1,024 bytes; a longer reference cannot name an object.
MAX_REFERENCE_LENGTH = 1024

KIND_GEOMETRY_LAYER = "geometryLayer"


KIND_DATA_FILE = "dataFile"


KIND_TEXTURE_OR_OTHER = "textureOrOther"


KIND_METADATA = "metadata"


KIND_RENDERER_MDL = "rendererMdl"


KIND_UNSAMPLED_TEXTURE = "unsampledTexture"


_USD_LAYER_EXTENSIONS = {".usd", ".usda", ".usdc"}


_USD_EXTENSIONS = _USD_LAYER_EXTENSIONS | {".usdz"}


_GEOMETRY_LAYER_EXTENSIONS = _USD_LAYER_EXTENSIONS | {".usdz"}


def is_geometry_layer(reference: str) -> bool:
    """True when ``reference`` names a USD layer file by its extension. Used only for a reference whose
    collector named no kind; a USD layer's own references are classified by their composition arc."""
    if not isinstance(reference, str):
        return False
    path = reference.strip().replace("\\", "/").split("[", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return posixpath.splitext(path.rstrip("/"))[1].lower() in _GEOMETRY_LAYER_EXTENSIONS


def _asset_values(value) -> List[str]:
    """The asset paths an attribute value holds (an ``Sdf.AssetPath`` or an array of them)."""
    from pxr import Sdf

    if isinstance(value, Sdf.AssetPath):
        return [value.path]
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        return []
    try:
        return [item.path for item in value if isinstance(item, Sdf.AssetPath)]
    except TypeError:
        return []


def _prim_info(spec, key):
    """One metadata field of a prim spec, or None when it is absent or this usd-core cannot read it."""
    try:
        return spec.GetInfo(key) if spec.HasInfo(key) else None
    except Exception as e:
        logger.debug(f"  Could not read {key!r} on {spec.path} ({type(e).__name__}); skipping it")
        return None


def _usd_reference_kinds(path: str) -> Optional[dict]:
    """The authored asset paths of one layer that decide a reference's kind, as authored, or None when
    the layer cannot be read for it:

    - ``composition``: its composition arcs (sublayers, references, payloads, variants included);
    - ``attributes``: the values of its ``asset`` and ``asset[]`` attributes (defaults and time
      samples). Only attributes of those types are read, so a layer's geometry and animation samples
      are never loaded for this;
    - ``clips``: its value-clip asset paths (``clips`` ``assetPaths`` and ``manifestAssetPath``).

    Each metadata field is read on its own, so a field this usd-core cannot read affects only that
    field. The pre-``clips`` keys (``clipAssetPaths``, ``clipManifestAssetPath``) are not read: usd-core
    reports them present but refuses to return them, and ``ExtractExternalReferences`` does not list
    their paths, so they are neither fetched nor reported.
    """
    from pxr import Sdf

    try:
        layer = Sdf.Layer.FindOrOpen(path)
        if layer is None:
            return None
        asset_types = (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray)
        found = {"composition": {str(value) for value in layer.GetCompositionAssetDependencies()},
                 "attributes": set(), "clips": set()}

        def visit(spec_path):
            spec = layer.GetObjectAtPath(spec_path)
            if isinstance(spec, Sdf.AttributeSpec):
                if spec.typeName not in asset_types:
                    return
                if spec.HasDefaultValue():
                    found["attributes"].update(_asset_values(spec.default))
                for time in layer.ListTimeSamplesForPath(spec_path):
                    found["attributes"].update(_asset_values(layer.QueryTimeSample(spec_path, time)))
            elif isinstance(spec, Sdf.PrimSpec):
                clips = _prim_info(spec, "clips") or {}
                for clip_set in (clips.values() if hasattr(clips, "values") else []):
                    if hasattr(clip_set, "get"):
                        found["clips"].update(_asset_values(clip_set.get("assetPaths")))
                        found["clips"].update(_asset_values(clip_set.get("manifestAssetPath")))

        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
        return found
    except Exception as e:
        logger.info(f"  Could not classify the references in {os.path.basename(path)} "
                    f"({type(e).__name__}); classifying them by extension")
        return None


def _usd_reference_kind(value: str, kinds: Optional[dict]) -> str:
    """The kind of one reference a USD layer names. A composition arc to a USD layer is a geometry
    layer, and to any other file (a ``.mtlx`` material, say) a texture or other file; a value clip is a
    data file (a missing clip drops animated geometry); an ``asset``-typed attribute (a texture) is a
    texture or other file; and any other asset path is layer or prim metadata (an ``assetInfo``
    identifier, ``customLayerData``, ``customData``), which loads nothing and is not reported."""
    if kinds is None:
        return ""
    if value in kinds["composition"]:
        return KIND_GEOMETRY_LAYER if is_geometry_layer(value) else KIND_TEXTURE_OR_OTHER
    if value in kinds["clips"]:
        return KIND_DATA_FILE
    if value in kinds["attributes"]:
        return KIND_TEXTURE_OR_OTHER
    return KIND_METADATA


def _usd_references(path: str) -> List[tuple]:
    """Sublayers, references, payloads and the other asset paths authored in one layer, each with
    whether it is required and its kind (``_usd_reference_kind``).

    ``ExtractExternalReferences`` reads the layer's specs rather than a composed stage, so the
    references of every variant are listed, whichever variant is selected. A path to another USD
    layer or package is required; any other (a texture) is optional. Which references are fetched
    does not depend on the kind.
    """
    from pxr import UsdUtils

    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(path)
    kinds = _usd_reference_kinds(path)
    found = []
    for arc, values in (("sublayer", sublayers), ("reference", references), ("payload", payloads)):
        for value in values:
            value = str(value)
            if arc != "reference":
                kind = KIND_GEOMETRY_LAYER if is_geometry_layer(value) else KIND_TEXTURE_OR_OTHER
            else:
                kind = _usd_reference_kind(value, kinds)
            if "<UDIM>" in value.upper():
                logger.info(f"  Not fetching UDIM texture set: {value}")
                continue
            # A package-relative path names a file inside a .usdz: fetch the package itself.
            if "[" in value:
                value = value.split("[", 1)[0]
            # A composition arc or value clip to a USD layer carries geometry; a USD path in layer or
            # prim metadata loads nothing, so its absence leaves the scene whole.
            required = (kind in (KIND_GEOMETRY_LAYER, KIND_DATA_FILE) if kind
                        else os.path.splitext(value)[1].lower() in _USD_EXTENSIONS)
            found.append((value, False, required, kind))
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

    The FBX loader draws vertex and material colours and does not sample textures, so no texture can
    change the preview: each name is offered as ``KIND_UNSAMPLED_TEXTURE`` and a texture that is
    missing, or refused (the absolute path beside a fetched relative one), is not reported.
    """
    with open(path, "rb") as handle:
        data = handle.read(_FBX_SCAN_LIMIT)
    names = []
    for match in _FBX_TEXTURE.finditer(data):
        name = match.group(0).decode("utf-8", errors="replace").strip()
        if name not in names:
            names.append(name)
    references = [(name, False, False, KIND_UNSAMPLED_TEXTURE) for name in names]
    # An authoring machine's absolute path keeps the file name as its last segment, and a binary
    # string record's length bytes can read as text and prefix the match; the bare file name is
    # offered too, as a sibling of the FBX -- where an uploader most often puts the textures.
    for name in names:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        if (base, False, False, KIND_UNSAMPLED_TEXTURE) not in references:
            references.append((base, False, False, KIND_UNSAMPLED_TEXTURE))
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
                    references.append((target, True, True, KIND_GEOMETRY_LAYER))

    def end(name):
        tag = local(name)
        if tag == "init_from" and state["init"]:
            state["init"] -= 1
            text = "".join(state["text"]).strip()
            if text and not text.startswith("#"):
                references.append((text, True, False, KIND_TEXTURE_OR_OTHER))
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


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


_SAFE_RESULT_EXTENSIONS = {
    ".usd", ".usda", ".usdc", ".usdz", ".gltf", ".glb", ".bin", ".obj", ".mtl", ".fbx", ".mdl",
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".dds", ".exr", ".hdr", ".psd", ".gif",
    ".ktx", ".ktx2", ".webp", ".lcc", ".lcc2", ".spz", ".sog", ".ply", ".splat", ".ksplat", ".vdb",
    ".abc",
}


_SAFE_BASE_NAME = re.compile(r"^[A-Za-z0-9 _.()+\-]{1,128}$")


def safe_base_name(reference) -> str:
    """The base file name of an as-authored reference with every location part removed, or "" when no
    safe file name is left. Percent-encoding is decoded first; then the URI scheme and authority
    (``s3://bucket/``, ``http://host:port/``, ``omniverse://host/``), a Windows drive or UNC server,
    the query, the fragment and any package path are dropped, and what remains must be a plain file
    name of a known file type."""
    if not isinstance(reference, str):
        return ""
    value = reference.strip()
    for _ in range(3):  # a doubly encoded value decodes in two passes
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    value = value.replace("\\", "/").split("?", 1)[0].split("#", 1)[0].split("[", 1)[0]
    scheme = _SCHEME.match(value)
    if scheme:
        value = value[scheme.end():]
    if value.startswith("//"):
        authority_and_path = value[2:]
        value = authority_and_path.split("/", 1)[1] if "/" in authority_and_path else ""
    if not value or value.endswith("/"):
        return ""
    base = value.rsplit("/", 1)[-1]
    if not _SAFE_BASE_NAME.match(base) or base.startswith("."):
        return ""
    if posixpath.splitext(base)[1].lower() not in _SAFE_RESULT_EXTENSIONS:
        return ""
    return base
