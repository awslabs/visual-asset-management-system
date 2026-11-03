# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Matrix expansion and scoring for the image-to-3D model comparison.

The comparison answers three questions at once -- how good is the result, how long did it take, and
what did it cost -- and they trade against each other, so this module deliberately does not reduce a
run to a single number. A single score is how a degenerate result wins: over the course of building
this pipeline a featureless blob passed the print gate, a placeholder swatch reported as a preserved
texture, a repair that discarded 97.5% of the volume satisfied every gate criterion, and a mesh lying
on its side was indistinguishable from an upright one by gate, volume, face count and file size alike.
Each of those would have topped a leaderboard ranked on pass-rate and runtime.

So the report carries the three axes side by side, plus the fidelity signals that catch those cases,
plus a preview per run -- because in every one of them the picture was what gave the result away.
"""

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Stage names the container reports, in the order they run. Cost is attributed per stage, so a model
# that spends its time downloading weights is distinguishable from one that spends it generating.
STAGE_ORDER = ("weightCache", "inputDownload", "preprocess", "generate", "meshLoad", "cleanup",
               "printFeatures", "export", "upload")

# Stages that are not the model's own work. Separated out so a cold cache or a slow upload does not
# read as model cost, which is the comparison this whole exercise exists to get right. printFeatures
# counts as overhead because it costs the same whichever model produced the mesh, so charging it to the
# model would move every model's figure by the same amount and tell the comparison nothing.
OVERHEAD_STAGES = ("weightCache", "inputDownload", "printFeatures", "export", "upload")

# The texture states a run can report, best first. A model that cannot carry colour is disqualified
# from the colour-print requirement however good its geometry is.
#
# rebakedFromAtlas is the model's own atlas rasterised into a fresh layout over the cleaned topology,
# so it keeps detail at the new atlas's resolution. resampledFromAtlas is the same atlas collapsed to
# per-vertex colour, which is valid but limited to the vertex spacing.
# fromVertexColors is colour the model produced per vertex rather than as an atlas. It counts as colour --
# it is the model's own -- but ranks below a carried atlas for the same vertex-spacing reason.
# Colour projected from the input photograph ranks below any colour a MODEL produced. It is real colour on
# the surface the camera saw and inferred everywhere else, so it meets the colour-print requirement while
# being the weakest way of meeting it.
# reprojected ranks BELOW even that, and below every other way of carrying an atlas, because it is not
# merely lossy but WRONG: it copies onto each rebuilt vertex the uv of its nearest pre-cleanup vertex, and
# a uv is a coordinate into a discontinuous chart layout, so the triangle between two such vertices sweeps
# across unrelated charts as it interpolates. Measured against the models' own untransferred output on the
# standing figure, as mean colour error over four rendered views: rebakedFromAtlas 7.9,
# resampledFromAtlas 10.9, reprojected 43.4 -- with 22.8% of the reprojected pixels badly wrong.
# sidecarsOnly means texture files shipped beside a mesh that does not reference them, which is not
# colour on the mesh at all.
TEXTURE_RANK = {"preserved": 7, "rebakedFromAtlas": 6, "resampledFromAtlas": 5, "fromVertexColors": 4,
                "projectedFromPhotograph": 3, "reprojected": 2, "sidecarsOnly": 1, "none": 0}

# The lowest rank that still puts colour ON the mesh, which is what the colour-print requirement asks.
# reprojected qualifies and sidecarsOnly does not: reprojected ships colour that is substantially in the
# wrong place, which is a fidelity problem this harness cannot measure from a status string, while
# sidecarsOnly ships a mesh that references no texture at all. Reporting a reprojected run as having no
# colour would be false.
COLOUR_THRESHOLD = TEXTURE_RANK["reprojected"]

# What a BASELINE comparison run measures: the model's own output, with nothing added to it. Print
# features are deliberately off here. A loop or a base is an optional post-process that costs the same
# whichever model produced the mesh, and adding one changes what the gate measures -- each feature
# joined by concat is a separate shell, so the body count no longer describes the model alone. Compare
# bare models first; sweep the features afterwards as their own comparison (see FEATURE_AXIS).
BASELINE_SETTINGS = {
    "PRINT_FEATURES": "none",
    "LOOP_DIAMETER_MM": "0",
    "LOOP_THICKNESS_MM": "0",
    "BASE_DIAMETER_MM": "0",
    "BASE_HEIGHT_MM": "0",
}

# The follow-up comparison: the same models with each print feature configuration, run separately so
# the feature's cost and its effect on the gate are attributable rather than mixed into the baseline.
FEATURE_AXIS = {"PRINT_FEATURES": ["none", "loop", "base", "loop+base"]}


@dataclass(frozen=True)
class Variation:
    """One cell of the matrix: a model plus the settings that distinguish this run from its siblings."""

    model: str
    image: str
    settings: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """A stable, readable identifier for the run, used in the report and to name its artifacts.

        Only settings that DIFFER from the baseline appear, so a label names what distinguishes this run
        rather than repeating the baseline on every row of the report.
        """
        parts = [self.model, self.image]
        parts += [f"{key}={self.settings[key]}" for key in sorted(self.settings)
                  if BASELINE_SETTINGS.get(key) != self.settings[key]]
        return " | ".join(parts)


def expand(models, images, settings_axes=None, baseline=True) -> List[Variation]:
    """The full cross product of models, images and setting axes.

    ``settings_axes`` maps a setting name to the values to sweep, so ``{"IMAGE_CLEANUP": ["auto",
    "skip"]}`` doubles the matrix. An empty axis map yields one run per model and image.

    ``baseline`` applies ``BASELINE_SETTINGS`` to every variation, so a comparison measures the models'
    own output with nothing added to it. An axis the caller sweeps WINS over the baseline value for that
    setting, which is what makes the print-feature comparison a matter of sweeping PRINT_FEATURES rather
    than of turning the baseline off.
    """
    if not models:
        raise ValueError("The matrix needs at least one model")
    if not images:
        raise ValueError("The matrix needs at least one input image")

    axes = {key: list(values) for key, values in (settings_axes or {}).items() if values}
    keys = sorted(axes)
    combinations = list(itertools.product(*(axes[key] for key in keys))) or [()]

    variations = []
    for model in models:
        for image in images:
            for combination in combinations:
                settings = dict(BASELINE_SETTINGS) if baseline else {}
                settings.update(dict(zip(keys, combination)))
                variations.append(Variation(model=model, image=image, settings=settings))
    return variations


@dataclass
class RunResult:
    """What one execution produced, read back from its own benchmark report."""

    variation: Variation
    status: str = "unknown"
    stage_seconds: Dict[str, float] = field(default_factory=dict)
    instance_type: str = ""
    gpu_name: str = ""
    texture_status: str = "none"
    print_ready: Optional[bool] = None
    volume_retained: Optional[float] = None
    output_bytes: Dict[str, int] = field(default_factory=dict)
    face_count: Optional[int] = None
    # Connected shells the gate measured, against how many it expected. A baseline run expects one, so
    # more than that means the subject came apart -- a defect no surface criterion detects.
    bodies: Optional[int] = None
    expected_bodies: Optional[int] = None
    # Pieces of the subject a repair joined. Joining assembles a torn reconstruction and also
    # absorbs a detached feature, and no measurement separates the two, so it is reported for
    # review against the preview rather than treated as a failure.
    pieces_fused: Optional[int] = None
    # The pose the photograph's colour was projected from, and how much of the surface it measured.
    # Present only for a run that had no colour of its own and was given the projection.
    projected_colour: Dict[str, object] = field(default_factory=dict)
    # Coarse shape measurements of the shipped mesh, from the run's own report. Mostly meaningless alone
    # and informative compared across the models given the same photograph -- see flag_shape_outliers.
    # ``genus`` is the exception and is read absolutely: any tunnel is a defect whatever the others did.
    shape: Dict[str, float] = field(default_factory=dict)
    # Notes derived by comparing this run against its SIBLINGS on the same image, which one result
    # cannot work out for itself.
    peer_notes: List[str] = field(default_factory=list)
    preview_path: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def total_seconds(self) -> float:
        return round(sum(self.stage_seconds.values()), 2)

    @property
    def model_seconds(self) -> float:
        """Time spent on the model's own work, excluding transfer and cache overhead."""
        return round(sum(v for k, v in self.stage_seconds.items()
                         if k not in OVERHEAD_STAGES), 2)

    @property
    def has_colour(self) -> bool:
        """Whether the run produced a mesh carrying colour, which the print requirement demands."""
        return TEXTURE_RANK.get(self.texture_status, 0) >= COLOUR_THRESHOLD

    def concerns(self) -> List[str]:
        """Signals that a good-looking metric may be hiding a degenerate result.

        Every entry here corresponds to a failure actually observed while building this pipeline, where
        the run reported success and the artifact was unusable.
        """
        notes = []
        if self.succeeded and not self.has_colour:
            notes.append(f"no colour (texture status '{self.texture_status}')")
        if self.texture_status == "fromVertexColors":
            notes.append("colour is per-vertex, so its detail is limited to the vertex spacing rather "
                         "than to a texture resolution")
        if self.texture_status == "projectedFromPhotograph":
            measured = (self.projected_colour or {}).get("verticesMeasured")
            decisive = (self.projected_colour or {}).get("poseDecisive")
            share = "an unrecorded share" if measured is None else f"{measured:.0%}"
            notes.append(f"colour came from the photograph rather than the model: {share} of the "
                         f"surface was sampled from it and the rest inferred from the nearest sampled "
                         f"point, so the side the camera could not see is a guess")
            if decisive is False:
                notes.append("the pose the colour was projected from was NOT decisive, so several "
                             "poses fitted the outline equally and the colour may be rotated or "
                             "mirrored on the subject")
        if self.pieces_fused:
            notes.append(f"a repair joined {self.pieces_fused} piece(s) of the subject; check "
                         f"the preview for a feature absorbed into the body")
        if self.bodies is not None and self.expected_bodies is not None \
                and self.bodies > self.expected_bodies:
            notes.append(f"mesh is in {self.bodies} pieces where {self.expected_bodies} was expected, "
                         f"so part of the subject is detached")
        if self.volume_retained is not None and self.volume_retained < 0.9:
            notes.append(f"repair removed {(1 - self.volume_retained):.0%} of the volume")
        if self.print_ready and self.face_count is not None and self.face_count < 5000:
            notes.append(f"passed the print gate on only {self.face_count} faces")
        genus = self.shape.get("genus")
        if genus:
            notes.append(f"the surface has {genus:.0f} tunnel(s) through it, which no print-gate "
                         f"criterion can see -- watertight, manifold and single-body are all satisfied "
                         f"by a closed surface carrying handles, and one surfacing as a pinhole is a "
                         f"visible defect in a likeness")
        if self.succeeded and not self.preview_path:
            notes.append("no preview retrieved, so the result was never looked at")
        notes.extend(self.peer_notes)
        return notes


