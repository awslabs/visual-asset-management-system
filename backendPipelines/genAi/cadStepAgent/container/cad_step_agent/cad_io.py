# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""STEP file inspection through CadQuery (OpenCascade).

CadQuery is imported lazily so the pure parts of the container (naming, reporting, sandbox environment)
import and test without the OCP wheel; ``inspect_step`` is the only entry point that needs it.
"""

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

    def to_dict(self):
        data = asdict(self)
        if data["volume_mm3"] is not None:
            data["volume_mm3"] = round(data["volume_mm3"], 3)
        if data["bounding_box_mm"] is not None:
            data["bounding_box_mm"] = [round(v, 3) for v in data["bounding_box_mm"]]
        if data["features"] is None:
            data.pop("features")
        return data

    def describe(self):
        if not self.valid:
            return f"invalid STEP: {self.error}"
        bbox = ""
        if self.bounding_box_mm:
            xmin, ymin, zmin, xmax, ymax, zmax = self.bounding_box_mm
            bbox = f" size {xmax - xmin:.2f} x {ymax - ymin:.2f} x {zmax - zmin:.2f} mm"
        volume = f" volume {self.volume_mm3:.1f} mm^3" if self.volume_mm3 is not None else ""
        features = f"; {describe_features(self.features)}" if self.features else ""
        return f"{self.solid_count} solid(s), {self.face_count} faces, {self.edge_count} edges{bbox}{volume}{features}"


# A cylindrical face group narrower than this is a fillet/round; wider than HOLE_MIN it is a hole or a boss.
FILLET_MAX_SWEEP_DEG = 200.0
HOLE_MIN_SWEEP_DEG = 300.0
_GROUP_TOL = 0.05  # mm, same radius / same axis when merging the faces of one cylinder


def describe_features(features):
    """One line: 'holes: 6 x D6.60 (through), 1 x D30.00 (through); fillet-like faces: 4 x R3.00; planar faces 10'."""
    if not features:
        return ""
    parts = []
    if features.get("holes"):
        parts.append("holes: " + ", ".join(
            f"{h['count']} x D{h['diameter_mm']:.2f} ({'through' if h.get('through') else 'not full depth: blind or counterbored'})" for h in features["holes"]))
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
    through/blind flag against the bounding box), cylindrical outer faces, fillet-like faces (convex partial
    cylinders and tori) and planar faces. Best effort: any failure yields None rather than an error."""
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
        for radius, direction, _foot, pmin, pmax, sweep, concave in groups:
            diameter = round(2 * radius, 2)
            if concave and sweep >= HOLE_MIN_SWEEP_DEG:
                through = (pmax - pmin) >= extent_along(direction) - 0.5
                key = (diameter, through)
                holes[key] = holes.get(key, 0) + 1
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
        return {
            "planar_faces": planar,
            "holes": [{"diameter_mm": d, "through": t, "count": n} for (d, t), n in sorted(holes.items())],
            "cylindrical_bosses": [{"diameter_mm": d, "count": n} for d, n in sorted(bosses.items())],
            "fillet_like_faces": [{"radius_mm": r, "count": n} for r, n in sorted(fillets.items())],
            "partial_round_cuts": [{"radius_mm": r, "count": n} for r, n in sorted(partial_cuts.items())],
        }
    except Exception:  # feature counting is advisory; the validity verdict above must not depend on it
        return None


def inspect_step(path):
    """Load a STEP file and summarize its geometry; a failure to load is a summary with valid=False."""
    try:
        import cadquery as cq  # noqa: WPS433 - lazy import by design
    except ImportError as exc:  # pragma: no cover - exercised only where CadQuery is absent
        return StepSummary(valid=False, error=f"CadQuery is not available: {exc}")
    try:
        shape = cq.importers.importStep(str(path))
        solids = shape.solids().vals()
        faces = shape.faces().vals()
        edges = shape.edges().vals()
        if not solids:
            return StepSummary(valid=False, face_count=len(faces), edge_count=len(edges),
                               error="the STEP file contains no solids")
        bb = shape.val().BoundingBox() if len(solids) == 1 else shape.combine().val().BoundingBox()
        volume = sum(s.Volume() for s in solids)
        bbox = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        return StepSummary(
            valid=True,
            solid_count=len(solids),
            face_count=len(faces),
            edge_count=len(edges),
            bounding_box_mm=bbox,
            volume_mm3=float(volume),
            features=summarize_features(shape, bbox),
        )
    except Exception as exc:  # the file is caller data; any failure is a validation outcome
        return StepSummary(valid=False, error=str(exc)[:500])
