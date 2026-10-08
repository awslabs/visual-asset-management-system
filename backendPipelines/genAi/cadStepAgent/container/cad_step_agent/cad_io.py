# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""STEP file inspection through CadQuery (OpenCascade).

CadQuery is imported lazily so the pure parts of the container (naming, reporting, sandbox environment)
import and test without the OCP wheel; ``inspect_step`` is the only entry point that needs it.
"""

import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

STEP_EXTENSIONS = (".stp", ".step")


@dataclass
class StepSummary:
    valid: bool
    solid_count: int = 0
    face_count: int = 0
    edge_count: int = 0
    bounding_box_mm: Optional[List[float]] = None  # [xmin, ymin, zmin, xmax, ymax, zmax]
    volume_mm3: Optional[float] = None
    error: str = ""
    # Feature counts the agent can compare against the instruction (see ``summarize_features``).
    features: Optional[Dict] = None
    # Geometry in the file that belongs to none of its solids -- the annotation planes and curve sets of an
    # AP242 export with PMI -- as {"faces": F, "edges": E} (an edge bounding one of those faces is not
    # counted again); None when the file holds nothing but its solids. Every figure above describes the
    # solids only.
    non_solid_geometry: Optional[Dict] = None
    # Such geometry that was in a script's output and has been removed from the file (see
    # ``drop_non_solid_geometry``): the file now holds the solids the summary describes, and nothing else.
    dropped_non_solid_geometry: Optional[Dict] = None
    # The largest planar faces with their outward normals and, for a sheet-like part, the axis its thickness
    # runs along (see ``summarize_orientation``): what a script drills through. Advisory; None when unknown.
    orientation: Optional[Dict] = None
    # One entry per solid, {"volume_mm3", "bounding_box_mm"}, so a per-body request is answered from the
    # summary; the first SOLIDS_LISTED_MAX bodies by volume are listed. Kept out of to_dict() for a single
    # solid, whose figures are the summary's own.
    solids: Optional[List[Dict]] = None

    def to_dict(self):
        data = asdict(self)
        if data["volume_mm3"] is not None:
            data["volume_mm3"] = round(data["volume_mm3"], 3)
        if data["bounding_box_mm"] is not None:
            data["bounding_box_mm"] = [round(v, 3) for v in data["bounding_box_mm"]]
        for key in ("features", "non_solid_geometry", "dropped_non_solid_geometry", "orientation"):
            if data[key] is None:
                data.pop(key)
        if not data["solids"] or len(data["solids"]) < 2:
            data.pop("solids")
        return data

    def describe(self):
        if not self.valid:
            return f"invalid STEP: {self.error}"
        bbox = ""
        if self.bounding_box_mm:
            xmin, ymin, zmin, xmax, ymax, zmax = self.bounding_box_mm
            bbox = f" size {xmax - xmin:.2f} x {ymax - ymin:.2f} x {zmax - zmin:.2f} mm"
        volume = f" volume {self.volume_mm3:.1f} mm^3" if self.volume_mm3 is not None else ""
        bodies = ""
        if self.solids and len(self.solids) > 1:
            bodies = "; bodies by volume " + ", ".join(f"{b['volume_mm3']:.1f} mm^3" for b in self.solids)
            if self.solid_count > len(self.solids):
                bodies += f", +{self.solid_count - len(self.solids)} more not listed"
        features = f"; {describe_features(self.features)}" if self.features else ""
        axis = (self.orientation or {}).get("thickness_axis")
        thickness = f"; sheet-like part, thickness along {axis.upper()}" if axis else ""
        ignored = (f"; {describe_non_solid_geometry(self.non_solid_geometry)} outside the solids ignored"
                   if self.non_solid_geometry else "")
        dropped = (f"; {describe_non_solid_geometry(self.dropped_non_solid_geometry)} outside the solids dropped "
                   "from the output file" if self.dropped_non_solid_geometry else "")
        return (f"{self.solid_count} solid(s), {self.face_count} faces, {self.edge_count} edges{bbox}{volume}{bodies}"
                f"{features}{thickness}{ignored}{dropped}")


def describe_non_solid_geometry(geometry):
    """'5 face(s) and 290 edge(s)' for {"faces": 5, "edges": 290}."""
    parts = [f"{geometry[key]} {key[:-1]}(s)" for key in ("faces", "edges") if geometry.get(key)]
    return " and ".join(parts) or "geometry"


# A cylindrical face group narrower than this is a fillet/round; wider than HOLE_MIN it is a hole or a boss.
FILLET_MAX_SWEEP_DEG = 200.0
HOLE_MIN_SWEEP_DEG = 300.0
_GROUP_TOL = 0.05  # mm, same radius / same axis when merging the faces of one cylinder
_AXIS_ALIGNED_COS = 0.9994  # cos(2 deg): a hole axis this close to x, y or z gets in-plane coordinates
# Hole centres are listed for at most this many holes; the count per diameter is always complete.
HOLE_CENTRES_MAX = 24
_PLANE_OF_AXIS = {0: "yz", 1: "xz", 2: "xy"}  # the two coordinates given for a hole along x, y or z


def _describe_hole_group(hole):
    depth = "through" if hole.get("through") else "not full depth: blind or counterbored"
    text = f"{hole['count']} x D{hole['diameter_mm']:.2f} ({depth}"
    centres = hole.get("centres_mm") or []
    if centres:
        by_plane = {}
        for c in centres:
            by_plane.setdefault(c["plane"], []).append(f"({c['from_bbox_min'][0]:.1f}, {c['from_bbox_min'][1]:.1f})")
        text += "; centres from bbox min corner " + "; ".join(
            f"{plane}: " + ", ".join(points) for plane, points in sorted(by_plane.items()))
        if hole.get("centres_omitted"):
            text += f", +{hole['centres_omitted']} more not listed"
    return text + ")"


def describe_features(features):
    """One line: 'holes: 4 x D4.50 (through; centres from bbox min corner xy: (8.0, 8.0), (92.0, 8.0), ...);
    fillet-like faces: 4 x R3.00; planar faces 10'."""
    if not features:
        return ""
    parts = []
    if features.get("holes"):
        parts.append("holes: " + ", ".join(_describe_hole_group(h) for h in features["holes"]))
    else:
        parts.append("holes: none")
    if features.get("cylindrical_bosses"):
        parts.append("cylindrical outer faces: " + ", ".join(f"{b['count']} x D{b['diameter_mm']:.2f}" for b in features["cylindrical_bosses"]))
    if features.get("fillet_like_faces"):
        parts.append("fillet-like faces: " + ", ".join(f"{f['count']} x R{f['radius_mm']:.2f}" for f in features["fillet_like_faces"]))
    else:
        parts.append("fillet-like faces: none")
    if features.get("partial_round_cuts"):
        parts.append("partial round cut faces (slot ends etc.): " + ", ".join(
            f"{f['count']} x R{f['radius_mm']:.2f}" for f in features["partial_round_cuts"]))
    parts.append(f"planar faces {features.get('planar_faces', 0)}")
    return "; ".join(parts)


