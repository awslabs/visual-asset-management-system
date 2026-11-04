#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Geometry scorer of the GenAI CAD STEP agent evaluation harness: what a STEP file measures, and how it
compares with a corpus case's ``expect`` block.

Per STEP file this computes, through CadQuery / OCP:

- import success, solid count, ``BRepCheck_Analyzer`` validity, every shell closed;
- bounding box (mm), extents and sorted extents, volume, surface area, centroid;
- planar-face count; cylindrical faces grouped into hole candidates (radius, axis, length, through vs
  blind, position in the plane perpendicular to the axis measured from the bbox min corner); coaxial
  pairs -> counterbores; convex full cylinders -> bosses (shaft steps, rims);
- convex partial cylinders and toroidal faces -> fillet candidates (radius); non-axis-aligned planar
  faces -> chamfer candidates (best effort, report only);
- for MODIFY cases a delta against the input STEP (volume change/ratio, extents change, holes
  added/removed).

``check_case`` compares the metrics against a corpus ``expect`` block with explicit tolerances
(defaults: +-0.5 mm dimensions, +-0.25 mm radius, +-2 % volume, exact counts) and returns a verdict:
PASS (all expects met), PARTIAL (valid closed solid, >= half the expects met), FAIL (absent, invalid,
or fewer than half met). ``markdown_row`` renders one report row.

