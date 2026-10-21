# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Bounds on how far one file's scene expands while it is read.

A format that instances geometry (3MF components, COLLADA ``instance_node``, STEP assembly
components) lets every level reference the same child several times. A file of a few kilobytes whose
levels each reference their child twice describes 2^depth placements, so the nesting-depth limits
alone allow billions of them, all expanded before the render budgets apply to the loaded geometry.
Each walk counts the nodes it visits and the vertices and triangles it places against one
``SceneLimit`` and stops, with one warning, once a bound is passed (ARCC BSC1: size limits on
untrusted input).
"""

from ..resources import MAX_TRIANGLES
from ..utils.logging import get_logger

logger = get_logger()

# Scene nodes one walk visits at most. Each visit costs a few microseconds of Python, so the bound
# keeps a walk to seconds; real scenes stay well below it.
MAX_SCENE_NODES = 1_000_000

# Vertices placed per triangle of the limit. A triangle soup (no shared vertices) has three per
# triangle; vertices beyond that are geometry no triangle within the limit can use.
VERTICES_PER_TRIANGLE = 3


class SceneLimit:
    """Counts one scene walk's visited nodes and placed geometry.

    ``visit()`` counts a node and returns False once more than ``max_nodes`` nodes were visited.
    ``add()`` counts placed vertices and triangles; once the triangles pass ``max_triangles`` or the
    vertices pass ``VERTICES_PER_TRIANGLE * max_triangles``, nothing more is placed. The placement
    that crosses the bound is kept, so a single mesh larger than the limit still loads and the
    triangle budget decimates it. The first bound passed logs one warning and ``reached`` stays True.
    """

    def __init__(self, what: str, max_triangles: int = None, max_nodes: int = None):
        self.what = what
        self.max_triangles = int(max_triangles if max_triangles is not None else MAX_TRIANGLES)
        self.max_nodes = int(max_nodes if max_nodes is not None else MAX_SCENE_NODES)
        self.nodes = 0
        self.vertices = 0
        self.triangles = 0
        self.reached = False

    def _stop(self, bound: str) -> None:
        if not self.reached:
            logger.warning(f"{self.what} expands past {bound}; the rest of the scene is skipped")
        self.reached = True

    def visit(self) -> bool:
        if self.reached:
            return False
        self.nodes += 1
        if self.nodes > self.max_nodes:
            self._stop(f"{self.max_nodes:,} nodes")
        return not self.reached

    def expect(self, nodes: int) -> bool:
        """Whether a walk counted in advance at ``nodes`` nodes fits; when it does not, the walk is
        over before it starts."""
        if nodes > self.max_nodes:
            self._stop(f"{self.max_nodes:,} nodes")
        return not self.reached

    def add(self, vertices: int, triangles: int) -> None:
        self.vertices += int(vertices)
        self.triangles += int(triangles)
        if self.triangles > self.max_triangles:
            self._stop(f"{self.max_triangles:,} triangles")
        elif self.vertices > VERTICES_PER_TRIANGLE * self.max_triangles:
            self._stop(f"{VERTICES_PER_TRIANGLE * self.max_triangles:,} vertices")
