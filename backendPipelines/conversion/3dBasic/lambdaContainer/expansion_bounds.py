# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The size a COLLADA or USD scene expands to, counted from the file before a library expands it.

A scene format whose elements refer to one another places the same child once per reference, so the
size of the scene a library builds can differ greatly from the size of the file. ``fbx_scene.scene_size``
counts an FBX scene before Assimp reads it; this module does the same for the two formats whose loader
expands the scene itself:

* ``collada_scene_size`` counts the nodes, vertices and triangles ``trimesh`` places for a COLLADA
  document (``instance_node`` and nested ``node`` elements, and one node for each primitive element of
  each geometry placed), in one pass over the document through the hardened parser (``xml_guard``) and
  a memoised count over the node graph.
* ``usd_composed_prims`` counts the prims ``Usd.Stage.Open`` composes, from the layers' own prim specs
  and composition arcs (references, payloads, inherits, specializes, sublayers, variants), without
  composing a stage.

Both counts are never lower than what the loader builds where the document is ambiguous, and both
refuse a scene that refers to itself. The work of each grows with the size of the file, not with the
number of placements it describes.
"""

import re
from typing import NamedTuple

import xml_guard

# Scene nodes a conversion counts and walks at most on a function of ``REFERENCE_MEMORY_BYTES``, the
# same bound as ``scene_limits.MAX_SCENE_NODES``. ``max_scene_nodes`` scales it with the memory of the
# task, as the input size limits scale (``size_check``); the walk's time is bounded by its deadline.
CONVERSION_MAX_SCENE_NODES = 1_000_000
REFERENCE_MEMORY_BYTES = 10_240 * 1024 ** 2


def max_scene_nodes(memory_bytes=None):
    """The node bound for a task with ``memory_bytes`` of memory: ``CONVERSION_MAX_SCENE_NODES`` for a
    task of ``REFERENCE_MEMORY_BYTES`` or less (or one whose memory is not known), in proportion to the
    memory above that."""
    if not memory_bytes or memory_bytes <= REFERENCE_MEMORY_BYTES:
        return CONVERSION_MAX_SCENE_NODES
    return int(CONVERSION_MAX_SCENE_NODES * memory_bytes // REFERENCE_MEMORY_BYTES)


# Counts above this are reported as this.
_CAP = 1 << 62
# The most prim bundles the USD count visits before it reports the scene as over the bound: every
# bundle it visits stands for at least one prim of the composed stage.
_MAX_USD_VISITS_FACTOR = 5
# The most layers one layer stack (a layer and the sublayers it names) may hold.
_MAX_LAYER_STACK = 1000


class ExpansionCycle(ValueError):
    """A scene that places itself inside itself."""


# --------------------------------------------------------------------------------------------------
# COLLADA
# --------------------------------------------------------------------------------------------------

class SceneSize(NamedTuple):
    """What a scene places (``nodes``, ``vertices``, ``triangles``) and what its geometry holds counted
    once each (``stored_vertices``, ``stored_triangles``)."""
    nodes: int
    vertices: int
    triangles: int
    stored_vertices: int
    stored_triangles: int


_PRIMITIVES = ("triangles", "polylist", "polygons", "trifans", "tristrips", "lines", "linestrips")
_LINES = ("lines", "linestrips")
_LEAF_INSTANCES = ("instance_controller", "instance_camera", "instance_light")
_TOKEN = re.compile(r"\S+")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _target(url):
    """The id a ``#id`` url names; None for a url that names another document."""
    return url[1:] if url and url.startswith("#") else None