CLI: ``stp_quality.py <file.step> [--expect expect.json] [--input input.step] [--out result.json]``
"""

import argparse
import itertools
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional

DEFAULT_TOL = {
    "dim": 0.5,          # mm, lengths / positions / extents
    "radius": 0.25,      # mm (diameter +-0.5)
    "volume_pct": 2.0,   # percent
    "angle_deg": 2.0,    # axis snapping
}
HOLE_MIN_SWEEP_DEG = 300.0     # a concave cylinder group at least this wide is a hole (a keyway may cut it)
FILLET_MAX_SWEEP_DEG = 200.0   # a convex cylinder narrower than this is a fillet candidate
CONCAVE_FILLET_MAX_SWEEP_DEG = 120.0  # a concave cylinder narrower than this is an inside fillet; 120-300 = slot end / partial
COAXIAL_TOL = 0.05             # mm, axis-line coincidence when grouping faces
RADIUS_GROUP_TOL = 0.02        # mm, same-radius when grouping faces
PRINCIPAL = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_FAIL = "FAIL"


# --------------------------------------------------------------------------- small vector helpers

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _unit(a):
    n = _norm(a)
    return _scale(a, 1.0 / n) if n else (0.0, 0.0, 0.0)


def _canonical_dir(d, angle_tol_deg):
    """Unit direction with a canonical sign (largest component positive), snapped to a principal
    axis when within ``angle_tol_deg`` of one. Returns (dir, axis_index_or_None)."""
    d = _unit(d)
    k = max(range(3), key=lambda i: abs(d[i]))
    if d[k] < 0:
        d = _scale(d, -1.0)
    for i, p in enumerate(PRINCIPAL):
        cos = min(1.0, abs(_dot(d, p)))
        if math.degrees(math.acos(cos)) <= angle_tol_deg:
            return p, i
    return d, None


def _axis_foot(point, direction):
    """The point of the axis line closest to the origin (a canonical point for the line)."""
    return _sub(point, _scale(direction, _dot(point, direction)))


def _line_distance(foot_a, foot_b, direction):
    diff = _sub(foot_a, foot_b)
    return _norm(_cross(diff, direction))


def _round(v, nd=3):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [_round(x, nd) for x in v]
    return round(float(v), nd)


# --------------------------------------------------------------------------- analysis

def analyze_step(path: str) -> Dict:
    """Metrics for one STEP file. Never raises for bad geometry: ``import_ok``/``valid``/``error`` say
    what happened."""
    result: Dict = {
        "path": str(path), "import_ok": False, "valid": False, "shells_closed": None,
        "solid_count": 0, "face_count": 0, "edge_count": 0,
        "bbox": None, "extents": None, "extents_sorted": None,
        "volume": None, "area": None, "centroid": None,
        "planar_faces": 0, "cylindrical_faces": 0, "face_types": {},
        "holes": [], "counterbores": [], "bosses": [], "fillets": [], "fillet_faces": [],
        "chamfer_candidates": [], "partial_concave_cylinders": [], "error": "",
        "solids_detail": [], "root_shapes": None, "declared_length_unit": None, "face_errors": 0,
    }
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        result["error"] = "file is absent or empty"
        return result
    try:
        import cadquery as cq
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepCheck import BRepCheck_Analyzer
    except ImportError as exc:  # pragma: no cover
        result["error"] = f"cadquery/OCP unavailable: {exc}"
        return result
    try:
        shape = cq.importers.importStep(str(path))
    except Exception as exc:
        result["error"] = f"import failed: {str(exc)[:300]}"
        return result
    result["import_ok"] = True
    result["declared_length_unit"] = _declared_length_unit(path)
    try:
        result["root_shapes"] = len(shape.vals())
    except Exception:
        pass
    try:
        solids = shape.solids().vals()
        faces = shape.faces().vals()
        edges = shape.edges().vals()
        shells = shape.shells().vals()
        result["solid_count"] = len(solids)
        result["face_count"] = len(faces)
        result["edge_count"] = len(edges)
        if not solids:
            result["error"] = "no solids"
            return result
        result["valid"] = all(BRepCheck_Analyzer(s.wrapped).IsValid() for s in solids)
        # The shells of the SOLIDS are judged: a PMI annotation root in a multi-root file is an open shell
        # that says nothing about the part. For a plain single-solid file this is the same set.
        solid_shells = [sh for s in solids for sh in s.Shells()] or shells
        result["shells_closed"] = bool(solid_shells) and all(BRep_Tool.IsClosed_s(sh.wrapped) for sh in solid_shells)
        # The compound is built from the solids themselves. A STEP file with several root shapes
        # (e.g. AP242 PMI annotation roots, NIST FTC-09 e1) can have an EMPTY first root, and
        # ``shape.val()`` then raises "Bnd_Box is void" although the solid is fine.
        compound = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
        bb = compound.BoundingBox()
        for s in solids:
            try:
                sb = s.BoundingBox()
                result["solids_detail"].append({
                    "volume": _round(s.Volume()), "faces": len(s.Faces()),
                    "bbox": _round([sb.xmin, sb.ymin, sb.zmin, sb.xmax, sb.ymax, sb.zmax]),
                    "extents_sorted": _round(sorted([sb.xlen, sb.ylen, sb.zlen]))})
            except Exception as exc:
                result["solids_detail"].append({"error": str(exc)[:120]})
        bbox = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        extents = [bb.xmax - bb.xmin, bb.ymax - bb.ymin, bb.zmax - bb.zmin]
        result["bbox"] = _round(bbox)
        result["extents"] = _round(extents)
        result["extents_sorted"] = _round(sorted(extents))
        volume = sum(s.Volume() for s in solids)
        result["volume"] = _round(volume)
        result["area"] = _round(sum(s.Area() for s in solids))
        c = compound.Center()
        result["centroid"] = _round([c.x, c.y, c.z])

        cyl_faces = []
        types: Dict[str, int] = {}
        tor_faces = []
        for f in faces:
            try:
                gt = f.geomType()
            except Exception:
                result["face_errors"] += 1
                types["UNKNOWN"] = types.get("UNKNOWN", 0) + 1
                continue
            types[gt] = types.get(gt, 0) + 1
            if gt == "PLANE":
                result["planar_faces"] += 1
                try:
                    n = f.normalAt()
                    nd = _unit((n.x, n.y, n.z))
                    best = max(abs(_dot(nd, p)) for p in PRINCIPAL)
                    ang = math.degrees(math.acos(min(1.0, best)))
                    if 30.0 <= ang <= 60.0:
                        result["chamfer_candidates"].append({"angle_to_axis_deg": _round(ang, 1),
                                                             "area": _round(f.Area(), 2)})
                except Exception:
                    pass
            elif gt == "CYLINDER":
                result["cylindrical_faces"] += 1
                cyl_faces.append(_cylinder_face(f, BRepAdaptor_Surface, cq))
            elif gt == "TORUS":
                tor_faces.append(_torus_face(f, BRepAdaptor_Surface))
        result["face_types"] = types
        cyl_faces = [c for c in cyl_faces if c]
        _group_cylinders(cyl_faces, bbox, extents, result)
        for t in tor_faces:
            if t:
                result["fillet_faces"].append(t)
        result["fillets"] = _summarize_fillets(result["fillet_faces"])
    except Exception as exc:
        result["error"] = f"analysis failed: {str(exc)[:300]}"
    return result


def _declared_length_unit(path):
    """The length unit the file declares (inch via CONVERSION_BASED_UNIT, else mm). OCC converts every
    file to millimetres on import; this only records what the author wrote. Text scan of the first 4 MB."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4 * 1024 * 1024).decode("latin-1")
    except Exception:
        return None
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", head, re.I):
        return "inch"
    if re.search(r"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)", head):
        return "mm"
    if re.search(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.\s*\)", head):
        return "m"
    return "unknown"


