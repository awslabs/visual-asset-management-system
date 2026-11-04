"""
Mesh repair ladder and the print-readiness gate.

A generated mesh is rarely printable as it comes out of a reconstruction model: it carries boundary
holes, non-manifold edges where two surface sheets meet along one edge, and flipped winding. The
ladder walks increasingly invasive repairs and stops as soon as the gate passes.

Ladder order is fixed -- trimesh.repair, manifold3d, pymeshfix, pymeshlab -- regardless of the
order REPAIR_BACKENDS names them, so a run is reproducible. pymeshfix and pymeshlab are GPL-3.0 and
present only when the image was built with ENABLE_GPL_MESH_REPAIR true; they import lazily and a
request for an absent backend raises rather than being skipped silently.
"""

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Ladder order, least to most invasive. REPAIR_BACKENDS selects from this; it does not reorder it.
REPAIR_LADDER = ("trimesh", "manifold3d", "pymeshfix", "pymeshlab")
# Backends carrying a GPL-3.0 licence, installed only under ENABLE_GPL_MESH_REPAIR
GPL_BACKENDS = ("pymeshfix", "pymeshlab")

PRINT_GATE_POLICIES = ("failClosed", "bestEffort")


# Fraction of the volume the mesh arrived with that a repair must leave behind for its result to be
# kept. Hole filling normally preserves or slightly increases volume, so a large loss means the rung
# discarded geometry rather than repaired it. Set to 0 to accept any repair.
DEFAULT_MIN_VOLUME_RETENTION = 0.5

# Largest hole, in boundary edges, that the pymeshlab rung will close. pymeshlab defaults this to 30 and
# leaves anything wider open without comment, which left meshes failing the gate after a rung that had
# reported success. See _repair_pymeshlab.
DEFAULT_CLOSE_HOLE_MAX_EDGES = 10000

# How large a detached piece has to be, as a fraction of the whole mesh's bounding-box diagonal, before
# it counts as part of the subject rather than as debris.
#
# The two cases are far apart when measured this way, which is why the measure is span rather than
# volume: a reconstruction arrived with 28 stray specks around a mug, each a fraction of a percent of the
# mesh's span, while another arrived with its handle detached, spanning 29% of it. By VOLUME those two
# look similar -- a mug handle is a thin loop and was only 0.7% of the mesh -- so a volume threshold
# would have thrown the handle away as debris.
#
# A piece below this is dropped: it would print as a loose speck beside the model. A piece above it is
# part of the subject, so the gate reports the mesh as being in pieces, and a repair that joins such
# pieces is recorded as piecesFused for review -- see the ladder, which explains why it is reported
# rather than refused.
DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION = 0.1

# Span alone is not enough, because a reconstruction also throws off long wispy strands that reach as far
# as a real feature while enclosing almost nothing. Two further tests separate them, both calibrated on
# the raw output of two models reconstructing the same mug:
#
#                        span   solidity   volume vs the main piece
#   detached handle      32%      68%          0.70%
#   spurious strand      15%      15%          0.14%
#   spurious strand      15%      26%          0.24%
#
# Solidity is the piece's volume against its own convex hull: a handle is a solid loop, a strand is a
# wisp. Both thresholds sit in the gap, and a piece has to clear BOTH plus the span test to count as part
# of the subject. These are heuristics fitted to one subject; revisit them as more subjects are measured.
DEFAULT_DEBRIS_SOLIDITY = 0.35
DEFAULT_DEBRIS_VOLUME_FRACTION = 0.005

# Pieces closer than this fraction of the mesh's span are treated as one piece, whatever their own
# measurements say. A mug's inner wall arrives as its own component sitting 0.068 mm from the outer wall
# -- 0.07% of the mesh -- and it is the same surface, so joining the two is repair rather than fusion.
# Without this the guard refuses the only rung that can close such a mesh and the run fails.
DEFAULT_TOUCHING_FRACTION = 0.01


class MeshRepairError(RuntimeError):
    """A repair backend that was asked for but cannot be used."""


class PrintGateError(RuntimeError):
    """A mesh that failed the print-readiness gate under the failClosed policy."""


@dataclass
class GateReport:
    """The print-readiness verdict and the measurements behind it."""

    is_watertight: bool
    non_manifold_edges: int
    non_manifold_vertices: int
    volume: float
    broken_faces: List[int] = field(default_factory=list)
    passed: bool = False
    backends_applied: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    repairs_rejected: List[str] = field(default_factory=list)
    volume_retained: Optional[float] = None
    bodies: int = 1
    expected_bodies: int = 1
    # Pieces of the subject a repair joined together. Joining is how a torn reconstruction becomes one
    # object, and also how a detached feature gets absorbed, and no measurement separates the two -- so
    # the count is reported for review rather than treated as a failure.
    pieces_fused: int = 0

    def as_dict(self) -> dict:
        report = {
            "isWatertight": bool(self.is_watertight),
            "nonManifoldEdges": int(self.non_manifold_edges),
            "nonManifoldVertices": int(self.non_manifold_vertices),
            "volume": float(self.volume),
            "brokenFaceCount": len(self.broken_faces),
            "bodies": int(self.bodies),
            "expectedBodies": int(self.expected_bodies),
            "passed": bool(self.passed),
            "backendsApplied": list(self.backends_applied),
            "failures": list(self.failures),
        }
        if self.repairs_rejected:
            report["repairsRejected"] = list(self.repairs_rejected)
        if self.volume_retained is not None:
            report["volumeRetained"] = round(float(self.volume_retained), 4)
        if self.pieces_fused:
            report["piecesFused"] = int(self.pieces_fused)
        return report


