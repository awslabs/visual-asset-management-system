#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""USD root selection and multi-file packaging helpers for the Miris upload container.

`pxr` (from usd-core) is imported lazily inside the wrapper functions so the pure helpers below
import without usd-core installed, which keeps their unit tests light.
"""
import os
import posixpath
import re
from typing import Callable, Iterable, List, Set, Tuple

USDZ_EXTENSION = ".usdz"
# Layer formats whose dependencies the root scan reads. A .usdz is self-contained and not opened.
LAYER_EXTENSIONS = (".usd", ".usda", ".usdc")


class UnreadableLayerError(Exception):
    """A USD layer pxr cannot read: empty, malformed, or not USD at all."""

    def __init__(self, path: str, error: Exception):
        super().__init__(f"{path}: {error}")
        self.path = path
        self.error = error


def local_download_plan(keys: Iterable[str], root_prefix: str) -> List[Tuple[str, str]]:
    """Map S3 keys under the asset root to (key, relative_path) download pairs.

    The relative path is normalized ('./' and 'a/../' segments resolved), so it names the file the
    way a resolved USD reference does. Skips folder markers (keys ending in '/'), keys outside the
    root, and any key whose relative path would resolve outside the download directory. The
    relative path preserves subdirectory structure (e.g. 'textures/wood.jpg') so USD relative
    references resolve on local disk.
    """
    plan: List[Tuple[str, str]] = []
    for key in keys:
        if key.endswith("/") or not key.startswith(root_prefix):
            continue
        rel = key[len(root_prefix):]
        normalized = posixpath.normpath(rel) if rel else ""
        if normalized in ("", ".") or normalized.startswith("/") or normalized.split("/")[0] == "..":
            continue
        plan.append((key, normalized))
    return plan


def root_candidates(relative_paths: Iterable[str], extensions: Iterable[str]) -> List[str]:
    """The distinct relative paths whose extension (case-insensitive) is one of `extensions`, sorted."""
    allowed = {ext.lower() for ext in extensions}
    return sorted({p for p in relative_paths if os.path.splitext(p)[1].lower() in allowed})


def _is_layer(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in LAYER_EXTENSIONS


def scan_references(starts: Iterable[str], references_of: Callable[[str], Set[str]]) -> Set[str]:
    """Every file referenced by a layer reachable from `starts`, following each layer's direct
    references once. A layer's reference to itself is ignored."""
    referenced: Set[str] = set()
    seen: Set[str] = set()
    queue = list(starts)
    while queue:
        layer = queue.pop()
        if layer in seen:
            continue
        seen.add(layer)
        if not _is_layer(layer):
            continue
        for path in references_of(layer):
            if path == layer:
                continue
            referenced.add(path)
            if path not in seen:
                queue.append(path)
    return referenced


def choose_roots(candidates: List[str], references_of: Callable[[str], Set[str]]) -> List[str]:
    """The candidates no layer reachable from the candidates references, in candidate order.

    `references_of(layer)` returns the relative paths the layer names directly; each layer is read
    once, so the scan is linear in the number of layers. A .usdz is self-contained, so it is never
    asked for its references; a single candidate is its own root without a scan.
    """
    if len(candidates) <= 1:
        return list(candidates)
    referenced = scan_references(candidates, references_of)
    return [candidate for candidate in candidates if candidate not in referenced]


def requested_root(value) -> str:
    """An asset-relative path as a user writes it ('/scenes/a.usda', './a.usda') in the form the
    candidates use ('scenes/a.usda'); '' for a blank value or one that leaves the asset."""
    if not isinstance(value, str) or not value.strip():
        return ""
    normalized = posixpath.normpath(value.strip().lstrip("/"))
    if normalized in ("", ".") or normalized.split("/")[0] == "..":
        return ""
    return normalized