def _cylinder_face(face, BRepAdaptor_Surface, cq):
    try:
        ad = BRepAdaptor_Surface(face.wrapped)
        cyl = ad.Cylinder()
        ax = cyl.Axis()
        loc, d = ax.Location(), ax.Direction()
        u0, u1 = ad.FirstUParameter(), ad.LastUParameter()
        v0, v1 = ad.FirstVParameter(), ad.LastVParameter()
        pnt = ad.Value(0.5 * (u0 + u1), 0.5 * (v0 + v1))
        p = (pnt.X(), pnt.Y(), pnt.Z())
        n = face.normalAt(cq.Vector(*p))
        nvec = (n.x, n.y, n.z)
        direction = _unit((d.X(), d.Y(), d.Z()))
        location = (loc.X(), loc.Y(), loc.Z())
        radial = _sub(p, location)
        radial = _sub(radial, _scale(direction, _dot(radial, direction)))
        concave = _dot(nvec, radial) < 0
        cdir, axis_index = _canonical_dir(direction, DEFAULT_TOL["angle_deg"])
        verts = [v.toTuple() for v in face.Vertices()]
        proj = [_dot(v, cdir) for v in verts]
        if len(proj) < 2:  # a closed cylinder with degenerate topology; fall back to V range
            proj = [_dot(location, cdir) + v0, _dot(location, cdir) + v1]
        return {
            "radius": float(cyl.Radius()), "dir": cdir, "axis_index": axis_index,
            "foot": _axis_foot(location, cdir), "sweep_deg": math.degrees(abs(u1 - u0)),
            "pmin": min(proj), "pmax": max(proj), "concave": bool(concave), "area": float(face.Area()),
        }
    except Exception:
        return None


def _torus_face(face, BRepAdaptor_Surface):
    try:
        ad = BRepAdaptor_Surface(face.wrapped)
        tor = ad.Torus()
        v0, v1 = ad.FirstVParameter(), ad.LastVParameter()
        return {"kind": "toroidal", "radius": _round(tor.MinorRadius()), "major_radius": _round(tor.MajorRadius()),
                "sweep_deg": _round(math.degrees(abs(v1 - v0)), 1), "area": _round(face.Area(), 2)}
    except Exception:
        return None


def _extent_along(bbox, direction):
    corners = itertools.product((bbox[0], bbox[3]), (bbox[1], bbox[4]), (bbox[2], bbox[5]))
    proj = [_dot(c, direction) for c in corners]
    return max(proj) - min(proj)


def _group_cylinders(cyl_faces, bbox, extents, result):
    """Cluster cylindrical faces into coaxial same-radius groups with connected axial intervals, then
    classify each group as hole / boss / fillet / partial."""
    groups: List[Dict] = []
    for cf in sorted(cyl_faces, key=lambda c: c["pmin"]):
        placed = False
        for g in groups:
            if g["concave"] != cf["concave"]:
                continue
            if abs(g["radius"] - cf["radius"]) > RADIUS_GROUP_TOL:
                continue
            if math.degrees(math.acos(min(1.0, abs(_dot(g["dir"], cf["dir"]))))) > DEFAULT_TOL["angle_deg"]:
                continue
            if _line_distance(g["foot"], cf["foot"], g["dir"]) > COAXIAL_TOL:
                continue
            if cf["pmin"] > g["pmax"] + COAXIAL_TOL or cf["pmax"] < g["pmin"] - COAXIAL_TOL:
                continue  # coaxial but a separate segment (e.g. holes through two walls)
            g["pmin"] = min(g["pmin"], cf["pmin"])
            g["pmax"] = max(g["pmax"], cf["pmax"])
            g["sweep_deg"] += cf["sweep_deg"]
            g["faces"] += 1
            placed = True
            break
        if not placed:
            groups.append({**cf, "faces": 1})

    dim_tol = DEFAULT_TOL["dim"]
    holes = []
    for g in groups:
        length = g["pmax"] - g["pmin"]
        extent = _extent_along(bbox, g["dir"])
        mid = 0.5 * (g["pmin"] + g["pmax"])
        center = tuple(g["foot"][i] + g["dir"][i] * mid for i in range(3))
        pos2d, pcd_center = None, None
        if g["axis_index"] is not None:
            k = g["axis_index"]
            i, j = [a for a in range(3) if a != k]
            pos2d = [center[i] - bbox[i], center[j] - bbox[j]]
            pcd_center = [0.5 * (bbox[i] + bbox[i + 3]), 0.5 * (bbox[j] + bbox[j + 3])]
        entry = {
            "radius": _round(g["radius"]), "diameter": _round(2 * g["radius"]),
            "axis": _round(g["dir"]), "axis_index": g["axis_index"], "center": _round(center),
            "length": _round(length), "body_extent_along_axis": _round(extent),
            "sweep_deg": _round(g["sweep_deg"], 1), "faces": g["faces"],
            "pos2d_from_bbox_min": _round(pos2d), "concave": g["concave"],
        }
        if pos2d is not None:
            entry["radial_from_bbox_center"] = _round(math.hypot(pos2d[0] - (pcd_center[0] - bbox[i]),
                                                                 pos2d[1] - (pcd_center[1] - bbox[j])))
        if g["concave"]:
            if g["sweep_deg"] >= HOLE_MIN_SWEEP_DEG:
                entry["through"] = bool(length >= extent - dim_tol)
                entry["through_with_counterbore"] = False
                entry["_pmin"], entry["_pmax"] = g["pmin"], g["pmax"]
                entry["_foot"] = g["foot"]
                holes.append(entry)
            elif g["sweep_deg"] <= CONCAVE_FILLET_MAX_SWEEP_DEG:
                result["fillet_faces"].append({"kind": "cylindrical-concave", "radius": entry["radius"],
                                               "sweep_deg": entry["sweep_deg"], "length": entry["length"],
                                               "axis": entry["axis"]})
            else:
                result["partial_concave_cylinders"].append(entry)
        else:
            if g["sweep_deg"] >= HOLE_MIN_SWEEP_DEG:
                result["bosses"].append(entry)
            elif g["sweep_deg"] <= FILLET_MAX_SWEEP_DEG:
                result["fillet_faces"].append({"kind": "cylindrical", "radius": entry["radius"],
                                               "sweep_deg": entry["sweep_deg"], "length": entry["length"],
                                               "axis": entry["axis"]})
            else:
                result["bosses"].append({**entry, "partial": True})

    # Coaxial pairs of different radius whose union spans the body -> counterbore.
    for a, b in itertools.combinations(holes, 2):
        if abs(a["radius"] - b["radius"]) <= RADIUS_GROUP_TOL:
            continue
        if math.degrees(math.acos(min(1.0, abs(_dot(a["axis"], b["axis"]))))) > DEFAULT_TOL["angle_deg"]:
            continue
        if _line_distance(a["_foot"], b["_foot"], a["axis"]) > COAXIAL_TOL:
            continue
        small, big = (a, b) if a["radius"] < b["radius"] else (b, a)
        touching = not (small["_pmin"] > big["_pmax"] + dim_tol or small["_pmax"] < big["_pmin"] - dim_tol)
        if not touching:
            continue
        union = max(a["_pmax"], b["_pmax"]) - min(a["_pmin"], b["_pmin"])
        spans = union >= a["body_extent_along_axis"] - dim_tol
        result["counterbores"].append({
            "through_radius": small["radius"], "cbore_radius": big["radius"], "cbore_depth": big["length"],
            "through_length": small["length"], "spans_body": bool(spans),
            "pos2d_from_bbox_min": small["pos2d_from_bbox_min"], "axis": small["axis"],
        })
        if spans:
            small["through_with_counterbore"] = True
    for h in holes:
        for key in ("_pmin", "_pmax", "_foot"):
            h.pop(key, None)
    result["holes"] = holes