def _faces(mesh) -> list:
    """The mesh's faces as a list of int triples, independent of the array type behind them."""
    return [tuple(int(v) for v in face) for face in mesh.faces]


def _edge_face_counts(faces) -> dict:
    """``{(lo, hi): [face indices]}`` for every edge in the mesh."""
    edges = defaultdict(list)
    for face_index, face in enumerate(faces):
        for a, b in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edges[(a, b) if a <= b else (b, a)].append(face_index)
    return edges


def _non_manifold_vertex_count(faces, edges) -> int:
    """Vertices whose incident faces do not form a single fan.

    A manifold vertex has its incident faces joined edge-to-edge into one connected group. Two
    surface sheets pinched at a single vertex leave two groups, which no slicer can thicken.
    """
    vertex_faces = defaultdict(set)
    for face_index, face in enumerate(faces):
        for vertex in face:
            vertex_faces[vertex].add(face_index)

    # Face adjacency restricted to manifold edges: a non-manifold edge is already counted
    adjacency = defaultdict(set)
    for face_indices in edges.values():
        if len(face_indices) == 2:
            first, second = face_indices
            adjacency[first].add(second)
            adjacency[second].add(first)

    count = 0
    for vertex, fan in vertex_faces.items():
        unvisited = set(fan)
        groups = 0
        while unvisited:
            groups += 1
            if groups > 1:
                break
            stack = [unvisited.pop()]
            while stack:
                current = stack.pop()
                for neighbour in adjacency[current]:
                    if neighbour in unvisited:
                        unvisited.discard(neighbour)
                        stack.append(neighbour)
        if groups > 1:
            count += 1
    return count


def welded_for_measurement(mesh):
    """A copy of the mesh with vertices merged across uv and normal seams, for measuring topology.

    A textured mesh carries one vertex per face corner wherever its atlas has a seam, because a single
    position needs two different uv coordinates there. That is required for the texture and changes no
    geometry -- the enclosed volume is identical -- but it splits the shared edge into two edges each
    used by one face. Measured on the indices, every seam then reads as a non-manifold edge, and a
    sphere that is watertight welded reports thousands of non-manifold edges and one body per face once
    it carries a texture.

    So printability is measured on a welded copy while the exported mesh keeps its splits: a slicer
    works on geometry, and coincident positions close the surface whatever the indexing says. Without
    this the gate rejects every textured mesh, which is the opposite of what a colour print needs.

    Returns the mesh unchanged when trimesh is unavailable or the weld fails, so the gate still reports
    something real rather than raising.
    """
    try:
        welded = mesh.copy()
        welded.merge_vertices(merge_tex=True, merge_norm=True)
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning(
            f"Could not weld the mesh for measurement ({type(e).__name__}: {e}); the gate is measured "
            f"on the mesh as supplied, so a textured mesh may report its uv seams as non-manifold")
        return mesh
    if len(getattr(welded, "faces", ())) == 0:
        logger.warning("Welding for measurement produced no faces; measuring the mesh as supplied")
        return mesh
    before, after = len(mesh.vertices), len(welded.vertices)
    if after < before:
        logger.info(f"Measuring the print gate on a welded copy: {before} vertices merged to {after} "
                    f"across uv and normal seams; geometry and volume are unchanged")
    return welded


def evaluate_gate(mesh, expected_bodies: int = 1) -> GateReport:
    """Measure print readiness: watertight, manifold, positive volume, no broken faces, connected.

    ``broken_faces`` are the faces touching an edge that is not shared by exactly two faces --
    the boundary of a hole or a non-manifold seam.

    ``expected_bodies`` is how many separate shells the mesh is meant to have: one for a model on its
    own, and one more for each print feature attached by ``concat``. More shells than that means the
    subject came apart -- measured on a live run, a mug whose handle had separated from its body was
    watertight, fully manifold and positive in volume, and would have printed as a loose crescent
    beside a mug. Every other criterion is about the surface of one shell and cannot see that.

    Measured on a welded copy -- see ``welded_for_measurement`` -- so a texture's uv seams are not
    counted as holes in the surface, nor as shell boundaries: measured on the raw indexing a textured
    sphere reports one body per face.
    """
    mesh = welded_for_measurement(mesh)
    faces = _faces(mesh)
    edges = _edge_face_counts(faces)

    non_manifold_edges = 0
    broken_faces = set()
    for face_indices in edges.values():
        if len(face_indices) != 2:
            non_manifold_edges += 1
            broken_faces.update(face_indices)

    non_manifold_vertices = _non_manifold_vertex_count(faces, edges)

    # trimesh's own watertight test is authoritative when it is available; otherwise every edge
    # being shared by exactly two faces is the same condition.
    is_watertight = getattr(mesh, "is_watertight", None)
    if is_watertight is None:
        is_watertight = non_manifold_edges == 0
    is_watertight = bool(is_watertight)

    try:
        volume = float(mesh.volume)
    except Exception as e:
        logger.warning(f"Could not read the mesh volume: {e}")
        volume = 0.0

    # body_count is trimesh's own connected-component count. A mesh that cannot report one is treated
    # as connected rather than failed: the criterion exists to catch a subject that came apart, and an
    # unmeasurable count is not evidence that it did.
    try:
        bodies = int(getattr(mesh, "body_count", expected_bodies))
    except (TypeError, ValueError):
        bodies = expected_bodies

    failures = []
    if not is_watertight:
        failures.append("The mesh is not watertight.")
    if bodies > expected_bodies:
        failures.append(
            f"The mesh is in {bodies} separate pieces where {expected_bodies} "
            f"{'was' if expected_bodies == 1 else 'were'} expected, so part of the subject is "
            f"detached and would print as a loose piece.")
    if non_manifold_edges:
        failures.append(f"The mesh has {non_manifold_edges} non-manifold edges.")
    if non_manifold_vertices:
        failures.append(f"The mesh has {non_manifold_vertices} non-manifold vertices.")
    if volume <= 0:
        failures.append(f"The mesh encloses no positive volume (volume {volume}).")
    if broken_faces:
        failures.append(f"The mesh has {len(broken_faces)} broken faces.")

    report = GateReport(
        is_watertight=is_watertight,
        non_manifold_edges=non_manifold_edges,
        non_manifold_vertices=non_manifold_vertices,
        volume=volume,
        broken_faces=sorted(broken_faces),
        passed=not failures,
        failures=failures,
        bodies=bodies,
        expected_bodies=expected_bodies,
    )
    logger.info(f"Print gate: {report.as_dict()}")
    return report