def summarize_features(shape, bbox):
    """Feature counts of a CadQuery shape: holes (concave cylinders, grouped per axis, by diameter, with a
    through/blind flag against the bounding box and, for axis-aligned holes, the centre in the plane
    perpendicular to the axis measured from the bounding box's min corner), cylindrical outer faces,
    fillet-like faces (convex partial cylinders and tori) and planar faces. Best effort: any failure yields
    None rather than an error."""
    try:
        import math
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        import cadquery as cq

        planar = 0
        torus_radii = []
        groups = []  # [radius, dir, foot, pmin, pmax, sweep, concave]
        for face in shape.faces().vals():
            kind = face.geomType()
            if kind == "PLANE":
                planar += 1
                continue
            if kind == "TORUS":
                torus_radii.append(round(BRepAdaptor_Surface(face.wrapped).Torus().MinorRadius(), 2))
                continue
            if kind != "CYLINDER":
                continue
            ad = BRepAdaptor_Surface(face.wrapped)
            cyl = ad.Cylinder()
            axis = cyl.Axis()
            d = axis.Direction()
            loc = axis.Location()
            direction = cq.Vector(d.X(), d.Y(), d.Z()).normalized()
            k = max(range(3), key=lambda i: abs(direction.toTuple()[i]))
            if direction.toTuple()[k] < 0:
                direction = direction * -1
            u0, u1 = ad.FirstUParameter(), ad.LastUParameter()
            v0, v1 = ad.FirstVParameter(), ad.LastVParameter()
            surface_point = ad.Value(0.5 * (u0 + u1), 0.5 * (v0 + v1))
            point = cq.Vector(surface_point.X(), surface_point.Y(), surface_point.Z())
            normal = face.normalAt(point)
            location = cq.Vector(loc.X(), loc.Y(), loc.Z())
            radial = point - location
            radial = radial - direction * radial.dot(direction)
            concave = normal.dot(radial) < 0
            foot = location - direction * location.dot(direction)
            projections = [cq.Vector(*v.toTuple()).dot(direction) for v in face.Vertices()]
            if len(projections) < 2:
                base = location.dot(direction)
                projections = [base + v0, base + v1]
            entry = [float(cyl.Radius()), direction, foot, min(projections), max(projections),
                     math.degrees(abs(u1 - u0)), bool(concave)]
            for g in groups:
                if g[6] == entry[6] and abs(g[0] - entry[0]) <= _GROUP_TOL and abs(g[1].dot(entry[1])) > 0.9994 \
                        and (g[2] - entry[2]).Length <= _GROUP_TOL \
                        and not (entry[3] > g[4] + _GROUP_TOL or entry[4] < g[3] - _GROUP_TOL):
                    g[3], g[4], g[5] = min(g[3], entry[3]), max(g[4], entry[4]), g[5] + entry[5]
                    break
            else:
                groups.append(entry)

        def extent_along(direction):
            xs = (bbox[0], bbox[3])
            ys = (bbox[1], bbox[4])
            zs = (bbox[2], bbox[5])
            values = [cq.Vector(x, y, z).dot(direction) for x in xs for y in ys for z in zs]
            return max(values) - min(values)

        holes, bosses, fillets, partial_cuts = {}, {}, {}, {}
        hole_centres = {}
        for radius, direction, foot, pmin, pmax, sweep, concave in groups:
            diameter = round(2 * radius, 2)
            if concave and sweep >= HOLE_MIN_SWEEP_DEG:
                through = (pmax - pmin) >= extent_along(direction) - 0.5
                key = (diameter, through)
                holes[key] = holes.get(key, 0) + 1
                axis_index = max(range(3), key=lambda i: abs(direction.toTuple()[i]))
                if abs(direction.toTuple()[axis_index]) >= _AXIS_ALIGNED_COS:
                    centre = (foot + direction * (0.5 * (pmin + pmax))).toTuple()
                    i, j = [a for a in range(3) if a != axis_index]
                    hole_centres.setdefault(key, []).append({
                        "plane": _PLANE_OF_AXIS[axis_index],
                        "from_bbox_min": [round(centre[i] - bbox[i], 2), round(centre[j] - bbox[j], 2)],
                    })
            elif concave:
                r = round(radius, 2)
                partial_cuts[r] = partial_cuts.get(r, 0) + 1
            elif not concave and sweep >= HOLE_MIN_SWEEP_DEG:
                bosses[diameter] = bosses.get(diameter, 0) + 1
            elif not concave and sweep <= FILLET_MAX_SWEEP_DEG:
                r = round(radius, 2)
                fillets[r] = fillets.get(r, 0) + 1
        for r in torus_radii:
            fillets[r] = fillets.get(r, 0) + 1

        hole_entries = []
        listed = 0
        for (d, t), n in sorted(holes.items()):
            entry = {"diameter_mm": d, "through": t, "count": n}
            centres = sorted(hole_centres.get((d, t), []), key=lambda c: c["from_bbox_min"])
            room = max(0, HOLE_CENTRES_MAX - listed)
            if centres:
                entry["centres_mm"] = centres[:room]
                listed += len(entry["centres_mm"])
                if len(centres) > room:
                    entry["centres_omitted"] = len(centres) - room
            hole_entries.append(entry)
        return {
            "planar_faces": planar,
            "holes": hole_entries,
            "cylindrical_bosses": [{"diameter_mm": d, "count": n} for d, n in sorted(bosses.items())],
            "fillet_like_faces": [{"radius_mm": r, "count": n} for r, n in sorted(fillets.items())],
            "partial_round_cuts": [{"radius_mm": r, "count": n} for r, n in sorted(partial_cuts.items())],
        }
    except Exception:  # feature counting is advisory; the validity verdict above must not depend on it
        return None