def check_geometry(root_path: str, base_dir: str = "") -> Tuple[bool, List[str], List[str]]:
    """(has geometry, unopenable files, unset stage metadata) for the root.

    It has geometry when its composed stage holds a defined, active, non-abstract geometry prim: a
    UsdGeom.Gprim (meshes, points, curves, primitive shapes such as Cube or Sphere, volumes) or a
    UsdGeom.PointInstancer. Geometry under instanceable prims counts. The stage is opened with no
    payloads loaded, and its payloads are loaded only when it holds no such prim.

    Without geometry, and given `base_dir`, the unopenable files are the asset-relative paths,
    sorted, of files inside `base_dir` that the payload-loaded stage names (as a reference, payload
    or sublayer) but pxr could not open: a malformed layer, or a format it cannot read (Alembic,
    OBJ). compute_dependencies finds them without composing the stage; this covers a root it does
    not read (a .usdz). A missing file is not listed; the dependency check reports it as unresolved.
    The unset stage metadata is 'upAxis' and 'metersPerUnit' when the root layer does not author
    them. Raises UnreadableLayerError when pxr cannot open the stage."""
    from pxr import Sdf, Usd, UsdGeom

    predicate = Usd.TraverseInstanceProxies(Usd.PrimIsActive & Usd.PrimIsDefined & ~Usd.PrimIsAbstract)
    try:
        stage = Usd.Stage.Open(root_path, Usd.Stage.LoadNone)
    except Exception as e:
        raise UnreadableLayerError(root_path, e) from e
    if stage is None:
        raise UnreadableLayerError(root_path, ValueError("pxr could not open the stage"))
    unset = [key for key in STAGE_METADATA_KEYS if not stage.HasAuthoredMetadata(key)]

    def has_geometry():
        return any(prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer)
                   for prim in stage.Traverse(predicate))

    if has_geometry():
        return True, [], unset
    # The same stage, its payloads loaded.
    stage.Load(Sdf.Path.absoluteRootPath, Usd.LoadWithDescendants)
    if has_geometry():
        return True, [], unset
    if not base_dir:
        return False, [], unset
    base = os.path.realpath(base_dir)
    found: Set[str] = set()
    for error in stage.GetCompositionErrors():
        # The first @path@ of the message names the file; a sublayer message's second names its parent.
        match = _UNOPENED_FILE.search(str(error))
        if match:
            real = os.path.realpath(_package_path(match.group(1)))
            if real.startswith(base + os.sep) and os.path.isfile(real):
                found.add(os.path.relpath(real, base).replace(os.sep, "/"))
    return False, sorted(found), unset


# Root layer metadata that sets the orientation and the scale the asset streams at.
STAGE_METADATA_KEYS = ("upAxis", "metersPerUnit")
_UNOPENED_FILE = re.compile(r"Could not (?:open asset|load sublayer) @([^@]+)@")


def defines_geometry(root_path: str) -> bool:
    """Whether the root defines geometry (see check_geometry)."""
    return check_geometry(root_path)[0]


def _package_path(identifier: str) -> str:
    """The file a layer identifier names: a file inside a package ('a.usdz[b.usdc]') names the package."""
    if identifier.endswith("]") and "[" in identifier:
        return identifier[: identifier.index("[")]
    return identifier


def direct_references(layer_path: str, base_dir: str) -> Set[str]:
    """Relative paths (from `base_dir`) of the files the layer names directly: sublayers,
    references, payloads, value clips and asset-valued attributes.

    Empty for a file that is not on disk, an unresolved reference the chosen root's dependency
    check reports. Raises UnreadableLayerError when pxr cannot read the layer.
    """
    if not os.path.isfile(layer_path):
        return set()
    from pxr import UsdUtils

    try:
        sublayers, references, payloads = UsdUtils.ExtractExternalReferences(layer_path)
    except Exception as e:
        raise UnreadableLayerError(layer_path, e) from e
    base = os.path.realpath(base_dir)
    layer_dir = os.path.dirname(os.path.realpath(layer_path))
    found: Set[str] = set()
    for authored in list(sublayers) + list(references) + list(payloads):
        path = _package_path(authored)
        if not path:
            continue
        absolute = os.path.realpath(path if os.path.isabs(path) else os.path.join(layer_dir, path))
        if absolute.startswith(base + os.sep):
            found.add(os.path.relpath(absolute, base).replace(os.sep, "/"))
    return found