def _resolve_negligible_fragment_fraction(params) -> float:
    """The span, as a fraction of the mesh's own, below which a detached piece is debris."""
    raw = (params or {}).get("NEGLIGIBLE_FRAGMENT_FRACTION", "")
    text = str(raw).strip() if raw is not None else ""
    if text == "":
        return DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning(f"NEGLIGIBLE_FRAGMENT_FRACTION '{raw}' is not a number; using the "
                       f"{DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION} default")
        return DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION
    if not 0.0 <= value < 1.0:
        logger.warning(f"NEGLIGIBLE_FRAGMENT_FRACTION {value} is outside 0 to 1; using the "
                       f"{DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION} default")
        return DEFAULT_NEGLIGIBLE_FRAGMENT_FRACTION
    return value


def _resolve_positive_fraction(params, key: str, default: float) -> float:
    """A 0-to-1 tuning fraction from the settings, falling back to ``default``."""
    raw = (params or {}).get(key, "")
    text = str(raw).strip() if raw is not None else ""
    if text == "":
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning(f"{key} '{raw}' is not a number; using the {default} default")
        return default
    if not 0.0 <= value <= 1.0:
        logger.warning(f"{key} {value} is outside 0 to 1; using the {default} default")
        return default
    return value


def _diagonal(points) -> float:
    """The bounding-box diagonal of a point set."""
    import numpy as np

    points = np.asarray(points, dtype=float)
    if not len(points):
        return 0.0
    return float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))


def body_face_groups(mesh):
    """The mesh's connected components, as a list of face-index arrays.

    Grouped by face adjacency on a welded copy's connectivity, so a texture's uv seams do not split one
    surface into many components -- measured on the raw indexing, a textured sphere reports one component
    per face.
    """
    import numpy as np

    welded = welded_for_measurement(mesh)
    try:
        from trimesh.graph import connected_components

        groups = connected_components(welded.face_adjacency, nodes=np.arange(len(welded.faces)))
    except Exception as e:
        logger.warning(f"Could not split the mesh into connected pieces: {e}; treating it as one")
        return [np.arange(len(mesh.faces))]
    return [np.asarray(group) for group in groups]


def _piece_measurements(mesh, group):
    """``(span, volume, solidity)`` for one connected piece."""
    import numpy as np

    try:
        piece = mesh.submesh([group], append=True)
    except Exception:
        return 0.0, 0.0, 0.0
    span = _diagonal(np.asarray(piece.vertices, dtype=float))
    volume = abs(_volume_of(piece))
    solidity = 0.0
    if len(piece.faces) > 3:
        try:
            hull = abs(float(piece.convex_hull.volume))
            solidity = volume / hull if hull > 0 else 0.0
        except Exception:
            solidity = 0.0
    return span, volume, solidity


