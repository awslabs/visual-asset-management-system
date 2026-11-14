#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The classification vocabulary offered to the analysis model and applied to its reply.

The template's configBody carries a ``classificationVocabulary`` object an admin edits without a deploy;
``DEFAULT_VOCABULARY`` is the shipped value. ``normalize_vocabulary`` puts a configured vocabulary in its
canonical shape and caps its size: one whose canonical JSON exceeds ``VOCABULARY_MAX_BYTES`` is replaced by
the default, so an edit cannot grow the prompt without bound. ``build_vocabulary_prompt_section`` renders it
into the user message; ``validate_against_vocabulary`` post-checks the reply — an open vocabulary keeps
off-list values (case-normalised to the vocabulary spelling when one matches), a closed one applies the
fallbacks: category -> Other, subcategory and style dropped, off-list materials and colors removed.
"""

import copy
import json
from typing import Any, Dict, List, Optional, Tuple

# The most bytes a configured vocabulary may take in its canonical JSON; a larger one is replaced by the default.
VOCABULARY_MAX_BYTES = 6 * 1024
FALLBACK_CATEGORY = "Other"

DEFAULT_VOCABULARY: Dict[str, Any] = {
    "categories": {
        "Vehicle": {
            "description": "Land, sea, air and space vehicles and their major assemblies",
            "subcategories": ["Car", "Truck", "Motorcycle", "Aircraft", "Watercraft", "Spacecraft", "Rail",
                              "Vehicle Part"],
        },
        "Industrial Equipment": {
            "description": "Machinery, tools and plant equipment used in production or maintenance",
            "subcategories": ["Machine", "Pump", "Valve", "Robot", "Tool", "Conveyor", "Engine", "Fixture"],
        },
        "Architecture": {
            "description": "Buildings, structures and building elements",
            "subcategories": ["Residential", "Commercial", "Industrial", "Historic", "Structural Element", "Facade",
                              "Roof", "Site Plan"],
        },
        "Interior & Furniture": {
            "description": "Furnishings, fixtures and interior decor",
            "subcategories": ["Seating", "Table", "Storage", "Bed", "Lighting", "Kitchen", "Bathroom", "Decor"],
        },
        "Terrain & Environment": {
            "description": "Landscapes, terrain, vegetation and outdoor scenes",
            "subcategories": ["Terrain", "Vegetation", "Water Body", "Urban Scene", "Rural Scene", "Survey Scan",
                              "Geological"],
        },
        "Character & Creature": {
            "description": "Humans, animals and fictional beings",
            "subcategories": ["Human", "Animal", "Fantasy Creature", "Robot Character", "Mannequin", "Anatomy"],
        },
        "Prop & Object": {
            "description": "Everyday objects, containers, weapons and small items",
            "subcategories": ["Household Item", "Container", "Weapon", "Sports Equipment", "Toy", "Food",
                              "Musical Instrument", "Sign"],
        },
        "Infrastructure & Utility": {
            "description": "Roads, bridges, pipelines, power and communication networks",
            "subcategories": ["Road", "Bridge", "Tunnel", "Pipeline", "Power", "Telecom", "Rail Infrastructure",
                              "Street Furniture"],
        },
        "Medical & Scientific": {
            "description": "Medical devices, anatomy, laboratory and scientific instruments",
            "subcategories": ["Medical Device", "Anatomical Model", "Laboratory Equipment", "Molecular",
                              "Scientific Instrument", "Prosthetic"],
        },
        "Electronics": {
            "description": "Consumer and industrial electronic devices and components",
            "subcategories": ["Computer", "Phone", "Camera", "Display", "Circuit Board", "Sensor", "Appliance",
                              "Cable & Connector"],
        },
        "Document": {
            "description": "Written documents, drawings and presentations",
            "subcategories": ["Report", "Specification", "Drawing", "Manual", "Presentation", "Form",
                              "Correspondence", "Source Code"],
        },
        "Media": {
            "description": "Photographs, video and audio recordings",
            "subcategories": ["Photograph", "Render", "Video Recording", "Animation", "Audio Recording", "Music",
                              "Screenshot", "Icon"],
        },
        "Data": {
            "description": "Tabular, geospatial and structured data files",
            "subcategories": ["Tabular", "Geospatial", "Time Series", "Configuration", "Log", "Simulation Output",
                              "Schema"],
        },
        "Other": {
            "description": "Content that fits none of the other categories",
            "subcategories": ["Abstract", "Test Asset", "Unknown"],
        },
    },
    "styles": ["realistic", "stylized", "low-poly", "technical/CAD", "scanned", "schematic", "other"],
    "materials": ["metal", "steel", "aluminium", "plastic", "wood", "glass", "concrete", "stone", "brick", "fabric",
                  "leather", "rubber", "ceramic", "composite", "paper", "other"],
    "colors": ["black", "white", "gray", "silver", "red", "orange", "yellow", "green", "blue", "purple", "pink",
               "brown", "beige", "gold", "transparent", "multicolor"],
    "allowUnlisted": True,
}


def _clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _string_list(values, fallback: List[str]) -> List[str]:
    """Whitespace-normalised, case-insensitively de-duplicated strings; the fallback when nothing is usable."""
    if not isinstance(values, list):
        return list(fallback)
    out: List[str] = []
    seen = set()
    for value in values:
        text = _clean(value)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out or list(fallback)


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return default


def encoded_size(vocab: dict) -> int:
    """The bytes of a vocabulary's canonical JSON (compact separators, UTF-8), the measure the cap applies to."""
    return len(json.dumps(vocab, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def _normalize_shape(raw: dict, default: dict) -> dict:
    """The canonical shape of a vocabulary object, every list filled from the default when missing or unusable."""
    categories: Dict[str, dict] = {}
    raw_categories = raw.get("categories")
    if isinstance(raw_categories, dict):
        for name, spec in raw_categories.items():
            label = _clean(name)
            if not label:
                continue
            if isinstance(spec, dict):
                categories[label] = {"description": _clean(spec.get("description")),
                                     "subcategories": _string_list(spec.get("subcategories"), [])}
            elif isinstance(spec, list):
                categories[label] = {"description": "", "subcategories": _string_list(spec, [])}
            else:
                categories[label] = {"description": _clean(spec), "subcategories": []}
    elif isinstance(raw_categories, list):
        for name in _string_list(raw_categories, []):
            categories[name] = {"description": "", "subcategories": []}
    return {
        "categories": categories or default["categories"],
        "styles": _string_list(raw.get("styles"), default["styles"]),
        "materials": _string_list(raw.get("materials"), default["materials"]),
        "colors": _string_list(raw.get("colors"), default["colors"]),
        "allowUnlisted": _as_bool(raw.get("allowUnlisted"), True),
    }


def exceeds_cap(raw: Any) -> bool:
    """True when ``raw`` is a vocabulary object whose canonical JSON exceeds VOCABULARY_MAX_BYTES, so
    ``normalize_vocabulary`` replaces it by the default."""
    if not isinstance(raw, dict):
        return False
    return encoded_size(_normalize_shape(raw, copy.deepcopy(DEFAULT_VOCABULARY))) > VOCABULARY_MAX_BYTES


def normalize_vocabulary(raw: Any) -> dict:
    """The vocabulary in its canonical shape, every list filled from the default when missing or unusable.
    ``categories`` may arrive as the ``{name: {description, subcategories}}`` object, as ``{name: [subs]}``,
    or as a plain list of names. A vocabulary over VOCABULARY_MAX_BYTES in its canonical JSON is replaced
    by the default. Idempotent."""
    default = copy.deepcopy(DEFAULT_VOCABULARY)
    if not isinstance(raw, dict):
        return default
    normalized = _normalize_shape(raw, default)
    if encoded_size(normalized) > VOCABULARY_MAX_BYTES:
        return default
    return normalized


def vocabulary_prompt_parts(vocab: dict) -> Tuple[str, List[str], str]:
    """The vocabulary as the prompt offers it, in three pieces: the opening instruction, the option lines
    (categories with descriptions and subcategories in the vocabulary's own order, then the style, material
    and color lists), and the closing open/closed instruction. The two instructions are the pipeline's own
    words; the option lines are what the operator edits on the template."""
    vocab = normalize_vocabulary(vocab)
    opening = "CATEGORY OPTIONS (pick one category and, when one fits, one of its subcategories):"
    options = []
    for name, spec in vocab["categories"].items():
        line = f"- {name}"
        if spec["description"]:
            line += f": {spec['description']}"
        if spec["subcategories"]:
            line += ". Subcategories: " + ", ".join(spec["subcategories"])
        options.append(line)
    options.append("STYLE OPTIONS: " + ", ".join(vocab["styles"]))
    options.append("MATERIAL OPTIONS: " + ", ".join(vocab["materials"]))
    options.append("COLOR OPTIONS: " + ", ".join(vocab["colors"]))
    if vocab["allowUnlisted"]:
        closing = "You may use values outside these lists when none fits."
    else:
        closing = ('Use only values from these lists. Use the category "Other" when none fits, and omit a '
                   "subcategory, style, material or color that is not listed.")
    return opening, options, closing


def build_vocabulary_prompt_section(vocab: dict) -> str:
    """The three vocabulary pieces joined in prompt order, for a prompt that is not split into guarded and
    plain blocks."""
    opening, options, closing = vocabulary_prompt_parts(vocab)
    return "\n".join([opening, *options, closing])


def _match(value: str, options: List[str]) -> Optional[str]:
    """The option whose case-insensitive spelling equals ``value``."""
    lowered = value.lower()
    for option in options:
        if option.lower() == lowered:
            return option
    return None


def _validate_scalar(value, options: List[str], allow_unlisted: bool, label: str, fallback: Optional[str],
                     corrections: List[str]) -> Optional[str]:
    text = _clean(value)
    if not text:
        return None
    match = _match(text, options)
    if match is not None:
        if match != text:
            corrections.append(f"{label}: '{text}' -> '{match}'")
        return match
    if allow_unlisted:
        return text
    if fallback is not None:
        corrections.append(f"{label}: '{text}' -> '{fallback}' (not in vocabulary)")
        return fallback
    corrections.append(f"{label}: '{text}' dropped (not in vocabulary)")
    return None


def _validate_list(values, options: List[str], allow_unlisted: bool, label: str,
                   corrections: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        text = _clean(value)
        if not text:
            continue
        match = _match(text, options)
        if match is None and not allow_unlisted:
            corrections.append(f"{label}: '{text}' removed (not in vocabulary)")
            continue
        if match is not None and match != text:
            corrections.append(f"{label}: '{text}' -> '{match}'")
        final = match or text
        if final.lower() not in seen:
            seen.add(final.lower())
            out.append(final)
    return out


def validate_against_vocabulary(result: dict, vocab: dict) -> Tuple[dict, List[str]]:
    """``(validated copy, corrections)``. Open lists keep off-list values, case-normalised to the vocabulary
    spelling when one matches; closed lists apply the fallbacks (category -> Other, subcategory and style
    dropped, off-list materials and colors removed). Every change is one line in ``corrections``."""
    vocab = normalize_vocabulary(vocab)
    allow = vocab["allowUnlisted"]
    corrections: List[str] = []
    out = dict(result or {})

    category = _validate_scalar(out.get("category"), list(vocab["categories"]), allow, "category",
                                FALLBACK_CATEGORY, corrections) or ""
    out["category"] = category
    subcategories = (vocab["categories"].get(category) or {}).get("subcategories", [])
    out["subcategory"] = _validate_scalar(out.get("subcategory"), subcategories, allow, "subcategory", None,
                                          corrections)
    out["style"] = _validate_scalar(out.get("style"), vocab["styles"], allow, "style", None, corrections)
    out["materials"] = _validate_list(out.get("materials"), vocab["materials"], allow, "materials", corrections)
    out["colors"] = _validate_list(out.get("colors"), vocab["colors"], allow, "colors", corrections)
    return out, corrections
