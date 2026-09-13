#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The classification vocabulary the analysis prompt offers and the model reply is checked against:
the shipped default's shape and size, its normalisation from an admin-edited configBody, the prompt
section, and the open/closed post-validation rules of spec §6.5 step 4."""

import copy
import json

import pytest

import sysgenai_harness as h

cv = h.load_local("classificationVocabulary")

SPEC_CATEGORIES = ["Vehicle", "Industrial Equipment", "Architecture", "Interior & Furniture", "Terrain & Environment",
                   "Character & Creature", "Prop & Object", "Infrastructure & Utility", "Medical & Scientific",
                   "Electronics", "Document", "Media", "Data", "Other"]
SPEC_STYLES = ["realistic", "stylized", "low-poly", "technical/CAD", "scanned", "schematic", "other"]

REPLY = {"title": "Gear pump", "description": "A pump.", "keywords": ["pump"], "category": "vehicle",
         "subcategory": "car", "style": "Realistic", "materials": ["Steel", "carbon fibre"], "colors": ["RED", "red"],
         "objects": ["pump"], "complexity": "medium", "orientation": None, "sizeEstimate": None, "textSummary": None}


def _closed(**over):
    vocab = copy.deepcopy(cv.DEFAULT_VOCABULARY)
    vocab["allowUnlisted"] = False
    vocab.update(over)
    return vocab


@pytest.mark.unit
class TestDefaultVocabulary:
    def test_shape_matches_the_spec(self):
        vocab = cv.DEFAULT_VOCABULARY
        assert list(vocab["categories"]) == SPEC_CATEGORIES
        for name, spec in vocab["categories"].items():
            assert isinstance(spec["description"], str) and spec["description"].strip(), name
            assert 3 <= len(spec["subcategories"]) <= 8, name
            assert all(isinstance(sub, str) and sub.strip() for sub in spec["subcategories"]), name
        assert vocab["styles"] == SPEC_STYLES
        assert len(vocab["materials"]) == 16 and "metal" in vocab["materials"] and "other" in vocab["materials"]
        assert len(vocab["colors"]) == 16 and {"black", "white", "red", "green", "blue"} <= set(vocab["colors"])
        assert vocab["allowUnlisted"] is True
        assert set(vocab) == {"categories", "styles", "materials", "colors", "allowUnlisted"}

    def test_serialised_default_stays_small(self):
        size = len(json.dumps(cv.DEFAULT_VOCABULARY, separators=(",", ":")).encode("utf-8"))
        assert 1500 < size < cv.VOCABULARY_MAX_BYTES == 6 * 1024, size

    def test_names_are_unique_case_insensitively(self):
        names = [name.lower() for name in cv.DEFAULT_VOCABULARY["categories"]]
        assert len(names) == len(set(names))
        for key in ("styles", "materials", "colors"):
            values = [value.lower() for value in cv.DEFAULT_VOCABULARY[key]]
            assert len(values) == len(set(values)), key
        for name, spec in cv.DEFAULT_VOCABULARY["categories"].items():
            subs = [sub.lower() for sub in spec["subcategories"]]
            assert len(subs) == len(set(subs)), name

    def test_the_default_is_already_normalised(self):
        assert cv.normalize_vocabulary(cv.DEFAULT_VOCABULARY) == cv.DEFAULT_VOCABULARY
        assert cv.normalize_vocabulary(cv.DEFAULT_VOCABULARY) is not cv.DEFAULT_VOCABULARY


@pytest.mark.unit
class TestNormalize:
    @pytest.mark.parametrize("raw", [None, "vocabulary", [], 7, {}])
    def test_unusable_input_yields_the_default(self, raw):
        assert cv.normalize_vocabulary(raw) == cv.DEFAULT_VOCABULARY

    def test_missing_or_invalid_lists_fall_back_to_the_default(self):
        vocab = cv.normalize_vocabulary({"categories": {"Widget": {"description": "Things", "subcategories": ["Small"]}},
                                         "styles": "realistic", "colors": []})
        assert vocab["categories"] == {"Widget": {"description": "Things", "subcategories": ["Small"]}}
        assert vocab["styles"] == cv.DEFAULT_VOCABULARY["styles"]
        assert vocab["materials"] == cv.DEFAULT_VOCABULARY["materials"]
        assert vocab["colors"] == cv.DEFAULT_VOCABULARY["colors"]
        assert vocab["allowUnlisted"] is True

    def test_categories_may_be_a_plain_list_or_a_name_to_list_map(self):
        vocab = cv.normalize_vocabulary({"categories": ["Widget", " Gadget "]})
        assert vocab["categories"] == {"Widget": {"description": "", "subcategories": []},
                                       "Gadget": {"description": "", "subcategories": []}}
        vocab = cv.normalize_vocabulary({"categories": {"Widget": ["Small", "Large"]}})
        assert vocab["categories"]["Widget"] == {"description": "", "subcategories": ["Small", "Large"]}

    @pytest.mark.parametrize("raw,expected", [(False, False), ("false", False), ("0", False), (True, True),
                                              ("yes", True), (None, True), ("maybe", True)])
    def test_allow_unlisted_is_coerced(self, raw, expected):
        assert cv.normalize_vocabulary({"allowUnlisted": raw})["allowUnlisted"] is expected

    def test_lists_are_whitespace_normalised_and_deduplicated(self):
        vocab = cv.normalize_vocabulary({"materials": [" steel ", "Steel", "carbon  fibre", "", None, 7]})
        assert vocab["materials"] == ["steel", "carbon fibre", "7"]
        vocab = cv.normalize_vocabulary({"categories": {" Widget ": {"description": "  A  thing ",
                                                                     "subcategories": ["a", "A", " b "]}}})
        assert vocab["categories"] == {"Widget": {"description": "A thing", "subcategories": ["a", "b"]}}

    def test_normalisation_is_idempotent(self):
        once = cv.normalize_vocabulary({"categories": ["X"], "styles": ["s"], "allowUnlisted": "false"})
        assert cv.normalize_vocabulary(once) == once


@pytest.mark.unit
class TestPromptSection:
    def test_layout_of_the_open_default(self):
        section = cv.build_vocabulary_prompt_section(cv.DEFAULT_VOCABULARY)
        lines = section.split("\n")
        assert lines[0].startswith("CATEGORY OPTIONS")
        assert lines[1].startswith("- Vehicle: ") and "Subcategories: Car, Truck" in lines[1]
        assert lines[1 + len(SPEC_CATEGORIES) - 1].startswith("- Other: ")
        assert "STYLE OPTIONS: realistic, stylized, low-poly, technical/CAD, scanned, schematic, other" in lines
        assert any(line.startswith("MATERIAL OPTIONS: metal, ") for line in lines)
        assert any(line.startswith("COLOR OPTIONS: ") for line in lines)
        assert lines[-1] == "You may use values outside these lists when none fits."

    def test_closed_vocabulary_sentence(self):
        section = cv.build_vocabulary_prompt_section(_closed())
        assert "outside these lists" not in section
        assert section.endswith('Use only values from these lists. Use the category "Other" when none fits, and omit a '
                                "subcategory, style, material or color that is not listed.")

    def test_a_custom_vocabulary_is_printed_in_its_own_order_and_normalised_first(self):
        section = cv.build_vocabulary_prompt_section({"categories": {"Gadget": {"description": "Small devices",
                                                                                "subcategories": []},
                                                                     "Widget": ["Round"]},
                                                      "styles": ["  cartoon "], "allowUnlisted": "false"})
        lines = section.split("\n")
        assert lines[1] == "- Gadget: Small devices" and lines[2] == "- Widget. Subcategories: Round"
        assert "STYLE OPTIONS: cartoon" in lines
        assert "MATERIAL OPTIONS: " + ", ".join(cv.DEFAULT_VOCABULARY["materials"]) in lines


@pytest.mark.unit
class TestValidateOpen:
    def test_case_normalised_to_the_vocabulary_spelling(self):
        result, corrections = cv.validate_against_vocabulary(REPLY, cv.DEFAULT_VOCABULARY)
        assert result["category"] == "Vehicle" and result["subcategory"] == "Car" and result["style"] == "realistic"
        assert result["materials"] == ["steel", "carbon fibre"]
        assert result["colors"] == ["red"]
        assert corrections == ["category: 'vehicle' -> 'Vehicle'", "subcategory: 'car' -> 'Car'",
                               "style: 'Realistic' -> 'realistic'", "materials: 'Steel' -> 'steel'",
                               "colors: 'RED' -> 'red'"]
        # Untouched keys ride along and the input is not mutated.
        assert result["title"] == "Gear pump" and result["complexity"] == "medium"
        assert REPLY["category"] == "vehicle" and REPLY["colors"] == ["RED", "red"]

    def test_off_list_values_are_kept(self):
        reply = dict(REPLY, category="Furniture Hardware", subcategory="Hinge", style="baroque",
                     materials=["brass"], colors=["teal"])
        result, corrections = cv.validate_against_vocabulary(reply, cv.DEFAULT_VOCABULARY)
        assert (result["category"], result["subcategory"], result["style"]) == ("Furniture Hardware", "Hinge", "baroque")
        assert result["materials"] == ["brass"] and result["colors"] == ["teal"]
        assert corrections == []

    def test_a_subcategory_is_matched_against_its_own_category_only(self):
        reply = dict(REPLY, category="Electronics", subcategory="car")
        result, corrections = cv.validate_against_vocabulary(reply, cv.DEFAULT_VOCABULARY)
        # "car" is a Vehicle subcategory, not an Electronics one: kept as given (open mode) and not corrected.
        assert result["subcategory"] == "car"
        assert not any(line.startswith("subcategory") for line in corrections)

    def test_blank_values_become_none_or_empty(self):
        reply = dict(REPLY, category="  ", subcategory="", style=None, materials=None, colors="red")
        result, corrections = cv.validate_against_vocabulary(reply, cv.DEFAULT_VOCABULARY)
        assert result["category"] == "" and result["subcategory"] is None and result["style"] is None
        assert result["materials"] == [] and result["colors"] == [] and corrections == []


@pytest.mark.unit
class TestValidateClosed:
    def test_category_falls_back_to_other_and_the_subcategory_drops(self):
        reply = dict(REPLY, category="Furniture Hardware", subcategory="Hinge")
        result, corrections = cv.validate_against_vocabulary(reply, _closed())
        assert result["category"] == "Other" and result["subcategory"] is None
        assert corrections[:2] == ["category: 'Furniture Hardware' -> 'Other' (not in vocabulary)",
                                   "subcategory: 'Hinge' dropped (not in vocabulary)"]

    def test_style_drops_and_off_list_materials_and_colors_are_removed(self):
        reply = dict(REPLY, style="baroque", materials=["Steel", "unobtainium"], colors=["teal", "Red"])
        result, corrections = cv.validate_against_vocabulary(reply, _closed())
        assert result["style"] is None
        assert result["materials"] == ["steel"] and result["colors"] == ["red"]
        assert "style: 'baroque' dropped (not in vocabulary)" in corrections
        assert "materials: 'unobtainium' removed (not in vocabulary)" in corrections
        assert "colors: 'teal' removed (not in vocabulary)" in corrections

    def test_listed_values_survive_closed_mode_unchanged(self):
        reply = dict(REPLY, category="Vehicle", subcategory="Car", style="realistic", materials=["steel"], colors=["red"])
        result, corrections = cv.validate_against_vocabulary(reply, _closed())
        assert (result["category"], result["subcategory"], result["style"]) == ("Vehicle", "Car", "realistic")
        assert result["materials"] == ["steel"] and result["colors"] == ["red"] and corrections == []

    def test_other_is_the_fallback_even_when_the_vocabulary_lacks_it(self):
        vocab = _closed(categories={"Widget": {"description": "", "subcategories": []}})
        result, corrections = cv.validate_against_vocabulary(dict(REPLY, category="Gizmo"), vocab)
        assert result["category"] == cv.FALLBACK_CATEGORY == "Other"
        assert corrections[0] == "category: 'Gizmo' -> 'Other' (not in vocabulary)"
