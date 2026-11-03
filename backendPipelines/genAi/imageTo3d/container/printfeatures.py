# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Printable features added to a finished reconstruction: hanging loops and stand bases.

A reconstruction is a likeness, not a product. A model that hangs needs something to thread a ring
through, and a model that stands needs a footprint that will sit flat and give the slicer a stable first
layer. Both are
parametric geometry attached to the mesh rather than anything a generative model can be asked for.

Two ordering constraints drive where this runs:

* **After the rescale.** The dimensions here are millimetres of the printed object. A model reconstructs
  into an arbitrary unit cube, so adding a 4 mm loop before ``TARGET_SIZE_MM`` is applied would scale it
  by whatever the rescale factor happened to be -- which is how a hanging loop ends up invisible.
* **Before the print gate.** The gate has to judge the solid that actually ships. A gate that ran before
  the attachment would report on geometry nobody prints.

**How the feature is attached is a genuine trade-off, not an implementation detail.** A boolean union
produces one clean solid but discards the texture entirely -- measured on trimesh 4.11 with the manifold
engine, ``visual.kind`` comes back ``None`` and the uv coordinates are gone. On a pipeline whose primary
requirement is full-colour output, a silent union would strip the colour from every run that adds a loop.
Concatenation keeps the texture but leaves the feature as a second closed shell overlapping the model.

So the join is chosen by what the mesh carries, and ``PRINT_FEATURE_JOIN`` can force either:

===========  ===============================  ==========================================================
Join         Result                           Chosen when
===========  ===============================  ==========================================================
``concat``   texture preserved, 2 shells      the mesh carries a real texture -- colour is the priority
``union``    1 solid, texture discarded       the mesh is geometry-only, so there is nothing to lose
===========  ===============================  ==========================================================

Overlapping closed shells slice correctly -- a slicer fills by winding rule, so the union happens at
slice time -- but the gate cannot tell the two apart: two disjoint closed shells satisfy every criterion
it checks (watertight, no non-manifold edges, no broken faces, positive volume). The join mode and the
resulting body count are therefore reported rather than left implicit, because "passed the gate" means
something different for each.

A concatenated feature also needs its **own** flat-colour material. Left bare it still receives uv
coordinates from the packed atlas -- measured in the 0.02-0.555 range, not zero -- so it samples whatever
texels the model's unwrap happens to occupy and prints as a smear of the subject's colours. Giving it a
solid swatch makes trimesh pack an atlas carrying both, and the loop prints the colour it was asked for.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# What a run can add. An explicit set so the template offers the choices rather than free text.
PRINT_FEATURES = ("none", "loop", "base", "loop+base")

# How the feature is attached. See the module docstring -- this decides whether colour survives.
JOIN_MODES = ("auto", "concat", "union")

# Fractions of the model's own size used for any dimension the run leaves to the pipeline, so a loop
# keeps sensible proportions across a small charm and a large model alike.
DEFAULT_LOOP_FRACTION = 0.12
DEFAULT_LOOP_THICKNESS_FRACTION = 0.30
DEFAULT_BASE_HEIGHT_FRACTION = 0.04
DEFAULT_BASE_MARGIN = 1.15

# How far the feature is driven into the model before the join. Exactly touching surfaces produce a
# coincident face, which a boolean engine resolves unpredictably and an overlap does not.
EMBED_FRACTION = 0.4

# Flat colour for added geometry, as printed on a colour machine. A neutral dark grey reads as a
# deliberate fitting rather than an attempt to match the subject.
DEFAULT_FEATURE_COLOR = "#3C3C3C"

# Edge length, in atlas texels, of the block painted with the feature colour. It carries one flat
# colour, so this only has to be large enough to survive the sampler's filtering at the edges.
SWATCH_BLOCK_PX = 16

# Resolution of the standalone swatch used only on the fallback path, where the mesh carries no atlas
# to paint into.
FEATURE_SWATCH_PX = 64


class PrintFeatureError(RuntimeError):
    """A requested print feature that cannot be added to this mesh."""


