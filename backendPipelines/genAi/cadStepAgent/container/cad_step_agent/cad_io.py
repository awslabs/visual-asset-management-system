# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""STEP file inspection through CadQuery (OpenCascade).

CadQuery is imported lazily so the pure parts of the container (naming, reporting, sandbox environment)
import and test without the OCP wheel; ``inspect_step`` is the only entry point that needs it.
"""

from dataclasses import asdict, dataclass
from typing import List, Optional

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

    def to_dict(self):
        data = asdict(self)
        if data["volume_mm3"] is not None:
            data["volume_mm3"] = round(data["volume_mm3"], 3)
        if data["bounding_box_mm"] is not None:
            data["bounding_box_mm"] = [round(v, 3) for v in data["bounding_box_mm"]]
        return data

    def describe(self):
        if not self.valid:
            return f"invalid STEP: {self.error}"
        bbox = ""
        if self.bounding_box_mm:
            xmin, ymin, zmin, xmax, ymax, zmax = self.bounding_box_mm
            bbox = f" size {xmax - xmin:.2f} x {ymax - ymin:.2f} x {zmax - zmin:.2f} mm"
        volume = f" volume {self.volume_mm3:.1f} mm^3" if self.volume_mm3 is not None else ""
        return f"{self.solid_count} solid(s), {self.face_count} faces, {self.edge_count} edges{bbox}{volume}"


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
        return StepSummary(
            valid=True,
            solid_count=len(solids),
            face_count=len(faces),
            edge_count=len(edges),
            bounding_box_mm=[bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax],
            volume_mm3=float(volume),
        )
    except Exception as exc:  # the file is caller data; any failure is a validation outcome
        return StepSummary(valid=False, error=str(exc)[:500])
