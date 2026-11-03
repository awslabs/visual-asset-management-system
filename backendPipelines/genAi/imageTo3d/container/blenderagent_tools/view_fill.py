# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The arithmetic behind filling a texture from a view: what a camera sees of the mesh, and where each pixel
of a picture taken from that camera lands in the atlas.

Plain numpy, no Blender, so the geometry is testable without one; ``bl_view_fill.py`` is the Blender side
that renders, reads the mesh and writes the images. An inpainting model completes a PICTURE. A packed uv
layout shows it islands with nothing around them to continue, and the first live request on one came back
a uniform grey-brown; a render from the back is the subject seen from behind, which it can complete. This
carries the completed picture back onto the surface the camera saw.
"""

import math

import numpy as np

# Azimuth/elevation per named view. Front looks along +Y at an object whose front faces -Y, which is
# Blender's own Front view (numpad 1). Shared with the renderer, so a fill view is a render view.
VIEWS = {
    "front": (0, 0),
    "left": (90, 0),
    "back": (180, 0),
    "right": (270, 0),
    "top": (0, 89),
    "threequarter": (35, 25),
}


def camera_direction(azimuth_deg, elevation_deg):
    """The unit vector from the scene's centre TOWARDS a camera at this azimuth and elevation."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    return np.array((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)), dtype=float)


def face_normals(vertices, triangles):
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    normals = np.cross(b - a, c - a)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, length, out=np.zeros_like(normals), where=length > 1e-12)


def facing(normals, azimuth_deg, elevation_deg):
    """How squarely each face faces a camera at this view: 1 head-on, 0 edge-on, negative turned away."""
    return normals @ camera_direction(azimuth_deg, elevation_deg)


def _pixel_coords(cam_xy, width, height):
    """Camera frame coordinates (x right, y up, 0..1) to pixel coordinates (column, row), centres on integers."""
    return cam_xy[:, 0] * width - 0.5, (1.0 - cam_xy[:, 1]) * height - 0.5


def _footprint(fx, fy, width, height):
    """A triangle's pixel bounding box and the barycentric weights of the pixels inside it, or None."""
    area = (fx[1] - fx[0]) * (fy[2] - fy[0]) - (fx[2] - fx[0]) * (fy[1] - fy[0])
    if not (np.isfinite(fx).all() and np.isfinite(fy).all()) or abs(area) < 1e-12:
        return None
    x0, x1 = int(max(np.floor(fx.min()), 0)), int(min(np.ceil(fx.max()) + 1, width))
    y0, y1 = int(max(np.floor(fy.min()), 0)), int(min(np.ceil(fy.max()) + 1, height))
    if x0 >= x1 or y0 >= y1:
        return None
    gx, gy = np.meshgrid(np.arange(x0, x1, dtype=float), np.arange(y0, y1, dtype=float))
    w1 = ((gx - fx[0]) * (fy[2] - fy[0]) - (gy - fy[0]) * (fx[2] - fx[0])) / area
    w2 = ((gy - fy[0]) * (fx[1] - fx[0]) - (gx - fx[0]) * (fy[1] - fy[0])) / area
    w0 = 1.0 - w1 - w2
    # Half a texel of slack on each edge, so a thin triangle does not leave a hole along its boundary.
    slack = 0.5 / max(abs(area) ** 0.5, 1.0)
    inside = (w0 >= -slack) & (w1 >= -slack) & (w2 >= -slack)
    if not inside.any():
        return None
    return (x0, x1, y0, y1), inside, (w0, w1, w2)


def rasterise_view(cam_xy, depth, triangles, keep, width, height):
    """A depth buffer and a triangle-id buffer of the kept triangles, as the camera sees them.

    ``cam_xy`` and ``depth`` are per vertex in the camera's frame; the nearest kept triangle wins each pixel.
    A pixel nothing covers holds ``inf`` and -1.
    """
    zbuf = np.full((height, width), np.inf, dtype=float)
    ids = np.full((height, width), -1, dtype=np.int64)
    px, py = _pixel_coords(cam_xy, width, height)
    for number in np.nonzero(keep)[0]:
        tri = triangles[number]
        footprint = _footprint(px[tri], py[tri], width, height)
        if footprint is None:
            continue
        (x0, x1, y0, y1), inside, (w0, w1, w2) = footprint
        fz = depth[tri]
        z = w0 * fz[0] + w1 * fz[1] + w2 * fz[2]
        window = zbuf[y0:y1, x0:x1]
        nearer = inside & (z < window)
        window[nearer] = z[nearer]
        ids[y0:y1, x0:x1][nearer] = number
    return zbuf, ids