def parse_color(raw) -> tuple:
    """An ``#RRGGBB`` string as an RGB triple."""
    text = str(raw or "").strip().lstrip("#")
    if len(text) != 6:
        raise PrintFeatureError(
            f"PRINT_FEATURE_COLOR '{raw}' is not a 6-digit hex colour such as {DEFAULT_FEATURE_COLOR}")
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise PrintFeatureError(f"PRINT_FEATURE_COLOR '{raw}' is not valid hexadecimal") from None


def has_real_texture(mesh) -> bool:
    """Whether the mesh carries a texture worth preserving.

    Delegates to the texturing module's placeholder check: trimesh substitutes a 2x2 swatch for a mesh
    with no material, and treating that as a real texture would pick the colour-preserving join for a
    mesh that has no colour to preserve.
    """
    import texturing

    visual = getattr(mesh, "visual", None)
    if visual is None or getattr(visual, "uv", None) is None:
        return False
    return texturing.real_material_image(getattr(visual, "material", None)) is not None


def carries_colour(mesh) -> bool:
    """Whether the mesh carries colour of any kind that a join has to preserve.

    Either a real texture, or per-vertex colour from a model that produces no atlas. The second form
    reaches this stage because the palette is packed after the features are attached.
    """
    import texturing

    return has_real_texture(mesh) or texturing.per_vertex_colors(mesh) is not None


def _flat_vertex_colored(addition, color: tuple):
    """Give the addition a flat PER-VERTEX colour, matching a mesh that carries colour that way.

    Concatenating two vertex-coloured meshes preserves both colour arrays exactly, so a feature added
    this way costs the model nothing -- no atlas, no packing, no resampling.
    """
    import numpy as np
    import trimesh

    colours = np.tile(np.array([[color[0], color[1], color[2], 255]], dtype=np.uint8),
                      (len(addition.vertices), 1))
    addition.visual = trimesh.visual.ColorVisuals(mesh=addition, vertex_colors=colours)
    return addition


def _flat_colored(addition, color: tuple):
    """Give the addition its own single-colour material so it does not sample the model's atlas."""
    import numpy as np
    import trimesh
    from PIL import Image

    swatch = Image.new("RGB", (FEATURE_SWATCH_PX, FEATURE_SWATCH_PX), color)
    # Every vertex points at the centre of its own swatch, so the whole feature takes the one colour
    # wherever the atlas packing places it.
    uv = np.full((len(addition.vertices), 2), 0.5)
    addition.visual = trimesh.visual.TextureVisuals(uv=uv, image=swatch)
    return addition


def _paint_swatch_into_atlas(mesh, color: tuple):
    """Paint the feature colour into a block of the model's own atlas that no uv references.

    Returns ``(image, (u, v))`` for the painted block, or ``None`` when the mesh carries no atlas to
    paint into.

    This exists because handing the feature a SECOND image makes trimesh pack the two into a combined
    atlas, and its packer grows the result whatever the swatch measures -- a 1-texel swatch enlarged a
    2048 atlas exactly as much as a 256-texel one. On a live run that turned a 2048 atlas into 8192,
    16 times the area and 94% of it empty, on the very output the colour requirement cares about.
    Painting into the existing atlas instead keeps one image at its original resolution.

    The block is chosen from texels no uv coordinate maps to, scanning from the far corner, so nothing
    the model textures is overwritten.
    """
    import numpy as np
    from PIL import Image

    visual = getattr(mesh, "visual", None)
    uv = getattr(visual, "uv", None)
    if uv is None or len(uv) == 0:
        return None
    import texturing
    source = texturing.real_material_image(getattr(visual, "material", None))
    if source is None:
        return None

    pixels = np.array(source.convert("RGB"))
    height, width = pixels.shape[:2]
    if height < SWATCH_BLOCK_PX or width < SWATCH_BLOCK_PX:
        return None

    # Mark every texel a uv coordinate lands on. v is flipped because uv puts the origin at the
    # bottom-left while an image row index counts from the top.
    referenced = np.zeros((height, width), dtype=bool)
    coords = np.clip((np.asarray(uv, dtype=float) * [width - 1, height - 1]).astype(int),
                     [0, 0], [width - 1, height - 1])
    referenced[(height - 1) - coords[:, 1], coords[:, 0]] = True

    block = SWATCH_BLOCK_PX
    for top in range(height - block, -1, -block):
        for left in range(width - block, -1, -block):
            if not referenced[top:top + block, left:left + block].any():
                pixels[top:top + block, left:left + block] = color
                u = (left + block / 2.0) / width
                v = 1.0 - (top + block / 2.0) / height
                logger.info(f"Painted the print feature colour into the model's atlas at texel "
                            f"({left}, {top}), so the atlas stays {width}x{height}")
                return Image.fromarray(pixels), (u, v)

    logger.warning(
        "Every block of the model's atlas is referenced by its uv coordinates, so the print feature "
        "cannot share it; falling back to a separate swatch, which enlarges the atlas")
    return None