def _summarize_fillets(fillet_faces):
    by_radius: Dict[str, Dict] = {}
    for f in fillet_faces:
        if f.get("radius") is None:
            continue
        key = f"{f['kind']}:{round(f['radius'], 2)}"
        entry = by_radius.setdefault(key, {"kind": f["kind"], "radius": round(f["radius"], 2), "count": 0})
        entry["count"] += 1
    return sorted(by_radius.values(), key=lambda e: (e["kind"], e["radius"]))


def compare_to_input(input_metrics: Dict, output_metrics: Dict) -> Dict:
    """Delta of a MODIFY output against its input."""
    delta: Dict = {"input_valid": bool(input_metrics.get("valid")), "output_valid": bool(output_metrics.get("valid"))}
    vi, vo = input_metrics.get("volume"), output_metrics.get("volume")
    if vi and vo is not None:
        delta["volume_change"] = _round(vo - vi)
        delta["volume_ratio"] = _round(vo / vi, 4)
    ei, eo = input_metrics.get("extents"), output_metrics.get("extents")
    if ei and eo:
        delta["extents_change"] = _round([eo[k] - ei[k] for k in range(3)])
        delta["extents_sorted_same"] = all(
            abs(a - b) <= DEFAULT_TOL["dim"] for a, b in zip(input_metrics["extents_sorted"], output_metrics["extents_sorted"]))
    hi, ho = input_metrics.get("holes", []), output_metrics.get("holes", [])
    delta["holes_count_change"] = len(ho) - len(hi)
    delta["holes_added"] = len(_unmatched(ho, hi))
    delta["holes_removed"] = len(_unmatched(hi, ho))
    delta["face_count_change"] = output_metrics.get("face_count", 0) - input_metrics.get("face_count", 0)
    return delta


def _hole_matches(a, b, tol=None):
    tol = tol or DEFAULT_TOL
    if abs(a["radius"] - b["radius"]) > tol["radius"]:
        return False
    pa, pb = a.get("pos2d_from_bbox_min"), b.get("pos2d_from_bbox_min")
    if pa is None or pb is None:
        return a.get("axis_index") == b.get("axis_index")
    return _pos_close(pa, pb, tol["dim"])


def _unmatched(candidates, pool):
    pool = list(pool)
    out = []
    for c in candidates:
        for i, p in enumerate(pool):
            if _hole_matches(c, p):
                pool.pop(i)
                break
        else:
            out.append(c)
    return out


def _pos_close(a, b, tol):
    return (abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol) or \
           (abs(a[0] - b[1]) <= tol and abs(a[1] - b[0]) <= tol)


# --------------------------------------------------------------------------- expectations

def _check(name, ok, detail):
    return {"check": name, "ok": bool(ok), "detail": detail}


