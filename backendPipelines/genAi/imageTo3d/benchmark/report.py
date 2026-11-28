# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rendering for the model comparison: a Markdown report and a CSV of the same rows.

The report presents quality, speed and cost together rather than ranking on one of them, and it names
the preview for every run. That is a deliberate design constraint, not a formatting preference: each
of the degenerate results seen while building this pipeline looked correct in the numbers and wrong in
the picture, so a report that does not put the picture in front of the reader is not fit for the
decision it exists to support.
"""

import csv
import io

from cost import cost_breakdown
from matrix import STAGE_ORDER, summarize

CSV_COLUMNS = ("label", "model", "image", "settings", "status", "colour", "textureStatus",
               "printReady", "faces", "fillRatio", "footprintAspect", "heightOverWidth",
               "medianCrossings", "shellLineShare",
               "volumeRetained", "modelSeconds", "totalSeconds",
               "instanceType", "gpu", "modelCost", "totalCost", "preview", "concerns")

# The quality table's columns and the headings over them, in one place so they cannot drift apart.
QUALITY_COLUMNS = (
    ("model", "Model"), ("settings", "Settings"), ("colour", "Colour"), ("printReady", "Print ready"),
    ("faces", "Faces"), ("fillRatio", "Fill"), ("footprintAspect", "Footprint aspect"),
    ("heightOverWidth", "Height/width"), ("medianCrossings", "Median crossings"),
    ("shellLineShare", "Shell share"), ("modelSeconds", "Model sec"), ("modelCost", "Model cost (USD)"),
    ("instanceType", "Instance"), ("concerns", "Concerns"))


def _shape(result, key, fmt=".3f") -> str:
    """One shape descriptor as text, blank when the run did not report it."""
    value = (result.shape or {}).get(key)
    return "" if value is None else f"{value:{fmt}}"


def _row(result, rates):
    rate = rates.get(result.instance_type)
    costs = cost_breakdown(result.stage_seconds, rate) if rate is not None else None
    return {
        "label": result.variation.label,
        "model": result.variation.model,
        "image": result.variation.image,
        "settings": ";".join(f"{k}={v}" for k, v in sorted(result.variation.settings.items())),
        "status": result.status,
        "colour": "yes" if result.has_colour else "no",
        "textureStatus": result.texture_status,
        "printReady": "" if result.print_ready is None else str(result.print_ready).lower(),
        "faces": "" if result.face_count is None else result.face_count,
        # Reported as VALUES, not only as flags. A median-based comparison cannot separate a
        # defect that half the group shares -- on four models given one mug, two had lost the
        # handle, so the footprint median sat between the two populations and flagged neither.
        # The reader comparing the column down the page can see it; the flag cannot.
        "fillRatio": _shape(result, "fillRatio"),
        "footprintAspect": _shape(result, "footprintAspect"),
        "heightOverWidth": _shape(result, "heightOverWidth"),
        # How many times a lattice ray along the longest axis crosses the surface. A solid reads 2.0 on
        # every ray; a second wall inside the first reads more than 2 on the rays through it, which no gate
        # criterion and no exterior view can see, and the share is how many rays read more than 2.
        "medianCrossings": _shape(result, "medianCrossings", ".1f"),
        "shellLineShare": _shape(result, "shellLineShare"),
        "volumeRetained": "" if result.volume_retained is None else f"{result.volume_retained:.4f}",
        "modelSeconds": result.model_seconds,
        "totalSeconds": result.total_seconds,
        "instanceType": result.instance_type,
        "gpu": result.gpu_name,
        "modelCost": "" if costs is None else f"{costs['modelCost']:.4f}",
        "totalCost": "" if costs is None else f"{costs['totalCost']:.4f}",
        "preview": result.preview_path,
        "concerns": "; ".join(result.concerns()),
    }


def to_csv(results, rates) -> str:
    """The comparison as CSV, one row per run."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for result in results:
        writer.writerow(_row(result, rates))
    return buffer.getvalue()


