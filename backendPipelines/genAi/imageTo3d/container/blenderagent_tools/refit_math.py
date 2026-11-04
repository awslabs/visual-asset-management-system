# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The arithmetic behind refitting a reconstruction to its photograph: how much of the photograph's outline
the mesh covers, where its outline is wrong and by how much, how a correction spreads over the surface, how
a monocular depth estimate becomes relief on the photographed side, and how a metaball body is sized.

Plain numpy, no Blender and no OpenCV -- Blender's own Python carries numpy and nothing else -- so it is
testable without either; ``bl_refit.py`` is the Blender side that reads the mesh and writes it back.

Image coordinates are continuous pixels: pixel (row r, column c) covers [c, c+1) x [r, r+1), its centre is
(c + 0.5, r + 0.5). The camera looks along +Y at a subject whose front faces -Y, so a column follows X, a row
follows -Z, and depth is Y.
"""

import math

import numpy as np

# The longer side of the working grid every outline comparison runs on, in pixels.
WORK_LONG_SIDE = 512


def iou(a, b):
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def resample_mask(mask, long_side=WORK_LONG_SIDE):
    """The mask on a working grid whose longer side is ``long_side`` pixels, sampled at pixel centres, and k,
    the working pixels per source pixel."""
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    k = long_side / max(h, w)
    rows, cols = max(int(round(h * k)), 1), max(int(round(w * k)), 1)
    src_r = np.clip(((np.arange(rows) + 0.5) / k).astype(int), 0, h - 1)
    src_c = np.clip(((np.arange(cols) + 0.5) / k).astype(int), 0, w - 1)
    return mask[src_r][:, src_c], k


def outline(mask):
    """The mask's boundary: pixels inside it with a 4-neighbour outside it, the frame's edge counting as outside."""
    m = np.asarray(mask, dtype=bool)
    p = np.pad(m, 1)
    interior = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m & ~interior


def edt(features):
    """Exact Euclidean distance from every pixel to the nearest feature pixel, and that pixel's row and column.

    Two separable passes: down each column to the nearest feature in it, then along each row to the nearest
    column's best, evaluated over all columns at once. Rows are taken in blocks to bound memory.
    """
    f = np.asarray(features, dtype=bool)
    h, w = f.shape
    far = h + w
    rows = np.repeat(np.arange(h)[:, None], w, axis=1)
    above = np.maximum.accumulate(np.where(f, rows, -far), axis=0)
    below = np.minimum.accumulate(np.where(f, rows, 2 * far)[::-1], axis=0)[::-1]
    up, down = rows - above, below - rows
    column_row = np.where(up <= down, above, below)
    g2 = np.minimum(up, down).astype(float) ** 2
    cols = np.arange(w, dtype=float)
    dc2 = (cols[:, None] - cols[None, :]) ** 2
    dist2 = np.empty((h, w))
    near_r = np.empty((h, w), dtype=np.int64)
    near_c = np.empty((h, w), dtype=np.int64)
    block = max(1, 4_000_000 // (w * w))
    for r0 in range(0, h, block):
        total = dc2[None, :, :] + g2[r0:r0 + block][:, None, :]
        j = total.argmin(axis=2)
        dist2[r0:r0 + block] = np.take_along_axis(total, j[..., None], axis=2)[..., 0]
        near_c[r0:r0 + block] = j
        near_r[r0:r0 + block] = np.take_along_axis(column_row[r0:r0 + block], j, axis=1)
    return np.sqrt(dist2), near_r, near_c


def outline_field(mask):
    """The signed distance in pixels from every pixel to the mask's outline (negative inside, positive outside),
    and the nearest outline pixel's row and column."""
    m = np.asarray(mask, dtype=bool)
    dist, near_r, near_c = edt(outline(m))
    return np.where(m, -dist, dist), near_r, near_c


def fit_frame(mask, vertices):
    """The orthographic frame that lays the mesh's front view over the subject in ``mask``.

    One scale for both axes -- the geometric mean of the width and height ratios of the two boxes -- and the
    boxes' centres on each other. A mesh in the wrong proportions therefore shows as an outline to correct
    rather than being stretched to fit.
    """
    rows, cols = np.nonzero(mask)
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    x, z = vertices[:, 0], vertices[:, 2]
    width, height = max(float(np.ptp(x)), 1e-9), max(float(np.ptp(z)), 1e-9)
    return {
        "scale": float(np.sqrt(((c1 - c0) / width) * ((r1 - r0) / height))),
        "cu": (c0 + c1) / 2.0, "cv": (r0 + r1) / 2.0,
        "xm": float((x.min() + x.max()) / 2), "zm": float((z.min() + z.max()) / 2),
        "rows": int(mask.shape[0]), "cols": int(mask.shape[1]), "subjectWidthPx": int(c1 - c0),
    }