def _concat_sharing_atlas(mesh, addition, color: tuple):
    """Append the addition to the mesh, both sampling the model's own atlas. ``None`` if not possible."""
    import numpy as np
    import trimesh

    painted = _paint_swatch_into_atlas(mesh, color)
    if painted is None:
        return None
    image, point = painted

    combined = trimesh.Trimesh(
        vertices=np.vstack([mesh.vertices, addition.vertices]),
        faces=np.vstack([mesh.faces, addition.faces + len(mesh.vertices)]),
        process=False)
    combined.visual = trimesh.visual.TextureVisuals(
        uv=np.vstack([np.asarray(mesh.visual.uv, dtype=float),
                      np.tile(point, (len(addition.vertices), 1))]),
        image=image)
    return combined


def _join(mesh, addition, mode: str, color: tuple):
    """Attach the addition using the resolved join mode. Returns ``(mesh, mode_used)``."""
    import trimesh

    if mode == "concat":
        import texturing

        # A mesh carrying PER-VERTEX colour takes the feature the same way, because concatenating two
        # vertex-coloured meshes preserves both arrays exactly. Handing such a mesh a swatch IMAGE
        # instead makes trimesh pack the palette with it, and resampling a palette blends unrelated
        # entries -- measured at 26.6% of the model's vertices wrong, one by 181 of 255.
        if texturing.per_vertex_colors(mesh) is not None:
            joined = trimesh.util.concatenate([mesh, _flat_vertex_colored(addition, color)])
            if joined is None or len(getattr(joined, "faces", ())) == 0:
                raise PrintFeatureError("Concatenating the print feature produced no geometry")
            return joined, "concat"

        # Sharing the model's atlas is preferred: packing a second image grows the atlas regardless of
        # how small the swatch is. The fallback covers a mesh with no atlas to paint into, which is a
        # concat that was explicitly asked for on geometry-only output.
        joined = _concat_sharing_atlas(mesh, addition, color)
        if joined is None:
            joined = trimesh.util.concatenate([mesh, _flat_colored(addition, color)])
        if joined is None or len(getattr(joined, "faces", ())) == 0:
            raise PrintFeatureError("Concatenating the print feature produced no geometry")
        return joined, "concat"

    before = _body_count(mesh)
    try:
        # check_volume is off: the input is a reconstruction that may carry small defects, and the
        # check rejects exactly the meshes this stage exists to work on.
        fused = trimesh.boolean.union([mesh, addition], engine="manifold", check_volume=False)
    except (AttributeError, TypeError, ValueError) as e:
        raise PrintFeatureError(
            f"The print feature could not be fused onto the mesh ({type(e).__name__}: {e}). The "
            f"manifold engine performs the union, so a mesh it cannot process would leave the feature "
            f"detached and shipping as a floating shell.") from e
    if fused is None or len(getattr(fused, "faces", ())) == 0:
        raise PrintFeatureError("The union of the mesh and the print feature produced no geometry")

    # A union is meant to produce one solid, so a shell count that GREW means the addition overlapped
    # nothing and is now a loose part inside the file. That cannot be left to the print gate: disjoint
    # closed shells satisfy every criterion it measures, so a detached loop ships as print-ready.
    after = _body_count(fused)
    if after > before:
        raise PrintFeatureError(
            f"The print feature did not fuse onto the mesh: the shell count went from {before} to "
            f"{after}, so the feature is a separate floating part rather than joined geometry. This "
            f"happens where the feature is placed over empty space -- the top-centre of a hollow "
            f"model, say. The print gate cannot detect it, because disjoint closed shells are each "
            f"watertight, so the run is failed here instead.")
    return fused, "union"