def _merge_touching(mesh, groups, tolerance: float):
    """Groups whose surfaces sit within ``tolerance`` of each other, combined into single pieces.

    A nested inner wall arrives as its own component a fraction of a millimetre from the outer one; it is
    the same surface and joining the two is repair, not fusion.
    """
    import numpy as np

    if len(groups) <= 1:
        return list(groups)

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        # Without scipy the proximity test is unavailable; every group stands alone, which is the
        # conservative reading -- the guard may refuse a rung it could have allowed.
        logger.warning("scipy is not installed, so touching pieces cannot be recognised as one surface")
        return list(groups)

    faces = np.asarray(mesh.faces)
    vertices = np.asarray(mesh.vertices, dtype=float)
    points = [vertices[np.unique(faces[group])] for group in groups]
    parent = list(range(len(groups)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    trees = [cKDTree(p) for p in points]
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            if find(i) == find(j):
                continue
            if trees[i].query(points[j])[0].min() <= tolerance:
                parent[find(j)] = find(i)

    merged = {}
    for index, group in enumerate(groups):
        merged.setdefault(find(index), []).append(group)
    return [np.concatenate(parts) if len(parts) > 1 else parts[0] for parts in merged.values()]


def classify_fragments(mesh, fraction: float, params=None, merge_touching: bool = False):
    """``(substantial, negligible)`` face-index groups: pieces of the subject, and debris.

    A piece counts as part of the subject only when it clears all three tests -- it spans at least
    ``fraction`` of the whole mesh's bounding-box diagonal, it is solid rather than wispy, and it holds a
    real share of the largest piece's volume. Span alone let long spurious strands through, which a
    reconstruction throws off as readily as it does specks.

    ``merge_touching`` combines pieces whose surfaces sit within a hair of each other before measuring,
    which is the right view for deciding whether a rung may JOIN two pieces: a nested inner wall is the
    same surface as the outer one. It is deliberately OFF for the debris decision, because a speck resting
    against the surface would otherwise be absorbed into the main piece and never dropped -- measured on a
    live run, that left every one of 22 components in place and the gate failed on all of them.

    The largest piece is always substantial, so a mesh made entirely of small pieces keeps one.
    """
    groups = body_face_groups(mesh)
    if len(groups) <= 1:
        return groups, []

    import numpy as np

    vertices = np.asarray(mesh.vertices, dtype=float)
    whole = _diagonal(vertices)
    if whole <= 0:
        return groups, []

    params = params if isinstance(params, dict) else {}
    solidity_floor = _resolve_positive_fraction(
        params, "DEBRIS_SOLIDITY", DEFAULT_DEBRIS_SOLIDITY)
    volume_floor = _resolve_positive_fraction(
        params, "DEBRIS_VOLUME_FRACTION", DEFAULT_DEBRIS_VOLUME_FRACTION)
    touching = _resolve_positive_fraction(
        params, "TOUCHING_FRACTION", DEFAULT_TOUCHING_FRACTION) * whole

    if merge_touching:
        groups = _merge_touching(mesh, groups, touching)
        if len(groups) <= 1:
            return groups, []

    measured = [_piece_measurements(mesh, group) for group in groups]
    volumes = [m[1] for m in measured]
    largest = int(np.argmax(volumes)) if max(volumes) > 0 else int(
        np.argmax([len(group) for group in groups]))
    main_volume = volumes[largest]

    substantial, negligible = [], []
    for index, group in enumerate(groups):
        span, volume, solidity = measured[index]
        if index == largest:
            substantial.append(group)
            continue
        keeps_span = span / whole >= fraction
        keeps_solidity = solidity >= solidity_floor
        keeps_volume = main_volume <= 0 or volume / main_volume >= volume_floor
        if keeps_span and keeps_solidity and keeps_volume:
            substantial.append(group)
        else:
            negligible.append(group)
    return substantial, negligible


def cull_negligible_fragments(mesh, params):
    """Drop detached debris from the mesh, keeping every piece that is part of the subject.

    Returns ``(mesh, dropped_count)``. Debris is removed before the repair ladder walks, so a stray
    speck cannot drive the ladder to the aggressive rungs -- one reconstruction arrived with 28 of them,
    and the rung that resolved them is the same one that fuses a real feature into the body.
    """
    fraction = _resolve_negligible_fragment_fraction(params)
    if fraction <= 0:
        return mesh, 0

    substantial, negligible = classify_fragments(mesh, fraction, params)
    if not negligible:
        return mesh, 0

    import numpy as np

    keep = np.zeros(len(mesh.faces), dtype=bool)
    for group in substantial:
        keep[group] = True
    dropped_faces = int(sum(len(group) for group in negligible))
    try:
        # update_faces reindexes the vertex set and carries the uv field with it, so the texture
        # survives; rebuilding the mesh from concatenated pieces would discard it.
        culled = mesh.copy()
        culled.update_faces(keep)
        culled.remove_unreferenced_vertices()
    except Exception as e:
        logger.warning(f"Could not drop {len(negligible)} detached fragments: {e}; keeping them")
        return mesh, 0

    logger.info(f"Dropped {len(negligible)} detached fragments ({dropped_faces} faces) whose span was "
                f"under {fraction:.0%} of the mesh's own; they would print as loose specks. "
                f"{len(substantial)} piece(s) of the subject kept")

    # Report what each surviving piece measured. The three thresholds are calibrated on a handful of
    # subjects, so a run that keeps a piece it should have dropped -- or the reverse -- has to be
    # diagnosable from its own log rather than by reproducing the mesh.
    import numpy as np

    whole = _diagonal(np.asarray(mesh.vertices, dtype=float))
    measured = [(_piece_measurements(mesh, group), len(group)) for group in substantial]
    main_volume = max((m[0][1] for m in measured), default=0.0)
    for (span, volume, solidity), face_count in sorted(measured, key=lambda m: -m[1]):
        share = volume / main_volume if main_volume > 0 else 0.0
        logger.info(f"  kept piece: {face_count} faces, span {span / whole:.1%} of the mesh, "
                    f"solidity {solidity:.1%}, volume {share:.2%} of the largest piece")
    return culled, len(negligible)


def _volume_of(mesh) -> float:
    """The mesh's enclosed volume, or 0.0 when it cannot be measured.

    An unmeasurable volume disables the retention guard for that run rather than rejecting every
    rung: the guard exists to catch a destructive repair, not to block repair altogether.
    """
    try:
        volume = getattr(mesh, "volume", 0.0)
        return float(volume) if volume is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve_min_volume_retention(params) -> float:
    """The fraction of the original volume a repair must leave for its result to be accepted."""
    raw = (params or {}).get("MIN_VOLUME_RETENTION", "")
    text = str(raw).strip() if raw is not None else ""
    if text == "":
        return DEFAULT_MIN_VOLUME_RETENTION
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning(
            f"MIN_VOLUME_RETENTION '{raw}' is not a number; using the "
            f"{DEFAULT_MIN_VOLUME_RETENTION} default")
        return DEFAULT_MIN_VOLUME_RETENTION
    if not 0.0 <= value <= 1.0:
        logger.warning(
            f"MIN_VOLUME_RETENTION {value} is outside 0 to 1; using the "
            f"{DEFAULT_MIN_VOLUME_RETENTION} default")
        return DEFAULT_MIN_VOLUME_RETENTION
    return value


def normalize_winding(mesh):
    """Flip the face winding when the mesh encloses a negative volume. Returns ``(mesh, flipped)``.

    A negative volume means the faces are wound inside-out: the surface is the same solid, but every
    normal points into it. That is a genuine print defect -- a slicer fills by winding, so it reads the
    solid as the surrounding space -- and the gate rejects it as enclosing no positive volume.

    It is also entirely mechanical to fix, which is why it is corrected rather than merely reported. A
    TripoSR mesh at marching-cubes resolution 384 came out watertight with zero non-manifold edges and
    zero broken faces and still failed the gate on a volume of -0.196; the repair rungs had left it
    inverted, most likely the last one, since the trimesh rung fixes winding itself. Inverting is
    lossless: the vertex set is untouched and only the face order changes.
    """
    volume = _volume_of(mesh)
    if volume >= 0:
        return mesh, False
    try:
        flipped = mesh.copy()
        flipped.invert()
    except (AttributeError, TypeError) as e:
        logger.warning(
            f"The mesh encloses a negative volume ({volume}) but could not be inverted "
            f"({type(e).__name__}: {e}); the gate will report it as enclosing no positive volume")
        return mesh, False
    logger.info(f"Flipped the face winding: the mesh enclosed {volume} before inverting and "
                f"{_volume_of(flipped)} after, which is the same solid wound the right way out")
    return flipped, True


def _copy_for_rung(mesh):
    """An independent copy of the mesh for a repair rung to work on.

    The rungs are not uniform about this: trimesh's repair functions mutate the mesh they are given and
    return that same object, so handing them the live mesh makes the candidate and the current mesh one
    and the same, and every guard below it becomes decorative -- it logs a rejection and keeps the
    damaged geometry anyway. Returns the mesh unchanged when it cannot be copied, which keeps the test
    doubles and any minimal mesh-like object working.
    """
    try:
        return mesh.copy()
    except (AttributeError, TypeError):
        return mesh


def _resolve_close_hole_max_edges(params) -> int:
    """The largest hole, in boundary edges, the pymeshlab rung is allowed to close."""
    raw = (params or {}).get("CLOSE_HOLE_MAX_EDGES", "")
    text = str(raw).strip() if raw is not None else ""
    if text == "":
        return DEFAULT_CLOSE_HOLE_MAX_EDGES
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        logger.warning(
            f"CLOSE_HOLE_MAX_EDGES '{raw}' is not a number; using the "
            f"{DEFAULT_CLOSE_HOLE_MAX_EDGES} default")
        return DEFAULT_CLOSE_HOLE_MAX_EDGES
    if value < 0:
        logger.warning(
            f"CLOSE_HOLE_MAX_EDGES {value} is negative; using the "
            f"{DEFAULT_CLOSE_HOLE_MAX_EDGES} default")
        return DEFAULT_CLOSE_HOLE_MAX_EDGES
    return value


def resolve_backends(requested) -> list:
    """The ladder rungs to run, in ladder order.

    A named backend that is not a ladder rung is an authoring error and raises: silently dropping
    it would leave a run claiming a repair it never attempted.
    """
    if isinstance(requested, str):
        names = [n.strip().lower() for n in requested.split(",") if n.strip()]
    else:
        names = [str(n).strip().lower() for n in (requested or []) if str(n).strip()]
    if not names:
        names = ["trimesh", "manifold3d"]

    ladder_lookup = {rung.lower(): rung for rung in REPAIR_LADDER}
    unknown = [n for n in names if n not in ladder_lookup]
    if unknown:
        raise MeshRepairError(
            f"Unknown mesh repair backend(s) {', '.join(unknown)}; "
            f"expected a comma list drawn from {', '.join(REPAIR_LADDER)}")

    selected = {ladder_lookup[n] for n in names}
    return [rung for rung in REPAIR_LADDER if rung in selected]


def _repair_trimesh(mesh):
    """trimesh's own repairs: fill boundary holes, then make winding and normals consistent."""
    import trimesh

    trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh)
    trimesh.repair.fix_normals(mesh)
    mesh.remove_unreferenced_vertices()
    return mesh


def _repair_manifold3d(mesh):
    """A manifold pass through manifold3d, which rebuilds the surface as a closed solid."""
    import trimesh

    try:
        import manifold3d  # noqa: F401
    except ImportError as e:
        raise MeshRepairError(
            "Mesh repair backend 'manifold3d' is not installed in this image") from e

    # A single-operand boolean union routes the mesh through manifold3d's solid representation.
    # check_volume is off: trimesh's default raises "Not all meshes are volumes!" for any input that
    # is not already watertight with consistent winding, which is precisely the input this rung
    # exists to repair.
    #
    # trimesh reaches manifold3d through an API that moves between releases -- an unpinned pair can
    # leave trimesh calling a constructor the installed manifold3d does not expose. That is an
    # environment mismatch rather than a property of the mesh, so this rung yields to the rest of the
    # ladder instead of ending the run: the geometry is already generated by the time cleanup starts.
    try:
        result = trimesh.boolean.union([mesh], engine="manifold", check_volume=False)
    except (AttributeError, TypeError) as e:
        logger.warning(
            f"The manifold3d rung could not run against the installed trimesh/manifold3d pair "
            f"({type(e).__name__}: {e}); continuing with the remaining ladder rungs")
        return mesh
    if isinstance(result, (list, tuple)):
        result = result[0]
    # manifold3d discards a surface it cannot interpret as a solid, yielding an empty mesh. An empty
    # result is worse than the input: the gate would read volume 0 with no diagnosable cause, and a
    # later rung would have nothing to work on.
    if len(getattr(result, "faces", ())) == 0:
        logger.warning(
            "The manifold3d pass produced an empty mesh; keeping the pre-pass mesh so the "
            "remaining ladder rungs and the gate report still describe real geometry")
        return mesh
    return result


def _repair_pymeshfix(mesh):
    """pymeshfix's hole filling and self-intersection removal (GPL-3.0, opt-in)."""
    import trimesh

    try:
        import pymeshfix
    except ImportError as e:
        raise MeshRepairError(
            "Mesh repair backend 'pymeshfix' is not installed in this image; build it with "
            "ENABLE_GPL_MESH_REPAIR=true to enable the GPL-3.0 repair backends") from e

    # pymeshfix takes its verbosity on the constructor, and returns the repaired arrays as
    # ``points`` / ``faces``. Passing ``verbose`` to repair() or reading ``v`` / ``f`` raises, and the
    # ladder's per-rung guard turns that into a skipped rung rather than a failure -- which is how
    # this rung came to be absent from every run while the report still looked healthy.
    fixer = pymeshfix.MeshFix(mesh.vertices, mesh.faces)
    fixer.repair()
    return trimesh.Trimesh(vertices=fixer.points, faces=fixer.faces, process=True)


def _repair_pymeshlab(mesh, max_hole_edges: int = None):
    """pymeshlab's non-manifold repair and hole closing (GPL-3.0, opt-in).

    ``meshing_close_holes`` defaults to ``maxholesize=30`` EDGES and silently leaves anything larger
    open, which is why this rung used to finish with a mesh the gate still rejected: the preceding
    non-manifold repair removes faces, and the holes that opens are frequently wider than 30 edges.
    Measured on a sphere with a 120-face patch removed, the filter chain at its defaults returned a
    mesh that was still not watertight, while the same chain with a large ceiling closed it and kept
    98.5% of the original volume.

    The ceiling stays configurable because closing without bound also closes holes that are part of
    the subject -- the opening of a cup, say. Printing needs a closed surface, so a generous default is
    right, but an operator reproducing a specific shape can lower it.
    """
    import trimesh

    try:
        import pymeshlab
    except ImportError as e:
        raise MeshRepairError(
            "Mesh repair backend 'pymeshlab' is not installed in this image; build it with "
            "ENABLE_GPL_MESH_REPAIR=true to enable the GPL-3.0 repair backends") from e

    ceiling = DEFAULT_CLOSE_HOLE_MAX_EDGES if max_hole_edges is None else int(max_hole_edges)

    mesh_set = pymeshlab.MeshSet()
    mesh_set.add_mesh(pymeshlab.Mesh(mesh.vertices, mesh.faces))
    for meshlab_filter, kwargs in (
        ("meshing_remove_duplicate_vertices", {}),
        ("meshing_remove_duplicate_faces", {}),
        ("meshing_repair_non_manifold_edges", {}),
        ("meshing_repair_non_manifold_vertices", {}),
        ("meshing_close_holes", {"maxholesize": ceiling}),
        ("meshing_remove_unreferenced_vertices", {}),
    ):
        try:
            mesh_set.apply_filter(meshlab_filter, **kwargs)
        except Exception as e:
            logger.warning(f"pymeshlab filter '{meshlab_filter}' did not apply: {e}")
    repaired = mesh_set.current_mesh()
    logger.info(f"pymeshlab closed holes up to {ceiling} edges")
    return trimesh.Trimesh(
        vertices=repaired.vertex_matrix(), faces=repaired.face_matrix(), process=True)


_REPAIR_FUNCTIONS = {
    "trimesh": _repair_trimesh,
    "manifold3d": _repair_manifold3d,
    "pymeshfix": _repair_pymeshfix,
    "pymeshlab": _repair_pymeshlab,
}


def run_cleanup(mesh, params):
    """Walk the repair ladder until the print gate passes. Returns ``(mesh, GateReport)``.

    Each rung is applied to the output of the previous one and the gate is re-measured; the walk
    stops on the first pass. The report records which rungs actually ran, so a mesh that needed the
    GPL rung is distinguishable from one the permissive rungs fixed.
    """
    params = params if isinstance(params, dict) else {}
    backends = resolve_backends(params.get("REPAIR_BACKENDS", ""))
    logger.info(f"Repair ladder: {', '.join(backends)}")

    # Correct an inside-out winding before anything measures or repairs the mesh, so the rungs work on
    # a solid wound the right way out rather than fighting it.
    mesh, _ = normalize_winding(mesh)

    # Drop detached debris first. It would print as loose specks, and leaving it in makes the gate fail
    # on connectivity, which drives the walk to the rung that resolves several bodies by bridging them --
    # the same rung that fuses a genuinely detached feature into the body.
    mesh, _dropped = cull_negligible_fragments(mesh, params)
    fragment_fraction = _resolve_negligible_fragment_fraction(params)

    report = evaluate_gate(mesh)
    if report.passed:
        logger.info("The generated mesh already passes the print gate; no repair applied")
        return mesh, report

    # The gate measures printability, not fidelity, and the two come apart: a rung that discards most
    # of the model leaves something trivially watertight and manifold, which the gate passes. pymeshfix
    # reduces its input to a single closed body, so on a reconstruction that arrived in many pieces it
    # keeps one fragment and throws the rest away -- a result that passes every check and is useless.
    # The volume the mesh arrived with is therefore the reference a repair is held against.
    original_volume = abs(_volume_of(mesh))
    min_retention = _resolve_min_volume_retention(params)

    applied = []
    skipped = []
    rejected = []
    pieces_fused = 0
    for backend in backends:
        logger.info(f"Applying mesh repair backend '{backend}'")
        # A rung that cannot run is not a reason to lose the run: generation has already happened,
        # and a later rung or the gate policy may still yield a usable result. The rung that raised
        # is recorded so a report never implies a repair that did not occur. A backend the operator
        # named but that is absent from the image still raises through resolve_backends before the
        # walk starts, so a genuine authoring error is not swallowed here.
        # Only the pymeshlab rung takes a tuning argument, so it is passed per backend rather than
        # widening every rung's signature.
        rung_kwargs = {}
        if backend == "pymeshlab":
            rung_kwargs["max_hole_edges"] = _resolve_close_hole_max_edges(params)

        try:
            # Every rung is handed a COPY. trimesh.repair works in place and returns the same object,
            # so without this the candidate IS the current mesh and discarding it below changes
            # nothing -- the guards would report a rejection while keeping the damaged geometry.
            candidate = _REPAIR_FUNCTIONS[backend](_copy_for_rung(mesh), **rung_kwargs)
        except Exception as e:
            logger.warning(f"Mesh repair backend '{backend}' did not run ({type(e).__name__}: {e})")
            skipped.append(f"{backend}: {type(e).__name__}")
            continue

        # Retention is measured against the volume the ladder started from, not against the previous
        # rung, so several rungs each shaving a tolerable amount cannot destroy the model between them.
        retained = None
        if original_volume > 0:
            retained = abs(_volume_of(candidate)) / original_volume
            # A ratio that is not a finite number fails EVERY comparison, so `retained < floor` is
            # False and the guard would accept the repair -- failing open, which is the opposite of
            # what a floor is for. It arises whenever the candidate's volume cannot be measured, and a
            # mesh a repair has just broken is exactly the case that produces one.
            if not math.isfinite(retained):
                logger.warning(
                    f"Mesh repair backend '{backend}' produced a mesh whose volume cannot be measured "
                    f"({retained}), so the retention floor cannot be applied to it and its result is "
                    f"discarded. A repair whose own volume is unmeasurable has not produced a solid.")
                rejected.append(f"{backend}: volume unmeasurable ({retained})")
                continue
            if retained < min_retention:
                logger.warning(
                    f"Mesh repair backend '{backend}' kept only {retained:.1%} of the original "
                    f"volume, below the {min_retention:.0%} floor, so its result is discarded and "
                    f"the ladder continues from the mesh as it stood. A repair that removes the "
                    f"model satisfies the print gate without producing anything printable.")
                rejected.append(f"{backend}: kept {retained:.1%} of the original volume")
                continue

        # A repair that leaves the surface MORE broken than it found it is not a repair. Measured on a
        # TripoSR mug, the first rung took a mesh with 31 non-manifold edges to 14739 and the remaining
        # rungs never recovered it; the raw mesh had been within reach of the gate. Volume alone cannot
        # catch that, because shredding the topology barely moves the enclosed volume. A candidate that
        # actually passes is always kept, whatever the count did.
        candidate_report = evaluate_gate(candidate)
        if (not candidate_report.passed
                and candidate_report.non_manifold_edges > report.non_manifold_edges):
            logger.warning(
                f"Mesh repair backend '{backend}' raised the non-manifold edge count from "
                f"{report.non_manifold_edges} to {candidate_report.non_manifold_edges}, so its result "
                f"is discarded and the ladder continues from the mesh as it stood. A rung that breaks "
                f"the surface further leaves the rungs after it a harder problem than the original.")
            rejected.append(
                f"{backend}: raised non-manifold edges {report.non_manifold_edges} -> "
                f"{candidate_report.non_manifold_edges}")
            continue

        # Record when a rung joins pieces of the subject, without refusing it.
        #
        # Joining is how a torn reconstruction becomes one object: Stable3DGen arrives as a hollow mug in
        # several substantial pieces, and bridging them is what produces its faithful result. On another
        # model the same rung absorbed a detached handle into the wall. Four candidate discriminators were
        # measured against the raw output of both models and none of them separates the two cases -- the
        # pieces are the same size class, both are open surfaces, the absorbed handle sat CLOSER than the
        # assembled shards, surface area rose in both (123% and 125%), and the fill against the convex
        # hull barely moved on either, because the model that produced the billet had already produced a
        # near-solid mesh (85% filled before any repair). Refusing the rung therefore fails a good
        # reconstruction as readily as it protects a bad one, which is what a live run showed.
        #
        # So the join is measured and reported instead. It reaches the gate report and the comparison's
        # concerns, where a fused result can be reviewed against the preview, and the operator's remedy --
        # raise the model's resolution so the piece arrives attached -- is a decision rather than a
        # guess. Measured on the touching-merged view, since joining a nested inner wall to its outer
        # surface is ordinary repair.
        before = len(classify_fragments(mesh, fragment_fraction, params, merge_touching=True)[0])
        after = len(classify_fragments(candidate, fragment_fraction, params, merge_touching=True)[0])
        if before > 1 and after < before:
            pieces_fused += before - after
            logger.warning(
                f"Mesh repair backend '{backend}' joined {before} pieces of the subject into {after}. "
                f"That is how a torn reconstruction becomes one object, and it is also how a detached "
                f"feature gets absorbed into the body, so the result is kept and recorded rather than "
                f"refused: check the preview, and raise the model's resolution if a feature was lost.")

        mesh = candidate
        applied.append(backend)
        # Reuse the measurement the guards above already made rather than gating the same mesh twice;
        # on a 200k-face mesh that is not a trivial saving.
        report = candidate_report
        report.backends_applied = list(applied)
        report.volume_retained = retained
        report.repairs_rejected = list(rejected)
        report.pieces_fused = pieces_fused
        report.failures = list(report.failures) + [f"repair backend skipped -- {s}" for s in skipped]
        report.failures = report.failures + [f"repair rejected -- {r}" for r in rejected]
        if report.passed:
            logger.info(f"The print gate passes after '{backend}'")
            return mesh, report
        logger.info(f"The print gate still fails after '{backend}': {'; '.join(report.failures)}")

    # A rung can leave the mesh inverted even when it repaired everything else -- measured on TripoSR,
    # the ladder produced a watertight mesh with no non-manifold edges and no broken faces that still
    # failed on a negative volume. Correcting the winding here and re-measuring turns that into the pass
    # it geometrically already was.
    mesh, flipped = normalize_winding(mesh)
    if flipped:
        report = evaluate_gate(mesh)

    report.backends_applied = list(applied)
    report.repairs_rejected = list(rejected)
    report.pieces_fused = pieces_fused
    if original_volume > 0:
        report.volume_retained = abs(_volume_of(mesh)) / original_volume
    for note in ([f"repair backend skipped -- {s}" for s in skipped]
                 + [f"repair rejected -- {r}" for r in rejected]):
        if note not in report.failures:
            report.failures = list(report.failures) + [note]
    return mesh, report


def regate(previous: GateReport, mesh, expected_bodies: int = 1) -> GateReport:
    """The gate re-measured on a mesh that changed after the cleanup ran.

    The print features attach after the rescale, so the verdict the cleanup produced describes geometry
    that is no longer what ships. The repair provenance is carried over rather than re-derived: it
    records what the cleanup did, which re-measuring the finished mesh cannot recover.

    ``expected_bodies`` accounts for the shells the features deliberately added, so a loop and a base
    joined by ``concat`` are not read as the subject having come apart. A model that was already in
    two pieces still exceeds the allowance and still fails.
    """
    fresh = evaluate_gate(mesh, expected_bodies=expected_bodies)
    fresh.backends_applied = list(previous.backends_applied)
    fresh.repairs_rejected = list(previous.repairs_rejected)
    fresh.volume_retained = previous.volume_retained
    # Carried over for the same reason as the rest of the provenance: re-measuring the finished mesh
    # cannot recover what a repair did to it. Omitting this dropped the count on every run that added a
    # print feature -- which is the configuration a colour print with a hanging loop uses, so the one
    # signal saying a feature may have been absorbed was missing exactly where it was most needed.
    fresh.pieces_fused = previous.pieces_fused
    return fresh


def resolve_gate_outcome(report: GateReport, policy: str):
    """``(write_mesh_outputs, gate_error)`` for a gate report under a policy.

    failClosed yields no mesh outputs and an error the caller raises once the gate report has been
    written -- the report is the only thing that makes the rejection legible to the operator.
    bestEffort yields the outputs and no error, with the failures recorded in the report.
    """
    policy = str(policy or "").strip() or "failClosed"
    if policy not in PRINT_GATE_POLICIES:
        raise PrintGateError(
            f"Unknown print gate policy '{policy}'; "
            f"expected one of {', '.join(PRINT_GATE_POLICIES)}")

    if report.passed:
        return True, None
    if policy == "bestEffort":
        logger.warning("The mesh failed the print gate; bestEffort writes the outputs anyway: "
                       + "; ".join(report.failures))
        return True, None
    return False, PrintGateError(
        "The mesh failed the print-readiness gate: " + " ".join(report.failures))