def paths_outside(paths: Iterable[str], base_dir: str) -> List[str]:
    """The real paths, sorted, of the files among `paths` that are not inside `base_dir`. A file
    inside a package ('a.usdz[b.usdc]') is judged by the package."""
    base = os.path.realpath(base_dir)
    outside: Set[str] = set()
    for path in paths:
        real = os.path.realpath(_package_path(path))
        if not real.startswith(base + os.sep):
            outside.add(real)
    return sorted(outside)


def dependency_tree_bytes(paths: Iterable[str]) -> int:
    """The bytes of the distinct files among `paths` (layer identifiers and asset paths), the
    files the packaged upload holds. A file inside a package ('a.usdz[b.usdc]') counts as the
    package; a path that is not a file on disk counts nothing."""
    files = {os.path.realpath(_package_path(path)) for path in paths}
    return sum(os.path.getsize(path) for path in files if os.path.isfile(path))


def tree_files(paths: Iterable[str], base_dir: str) -> list:
    """Sorted asset-relative paths of the distinct files on disk among `paths` (layer identifiers
    and asset paths), a file inside a package ('a.usdz[b.usdc]') counting as the package."""
    base = os.path.realpath(base_dir)
    files = {os.path.realpath(_package_path(path)) for path in paths}
    return sorted(os.path.relpath(path, base).replace(os.sep, "/")
                  for path in files if os.path.isfile(path))


def should_skip_packaging(num_layers: int, num_assets: int) -> bool:
    """Return True when the root USD has no external dependencies.

    `ComputeAllDependencies` returns the root layer in `layers`, so a
    dependency-free asset has exactly one layer and zero referenced assets.
    In that case we upload the original file unchanged.
    """
    return num_layers <= 1 and num_assets == 0


def compute_dependencies(root_path: str):
    """Resolve the full dependency set of a root USD file.

    Returns (layer_identifiers, asset_paths, unresolved_paths, unopenable_paths). A non-empty
    unresolved_paths means a reference (commonly an absolute texture path)
    could not be located on disk; the caller should fail clean rather than
    ship an incomplete package to Miris. unopenable_paths are the real paths, sorted, of files on
    disk that a layer names as a sublayer, reference or payload but pxr could not open as a layer:
    a malformed layer, or a format it cannot read (Alembic, OBJ). They are read from each layer's
    composition arcs, so no stage is composed and no payload is loaded. Raises UnreadableLayerError
    when pxr cannot read the root or one of its layers.
    """
    from pxr import UsdUtils

    try:
        layers, assets, unresolved = UsdUtils.ComputeAllDependencies(root_path)
    except Exception as e:
        raise UnreadableLayerError(root_path, e) from e
    if not layers:
        raise UnreadableLayerError(root_path, ValueError("pxr read no layers from the file"))
    # Every layer an arc names that pxr could open is among `layers`; a file on disk that is not
    # was named but not opened.
    opened = {os.path.realpath(_package_path(layer.identifier)) for layer in layers}
    unopenable: Set[str] = set()
    for layer in layers:
        for authored in layer.GetCompositionAssetDependencies():
            target = os.path.realpath(_package_path(layer.ComputeAbsolutePath(authored)))
            if target not in opened and os.path.isfile(target):
                unopenable.add(target)
    return (
        [layer.identifier for layer in layers],
        list(assets),
        list(unresolved),
        sorted(unopenable),
    )


def package_usdz(root_path: str, out_path: str) -> None:
    """Bundle the root USD and all resolved dependencies into a .usdz package."""
    from pxr import Sdf, UsdUtils

    ok = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(root_path), out_path)
    if not ok:
        raise RuntimeError(f"usdz packaging failed for {root_path}")