def check_case(metrics: Dict, expect: Optional[Dict], input_metrics: Optional[Dict] = None,
               tol: Optional[Dict] = None) -> Dict:
    """Verdict + individual checks for one case's output metrics against its ``expect`` block."""
    tol = {**DEFAULT_TOL, **(tol or {}), **((expect or {}).get("tol") or {})}
    checks: List[Dict] = []
    expect = expect or {}
    solid_ok = bool(metrics.get("import_ok") and metrics.get("solid_count", 0) >= 1 and metrics.get("valid")
                    and metrics.get("shells_closed"))
    if not solid_ok:
        reason = metrics.get("error") or ("no solids" if not metrics.get("solid_count") else
                                          "invalid (BRepCheck)" if not metrics.get("valid") else "open shell")
        return {"verdict": VERDICT_FAIL, "checks": [_check("valid_closed_solid", False, reason)],
                "passed": 0, "total": 1, "reason": reason, "delta": None}
    checks.append(_check("valid_closed_solid", True, f"{metrics['solid_count']} solid(s), BRepCheck valid, shells closed"))

    if "solids" in expect:
        checks.append(_check("solids", metrics["solid_count"] == expect["solids"],
                             f"got {metrics['solid_count']}, want {expect['solids']}"))
    if "extents_sorted" in expect:
        got, want = metrics["extents_sorted"], sorted(expect["extents_sorted"])
        ok = all(abs(g - w) <= tol["dim"] for g, w in zip(got, want))
        checks.append(_check("extents_sorted", ok, f"got {got}, want {want} (+-{tol['dim']})"))
    if "volume" in expect:
        want = float(expect["volume"])
        ok = abs(metrics["volume"] - want) <= want * tol["volume_pct"] / 100.0
        checks.append(_check("volume", ok, f"got {metrics['volume']:.1f}, want {want:.1f} (+-{tol['volume_pct']}%)"))
    if "volume_range" in expect:
        lo, hi = expect["volume_range"]
        ok = lo <= metrics["volume"] <= hi
        checks.append(_check("volume_range", ok, f"got {metrics['volume']:.1f}, want [{lo}, {hi}]"))
    if "volume_below" in expect:
        ok = metrics["volume"] < float(expect["volume_below"]) - 0.1
        checks.append(_check("volume_below", ok, f"got {metrics['volume']:.1f}, want < {expect['volume_below']}"))
    if "planar_faces" in expect:
        checks.append(_check("planar_faces", metrics["planar_faces"] == expect["planar_faces"],
                             f"got {metrics['planar_faces']}, want {expect['planar_faces']}"))
    if "holes_total" in expect:
        checks.append(_check("holes_total", len(metrics["holes"]) == expect["holes_total"],
                             f"got {len(metrics['holes'])}, want {expect['holes_total']}"))
    for idx, group in enumerate(expect.get("holes") or []):
        checks += _check_hole_group(metrics, group, tol, idx)
    if "hole_pattern_rect" in expect:
        checks.append(_check_rect_pattern(metrics, expect["hole_pattern_rect"], tol))
    if "counterbores" in expect:
        checks += _check_counterbores(metrics, expect["counterbores"], tol)
    for idx, boss in enumerate(expect.get("bosses") or []):
        matches = [b for b in metrics["bosses"] if abs(b["radius"] - boss["radius"]) <= tol["radius"]
                   and ("length" not in boss or abs(b["length"] - boss["length"]) <= tol["dim"])]
        checks.append(_check(f"boss[{idx}] r={boss['radius']}", len(matches) >= boss.get("count", 1),
                             f"matched {len(matches)} of {[(b['radius'], b['length']) for b in metrics['bosses']]}"))
    if "fillets" in expect:
        fx = expect["fillets"]
        # A block without `radius` means "fillets of any radius" (vague prompts, e.g. "rounded corners").
        faces = [f for f in metrics["fillet_faces"] if f.get("radius") is not None
                 and ("radius" not in fx or abs(f["radius"] - fx["radius"]) <= tol["radius"])
                 and (fx.get("kind") is None or f["kind"] == fx["kind"])]
        want = fx.get("count")
        if want is None:
            ok = len(faces) >= fx.get("min_count", 1)
        else:
            ok = len(faces) == want
        label = f"fillets r={fx['radius']}" if "radius" in fx else "fillets (any radius)"
        checks.append(_check(label, ok,
                             f"got {len(faces)} face(s) (all fillet faces: {metrics['fillets']}), want {want or ('>=' + str(fx.get('min_count', 1)))}"))
    if "no_fillets" in expect and expect["no_fillets"]:
        checks.append(_check("no_fillets", not metrics["fillet_faces"], f"fillet faces: {metrics['fillets']}"))
    for r in expect.get("fillets_absent") or []:
        present = [f for f in metrics["fillet_faces"] if f.get("radius") is not None and abs(f["radius"] - r) <= tol["radius"]]
        checks.append(_check(f"fillets_absent r={r}", not present, f"{len(present)} face(s) still at r={r}"))
    if "partial_concave" in expect:
        pc = expect["partial_concave"]
        sel = [p for p in metrics["partial_concave_cylinders"] if abs(p["radius"] - pc["radius"]) <= tol["radius"]]
        checks.append(_check(f"partial_concave r={pc['radius']}", len(sel) >= pc.get("min_count", 1),
                             f"got {len(sel)} (all partial concave: {[(p['radius'], p['sweep_deg']) for p in metrics['partial_concave_cylinders']]}), want >= {pc.get('min_count', 1)}"))
    if "largest_solid_volume" in expect:  # multi-body cases (keep the largest body)
        vols = sorted((d.get("volume") or 0.0) for d in metrics.get("solids_detail") or [])
        got = vols[-1] if vols else metrics["volume"]
        want = float(expect["largest_solid_volume"])
        ok = abs(got - want) <= want * tol["volume_pct"] / 100.0
        checks.append(_check("largest_solid_volume", ok, f"got {got:.1f}, want {want:.1f} (+-{tol['volume_pct']}%); per-solid {vols}"))
    if "solids_extents_sorted" in expect:  # every listed body present by its sorted extents
        got = [d.get("extents_sorted") for d in metrics.get("solids_detail") or [] if d.get("extents_sorted")]
        pool = list(got); missing = []
        for w in expect["solids_extents_sorted"]:
            for i, g in enumerate(pool):
                if all(abs(a - b) <= tol["dim"] for a, b in zip(g, sorted(w))):
                    pool.pop(i); break
            else:
                missing.append(w)
        checks.append(_check("solids_extents_sorted", not missing and not pool, f"got {got}, want {expect['solids_extents_sorted']}; missing {missing}, extra {pool}"))
    if "min_extent_range" in expect:  # loose thickness band for vague prompts
        lo, hi = expect["min_extent_range"]; got = metrics["extents_sorted"][0]
        checks.append(_check(f"min_extent in [{lo}, {hi}]", lo <= got <= hi, f"got {got}"))
    if "min_extent" in expect:
        got = metrics["extents_sorted"][0]
        checks.append(_check(f"min_extent={expect['min_extent']}", abs(got - float(expect["min_extent"])) <= tol["dim"], f"got {got}"))
    if "max_extent" in expect:
        got = metrics["extents_sorted"][-1]
        checks.append(_check(f"max_extent={expect['max_extent']}", abs(got - float(expect["max_extent"])) <= tol["dim"], f"got {got}"))

    delta = None
    if "delta" in expect:
        if not input_metrics or not input_metrics.get("valid"):
            checks.append(_check("delta", False, "input metrics unavailable"))
        else:
            delta = compare_to_input(input_metrics, metrics)
            checks += _check_delta(delta, expect["delta"], input_metrics, metrics, tol)

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    result = {"checks": checks, "passed": passed, "total": total, "delta": delta}
    return recompute_verdict(result)


