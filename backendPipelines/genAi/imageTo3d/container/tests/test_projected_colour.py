#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for colouring a geometry-only mesh from the photograph it was reconstructed from.

The model this exists for produces the best geometry measured on both test subjects and no colour at
all, so the photograph is the only place its colour can come from. Two things have to hold for that to
be worth shipping, and both are tested here over real geometry rather than asserted:

*   the pose recovery finds the pose it was given, and reports a MARGIN that says whether the answer
    means anything -- an asymmetric subject gives a decisive one, a rotationally symmetric subject
    cannot and must not claim to;
*   a mesh and a photograph that do not describe the same subject produce NO colour rather than colour
    sampled from a pose that is wrong, because a wrong pose does not give slightly wrong colour -- it
    paints one part of the subject with another part's colour.

The mask test is here for a reason found the hard way: an earlier measurement included the
photograph's drop shadow in the subject and concluded from it that the pose was unrecoverable.
"""

import sys
from unittest.mock import MagicMock

import pytest

import texturing

# Real geometry and real image arrays are the point of these tests, so they skip rather than pass
# vacuously where conftest has stubbed the dependencies with a MagicMock.
_INSTALLED = not any(isinstance(sys.modules.get(name), MagicMock) for name in ("trimesh", "PIL"))
requires_geometry = pytest.mark.skipif(
    not _INSTALLED, reason="trimesh and PIL are not installed in this environment")

try:
    import scipy  # noqa: F401

    _SCIPY = True
except ImportError:
    _SCIPY = False

requires_scipy = pytest.mark.skipif(not _SCIPY, reason="scipy is not installed in this environment")


def _asymmetric_mesh():
    """A z-up mesh with one feature on one side, so its outline differs by azimuth.

    A box alone is symmetric every 90 degrees and a cylinder from every angle; the protruding arm is
    what gives the silhouette an azimuth to find, the way a mug's handle does.

    The arm extends along X, so it is only VISIBLE in the outline from an azimuth that is not looking
    down its own length: from azimuth 90 and 270 alike it foreshortens to nothing behind the body, and
    those two poses are then genuinely indistinguishable. The tests plant a pose where the arm shows.
    """
    import numpy as np
    import trimesh

    body = trimesh.creation.box(extents=(1.0, 1.0, 2.0))
    arm = trimesh.creation.box(extents=(1.2, 0.2, 0.3))
    arm.apply_translation((0.9, 0.0, 0.4))
    mesh = trimesh.util.concatenate([body, arm])
    # Subdivided, because a bare box has all sixteen of its vertices on a silhouette edge and the
    # sampling deliberately keeps off the subject mask's boundary -- so an unsubdivided box has NO
    # sampleable vertex at all, while the 100,000-vertex meshes this pipeline handles are almost
    # entirely interior. Testing the boundary case as if it were the normal one measures the wrong thing.
    for _ in range(4):
        mesh = mesh.subdivide()
    return np.asarray(mesh.vertices, dtype=float), mesh


SUBJECT_LOWER = (200, 80, 40)
SUBJECT_UPPER = (30, 200, 90)


def _photograph(mesh, azimuth, elevation, size=256, background=(255, 255, 255)):
    """A synthetic photograph of ``mesh`` at a pose: the subject in two flat tones on a flat background.

    Drawn through the same projection the pose search uses, which makes this a self-consistency test of
    the recovery rather than a test of a renderer -- whether the search can find a pose from an outline
    is the property under test. The tones are flat because a real photograph's shading would test the
    sampling as well, and those are separate questions; there are TWO of them because a subject in one
    uniform colour is refused by design, being indistinguishable from a mesh carrying no colour.
    """
    import numpy as np
    from PIL import Image

    frame = texturing._view_frame(azimuth, elevation)
    silhouette = texturing._silhouette(texturing.surface_points(mesh), frame, resolution=size)

    pixels = np.zeros((size, size, 4), dtype=np.uint8)
    pixels[..., :3] = background
    pixels[..., 3] = 0
    pixels[silhouette, :3] = SUBJECT_LOWER
    upper = np.zeros_like(silhouette)
    upper[: size // 2, :] = True
    pixels[silhouette & upper, :3] = SUBJECT_UPPER
    pixels[silhouette, 3] = 255
    return Image.fromarray(pixels, mode="RGBA")


@requires_geometry
@requires_scipy
class TestPoseRecovery:
    def test_the_planted_azimuth_is_recovered_and_decisive(self):
        """A subject with a feature on one side has an azimuth, and the search finds it."""
        _, mesh = _asymmetric_mesh()
        photograph = _photograph(mesh, azimuth=0, elevation=0)

        pose = texturing.recover_photo_pose(mesh, texturing.subject_mask(photograph))

        assert pose["azimuth"] == 0.0
        assert pose["elevation"] == 0.0
        assert pose["iou"] > 0.95
        assert pose["decisive"], pose

    def test_a_mirror_symmetric_subject_is_recovered_only_up_to_reflection(self):
        """An orthographic outline cannot tell azimuth A from -A when the subject has a mirror plane.

        Measured rather than assumed: this mesh has a mirror plane through its arm, so the outline it
        presents at azimuth 45 is identical to the one at 315, and the search reports the pair as
        undecided. The limit matters rather than being pedantic -- a mug is mirror-symmetric too, so a
        photograph taken at a three-quarter angle has two poses fitting its outline equally well, and the
        colour they project is reflected left-for-right between them. What must not happen is the search
        claiming one of the two as measured.
        """
        _, mesh = _asymmetric_mesh()
        photograph = _photograph(mesh, azimuth=45, elevation=0)

        pose = texturing.recover_photo_pose(mesh, texturing.subject_mask(photograph))

        assert pose["azimuth"] in (45.0, 315.0), pose
        assert pose["iou"] > 0.95
        assert not pose["decisive"], pose

    def test_a_rotationally_symmetric_subject_is_reported_as_not_decisive(self):
        """A subject with no asymmetry has no azimuth signal, and must not claim one.

        The recovery still returns a pose, because the projection is still worth running -- such a
        subject looks much the same from any side -- but the margin has to say the azimuth was not
        measured. Reporting a confident azimuth here would be the failure.
        """
        import trimesh

        cylinder = trimesh.creation.cylinder(radius=0.5, height=1.5)
        photograph = _photograph(cylinder, azimuth=45, elevation=0)

        pose = texturing.recover_photo_pose(cylinder, texturing.subject_mask(photograph))

        assert pose["iou"] > 0.9, "a cylinder's outline should still match itself"
        assert not pose["decisive"], pose
        assert pose["margin"] < texturing.POSE_DECISIVE_MARGIN

    def test_the_elevation_is_recovered_but_only_up_to_its_sign(self):
        """The magnitude of the tilt is recoverable; which way the camera tilted is not.

        A subject roughly symmetric about a horizontal plane presents almost the same outline tilted up
        as tilted down. Measured on this one at a planted +20: the grid scores -20 at 0.9859 against
        +20's 0.9858, a difference of one ten-thousandth. So the test pins the magnitude, and pins that
        the report SAYS the sign was not determined -- a pose reported without that caveat would be
        claiming a measurement it does not have.
        """
        _, mesh = _asymmetric_mesh()
        photograph = _photograph(mesh, azimuth=0, elevation=20)

        pose = texturing.recover_photo_pose(mesh, texturing.subject_mask(photograph))

        assert pose["azimuth"] == 0.0
        assert abs(pose["elevation"]) == 20.0
        assert pose["elevationSignMargin"] is not None
        assert abs(pose["elevationSignMargin"]) < 0.01, pose


@requires_geometry
@requires_scipy
class TestSubjectMask:
    def test_the_alpha_channel_is_preferred_when_it_carries_a_mask(self):
        """The background removal stage has already answered this; re-deriving it would be worse."""
        import numpy as np
        from PIL import Image

        pixels = np.zeros((32, 32, 4), dtype=np.uint8)
        # Deliberately the SAME rgb inside and outside the subject, so only alpha can separate them.
        pixels[..., :3] = 120
        pixels[8:24, 8:24, 3] = 255

        mask = texturing.subject_mask(Image.fromarray(pixels, mode="RGBA"))

        assert mask.sum() == 16 * 16
        assert mask[16, 16] and not mask[2, 2]

    def test_a_drop_shadow_is_kept_out_of_the_mask(self):
        """A shadow stretches the mask downward, and the silhouette then matches the wrong pose.

        This is the defect that made an earlier measurement conclude the pose was unrecoverable, so it
        is pinned rather than trusted: the shadow here is only slightly darker than the background,
        which is what a real product shot's shadow looks like at its edges.
        """
        import numpy as np
        from PIL import Image

        pixels = np.full((64, 64, 3), 250, dtype=np.uint8)
        pixels[16:40, 20:44] = (40, 90, 160)  # the subject
        pixels[40:56, 20:52] = 235  # its shadow, close to the background and darker

        mask = texturing.subject_mask(Image.fromarray(pixels, mode="RGB"))

        assert mask[28, 32], "the subject itself must be in the mask"
        assert not mask[48, 36], "the shadow must not be"
        rows = np.where(mask)[0]
        assert rows.max() < 40, "the mask's bounding box must not extend into the shadow"

    def test_an_alpha_channel_marking_every_pixel_alike_falls_back_to_the_colour_test(self):
        import numpy as np
        from PIL import Image

        pixels = np.full((48, 48, 4), 255, dtype=np.uint8)
        pixels[..., :3] = 250
        pixels[10:30, 10:30, :3] = (20, 60, 200)

        mask = texturing.subject_mask(Image.fromarray(pixels, mode="RGBA"))

        assert 0 < mask.sum() < mask.size
        assert mask[20, 20] and not mask[2, 2]


@requires_geometry
@requires_scipy
class TestProjection:
    def test_the_photographs_colour_reaches_the_mesh(self, tmp_path):
        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "subject.png"
        _photograph(mesh, azimuth=0, elevation=0).save(image_path)

        coloured, report = texturing.project_photograph(mesh, str(image_path))

        assert report is not None
        assert report["poseAzimuth"] == 0.0
        assert report["poseDecisive"] is True
        assert 0.0 < report["verticesMeasured"] <= 1.0
        assert report["distinctColours"] >= 2
        assert report["subjectMaskFrom"] == "alpha"
        assert report["poseUpAxis"] == [0.0, 0.0, 1.0]
        assert coloured.visual.kind == "vertex"

        import numpy as np

        # Every vertex ends up coloured -- those the camera saw from the photograph, the rest from the
        # nearest one that it did -- and every colour is one the photograph actually contains.
        colours = np.asarray(coloured.visual.vertex_colors)
        assert len(colours) == len(mesh.vertices)
        assert (colours[:, 3] == 255).all()
        matches_a_tone = (np.all(np.abs(colours[:, :3].astype(int) - SUBJECT_LOWER) <= 2, axis=1)
                          | np.all(np.abs(colours[:, :3].astype(int) - SUBJECT_UPPER) <= 2, axis=1))
        assert matches_a_tone.all(), np.unique(colours[~matches_a_tone][:, :3], axis=0)

    def test_a_subject_no_pose_of_the_mesh_can_match_is_refused(self, tmp_path):
        """A wrong pose paints one part of the subject with another part's colour, so refuse instead.

        The subject here is a thin diagonal stripe: a compact mesh has no pose whose outline resembles
        it, from any of the three up axes. The pair has to be that unlike, because an orthographic
        outline is permissive -- a cylinder seen end-on is a disc and lying down is a rectangle, so most
        "obviously different" pairs still score above the floor. That permissiveness is itself the
        limit of this guard, and it is why the pose margin is reported rather than relied on.
        """
        import numpy as np
        from PIL import Image

        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "stripe.png"
        size = 192
        pixels = np.zeros((size, size, 4), dtype=np.uint8)
        pixels[..., :3] = SUBJECT_LOWER
        for offset in range(size):
            pixels[offset, max(offset - 1, 0):offset + 2, 3] = 255

        Image.fromarray(pixels, mode="RGBA").save(image_path)

        returned, report = texturing.project_photograph(mesh, str(image_path))

        assert report is None
        assert returned is mesh, "the mesh is returned as it arrived, with no colour attached"

    def test_a_blank_photograph_projects_nothing(self, tmp_path):
        import numpy as np
        from PIL import Image

        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "blank.png"
        Image.fromarray(np.full((64, 64, 3), 250, dtype=np.uint8), mode="RGB").save(image_path)

        _, report = texturing.project_photograph(mesh, str(image_path))

        assert report is None

    def test_a_guessed_mask_covering_the_whole_frame_is_refused(self, tmp_path):
        """A guess that selects the whole photograph has not found a subject, and must not be sampled.

        Measured on the real inputs: a person photographed in a room gives a guessed mask over 87.9% of
        the frame, and every colour sampled through it is the room's rather than the person's. The mug on
        a flat backdrop gives 14.7%, so the bound separates them by a wide margin.
        """
        import numpy as np
        from PIL import Image

        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "busy.png"
        # No alpha, and no flat border: the corners are part of the scene, so the background reference
        # is wrong and the mask takes almost everything.
        rng = np.random.default_rng(0)
        pixels = rng.integers(0, 255, size=(96, 96, 3), dtype=np.uint8)
        Image.fromarray(pixels, mode="RGB").save(image_path)

        returned, report = texturing.project_photograph(mesh, str(image_path))

        assert report is None
        assert returned is mesh

    def test_the_bound_does_not_apply_to_a_cutout_that_fills_its_frame(self, tmp_path):
        """An alpha channel is a segmentation model's own answer, so a subject filling the frame is fine.

        The bound exists to catch a FAILED guess. Applying it to alpha as well would refuse colour on a
        tightly cropped cutout, which is what the framing stage produces on purpose.
        """
        import numpy as np
        from PIL import Image

        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "tight_cutout.png"
        size = 96
        pixels = np.zeros((size, size, 4), dtype=np.uint8)
        pixels[..., :3] = SUBJECT_LOWER
        pixels[: size // 2, :, :3] = SUBJECT_UPPER
        # Opaque everywhere but a one-pixel border: 95.9% of the frame, well past the guessed bound.
        pixels[1:-1, 1:-1, 3] = 255
        Image.fromarray(pixels, mode="RGBA").save(image_path)
        assert (pixels[..., 3] > 8).mean() > texturing.MAX_GUESSED_SUBJECT_FRACTION

        _, report = texturing.project_photograph(mesh, str(image_path))

        assert report is not None
        assert report["subjectMaskFrom"] == "alpha"

    def test_an_unreadable_photograph_does_not_end_the_run(self, tmp_path):
        """The geometry is finished by this point; a failed colour transfer must not discard it."""
        _, mesh = _asymmetric_mesh()
        missing = tmp_path / "not-here.png"

        returned, report = texturing.project_photograph(mesh, str(missing))

        assert report is None
        assert returned is mesh

    def test_the_projected_colour_bakes_into_an_atlas(self, tmp_path):
        """The projection puts colour on per vertex, which is what glTF and a renderer both want. The
        bake is what an OBJ needs, so the projected colour has to survive it."""
        _, mesh = _asymmetric_mesh()
        image_path = tmp_path / "subject.png"
        _photograph(mesh, azimuth=0, elevation=0).save(image_path)

        coloured, report = texturing.project_photograph(mesh, str(image_path))
        assert report is not None and report["distinctColours"] >= 2

        baked = texturing.bake_vertex_colours(coloured, resolution=256)

        assert baked.visual.uv is not None
        assert texturing.material_image(baked.visual.material) is not None
