# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 9: the deterministic merge of per-window extractions (no model call)."""

import re
from dataclasses import dataclass, field

from .vocab import normalize_vocab_value

STEP_DEDUPE_WINDOW_S = 5.0
KEY_MOMENT_DEDUPE_S = 2.0

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


@dataclass
class Merged:
    steps: list = field(default_factory=list)
    components: list = field(default_factory=list)
    key_moments: list = field(default_factory=list)
    product_name_from_narration: str = ""
    window_count: int = 0


def normalise_text(value):
    """The dedupe key: vocab's ASCII fold, lower-cased, punctuation dropped, whitespace collapsed."""
    lowered = _NON_WORD.sub(" ", normalize_vocab_value(value).lower())
    return _SPACES.sub(" ", lowered).strip()


def _step_key(step):
    return (normalise_text(step.get("action")), normalise_text(step.get("component")))


def _merge_steps(results):
    """Dedupe key = (normalised action + component, |Δt| ≤ 5 s); the copy from the window whose centre is
    nearer wins. Each window's local numbering is mapped to the kept entries, then to final numbers."""
    kept = []  # {"key", "t", "distance", "step", "window"}
    local_maps = {}  # window index -> {local step number: kept index}
    for window, result in results:
        local_map = {}
        for step in result.get("steps", []) or []:
            t = float(step.get("timestamp_seconds", 0.0))
            key = _step_key(step)
            distance = abs(t - window.centre_s)
            match = next((i for i, entry in enumerate(kept) if entry["key"] == key and abs(entry["t"] - t) <= STEP_DEDUPE_WINDOW_S), None)
            if match is None:
                kept.append({"key": key, "t": t, "distance": distance, "step": dict(step), "window": window.index})
                match = len(kept) - 1
            elif distance < kept[match]["distance"]:
                kept[match].update(t=t, distance=distance, step=dict(step), window=window.index)
            local_map[step.get("step")] = match
        local_maps[window.index] = local_map

    order = sorted(range(len(kept)), key=lambda i: (kept[i]["t"], i))
    final_number = {kept_index: number for number, kept_index in enumerate(order, start=1)}

    merged = []
    for kept_index in order:
        entry = kept[kept_index]
        step = dict(entry["step"])
        local_map = local_maps.get(entry["window"], {})
        dependencies = []
        for dependency in step.get("dependencies", []) or []:
            local = dependency.get("step")
            target = local_map.get(local) if local is not None else None
            dependencies.append({"step": final_number.get(target) if target is not None else None, "text": dependency.get("text", "")})
        step["dependencies"] = dependencies
        step["step"] = final_number[kept_index]
        step["source_window"] = entry["window"]
        merged.append(step)
    return merged


def _merge_components(results):
    seen = {}
    for _, result in results:
        for component in result.get("components", []) or []:
            key = normalise_text(component.get("part_description"))
            if not key:
                continue
            existing = seen.get(key)
            if existing is None:
                seen[key] = dict(component)
                continue
            if existing.get("mass_g_per_unit") is None and component.get("mass_g_per_unit") is not None:
                existing["mass_g_per_unit"] = component["mass_g_per_unit"]
            if not existing.get("primary_manufacturing_process") and component.get("primary_manufacturing_process"):
                existing["primary_manufacturing_process"] = component["primary_manufacturing_process"]
            existing["qty"] = max(existing.get("qty") or 1, component.get("qty") or 1)
    return list(seen.values())


def _merge_key_moments(results):
    moments = sorted(
        (dict(moment) for _, result in results for moment in (result.get("key_moments", []) or [])),
        key=lambda moment: float(moment.get("timestamp_seconds", 0.0)),
    )
    deduped = []
    for moment in moments:
        if deduped and float(moment["timestamp_seconds"]) - float(deduped[-1]["timestamp_seconds"]) < KEY_MOMENT_DEDUPE_S:
            continue
        deduped.append(moment)
    return deduped


def merge_windows(results):
    """`results` is the ordered list of (Window, record_window_extraction payload) from stage 8."""
    narrated = next((result.get("product_name_from_narration", "") for _, result in results if result.get("product_name_from_narration")), "")
    return Merged(
        steps=_merge_steps(results),
        components=_merge_components(results),
        key_moments=_merge_key_moments(results),
        product_name_from_narration=narrated,
        window_count=len(results),
    )