def _table(rows, columns, headings):
    if len(columns) != len(headings):
        raise ValueError(f"{len(columns)} columns under {len(headings)} headings")
    lines = ["| " + " | ".join(headings) + " |",
             "| " + " | ".join("---" for _ in headings) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(lines)


def to_markdown(results, rates, region="", missing_rates=()) -> str:
    """The comparison as Markdown: the three axes together, then the caveats, then per-stage detail."""
    rows = [_row(r, rates) for r in results]
    stats = summarize(results)

    out = ["# Image to 3D -- model comparison", ""]
    out.append(
        f"{stats['runs']} runs, {stats['succeeded']} succeeded, {stats['failed']} failed. "
        f"{stats['producedColour']} produced a coloured mesh and {stats['printReady']} passed the "
        f"print gate.")
    out.append("")

    if stats["withConcerns"]:
        out.append(
            f"> **{stats['withConcerns']} successful runs carry a concern.** A run can report success, "
            f"pass the print gate and still be unusable -- read the Concerns column and look at the "
            f"preview before drawing a conclusion from the timings.")
        out.append("")

    if not stats["producedColour"]:
        out.append(
            "> **No run produced a coloured mesh.** Colour is a hard requirement for the intended "
            "full-colour printing, so on this matrix no candidate satisfies it.")
        out.append("")

    out.append("## Quality, speed and cost")
    out.append("")
    out.append("Presented together deliberately; there is no single ranking, because the three trade "
               "against each other and the right choice depends on which one is binding.")
    out.append("")
    out.append(_table(rows, [c for c, _ in QUALITY_COLUMNS], [h for _, h in QUALITY_COLUMNS]))
    out.append("")
    out.append("Median crossings is how many times a ray along the subject's longest axis crosses the "
               "surface: a solid reads 2, and a second wall inside the first reads more than 2 on the "
               "rays through it. Shell share is the share of rays reading more than 2; a concavity such "
               "as the gap between a quadruped's legs also reads 4 on the rays through it, so the median "
               "is the robust indicator and the share is read against it. Neither the print gate nor an "
               "exterior view can see an inner wall; a section cut in the preview can.")
    out.append("")

    if missing_rates:
        out.append(
            f"> Cost is blank for {', '.join(sorted(set(missing_rates)))}: no on-demand rate could be "
            f"resolved{f' in {region}' if region else ''}. Durations are still reported. Supply a rate "
            f"override to price these rather than reading the blank as free.")
        out.append("")

    out.append("## Per-stage cost")
    out.append("")
    out.append("Cost is the time the instance was held while each stage ran. Weight cache, transfer "
               "and upload are overhead rather than model work, so they are totalled separately -- a "
               "model that spends its time fetching weights is a different proposition from one that "
               "spends it generating, at the same run cost.")
    out.append("")
    stage_headings = ["Model", "Settings"] + [s for s in STAGE_ORDER] + ["Model cost", "Overhead"]
    lines = ["| " + " | ".join(stage_headings) + " |",
             "| " + " | ".join("---" for _ in stage_headings) + " |"]
    for result in results:
        rate = rates.get(result.instance_type)
        costs = cost_breakdown(result.stage_seconds, rate) if rate is not None else None
        cells = [result.variation.model,
                 ";".join(f"{k}={v}" for k, v in sorted(result.variation.settings.items()))]
        for stage in STAGE_ORDER:
            seconds = result.stage_seconds.get(stage)
            if seconds is None:
                cells.append("")
            elif costs is None:
                cells.append(f"{seconds:.1f}s")
            else:
                cells.append(f"{seconds:.1f}s / ${costs['perStage'].get(stage, 0):.4f}")
        cells.append("" if costs is None else f"${costs['modelCost']:.4f}")
        cells.append("" if costs is None else f"${costs['overheadCost']:.4f}")
        lines.append("| " + " | ".join(cells) + " |")
    out.append("\n".join(lines))
    out.append("")

    out.append("## Previews")
    out.append("")
    out.append("Look at these before trusting the table above.")
    out.append("")
    for result in results:
        if result.preview_path:
            out.append(f"-   **{result.variation.label}** -- `{result.preview_path}`")
        elif result.succeeded:
            out.append(f"-   **{result.variation.label}** -- no preview retrieved")
    out.append("")

    failures = [r for r in results if r.status not in ("SUCCEEDED", "unknown")]
    if failures:
        out.append("## Failures")
        out.append("")
        for result in failures:
            out.append(f"-   **{result.variation.label}** -- {result.status}: "
                       f"{result.error or 'no error recorded'}")
        out.append("")

    return "\n".join(out)
