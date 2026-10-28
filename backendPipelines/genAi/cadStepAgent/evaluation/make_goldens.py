# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build the reference goldens of the generate cases G1-G9 with CadQuery.

Each builder is the textbook CadQuery construction of its corpus case, reviewed against the case's ``expect``
block (``stp_quality.check_case`` scores every golden PASS; ``tests/test_cadstep_eval_goldens.py`` pins it). A
golden that already exists is left as it is, so a committed golden keeps its bytes; delete one to rebuild it.

    python make_goldens.py [--out goldens] [--only g2-counterbored-plate.step,...]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _g1_plate(cq):
    return (cq.Workplane("XY").box(100, 60, 10).faces(">Z").workplane()
            .pushPoints([(-42, -22), (42, -22), (-42, 22), (42, 22)]).hole(4.5))


def _g2_counterbored_plate(cq):
    return (cq.Workplane("XY").box(100, 60, 10).faces(">Z").workplane()
            .pushPoints([(-40, -20), (40, -20), (-40, 20), (40, 20)]).cboreHole(4.5, 8.0, 4.0))


def _g3_l_bracket(cq):
    base = cq.Workplane("XY").box(50, 40, 4, centered=False)
    wall = cq.Workplane("XY").box(4, 40, 50, centered=False)
    bracket = base.union(wall)
    slots = (cq.Workplane("XY").workplane(offset=-1).center(27, 20)
             .pushPoints([(0, -6), (0, 6)]).slot2D(20, 6, 0).extrude(6))
    return bracket.cut(slots)


def _g4_hub_keyway(cq):
    hub = cq.Workplane("XY").circle(20).circle(6).extrude(30)
    key = cq.Workplane("XY").center(6 + 2.5 - 4.25, 0).rect(8.5, 4).extrude(30)
    return hub.cut(key)


def _g5_flange_disc(cq):
    return (cq.Workplane("XY").circle(60).extrude(8).faces(">Z").workplane()
            .hole(30).faces(">Z").workplane().polarArray(50, 0, 360, 6).hole(6.6))


def _g6_stepped_shaft(cq):
    shaft = cq.Workplane("YZ").circle(10).extrude(30)
    shaft = shaft.union(cq.Workplane("YZ").workplane(offset=30).circle(8).extrude(40))
    return shaft.union(cq.Workplane("YZ").workplane(offset=70).circle(6).extrude(30))


def _g7_filleted_box(cq):
    return cq.Workplane("XY").box(60, 40, 20).edges("|Z").fillet(3)


def _g8_jetson_carrier_plate(cq):
    # The developer kit's carrier board is 100 x 80 mm with an 86 x 58 mm mounting-hole pattern centred on it.
    return (cq.Workplane("XY").box(110, 90, 4).faces(">Z").workplane()
            .rect(86, 58, forConstruction=True).vertices().hole(3.2))


def _g9_inch_plate(cq):
    inch = 25.4
    return cq.Workplane("XY").box(2 * inch, 1 * inch, 0.25 * inch).faces(">Z").workplane().hole(0.25 * inch)


GOLDENS = {
    "g1-plate.step": _g1_plate,
    "g2-counterbored-plate.step": _g2_counterbored_plate,
    "g3-l-bracket.step": _g3_l_bracket,
    "g4-hub-keyway.step": _g4_hub_keyway,
    "g5-flange-disc.step": _g5_flange_disc,
    "g6-stepped-shaft.step": _g6_stepped_shaft,
    "g7-filleted-box.step": _g7_filleted_box,
    "g8-jetson-carrier-plate.step": _g8_jetson_carrier_plate,
    "g9-inch-plate.step": _g9_inch_plate,
}


def build(out_dir, only=None):
    import cadquery as cq  # noqa: WPS433 - the builders need the kernel; the module imports without it

    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name, builder in GOLDENS.items():
        path = os.path.join(out_dir, name)
        if (only and name not in only) or os.path.isfile(path):
            continue
        cq.exporters.export(builder(cq), path, exportType="STEP")
        made.append(name)
    return made


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=os.path.join(HERE, "goldens"))
    parser.add_argument("--only", help="comma-separated golden file names")
    args = parser.parse_args(argv)
    made = build(args.out, set(args.only.split(",")) if args.only else None)
    print("built: " + (", ".join(made) if made else "nothing (every golden exists)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