def recompute_verdict(result: Dict) -> Dict:
    """(Re)derive verdict/passed/total/reason from ``result['checks']``; used after a caller appends
    extra checks (e.g. the harness's report-text check). The first check is the valid-solid gate."""
    checks = result["checks"]
    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    if not checks or not checks[0]["ok"]:
        verdict = VERDICT_FAIL
    elif passed == total:
        verdict = VERDICT_PASS
    elif passed >= math.ceil(total / 2.0):
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_FAIL
    failed = [f"{c['check']}: {c['detail']}" for c in checks if not c["ok"]]
    result.update({"verdict": verdict, "passed": passed, "total": total, "reason": "; ".join(failed)})
    return result


def _select_holes(metrics, group, tol):
    holes = metrics["holes"]
    if "radius" in group:
        holes = [h for h in holes if abs(h["radius"] - group["radius"]) <= tol["radius"]]
    if "radius_range" in group:
        lo, hi = group["radius_range"]
        holes = [h for h in holes if lo <= h["radius"] <= hi]
    if "axis_index" in group:
        holes = [h for h in holes if h.get("axis_index") == group["axis_index"]]
    return holes


def _check_hole_group(metrics, group, tol, idx):
    label = f"holes[{idx}]" + (f" r={group['radius']}" if "radius" in group else "")
    holes = _select_holes(metrics, group, tol)
    out = []
    if "count" in group:
        out.append(_check(f"{label} count", len(holes) == group["count"],
                          f"got {len(holes)} (all holes: {[(h['radius'], h['sweep_deg']) for h in metrics['holes']]}), want {group['count']}"))
    if group.get("through") is not None and holes:
        thr = [h for h in holes if h["through"] or h.get("through_with_counterbore")]
        want_all = bool(group["through"])
        ok = (len(thr) == len(holes)) if want_all else (len(thr) == 0)
        out.append(_check(f"{label} through={want_all}", ok,
                          f"{len(thr)} of {len(holes)} through (lengths {[h['length'] for h in holes]}, body extent {[h['body_extent_along_axis'] for h in holes]})"))
    if "positions_from_bbox_min" in group:
        want = [list(p) for p in group["positions_from_bbox_min"]]
        got = [h["pos2d_from_bbox_min"] for h in holes if h.get("pos2d_from_bbox_min") is not None]
        pool = list(got)
        missing = []
        for w in want:
            for i, g in enumerate(pool):
                if _pos_close(w, g, tol["dim"]):
                    pool.pop(i)
                    break
            else:
                missing.append(w)
        ok = not missing and not pool
        out.append(_check(f"{label} positions", ok,
                          f"got {[[round(x, 2) for x in g] for g in got]}, want {want}; missing {missing}, extra {[[round(x, 2) for x in g] for g in pool]}"))
    if "pcd_radius" in group and holes:
        rs = [h.get("radial_from_bbox_center") for h in holes]
        ok = all(r is not None and abs(r - group["pcd_radius"]) <= tol["dim"] for r in rs)
        out.append(_check(f"{label} pcd_radius={group['pcd_radius']}", ok, f"radial distances {rs}"))
    if "length" in group and holes:
        ok = all(abs(h["length"] - group["length"]) <= tol["dim"] for h in holes)
        out.append(_check(f"{label} length={group['length']}", ok, f"lengths {[h['length'] for h in holes]}"))
    return out