def project(vertices, frame):
    """Each vertex's (column, row) position on the working grid."""
    u = frame["cu"] + (vertices[:, 0] - frame["xm"]) * frame["scale"]
    v = frame["cv"] - (vertices[:, 2] - frame["zm"]) * frame["scale"]
    return u, v


def rasterise_depth(u, v, depth, triangles, rows, cols):
    """The nearest depth at every pixel centre, ``inf`` where no triangle covers it.

    Triangles are grouped by the size of their pixel footprint -- 1, 2, (2,4], (4,8] ... pixels across -- and
    each group is rasterised in one vectorised pass, so a dense reconstruction of a few hundred thousand
    triangles costs a second rather than a Python loop per triangle.
    """
    zbuf = np.full(rows * cols, np.inf)
    tu, tv, tz = u[triangles], v[triangles], depth[triangles]
    c_lo = np.maximum(np.ceil(tu.min(axis=1) - 0.5).astype(np.int64), 0)
    c_hi = np.minimum(np.floor(tu.max(axis=1) - 0.5).astype(np.int64), cols - 1)
    r_lo = np.maximum(np.ceil(tv.min(axis=1) - 0.5).astype(np.int64), 0)
    r_hi = np.minimum(np.floor(tv.max(axis=1) - 0.5).astype(np.int64), rows - 1)
    area = ((tu[:, 1] - tu[:, 0]) * (tv[:, 2] - tv[:, 0]) - (tu[:, 2] - tu[:, 0]) * (tv[:, 1] - tv[:, 0]))
    live = (c_hi >= c_lo) & (r_hi >= r_lo) & (np.abs(area) > 1e-12)
    span = np.maximum(c_hi - c_lo, r_hi - r_lo) + 1
    max_span = int(span[live].max()) if live.any() else 0
    size = 1
    while size // 2 < max_span:
        group = np.nonzero(live & (span > size // 2) & (span <= size))[0]
        off_r, off_c = np.divmod(np.arange(size * size), size)
        chunk = max(1, 4_000_000 // (size * size))
        for start in range(0, len(group), chunk):
            t = group[start:start + chunk]
            pc = c_lo[t, None] + off_c[None, :]
            pr = r_lo[t, None] + off_r[None, :]
            x, y = pc + 0.5, pr + 0.5
            u0, v0 = tu[t, 0, None], tv[t, 0, None]
            du1, dv1 = tu[t, 1, None] - u0, tv[t, 1, None] - v0
            du2, dv2 = tu[t, 2, None] - u0, tv[t, 2, None] - v0
            a = area[t, None]
            w1 = ((x - u0) * dv2 - (y - v0) * du2) / a
            w2 = ((y - v0) * du1 - (x - u0) * dv1) / a
            w0 = 1.0 - w1 - w2
            hit = ((pc <= c_hi[t, None]) & (pr <= r_hi[t, None])
                   & (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9))
            z = w0 * tz[t, 0, None] + w1 * tz[t, 1, None] + w2 * tz[t, 2, None]
            np.minimum.at(zbuf, (pr * cols + pc)[hit], z[hit])
        size *= 2
    return zbuf.reshape(rows, cols)


def front_mask(vertices, triangles, frame):
    """The mesh's front outline on the working grid, as a filled mask."""
    u, v = project(vertices, frame)
    return np.isfinite(rasterise_depth(u, v, vertices[:, 1], triangles, frame["rows"], frame["cols"]))


def _pixel(u, v, rows, cols):
    return (np.clip(np.floor(v).astype(np.int64), 0, rows - 1),
            np.clip(np.floor(u).astype(np.int64), 0, cols - 1))


def visible(u, v, depth, zbuf, epsilon):
    """Which vertices the camera sees: each lies within ``epsilon`` of the depth buffer at its own pixel."""
    r, c = _pixel(u, v, *zbuf.shape)
    return depth <= zbuf[r, c] + epsilon


def view_rotation(azimuth, elevation):
    """The 3x3 rotation that turns a mesh by ``azimuth`` degrees about Z and then tips it by ``elevation``
    degrees about X: a camera on -Y then sees it as a camera that far round and that far above would."""
    a, e = math.radians(azimuth), math.radians(elevation)
    turn = np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])
    tip = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(e), -math.sin(e)], [0.0, math.sin(e), math.cos(e)]])
    return tip @ turn