def summarize(results) -> dict:
    """Aggregate counts and the per-axis leaders, without collapsing them into one ranking."""
    done = [r for r in results if r.succeeded]
    coloured = [r for r in done if r.has_colour]
    return {
        "runs": len(results),
        "succeeded": len(done),
        "failed": len([r for r in results if r.status not in ("SUCCEEDED", "unknown")]),
        "producedColour": len(coloured),
        "printReady": len([r for r in done if r.print_ready]),
        "withConcerns": len([r for r in done if r.concerns()]),
        "fastestModelSeconds": min((r.model_seconds for r in done), default=None),
        "mostFaces": max((r.face_count for r in done if r.face_count is not None), default=None),
    }


# How a shape descriptor is compared against the other models given the SAME image, per descriptor.
# Every model in a group reconstructed one photograph, so they should broadly agree; the one that does
# not is the one to look at.
#
# ``high``/``low`` are the factors away from the group median that count as a disagreement, and either
# may be None where a deviation in that direction is not a defect. All three are calibrated on four
# models given one mug and should be revisited as more subjects are measured:
#
#     descriptor        stable3dgen   sam3d   trellis2   triposr   median
#     fillRatio            0.217      0.435    0.436      0.936     0.436
#     footprintAspect      1.224      1.325    1.004      0.964     1.224
#     heightOverWidth      0.758      1.334    0.808      0.890     0.890
#
# fillRatio is deliberately one-sided. A run filling MORE of its hull than its siblings is the billet
# case; one filling less is the more hollow reconstruction, which for a vessel is fidelity rather than a
# defect -- a symmetric test flagged the best model in the group.
SHAPE_COMPARISONS = {
    "fillRatio": {
        "high": 1.6,
        "low": None,
        "high_meaning": ("fills much more of its convex hull than the other models given this image, "
                         "which is what a hollow subject reconstructed as a solid lump looks like"),
    },
    "footprintAspect": {
        "high": 1.25,
        "low": 1.25,
        "high_meaning": ("has a more elongated footprint than the other models given this image"),
        "low_meaning": ("has a rounder footprint than the other models given this image, which is what "
                        "a protruding feature such as a handle looks like when it is missing or has "
                        "been absorbed into the body"),
    },
    "heightOverWidth": {
        "high": 1.25,
        "low": 1.25,
        "high_meaning": ("makes the subject taller against its width than the other models given this "
                         "image, so at most one of them has the proportions right"),
        "low_meaning": ("makes the subject squatter against its width than the other models given this "
                        "image, so at most one of them has the proportions right"),
    },
}

