# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generates the plate fixture: a plain 40 x 40 x 10 mm plate as STEP AP214 (no holes).

    python make_plate_fixture.py plate-40x40x10.step
"""
import sys

import cadquery as cq

out = sys.argv[1]
plate = cq.Workplane("XY").box(40, 40, 10)
cq.exporters.export(plate, out, exportType="STEP")
back = cq.importers.importStep(out)
bb = back.val().BoundingBox()
print(f"solids={len(back.solids().vals())} bbox=({bb.xlen:.1f},{bb.ylen:.1f},{bb.zlen:.1f}) volume={back.val().Volume():.0f}")
