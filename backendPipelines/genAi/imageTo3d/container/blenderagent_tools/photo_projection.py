# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The arithmetic behind projecting a photograph onto a mesh that already carries its own colour: which
vertices the photograph saw, how much of it each takes, and how the captured colour is prepared to
continue beside it on the surfaces the photograph never reached.

Plain numpy, no Blender, so it is testable without one; ``bl_project_photo.py`` is the Blender side that
reads the mesh and writes the material. A reconstruction's own colour is smooth: neighbouring vertices
differ by four or five steps of 255. Replacing all of it with one orthographic photograph, and the rest
with a flat row colour completed by an image model, gave surfaces where they differed by ten to fourteen,
and a blind judge marked every one of those down. So the photograph goes only where it was actually
taken -- faces that squarely face the camera, pass the depth test and land inside the cutout -- and fades
into the captured colour over a band of facing angle rather than ending at a seam.
"""

import numpy as np

# Rec. 709 luma weights, applied to linear RGB.
LUMA = np.array([0.2126, 0.7152, 0.0722])


def srgb_to_linear(rgb):
    rgb = np.asarray(rgb, dtype=float)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


def visible_vertices(cam_xy, depth, zbuf, epsilon):
    """Which vertices the camera sees: each is within ``epsilon`` of the depth buffer at its own pixel.

    ``cam_xy`` is per vertex in the camera frame (x right, y up, 0..1) and ``zbuf`` the buffer
    ``view_fill.rasterise_view`` built from the same frame. A vertex behind another surface reads that
    surface's depth there and is not seen.
    """
    height, width = zbuf.shape
    col = np.clip(np.rint(cam_xy[:, 0] * width - 0.5), 0, width - 1).astype(int)
    row = np.clip(np.rint((1.0 - cam_xy[:, 1]) * height - 0.5), 0, height - 1).astype(int)
    return depth <= zbuf[row, col] + epsilon


def photo_weights(facing, visible, on_photo, threshold, band):
    """How much of the photograph each vertex takes, 0..1: nothing below ``threshold`` facing, all of it
    once ``band`` past it, a smooth step between; and nothing at all where the camera did not see the vertex
    or where it lands outside the cutout, so a body wider than its photograph keeps its own colour at the
    edge instead of the cutout's background."""
    t = np.clip((np.asarray(facing, dtype=float) - threshold) / max(band, 1e-9), 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    return smooth * np.asarray(visible, dtype=float) * np.asarray(on_photo, dtype=float)


def flood_dark(colours, edges, threshold, max_rounds=64):
    """Near-black vertices take the mean colour of their lit neighbours, round by round until none is left
    or nothing lit borders the rest; returns the new colours and how many were filled.

    A reconstruction leaves the surfaces it never saw black; kept as they are they would print as holes in
    the colour. ``colours`` are linear RGB rows, ``edges`` vertex index pairs, ``threshold`` a luminance.
    """
    colours = np.array(colours, dtype=float, copy=True)
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    dark = colours @ LUMA < threshold
    filled = 0
    for _ in range(max_rounds):
        if not dark.any():
            break
        a, b = edges[:, 0], edges[:, 1]
        total = np.zeros_like(colours)
        count = np.zeros(len(colours))
        for source, target in ((a, b), (b, a)):
            take = dark[target] & ~dark[source]
            np.add.at(total, target[take], colours[source[take]])
            np.add.at(count, target[take], 1.0)
        reached = dark & (count > 0)
        if not reached.any():
            break
        colours[reached] = total[reached] / count[reached, None]
        dark[reached] = False
        filled += int(reached.sum())
    return colours, filled


def extend_edge_colours(rgb, alpha, rounds):
    """The cutout's edge colour carried outward into its transparent surround, one ring of pixels per round,
    so a face at the outline of a body slightly wider than its photograph samples the subject's edge and not
    the background. Returns a new image."""
    rgb = np.array(rgb, dtype=float, copy=True)
    filled = np.array(alpha, dtype=bool, copy=True)
    for _ in range(rounds):
        total = np.zeros_like(rgb)
        count = np.zeros(filled.shape)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            source = np.roll(filled, shift, axis=axis)
            colour = np.roll(rgb, shift, axis=axis)
            # np.roll wraps round; the row or column that came from the far side is not a neighbour.
            edge = 0 if shift == 1 else -1
            if axis == 0:
                source[edge, :] = False
            else:
                source[:, edge] = False
            take = source & ~filled
            total[take] += colour[take]
            count[take] += 1
        reached = ~filled & (count > 0)
        if not reached.any():
            break
        rgb[reached] = total[reached] / count[reached, None]
        filled |= reached
    return rgb


def shading_gains(colours, seen, heights, bands=10, clamp=(0.5, 2.0)):
    """A luminance gain per vertex that lifts the unseen surfaces to the photographed side's brightness.

    A reconstruction's colour carries the photograph's lighting: the side the camera saw is lit, the far side
    and the grazing sides are in its shadow (a cat read 71, 62 and 56 in luminance). Kept as captured, the
    unseen surfaces ship darker than the photographed front they meet. Per height band, the unseen vertices'
    mean luminance is brought to the seen vertices' mean in that band, held to ``clamp`` and interpolated
    with height; seen vertices keep a gain of one. A band lacking either side takes the overall ratio, and a
    mesh that is all seen or all unseen is left alone.
    """
    colours = np.asarray(colours, dtype=float).reshape(-1, 3)
    seen = np.asarray(seen, dtype=bool)
    heights = np.asarray(heights, dtype=float)
    gains = np.ones(len(colours))
    if seen.all() or not seen.any():
        return gains
    luminance = colours @ LUMA

    def ratio(lit, dark):
        return float(np.clip(lit.mean() / max(dark.mean(), 1e-6), *clamp))

    overall = ratio(luminance[seen], luminance[~seen])
    edges = np.linspace(float(heights.min()), float(heights.max()), bands + 1)
    centres = (edges[:-1] + edges[1:]) / 2
    per_band = np.full(bands, overall)
    for index, (bottom, top) in enumerate(zip(edges[:-1], edges[1:])):
        inside = (heights >= bottom) & (heights <= top)
        if (inside & seen).sum() >= 3 and (inside & ~seen).sum() >= 3:
            per_band[index] = ratio(luminance[inside & seen], luminance[inside & ~seen])
    gains[~seen] = np.interp(heights[~seen], centres, per_band)
    return gains


def band_gains(captured, photo, heights_seen, heights_all, bands=10, clamp=(0.5, 2.0)):
    """A per-channel gain per vertex that carries the captured colour to the photograph's tone.

    Where the photograph saw the surface both colours are known, so their ratio per height band is how far
    the reconstruction's tone sits from the photograph's -- usually darker. The ratio is taken band by band
    from the top, held to ``clamp`` so a white blaze beside a bay coat does not double the coat, and
    interpolated with height so the kept surfaces change tone smoothly. A band the photograph did not reach
    takes the overall ratio; with nothing seen at all the gain is one.
    """
    heights_all = np.asarray(heights_all, dtype=float)
    captured = np.asarray(captured, dtype=float).reshape(-1, 3)
    photo = np.asarray(photo, dtype=float).reshape(-1, 3)
    heights_seen = np.asarray(heights_seen, dtype=float)
    if len(captured) == 0:
        return np.ones((len(heights_all), 3))

    def ratio(cap, pho):
        return np.clip(pho.mean(axis=0) / np.maximum(cap.mean(axis=0), 1e-4), *clamp)

    overall = ratio(captured, photo)
    lo, hi = float(heights_all.min()), float(heights_all.max())
    edges = np.linspace(lo, hi, bands + 1)
    centres = (edges[:-1] + edges[1:]) / 2
    per_band = np.tile(overall, (bands, 1))
    for index, (bottom, top) in enumerate(zip(edges[:-1], edges[1:])):
        inside = (heights_seen >= bottom) & (heights_seen <= top)
        in_band = ((heights_all >= bottom) & (heights_all <= top)).sum()
        # A band the photograph barely reached -- under a fiftieth of its vertices -- keeps the overall ratio.
        if inside.sum() >= max(3, 0.02 * in_band):
            per_band[index] = ratio(captured[inside], photo[inside])
    gains = np.empty((len(heights_all), 3))
    for channel in range(3):
        gains[:, channel] = np.interp(heights_all, centres, per_band[:, channel])
    return gains


def vertex_normals(vertices, triangles):
    """Area-weighted unit normals per vertex, from the world-space triangles."""
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    weighted = np.cross(b - a, c - a)
    normals = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(normals, triangles[:, corner], weighted)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, length, out=np.zeros_like(normals), where=length > 1e-12)