def _solids_body(cq, solids):
    """The shape that stands for a file's design content: its one solid, or a compound of its solids."""
    return solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)


# A part is sheet-like when its smallest extent is at most this fraction of the next one; the axis of that
# extent is then the one a hole through the sheet runs along.
SHEET_THICKNESS_RATIO = 0.25
ORIENTATION_FACES_MAX = 3
# Per-body figures are listed for at most this many solids (largest first); the solid count is always complete.
SOLIDS_LISTED_MAX = 20


def summarize_solids(solids):
    """One {"volume_mm3", "bounding_box_mm"} entry per solid, largest first, at most SOLIDS_LISTED_MAX."""
    entries = []
    for solid in solids:
        bb = solid.BoundingBox()
        entries.append({"volume_mm3": round(solid.Volume(), 3),
                        "bounding_box_mm": [round(v, 3) for v in (bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax)]})
    entries.sort(key=lambda e: -e["volume_mm3"])
    return entries[:SOLIDS_LISTED_MAX]


def summarize_orientation(body, bbox):
    """The largest planar faces of a shape (area, outward unit normal) and, for a sheet-like part, the axis
    its thickness runs along -- what a script has to drill through, so a Y-thick sheet gets faces(">Y")
    rather than the Z-up habit. Advisory: any failure yields None."""
    try:
        planes = []
        for face in body.Faces():
            if face.geomType() != "PLANE":
                continue
            n = face.normalAt()
            planes.append({"area_mm2": round(face.Area(), 2),
                           "normal": [round(n.x, 3) + 0.0, round(n.y, 3) + 0.0, round(n.z, 3) + 0.0]})
        planes.sort(key=lambda p: -p["area_mm2"])
        extents = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
        thin = min(range(3), key=lambda i: extents[i])
        others = sorted(extents[i] for i in range(3) if i != thin)
        axis = "xyz"[thin] if extents[thin] <= SHEET_THICKNESS_RATIO * others[0] else None
        orientation = {"largest_planar_faces": planes[:ORIENTATION_FACES_MAX], "thickness_axis": axis}
        if axis:
            in_plane = [a for a in "XYZ" if a != axis.upper()]
            direction = [1 if a == axis.upper() else 0 for a in "XYZ"]
            orientation["hint"] = (
                f"sheet-like part, {extents[thin]:.2f} mm thick along {axis.upper()}: a hole through the sheet runs "
                f"along {axis.upper()} (direction {tuple(direction)}), and its position is given in {in_plane[0]} and "
                f"{in_plane[1]}")
        return orientation
    except Exception:
        return None