class _ColladaScan:
    """An ``ElementTree.TreeBuilder``-shaped target for ``xml_guard._safe_expat`` that keeps only the
    node graph and the size of each geometry."""

    def __init__(self):
        self.children = {}        # node key -> [(kind, reference)]
        self.geometries = {}      # geometry id -> [corners, triangles, primitive elements]
        self.scene_urls = []
        self.visual_scenes = []
        self._stack = []          # (local tag, node key or None)
        self._anonymous = 0
        self._geometry = None
        self._primitive = None    # [tag, polygons, stride, tokens]
        self._in_index = False
        self._open_token = False

    def start(self, tag, attributes):
        name = _local(tag)
        parent_name, parent_key = self._stack[-1] if self._stack else (None, None)
        key = None
        if name == "node" and parent_name in ("library_nodes", "visual_scene", "node"):
            ident = attributes.get("id")
            if ident:
                key = ("n", ident)
            else:
                self._anonymous += 1
                key = ("a", self._anonymous)
            self.children.setdefault(key, [])
            if parent_key is not None:
                self.children[parent_key].append(("node", key))
        elif name == "visual_scene" and parent_name == "library_visual_scenes":
            key = ("vs", attributes.get("id") or "")
            self.children.setdefault(key, [])
            self.visual_scenes.append(key)
        elif name == "instance_node" and parent_key is not None:
            target = _target(attributes.get("url"))
            if target is not None:
                self.children[parent_key].append(("node", ("n", target)))
        elif name == "instance_geometry" and parent_key is not None:
            target = _target(attributes.get("url"))
            self.children[parent_key].append(("geometry", target))
        elif name in _LEAF_INSTANCES and parent_key is not None:
            self.children[parent_key].append(("leaf", None))
        elif name == "instance_visual_scene" and parent_name == "scene":
            target = _target(attributes.get("url"))
            if target is not None:
                self.scene_urls.append(("vs", target))
        elif name == "geometry" and parent_name == "library_geometries":
            self._geometry = attributes.get("id") or ""
            self.geometries.setdefault(self._geometry, [0, 0, 0])
        elif self._geometry is not None and name in _PRIMITIVES:
            polygons = 0
            if name == "polylist":
                try:
                    polygons = max(int(attributes.get("count", "0")), 0)
                except ValueError:
                    polygons = 0
            self._primitive = [name, polygons, 1, 0]
        elif self._primitive is not None and name == "input":
            try:
                offset = int(attributes.get("offset", "0"))
            except ValueError:
                offset = 0
            self._primitive[2] = max(self._primitive[2], offset + 1)
        elif self._primitive is not None and name == "p":
            self._in_index = True
            self._open_token = False
            if self._primitive[0] != "polylist":
                self._primitive[1] += 1
        self._stack.append((name, key))

    def end(self, tag):
        name = _local(tag)
        if self._stack:
            self._stack.pop()
        if name == "p":
            self._in_index = False
        elif self._primitive is not None and name == self._primitive[0]:
            kind, polygons, stride, tokens = self._primitive
            corners = tokens // max(stride, 1)
            if kind in _LINES:
                triangles = 0
            else:
                triangles = corners // 3 if kind == "triangles" else max(corners - 2 * polygons, 0)
            total = self.geometries[self._geometry]
            total[0] += corners
            total[1] += triangles
            total[2] += 1
            self._primitive = None
        elif name == "geometry":
            self._geometry = None

    def data(self, text):
        if not self._in_index or not text:
            return
        found = len(_TOKEN.findall(text))
        if found and self._open_token and not text[0].isspace():
            found -= 1  # the first token continues the last one of the previous piece
        self._primitive[3] += found
        self._open_token = not text[-1].isspace()


def _placed(children, geometries, roots, over):
    """(nodes, vertices, triangles) placed under ``roots``, nodes at most ``over``, memoised over the
    node graph. Iterative, so the depth of a chain of nodes does not matter; a node met
    again inside its own subtree is a cycle (``ExpansionCycle``)."""
    in_progress = object()
    counted = {}
    totals = [0, 0, 0]
    for root in roots:
        if root not in children or root in counted:
            if root in counted and counted[root] is not in_progress:
                _accumulate(totals, counted[root], 0)
            continue
        counted[root] = in_progress
        stack = [[root, 0, 0 if root[0] == "vs" else 1, 0, 0]]  # key, next child, nodes, vertices, triangles
        while stack:
            frame = stack[-1]
            kids = children.get(frame[0], ())
            if frame[2] >= over or frame[1] >= len(kids):
                stack.pop()
                counted[frame[0]] = (min(frame[2], over), min(frame[3], _CAP), min(frame[4], _CAP))
                if stack:
                    _accumulate(stack[-1], counted[frame[0]], 2)
                continue
            kind, reference = kids[frame[1]]
            frame[1] += 1
            if kind == "geometry":
                vertices, triangles, primitives = geometries.get(reference, (0, 0, 0))
                # trimesh adds one scene node and one mesh for each primitive element of a placement.
                _accumulate(frame, (max(primitives, 1), vertices, triangles), 2)
            elif kind == "leaf":
                _accumulate(frame, (1, 0, 0), 2)
            else:
                known = counted.get(reference)
                if reference not in children:
                    continue  # an instance_node whose target the document does not define
                if known is None:
                    counted[reference] = in_progress
                    stack.append([reference, 0, 1, 0, 0])
                elif known is in_progress:
                    raise ExpansionCycle("a node places itself inside itself")
                else:
                    _accumulate(frame, known, 2)
        _accumulate(totals, counted[root], 0)
        totals[0] = min(totals[0], over)
    return totals[0], totals[1], totals[2]


