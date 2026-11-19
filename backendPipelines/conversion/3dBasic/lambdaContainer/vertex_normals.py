# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Vertex normals without SciPy.

trimesh computes ``Trimesh.vertex_normals`` (the angle-weighted average of the face normals around each
vertex) with a SciPy sparse matrix, and when SciPy is not installed, as in this image, it falls back to
a loop that scans every face for every vertex: O(vertices x faces), minutes for a mesh of 100,000
vertices. The USD writer and the COLLADA exporter both read vertex normals, so they are computed here
with the same weighting in O(faces) with NumPy, and the result is put in the mesh's cache before an
exporter asks for it.

Only the outputs that always write normals (USD, COLLADA) get them: an OBJ, PLY or glTF export writes
normals only when they are already cached, so filling the cache for those would change their output.
"""

import numpy as np


def angle_weighted_vertex_normals(vertices, faces, face_normals=None):
    """``(len(vertices), 3)`` unit normals: each vertex's sum of the normals of the faces around it,
    weighted by the face's angle at that vertex (trimesh's ``weighted_vertex_normals``). A vertex no
    face uses, or whose sum is zero, gets a zero normal, as in trimesh."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    count = len(vertices)
    if count == 0 or len(faces) == 0:
        return np.zeros((count, 3), dtype=np.float64)
    corners = vertices[faces]
    if face_normals is None:
        face_normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    face_normals = _unitize(np.asarray(face_normals, dtype=np.float64))
    angles = _corner_angles(corners)
    summed = np.zeros((count, 3), dtype=np.float64)
    for corner in range(3):
        weighted = face_normals * angles[:, corner, None]
        for axis in range(3):
            summed[:, axis] += np.bincount(faces[:, corner], weights=weighted[:, axis], minlength=count)
    return _unitize(summed)


def _corner_angles(corners):
    """The angle of each triangle at each of its three corners, in radians."""
    angles = np.zeros(corners.shape[:2], dtype=np.float64)
    for corner in range(3):
        a = corners[:, (corner + 1) % 3] - corners[:, corner]
        b = corners[:, (corner + 2) % 3] - corners[:, corner]
        cosine = np.einsum("ij,ij->i", _unitize(a), _unitize(b))
        angles[:, corner] = np.arccos(np.clip(cosine, -1.0, 1.0))
    angles[~np.isfinite(angles)] = 0.0
    return angles


def _unitize(vectors):
    lengths = np.linalg.norm(vectors, axis=1)
    out = np.zeros_like(vectors)
    valid = lengths > 1e-30
    out[valid] = vectors[valid] / lengths[valid, None]
    return out


def fill_vertex_normals(geometry):
    """Put the vertex normals of a ``Trimesh`` (or of every mesh in a ``Scene``) in its cache, so an
    exporter that reads ``vertex_normals`` never reaches trimesh's SciPy-less fallback. Returns the
    number of meshes filled."""
    meshes = list(geometry.geometry.values()) if hasattr(geometry, "geometry") else [geometry]
    filled = 0
    for mesh in meshes:
        faces = getattr(mesh, "faces", None)
        if faces is None or len(faces) == 0:
            continue
        if "vertex_normals" in mesh._cache:
            continue
        mesh.vertex_normals = angle_weighted_vertex_normals(mesh.vertices, faces, mesh.face_normals)
        filled += 1
    return filled