def _non_solid_geometry(shape, body):
    """The faces and edges of an imported file that belong to none of its solids, as {"faces": F, "edges": E}
    (an edge that bounds one of those faces is not counted again), or None when there are none. A STEP
    reader presents an AP242 file with PMI as the solid beside annotation roots (planes, curve sets); a
    script that re-exports the imported part whole writes them into one compound beside the solid -- the
    count is the same either way. Best effort: a failure to count is None."""
    try:
        body_faces, body_edges = set(body.Faces()), set(body.Edges())
        faces = [f for f in shape.faces().vals() if f not in body_faces]
        face_edges = {e for f in faces for e in f.Edges()}
        edges = [e for e in shape.edges().vals() if e not in body_edges and e not in face_edges]
    except Exception:
        return None
    if not faces and not edges:
        return None
    return {"faces": len(faces), "edges": len(edges)}


def inspect_step(path):
    """Load a STEP file and summarize its geometry; a failure to load is a summary with valid=False."""
    try:
        import cadquery as cq  # noqa: WPS433 - lazy import by design
    except ImportError as exc:  # pragma: no cover - exercised only where CadQuery is absent
        return StepSummary(valid=False, error=f"CadQuery is not available: {exc}")
    try:
        shape = cq.importers.importStep(str(path))
        solids = shape.solids().vals()
        if not solids:
            return StepSummary(valid=False, face_count=len(shape.faces().vals()), edge_count=len(shape.edges().vals()),
                               error="the STEP file contains no solids")
        # A file may carry several root shapes (an AP242 export with PMI: the solid, annotation planes,
        # curve sets, empty compounds). ``shape.val()`` is only the FIRST root -- an empty one has no
        # bounding box, and an annotation root would inflate it -- so every figure is measured on the solids.
        body = _solids_body(cq, solids)
        bb = body.BoundingBox()
        bbox = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        return StepSummary(
            valid=True,
            solid_count=len(solids),
            face_count=len(body.Faces()),
            edge_count=len(body.Edges()),
            bounding_box_mm=bbox,
            volume_mm3=float(sum(s.Volume() for s in solids)),
            features=summarize_features(cq.Workplane("XY").newObject([body]), bbox),
            non_solid_geometry=_non_solid_geometry(shape, body),
            orientation=summarize_orientation(body, bbox),
            solids=summarize_solids(solids),
        )
    except Exception as exc:  # the file is caller data; any failure is a validation outcome
        return StepSummary(valid=False, error=str(exc)[:500])


def drop_non_solid_geometry(path):
    """Rewrite the STEP file at ``path`` as its solids alone when it carries geometry that belongs to no
    solid (the PMI annotation planes and curve sets a script re-exports along with an imported part).
    Returns what was dropped, as ``StepSummary.non_solid_geometry`` counts it, or None when the file was
    left untouched. The solids are written to a sibling ``<path>.tmp`` that replaces ``path`` only once the
    STEP writer reports the file complete, so a write that fails or stops short leaves the original as it
    was. Raises on a file that cannot be read or written -- the caller validates the file first."""
    import cadquery as cq  # noqa: WPS433 - lazy import by design
    from OCP.IFSelect import IFSelect_RetDone  # noqa: WPS433 - lazy import by design

    shape = cq.importers.importStep(str(path))
    solids = shape.solids().vals()
    if not solids:
        return None
    body = _solids_body(cq, solids)
    dropped = _non_solid_geometry(shape, body)
    if not dropped:
        return None
    tmp = f"{path}.tmp"
    try:
        # Shape.exportStep returns the writer's status; the extension-driven cq.exporters.export discards
        # it (and refuses ``.stp``).
        status = body.exportStep(tmp)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"STEP write failed ({getattr(status, 'name', status)})")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dropped