def choose_view(vertices, triangles, mask, azimuths=tuple(range(0, 360, 15)), elevations=(0, 15, -15, 30),
                long_side=256):
    """The azimuth and elevation, in degrees, from which the mesh's rendered outline matches ``mask`` best, and
    that IoU, each view framed as the fit frames it.

    Searching elevation matters: a photograph taken from a little above shows the top of a mug or the back of
    a dog, and an outline fitted level would bend the body to match it. Level is tried first, so a tie keeps
    the mesh level.
    """
    small, _ = resample_mask(mask, long_side)
    best = (-1.0, 0, 0)
    for elevation in elevations:
        for azimuth in azimuths:
            turned = vertices @ view_rotation(azimuth, elevation).T
            score = iou(front_mask(turned, triangles, fit_frame(small, turned)), small)
            if score > best[0] + 1e-9:
                best = (score, azimuth, elevation)
    return best[1], best[2], best[0]


def sample(image, u, v):
    """Bilinear samples of a working-grid image at continuous pixel positions, clamped at the border."""
    h, w = image.shape
    x, y = np.clip(u - 0.5, 0, w - 1), np.clip(v - 0.5, 0, h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0
    return (image[y0, x0] * (1 - fx) * (1 - fy) + image[y0, x1] * fx * (1 - fy)
            + image[y1, x0] * (1 - fx) * fy + image[y1, x1] * fx * fy)


def warp_field(rendered, target_sdf, sigma, step_cap):
    """A smooth image-plane move (du, dv) per pixel that carries the rendered outline towards the target's.

    Each pixel of the rendered outline more than a pixel off the target's outline asks to move along the
    rendered outline's own normal by that distance, at most ``step_cap``; a pixel already on it asks to stay.
    The requests are spread by a normalised Gaussian of ``sigma`` pixels, so the front of the body moves with
    its outline instead of the outline tearing away from it, and fade out where no outline is near.
    """
    gv, gu = np.gradient(outline_field(rendered)[0])
    length = np.hypot(gu, gv) + 1e-9
    edge = outline(rendered)
    off = np.abs(target_sdf) > 1.0
    amount = np.where(edge & off, np.clip(-target_sdf, -step_cap, step_cap), 0.0)
    weight = gaussian_blur(edge.astype(float), sigma)
    confidence = np.clip(weight * 2.0 * np.sqrt(2.0 * np.pi) * sigma, 0.0, 1.0)
    scale = confidence / np.maximum(weight, 1e-12)
    du = gaussian_blur(amount * gu / length, sigma) * scale
    dv = gaussian_blur(amount * gv / length, sigma) * scale
    return du, dv


def fit_outline(vertices, triangles, target, height, max_iterations=20, min_gain=0.002,
                sigma_fraction=0.06, step_fraction=0.02, max_shift=0.03, warp=warp_field):
    """Warp the mesh in the image plane until its front outline matches ``target``.

    Every vertex moves by the warp at its own pixel -- X and Z only, depth is never touched -- so the body
    moves as a whole and keeps its surface. Each iteration is kept only if the IoU does not fall; a warp whose
    gradient reaches 0.5 is scaled down so it cannot fold the surface; and no vertex ends more than
    ``max_shift`` of the height from where it started, which keeps a rigid object from being bent to a
    photograph's perspective. The frame is fitted afresh to every trial, because the photograph projection
    and the pipeline's own outline check both frame the mesh by its box: the IoU kept is the one they will
    measure. Returns the vertices and a record of the IoU per accepted iteration, why it stopped, and the
    largest move and gradient used.
    """
    target_sdf = outline_field(target)[0]
    frame = fit_frame(target, vertices)
    sigma = max(sigma_fraction * frame["subjectWidthPx"], 1.0)
    step_cap = max(step_fraction * frame["subjectWidthPx"], 1.0)
    limit = max_shift * height
    start = np.array(vertices, dtype=float)
    current = start.copy()
    rendered = front_mask(current, triangles, frame)
    best = iou(rendered, target)
    record = {"iou": [round(best, 4)], "stop": "max-iterations", "refused": None, "maxGradient": 0.0}
    for _ in range(max_iterations):
        du, dv = warp(rendered, target_sdf, sigma, step_cap)
        gradient = max(float(np.abs(np.gradient(du)).max()), float(np.abs(np.gradient(dv)).max()))
        if gradient > 0.5:
            du, dv = du * (0.5 / gradient), dv * (0.5 / gradient)
        record["maxGradient"] = round(max(record["maxGradient"], min(gradient, 0.5)), 3)
        u, v = project(current, frame)
        moved = current.copy()
        moved[:, 0] += sample(du, u, v) / frame["scale"]
        moved[:, 2] -= sample(dv, u, v) / frame["scale"]
        shift = moved - start
        length = np.linalg.norm(shift, axis=1)
        over = length > limit
        moved[over] = start[over] + shift[over] * (limit / length[over])[:, None]
        trial_frame = fit_frame(target, moved)
        trial_mask = front_mask(moved, triangles, trial_frame)
        trial = iou(trial_mask, target)
        if trial < best:
            record["stop"], record["refused"] = "reverted", round(trial, 4)
            break
        gain = trial - best
        current, best, rendered, frame = moved, trial, trial_mask, trial_frame
        record["iou"].append(round(best, 4))
        if gain < min_gain:
            record["stop"] = "converged"
            break
    record["maxShift"] = round(float(np.linalg.norm(current - start, axis=1).max() / max(height, 1e-9)), 4)
    return current, record


def gaussian_blur(image, sigma):
    """A separable Gaussian blur with the edge repeated, as shifted sums."""
    radius = max(int(3.0 * sigma + 0.5), 1)
    x = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (x / max(sigma, 1e-9)) ** 2)
    kernel /= kernel.sum()
    out = np.asarray(image, dtype=float)
    for axis in (0, 1):
        pad = [(radius, radius) if a == axis else (0, 0) for a in (0, 1)]
        padded = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, weight in enumerate(kernel):
            window = [slice(None), slice(None)]
            window[axis] = slice(i, i + out.shape[axis])
            acc += weight * padded[tuple(window)]
        out = acc
    return out