def dilate(mask, steps):
    for _ in range(steps):
        padded = np.pad(mask, 1)
        mask = (padded[1:-1, 1:-1] | padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:])
    return mask


def view_mask(ids, eligible, dilation=2, edge_margin=0):
    """White where a pixel's visible triangle is one to fill, grown a little so the fill meets what it joins,
    but kept ``edge_margin`` pixels clear of the background: the model then reads the figure's outline as
    fixed and cannot redraw the silhouette inside the mask, which it did when the mask ran to the edge."""
    mask = (ids >= 0) & eligible[np.maximum(ids, 0)]
    if dilation:
        mask = dilate(mask, dilation)
    if edge_margin:
        mask &= ~dilate(ids < 0, edge_margin)
    return mask


def view_bit(name):
    """The bit a view occupies in a per-face record of the views already tried on it."""
    return 1 << list(VIEWS).index(name)


def _bilinear(image, px, py):
    """Sample an (H, W, C) image at fractional pixel coordinates, clamped at the border."""
    h, w = image.shape[:2]
    x = np.clip(px, 0, w - 1)
    y = np.clip(py, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    return (image[y0, x0] * (1 - fx) * (1 - fy) + image[y0, x1] * fx * (1 - fy)
            + image[y1, x0] * (1 - fx) * fy + image[y1, x1] * fx * fy)


def bake_view(atlas, tri_uv, triangles, select, cam_xy, depth, zbuf, image, epsilon):
    """Write the picture's pixels into the atlas texels of the selected triangles the camera saw.

    Each texel inside a selected triangle's uv footprint is carried to the camera frame by its barycentric
    weights -- exact under an orthographic camera -- and takes the picture's colour there when its depth is
    within ``epsilon`` of the depth buffer, so a texel behind an arm is left alone. ``atlas`` is (H, W, 3)
    and is written in place; returns the texels written per triangle.
    """
    height, width = atlas.shape[:2]
    img_h, img_w = image.shape[:2]
    px, py = _pixel_coords(cam_xy, img_w, img_h)
    written = np.zeros(len(triangles), dtype=np.int64)
    ax = np.clip(tri_uv[..., 0], 0.0, 1.0) * (width - 1)
    ay = (1.0 - np.clip(tri_uv[..., 1], 0.0, 1.0)) * (height - 1)
    for number in np.nonzero(select)[0]:
        footprint = _footprint(ax[number], ay[number], width, height)
        if footprint is None:
            continue
        (x0, x1, y0, y1), inside, (w0, w1, w2) = footprint
        weights = np.clip(np.stack([w0[inside], w1[inside], w2[inside]], axis=1), 0.0, 1.0)
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
        tri = triangles[number]
        sx, sy, sz = weights @ px[tri], weights @ py[tri], weights @ depth[tri]
        # A point on the frame's edge sits half a pixel outside the outermost pixel centre; it reads that pixel.
        on_picture = (sx >= -0.5) & (sx <= img_w - 0.5) & (sy >= -0.5) & (sy <= img_h - 0.5)
        col = np.clip(np.rint(sx), 0, img_w - 1).astype(int)
        row = np.clip(np.rint(sy), 0, img_h - 1).astype(int)
        visible = np.zeros_like(on_picture)
        visible[on_picture] = sz[on_picture] <= zbuf[row[on_picture], col[on_picture]] + epsilon
        if not visible.any():
            continue
        rows, cols = np.nonzero(inside)
        atlas[y0:y1, x0:x1][rows[visible], cols[visible]] = _bilinear(image, sx[visible], sy[visible])
        written[number] = int(visible.sum())
    return written


def suggest_view(normals, remaining, threshold, exclude=("front",), tried=None):
    """The named view that squarely faces the most remaining triangles, with their count; (None, 0) if none.

    ``tried`` is a per-triangle bitmask of the views already applied to it (`view_bit`). A face still
    remaining after a view was applied was hidden in that view, so it is not counted for it again --
    otherwise the same view is suggested for ever.
    """
    best, best_count = None, 0
    for name, (az, el) in VIEWS.items():
        if name in exclude:
            continue
        candidates = (facing(normals, az, el) > threshold) & remaining
        if tried is not None:
            candidates &= (tried & view_bit(name)) == 0
        count = int(candidates.sum())
        if count > best_count:
            best, best_count = name, count
    return best, best_count