def loop_geometry(mesh, diameter_mm: Optional[float] = None, thickness_mm: Optional[float] = None):
    """A hanging loop sized to the mesh and positioned at its top, hole axis horizontal so it hangs."""
    import numpy as np
    import trimesh

    height = float(mesh.extents[2])
    if height <= 0:
        raise PrintFeatureError("The mesh has no height, so a hanging loop has nowhere to attach")

    diameter = float(diameter_mm) if diameter_mm else height * DEFAULT_LOOP_FRACTION
    thickness = float(thickness_mm) if thickness_mm else diameter * DEFAULT_LOOP_THICKNESS_FRACTION
    if diameter <= 0 or thickness <= 0:
        raise PrintFeatureError(
            f"A loop needs a positive diameter and thickness; got {diameter} and {thickness}")
    if thickness >= diameter:
        raise PrintFeatureError(
            f"The loop thickness {thickness} is not smaller than its diameter {diameter}, so it would "
            f"close the hole a ring has to pass through")

    # major_radius measures to the centre of the tube, so the clear hole is the requested diameter.
    minor = thickness / 2.0
    major = (diameter / 2.0) + minor
    loop = trimesh.creation.torus(major_radius=major, minor_radius=minor)
    # Stand it up: a torus is created in the XY plane with its hole facing up, which would hang edge-on.
    loop.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0]))

    # Anchored over the HIGHEST VERTEX, not the centre of the bounding box. The bounding-box centre is
    # only inside the model for a convex shape: on anything hollow or concave -- a mug, a cup, a ring --
    # the top-centre is the opening, so a loop placed there spans empty space, fuses onto nothing, and
    # ships as a floating ring. Measured on a mug, that left the loop as a separate 2048-face shell
    # while the print gate still passed, because two disjoint closed shells are both watertight.
    # The highest vertex is by definition on the surface, so there is material to fuse into.
    apex = mesh.vertices[int(np.argmax(mesh.vertices[:, 2]))]
    loop.apply_translation([apex[0], apex[1], float(apex[2]) + major - (major * EMBED_FRACTION)])
    logger.info(f"Loop geometry: {diameter:.2f} across, {thickness:.2f} thick, anchored over the "
                f"highest point of the model at ({apex[0]:.2f}, {apex[1]:.2f}, {apex[2]:.2f})")
    return loop


def base_geometry(mesh, diameter_mm: Optional[float] = None, height_mm: Optional[float] = None):
    """A cylindrical base sized to the mesh's footprint, positioned under it."""
    import trimesh

    extents = mesh.extents
    footprint = float(max(extents[0], extents[1]))
    if footprint <= 0:
        raise PrintFeatureError("The mesh has no footprint, so a base cannot be sized to it")

    diameter = float(diameter_mm) if diameter_mm else footprint * DEFAULT_BASE_MARGIN
    height = float(height_mm) if height_mm else float(extents[2]) * DEFAULT_BASE_HEIGHT_FRACTION
    if diameter <= 0 or height <= 0:
        raise PrintFeatureError(
            f"A base needs a positive diameter and height; got {diameter} and {height}")

    base = trimesh.creation.cylinder(radius=diameter / 2.0, height=height)
    centre = mesh.bounds.mean(axis=0)
    bottom_z = float(mesh.bounds[0][2])
    # A cylinder is centred on its own origin, so this puts its top face at the model's underside,
    # less the deliberate overlap that makes the join a single solid rather than two touching ones.
    base.apply_translation(
        [centre[0], centre[1], bottom_z - (height / 2.0) + (height * EMBED_FRACTION)])
    logger.info(f"Base geometry: {diameter:.2f} across and {height:.2f} tall under the model")
    return base