def depth_detail(depth, valid, sigma):
    """The depth minus its local mean over valid pixels only: the fine relief, with the subject's overall
    shape and the background both taken out."""
    weights = np.asarray(valid, dtype=float)
    mean_num = gaussian_blur(np.where(valid, depth, 0.0), sigma)
    mean_den = gaussian_blur(weights, sigma)
    mean = np.divide(mean_num, mean_den, out=np.zeros_like(mean_num), where=mean_den > 1e-6)
    return np.where(valid, depth - mean, 0.0)


def relief_offsets(vertices, normals, triangles, frame, k, depth, valid, inside_distance, metric_width, cap,
                   sigma_fraction=0.03, grazing=(0.2, 0.5)):
    """A signed offset along each vertex normal that adds the photographed side's fine relief.

    ``depth`` and ``valid`` are a depth estimate on the cutout's own pixel grid, ``k`` working pixels per cutout
    pixel, ``inside_distance`` the working-grid distance inside the cutout to its outline, ``metric_width`` the
    subject's width in the estimate's units. A vertex the camera sees takes the high-pass of the estimate at its
    pixel, in mesh units, times its facing; a vertex nearer the camera than its surroundings in the estimate
    moves towards the camera. The offset fades to nothing at the outline and at grazing angles and is clipped
    to ``cap``. Returns the offsets and a summary.
    """
    u, v = project(vertices, frame)
    zbuf = rasterise_depth(u, v, vertices[:, 1], triangles, frame["rows"], frame["cols"])
    height = float(np.ptp(vertices[:, 2]))
    seen = visible(u, v, vertices[:, 1], zbuf, 0.002 * height)
    facing = -normals[:, 1]
    fr, fc = _pixel(u / k, v / k, *depth.shape)
    wr, wc = _pixel(u, v, frame["rows"], frame["cols"])
    sigma = sigma_fraction * frame["subjectWidthPx"] / k
    detail = depth_detail(depth, valid, sigma)
    to_mesh = float(np.ptp(vertices[:, 0])) / max(float(metric_width), 1e-9)
    band = max(sigma * k, 1.0)
    edge_fade = np.clip(inside_distance[wr, wc] / band, 0.0, 1.0)
    angle_fade = np.clip((facing - grazing[0]) / (grazing[1] - grazing[0]), 0.0, 1.0)
    take = seen & (facing > 0) & valid[fr, fc]
    offsets = np.where(take, -detail[fr, fc] * to_mesh * facing * edge_fade * angle_fade, 0.0)
    offsets = np.clip(offsets, -cap, cap)
    moved = np.abs(offsets) > 1e-12
    summary = {"vertices": int(moved.sum()),
               "p50": float(np.percentile(np.abs(offsets[moved]), 50)) if moved.any() else 0.0,
               "p95": float(np.percentile(np.abs(offsets[moved]), 95)) if moved.any() else 0.0,
               "sigmaPx": round(float(sigma), 2)}
    return offsets, summary