def _accumulate(target, counts, offset):
    """Add ``counts`` (nodes, vertices, triangles) to the three slots of ``target`` from ``offset``."""
    for i in range(3):
        target[offset + i] = min(target[offset + i] + counts[i], _CAP)


def collada_scene_size(path, max_nodes):
    """The nodes, vertices and triangles the COLLADA document at ``path`` places, node counting stopping
    at ``max_nodes`` (``nodes`` is then ``max_nodes``). Raises ``ValueError`` for a document the
    hardened parser refuses and ``ExpansionCycle`` for a node that places itself."""
    scan = _ColladaScan()
    parser = xml_guard._safe_expat(scan)
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(4 * 1024 ** 2)
                if not chunk:
                    break
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except xml_guard.expat.ExpatError as e:
        raise ValueError(f"Invalid XML: {e}")
    roots = scan.scene_urls or scan.visual_scenes
    nodes, vertices, triangles = _placed(scan.children, scan.geometries, roots, max_nodes + 1)
    stored = scan.geometries.values()
    return SceneSize(nodes, vertices, triangles,
                     min(sum(g[0] for g in stored), _CAP), min(sum(g[1] for g in stored), _CAP))


# --------------------------------------------------------------------------------------------------
# USD
# --------------------------------------------------------------------------------------------------