def _body_count(mesh) -> int:
    """Connected shells in the mesh, counted on the geometry rather than the uv indexing."""
    import meshcleanup

    return int(getattr(meshcleanup.welded_for_measurement(mesh), "body_count", 1))


def resolve_join(mesh, requested: str) -> str:
    """The join mode to use, resolving ``auto`` against what the mesh carries."""
    if requested not in JOIN_MODES:
        raise PrintFeatureError(
            f"Unknown PRINT_FEATURE_JOIN '{requested}'; expected one of {', '.join(JOIN_MODES)}")
    if requested != "auto":
        return requested
    # A mesh carrying colour takes concat so the colour survives; a geometry-only one takes union, which
    # is strictly better there because it has no colour to lose. Per-vertex colour counts: a boolean
    # union returns geometry alone, so choosing it for a vertex-coloured mesh discarded every colour the
    # model produced -- the state a mesh is in when the palette is packed after the features.
    return "concat" if carries_colour(mesh) else "union"


def apply_features(mesh, params):
    """Add whatever features the run asked for. Returns ``(mesh, report)``.

    Called after the rescale and before the print gate, so the dimensions mean millimetres of the
    printed object and the gate judges the solid that ships.
    """
    from models.base import setting

    requested = str(setting(params, "PRINT_FEATURES", "none")).strip().lower()
    if requested not in PRINT_FEATURES:
        raise PrintFeatureError(
            f"Unknown PRINT_FEATURES value '{requested}'; expected one of {', '.join(PRINT_FEATURES)}")
    if requested == "none":
        return mesh, {}

    join_mode = resolve_join(mesh, str(setting(params, "PRINT_FEATURE_JOIN", "auto")).strip().lower())
    color = parse_color(setting(params, "PRINT_FEATURE_COLOR", DEFAULT_FEATURE_COLOR))

    def _mm(key):
        raw = setting(params, key, "0")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise PrintFeatureError(f"{key} '{raw}' is not a number") from None
        return value if value > 0 else None

    builders = []
    if "loop" in requested:
        builders.append(("loop", lambda m: loop_geometry(
            m, _mm("LOOP_DIAMETER_MM"), _mm("LOOP_THICKNESS_MM"))))
    if "base" in requested:
        builders.append(("base", lambda m: base_geometry(
            m, _mm("BASE_DIAMETER_MM"), _mm("BASE_HEIGHT_MM"))))

    applied = []
    for name, build in builders:
        # Each feature is sized against the mesh as it stands, so a base added after a loop measures
        # the footprint of the model rather than of the model plus its loop.
        mesh, join_mode = _join(mesh, build(mesh), join_mode, color)
        applied.append(name)

    report = {
        "printFeatures": ",".join(applied),
        "printFeatureJoin": join_mode,
        # The gate cannot distinguish overlapping closed shells from one solid, so the count is
        # reported: with concat this is the model plus one shell per feature, by design.
        #
        # Counted on a welded copy, for the same reason the gate is. A textured mesh splits a vertex at
        # every atlas seam, and counted on the raw indices each split face reads as its own shell -- a
        # live run reported 6663 bodies where the geometry had 4.
        "printFeatureBodies": str(_body_count(mesh)),
        # How many shells the finished mesh is MEANT to have, which the gate needs to tell a feature
        # that was attached on purpose from a subject that came apart. concat leaves each feature as
        # its own shell; union fuses them into the model, so the model's own count stands.
        "printFeatureExpectedBodies": str(1 + len(applied) if join_mode == "concat" else 1),
        "printFeatureColor": "#%02X%02X%02X" % color,
    }
    logger.info(f"Print features added via {join_mode}: {', '.join(applied)} "
                f"({report['printFeatureBodies']} bodies)")
    return mesh, report