def smooth(values, edges):
    """One Laplacian pass: each vertex's value becomes half its own and half its neighbours' mean."""
    values = np.asarray(values, dtype=float)
    flat = values.reshape(len(values), -1)
    total = np.zeros_like(flat)
    count = np.zeros(len(values))
    np.add.at(total, edges[:, 0], flat[edges[:, 1]])
    np.add.at(total, edges[:, 1], flat[edges[:, 0]])
    np.add.at(count, edges[:, 0], 1.0)
    np.add.at(count, edges[:, 1], 1.0)
    mean = total / np.maximum(count, 1.0)[:, None]
    return (0.5 * flat + 0.5 * mean).reshape(values.shape)


def mesh_volume(vertices, triangles):
    """The signed volume a closed triangle mesh encloses: positive when its faces wind outwards."""
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def inside_along_line(hits, samples):
    """Which samples along a line lie inside a closed surface the line crosses at ``hits``: those with an odd
    number of crossings before them."""
    return (np.searchsorted(np.sort(np.asarray(hits, dtype=float)), samples, side="right") % 2) == 1


def choose_stride(count, cap):
    """The smallest lattice stride that keeps ``count`` cells under ``cap`` elements: taking every stride-th cell
    along each axis divides the count by the stride cubed."""
    stride = 1
    while count / stride ** 3 > cap:
        stride += 1
    return stride


def solve_threshold(volume_at, target, low=0.05, high=5.0, tolerance=0.02, max_steps=12):
    """The metaball threshold whose surface encloses ``target``, by bisection on a geometric scale.

    ``volume_at`` falls as the threshold rises. Returns the threshold, its volume, how it ended -- solved,
    closest, or below/above when even an end of the range misses -- and every (threshold, volume) tried.
    """
    tried = [(low, volume_at(low)), (high, volume_at(high))]
    if tried[0][1] < target:
        return low, tried[0][1], "below", tried
    if tried[1][1] > target:
        return high, tried[1][1], "above", tried
    for _ in range(max_steps):
        mid = float(np.sqrt(low * high))
        volume = volume_at(mid)
        tried.append((mid, volume))
        if abs(volume - target) <= tolerance * abs(target):
            return mid, volume, "solved", tried
        if volume > target:
            low = mid
        else:
            high = mid
    threshold, volume = min(tried, key=lambda t: abs(t[1] - target))
    return threshold, volume, "closest", tried


def drift(distances, height):
    """Mean and 95th-percentile distance of a rebuilt surface from its source, as fractions of the height."""
    d = np.asarray(distances, dtype=float)
    return {"mean": float(d.mean() / height) if len(d) else 0.0,
            "p95": float(np.percentile(d, 95) / height) if len(d) else 0.0}


def barycentric(points, corners):
    """The barycentric weights of each point in its triangle, clipped into it: ``corners`` is (n, 3, 3)."""
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    v0, v1, v2 = b - a, c - a, points - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    denom = d00 * d11 - d01 * d01
    safe = np.where(np.abs(denom) > 1e-30, denom, 1.0)
    w1 = np.where(np.abs(denom) > 1e-30, (d11 * d20 - d01 * d21) / safe, 1.0 / 3.0)
    w2 = np.where(np.abs(denom) > 1e-30, (d00 * d21 - d01 * d20) / safe, 1.0 / 3.0)
    weights = np.clip(np.column_stack([1.0 - w1 - w2, w1, w2]), 0.0, 1.0)
    return weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)


def overlay_image(rendered, target):
    """An RGB picture of the mesh's outline over the photograph's: grey where they agree, blue where only the
    mesh covers, orange where only the photograph does, the photograph's outline in black."""
    image = np.ones(rendered.shape + (3,))
    image[rendered & target] = (0.75, 0.75, 0.75)
    image[rendered & ~target] = (0.2, 0.4, 1.0)
    image[~rendered & target] = (1.0, 0.55, 0.1)
    image[outline(target)] = (0.0, 0.0, 0.0)
    return image