# ``genus`` is deliberately absent from this table. It is a count of tunnels, and a comparison against the
# group median is the wrong instrument for it: measured on one photograph of a person the five models came
# out at 16, 9, 7, 5 and 8 tunnels, so the median is around 8 and a median test flags nobody while every
# result carries the defect. Any tunnel is a defect on its own terms, so it is reported as a per-run concern
# instead -- see RunResult.concerns.

# A group needs at least this many runs of one image for its median to be worth comparing against.
SHAPE_COMPARISON_MINIMUM = 3


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def flag_shape_outliers(results):
    """Annotate each result whose shape disagrees with the other models given the same image.

    This is the one likeness signal available with no ground truth and no model call: the models in a
    group all reconstructed the same photograph, so a descriptor where one of them departs from the
    median points at that one. It says nothing when the models agree -- including when they agree on
    something wrong -- which is why it is a signal to read against the preview and not a verdict.

    Mutates and returns ``results``.
    """
    by_image = {}
    for result in results:
        if result.succeeded and result.shape:
            by_image.setdefault(result.variation.image, []).append(result)

    for image, group in by_image.items():
        if len(group) < SHAPE_COMPARISON_MINIMUM:
            continue
        for key, rule in SHAPE_COMPARISONS.items():
            values = [r.shape[key] for r in group if r.shape.get(key)]
            if len(values) < SHAPE_COMPARISON_MINIMUM:
                continue
            median = _median(values)
            if median <= 0:
                continue
            for result in group:
                value = result.shape.get(key)
                if not value:
                    continue
                ratio = value / median
                high, low = rule.get("high"), rule.get("low")
                if high is not None and ratio >= high:
                    meaning = rule["high_meaning"]
                elif low is not None and ratio <= 1.0 / low:
                    meaning = rule["low_meaning"]
                else:
                    continue
                result.peer_notes.append(
                    f"{key} {value:.3f} against a median of {median:.3f} across the {len(values)} "
                    f"models on {image} ({ratio:.2f}x): it {meaning}")
    return results