def _check_rect_pattern(metrics, spec, tol):
    holes = _select_holes(metrics, spec, tol)
    a, b = sorted(spec["sides"])
    ptol = spec.get("tol", tol["dim"])
    pts = [h["pos2d_from_bbox_min"] for h in holes if h.get("pos2d_from_bbox_min") is not None]
    if len(pts) != spec.get("count", 4):
        return _check("hole_pattern_rect", False, f"{len(pts)} candidate hole(s) with 2D positions, want {spec.get('count', 4)}")
    dists = sorted(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in itertools.combinations(pts, 2))
    want = sorted([a, a, b, b, math.hypot(a, b), math.hypot(a, b)])
    ok = all(abs(d - w) <= ptol * (1.5 if i >= 4 else 1.0) for i, (d, w) in enumerate(zip(dists, want)))
    return _check("hole_pattern_rect", ok, f"pairwise distances {[round(d, 2) for d in dists]}, want {[round(w, 2) for w in want]} (+-{ptol})")


def _check_counterbores(metrics, spec, tol):
    out = []
    cbs = metrics["counterbores"]
    sel = [c for c in cbs
           if ("through_radius" not in spec or abs(c["through_radius"] - spec["through_radius"]) <= tol["radius"])
           and ("cbore_radius" not in spec or abs(c["cbore_radius"] - spec["cbore_radius"]) <= tol["radius"])]
    out.append(_check("counterbores count", len(sel) == spec.get("count", len(sel)),
                      f"got {len(sel)} matching of {[(c['through_radius'], c['cbore_radius'], c['cbore_depth']) for c in cbs]}, want {spec.get('count')}"))
    if sel and spec.get("spans_body", True):
        out.append(_check("counterbores span body", all(c["spans_body"] for c in sel),
                          f"spans {[c['spans_body'] for c in sel]}"))
    if sel and "cbore_depth" in spec:
        ok = all(abs(c["cbore_depth"] - spec["cbore_depth"]) <= tol["dim"] for c in sel)
        out.append(_check(f"counterbores depth={spec['cbore_depth']}", ok, f"depths {[c['cbore_depth'] for c in sel]}"))
    return out