class _Composition:
    """Counts the prims of the stage a root layer composes from its prim specs, one count per distinct
    group of specs (a "bundle": every spec of one prim in one layer stack, a variant's own spec
    included), so a prim many references reach is counted once and multiplied through the graph."""

    def __init__(self, Sdf, limit_nodes):
        self.Sdf = Sdf
        self.limit_nodes = limit_nodes
        self.max_visits = max(_MAX_USD_VISITS_FACTOR * limit_nodes, 1_000_000)
        self._stacks = {}
        self._roots = {}

    # ---- layer stacks
    def stack(self, layer):
        """The layers of ``layer``'s layer stack: the layer and the sublayers it names, strongest first."""
        cached = self._stacks.get(layer.identifier)
        if cached is not None:
            return cached
        order, seen, queue = [], set(), [layer]
        while queue and len(order) < _MAX_LAYER_STACK:
            current = queue.pop(0)
            if current.identifier in seen:
                continue
            seen.add(current.identifier)
            order.append(current)
            for sublayer in current.subLayerPaths:
                opened = self._open(current, sublayer)
                if opened is not None:
                    queue.append(opened)
        result = tuple(order)
        self._stacks[layer.identifier] = result
        return result

    def _open(self, anchor, asset_path):
        try:
            resolved = self.Sdf.ComputeAssetPathRelativeToLayer(anchor, str(asset_path))
            return self.Sdf.Layer.FindOrOpen(resolved) if resolved else None
        except Exception:  # noqa: BLE001 - a layer that cannot be opened composes nothing
            return None

    # ---- bundles
    def bundle(self, root_layer, specs, body=False):
        """``(key, bundle)`` of a group of prim specs that compose one prim of the layer stack rooted at
        ``root_layer``;
        ``body`` marks a group that stands for content merged into a prim (a variant's spec, the
        pseudo-root) and adds no prim of its own."""
        self._roots[root_layer.identifier] = root_layer
        specs = tuple(specs)
        key = (root_layer.identifier, tuple((spec.layer.identifier, spec.path.pathString) for spec in specs), body)
        return key, (root_layer.identifier, specs, body)

    def _arc_targets(self, root_layer, spec):
        """The bundles the composition arcs authored on ``spec`` bring in."""
        found = []
        lists = (((spec.referenceList,) if spec.hasReferences else ())
                 + ((spec.payloadList,) if spec.hasPayloads else ()))
        for arcs in lists:
            for item in _items(arcs):
                if item.assetPath:
                    target = self._open(spec.layer, item.assetPath)
                    if target is None:
                        continue
                    stack_root = target
                    primary = item.primPath if item.primPath else (
                        self.Sdf.Path.absoluteRootPath.AppendChild(target.defaultPrim)
                        if target.defaultPrim else None)
                else:
                    stack_root = root_layer
                    primary = item.primPath if item.primPath else None
                if primary is None:
                    continue
                found.append((stack_root, primary))
        lists = (((spec.inheritPathList,) if spec.hasInheritPaths else ())
                 + ((spec.specializesList,) if spec.hasSpecializes else ()))
        for arcs in lists:
            for item in _items(arcs):
                if item.IsAbsolutePath():
                    found.append((root_layer, item.StripAllVariantSelections()))
        bundles = []
        for stack_root, path in found:
            specs = [layer.GetPrimAtPath(path) for layer in self.stack(stack_root)]
            specs = [s for s in specs if s]
            if specs:
                bundles.append(self.bundle(stack_root, specs))
        return bundles

    def _children(self, bundle):
        """(self count, arc targets, child bundles, groups of alternative dependencies, instanceable),
        each dependency a ``(key, bundle)`` pair."""
        root_id, specs, body = bundle
        root_layer = self._roots[root_id]
        names = {}
        arcs, plain, groups = [], [], []
        instanceable = False
        forbidden = False
        for spec in specs:
            for child in spec.nameChildren:
                names.setdefault(child.name, []).append(child)
            arcs.extend(self._arc_targets(root_layer, spec))
            for variant_set in (spec.variantSets.values() if spec.variantSets else ()):
                alternatives = [self.bundle(root_layer, (variant.primSpec,), True)
                                for variant in variant_set.variants.values()]
                if alternatives:
                    groups.append(alternatives)
            if spec.HasInfo("instanceable"):
                if spec.GetInfo("instanceable"):
                    instanceable = True
                else:
                    forbidden = True
        for children in names.values():
            plain.append(self.bundle(root_layer, children))
        return (0 if body else 1), arcs, plain, groups, (instanceable and not forbidden and not body)

    def count(self, root_layer):
        """Prims the stage composed from ``root_layer`` holds, instanceable prims counted once with
        their shared content counted once per distinct prototype. Raises ``ExpansionCycle`` for an arc
        that reaches the prim it is authored on."""
        top = self.bundle(root_layer, [layer.pseudoRoot for layer in self.stack(root_layer)], True)
        memo, extras, open_keys = {}, {}, set()
        visits = 0

        def make(entry):
            own, arcs, plain, groups, instanced = self._children(entry[1])
            flat = arcs + plain + [alternative for group in groups for alternative in group]
            return [entry[0], own, arcs, plain, groups, instanced, flat, 0]

        stack = [make(top)]
        open_keys.add(top[0])
        while stack:
            frame = stack[-1]
            if frame[7] < len(frame[6]):
                entry = frame[6][frame[7]]
                frame[7] += 1
                if entry[0] in memo:
                    continue
                if entry[0] in open_keys:
                    raise ExpansionCycle("a prim composes itself through its references")
                visits += 1
                if visits > self.max_visits:
                    return _CAP
                open_keys.add(entry[0])
                stack.append(make(entry))
                continue
            key, own, arcs, plain, groups, instanced = frame[:6]
            local = sum(memo[k] for k, _ in plain)
            for group in groups:
                local += max(memo[k] for k, _ in group)
            shared = sum(memo[k] for k, _ in arcs)
            if instanced:
                # Instances with the same arcs share one prototype: its content counts once.
                prototype = frozenset(k for k, _ in arcs)
                extras[prototype] = max(extras.get(prototype, 0), shared)
                total = own + local
            else:
                total = own + local + shared
            memo[key] = min(total, _CAP)
            open_keys.discard(key)
            stack.pop()
        return min(memo[top[0]] + sum(extras.values()), _CAP)


def _items(arcs):
    """Every item a composition-arc list authors, whichever list op holds it."""
    items = []
    for name in ("prependedItems", "appendedItems", "explicitItems", "addedItems"):
        items.extend(getattr(arcs, name, ()) or ())
    return items


def usd_composed_prims(path, max_nodes):
    """An upper estimate of the prims ``Usd.Stage.Open(path)`` composes, counted from the layers'
    specs and arcs without composing them: instanceable prims count as one (their shared content once
    per distinct prototype). The count stops once it is clearly over ``max_nodes``. Raises
    ``ExpansionCycle`` for a prim that composes itself and ``ValueError`` for a layer that cannot be
    read."""
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(path)
    if layer is None:
        raise ValueError(f"The USD layer {path.rsplit('/', 1)[-1]} could not be read.")
    return _Composition(Sdf, max_nodes).count(layer)