def _check_delta(delta, spec, input_metrics, metrics, tol):
    out = []
    if spec.get("identical"):
        vol_ok = abs(delta.get("volume_change", 1e9)) <= abs(input_metrics["volume"]) * 1e-4
        ext_ok = all(abs(x) <= 0.01 for x in delta.get("extents_change", [1e9]))
        holes_ok = delta["holes_count_change"] == 0 and delta["holes_added"] == 0
        faces_ok = delta["face_count_change"] == 0
        out.append(_check("delta identical", vol_ok and ext_ok and holes_ok and faces_ok,
                          f"volume_change {delta.get('volume_change')}, extents_change {delta.get('extents_change')}, "
                          f"holes +{delta['holes_added']}/-{delta['holes_removed']}, faces {delta['face_count_change']:+d}"))
    if "volume_ratio" in spec:
        want = float(spec["volume_ratio"])
        got = delta.get("volume_ratio")
        ok = got is not None and abs(got - want) <= want * tol["volume_pct"] / 100.0
        out.append(_check(f"delta volume_ratio={want}", ok, f"got {got}"))
    if "volume_change" in spec:
        want = float(spec["volume_change"])
        vtol = float(spec.get("volume_change_tol", max(abs(want) * 0.05, 25.0)))
        got = delta.get("volume_change")
        ok = got is not None and abs(got - want) <= vtol
        out.append(_check(f"delta volume_change={want}", ok, f"got {got} (+-{vtol})"))
    if "volume_decreases" in spec:
        got = delta.get("volume_change")
        ok = got is not None and (got < -0.1) == bool(spec["volume_decreases"])
        out.append(_check("delta volume_decreases", ok, f"volume_change {got}"))
    if "extents_sorted_same" in spec:
        ok = delta.get("extents_sorted_same") == bool(spec["extents_sorted_same"])
        out.append(_check("delta extents_sorted_same", ok, f"extents_change {delta.get('extents_change')}"))
    if "extent_axis_change" in spec:  # {"axis": 2, "to": 15}
        k, to = spec["extent_axis_change"]["axis"], float(spec["extent_axis_change"]["to"])
        got = metrics["extents"][k]
        ok = abs(got - to) <= tol["dim"]
        out.append(_check(f"delta extent[{k}]={to}", ok, f"got {got} (input {input_metrics['extents'][k]})"))
    if "max_extent" in spec:
        got = metrics["extents_sorted"][-1]
        ok = abs(got - float(spec["max_extent"])) <= tol["dim"]
        out.append(_check(f"delta max_extent={spec['max_extent']}", ok, f"got {got}"))
    if "min_extent" in spec:
        got = metrics["extents_sorted"][0]
        ok = abs(got - float(spec["min_extent"])) <= tol["dim"]
        out.append(_check(f"delta min_extent={spec['min_extent']}", ok, f"got {got} (input {input_metrics['extents_sorted'][0]})"))
    if "holes_added" in spec:
        out.append(_check(f"delta holes_added={spec['holes_added']}", delta["holes_added"] == spec["holes_added"],
                          f"added {delta['holes_added']}, removed {delta['holes_removed']}"))
    if "holes_removed" in spec:
        out.append(_check(f"delta holes_removed={spec['holes_removed']}", delta["holes_removed"] == spec["holes_removed"],
                          f"added {delta['holes_added']}, removed {delta['holes_removed']}"))
    if "holes_kept" in spec:  # input holes that must survive unchanged
        out.append(_check("delta holes_kept", delta["holes_removed"] == 0 and delta["holes_count_change"] >= 0,
                          f"removed {delta['holes_removed']}"))
    return out


def evaluate(step_path: str, expect: Optional[Dict], input_path: Optional[str] = None) -> Dict:
    """analyze + check in one call; the dict is what ``<case>.quality.json`` holds."""
    metrics = analyze_step(step_path)
    input_metrics = analyze_step(input_path) if input_path else None
    check = check_case(metrics, expect, input_metrics)
    return {"metrics": metrics, "input_metrics": input_metrics, "expect": expect, **check}


def key_metrics_text(metrics: Dict) -> str:
    if not metrics.get("import_ok"):
        return f"no geometry ({metrics.get('error', '')[:60]})"
    if not metrics.get("solid_count"):
        return "no solids"
    parts = [f"{metrics['solid_count']}s", f"ext {'x'.join(str(round(e, 1)) for e in metrics['extents_sorted'])}",
             f"V {metrics['volume']:.0f}"]
    if metrics["holes"]:
        radii: Dict[str, int] = {}
        for h in metrics["holes"]:
            k = f"Ø{round(h['diameter'], 2)}{'' if h['through'] or h.get('through_with_counterbore') else 'b'}"
            radii[k] = radii.get(k, 0) + 1
        parts.append("holes " + ", ".join(f"{n}×{k}" for k, n in radii.items()))
    if metrics["counterbores"]:
        parts.append(f"cbore {len(metrics['counterbores'])}")
    if metrics["fillets"]:
        parts.append("fillets " + ", ".join(f"{f['count']}×R{f['radius']}" for f in metrics["fillets"]))
    if metrics["bosses"]:
        parts.append("bosses " + ", ".join(f"R{b['radius']}" for b in metrics["bosses"][:4]))
    if not metrics.get("valid"):
        parts.append("INVALID")
    return "; ".join(parts)


def markdown_row(case_id: str, mode: str, result: Dict, attempts=None, wall_s=None, extra: str = "") -> str:
    metrics = result.get("metrics") or {}
    reason = (result.get("reason") or "")[:220].replace("|", "\\|")
    km = key_metrics_text(metrics).replace("|", "\\|")
    return (f"| {case_id} | {mode} | **{result.get('verdict')}** ({result.get('passed')}/{result.get('total')}) | "
            f"{'' if attempts is None else attempts} | {'' if wall_s is None else round(wall_s)} | {km} | {reason} | {extra} |")


MARKDOWN_HEADER = ("| case | mode | verdict (checks) | attempts | wall s | key metrics | failed checks | notes |\n"
                   "|---|---|---|---|---|---|---|---|")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step")
    parser.add_argument("--expect", help="JSON file (or inline JSON) with the expect block")
    parser.add_argument("--input", help="input STEP for modify deltas")
    parser.add_argument("--out", help="write the result JSON here")
    args = parser.parse_args(argv)
    expect = None
    if args.expect:
        expect = json.load(open(args.expect)) if os.path.isfile(args.expect) else json.loads(args.expect)
    result = evaluate(args.step, expect, args.input)
    text = json.dumps(result, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0 if result["verdict"] == VERDICT_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
