#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The MEDIA branch's vocabulary: the extension table equals the spec §6.3 MEDIA rows, every listed
extension is one a viewer serves (`.webp` excepted -- the spec lists it and no viewer does), the entries
the pipeline's classifier treats differently are named with their reasons, the promotion source contract
equals the master §3.6 registry, and the text and result helpers behave as the extractors rely on."""

import json
import os

import pytest

from media_extractors import common

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *([".."] * 6)))
_VIEWER_CONFIG = os.path.join(_REPO_ROOT, "web", "src", "visualizerPlugin", "config", "viewerConfig.json")

# Spec §6.3, MEDIA rows, transcribed literally.
SPEC_MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
    "video": {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v"},
    "audio": {".mp3", ".wav", ".ogg", ".aac", ".flac", ".m4a"},
    "document": {".pdf", ".docx", ".pptx"},
    "text": {".txt", ".md", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".inf", ".log",
             ".py", ".js", ".ts", ".sql", ".sh", ".ps1", ".ipynb", ".html", ".htm"},
    "data": {".csv", ".fcs", ".xlsx"},
}
# The one spec extension no viewer serves; the allow-list rule (§6.3) keeps it out of the pipeline's
# allow list, and the image still handles it should a viewer gain it.
_NOT_A_VIEWER_EXTENSION = {".webp"}
# Office formats no viewer renders, admitted by the pipeline's ADDITIONAL_EXTENSIONS for their text.
_OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
_FILE_CLASSIFIER = os.path.join(_REPO_ROOT, "backendPipelines", "system", "genAiMetadata", "lambda", "fileClassifier.py")


def _file_classifier():
    """`lambda/fileClassifier.py`, loaded by path under a suite-private name (it imports only json and struct)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("system_genai_media_file_classifier", _FILE_CLASSIFIER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Master §3.6 "Promotion source contract", MEDIA groups, transcribed literally; nested objects as dotted paths.
REGISTRY_PROMOTION_SOURCES = {
    "sys_image": ("width", "height", "mode", "exif"),
    "sys_image.exif": ("make", "model", "dateTimeOriginal", "gps"),
    "sys_image.exif.gps": ("latitude", "longitude", "altitude"),
    "sys_media": ("kind", "durationSeconds", "width", "height", "frameRate", "videoCodec", "audioCodec",
                  "bitrateKbps", "channels", "sampleRate", "tags"),
    "sys_media.tags": ("title", "artist", "album", "year"),
    "sys_document": ("pageCount", "title", "author", "createdAt", "hasText"),
    "sys_text": ("encoding", "lineCount", "wordCount", "language"),
    "sys_data": ("columnCount", "rowCount", "columns"),
    "sys_tiles3d": ("geometricError", "tileCount", "region"),
    "sys_geo": ("featureCount", "geometryTypes", "footprint"),
}
_MEDIA_ATTRIBUTE_GROUPS = {"sys_image", "sys_media", "sys_document", "sys_text", "sys_data", "sys_tiles3d", "sys_geo"}


@pytest.mark.unit
class TestExtensionTable:
    def test_every_spec_row_is_present_with_its_class(self):
        for file_class, extensions in SPEC_MEDIA_EXTENSIONS.items():
            for extension in extensions:
                assert common.MEDIA_EXTENSION_CLASSES[extension] == file_class, extension

    def test_no_extension_beyond_the_spec_rows(self):
        expected = set().union(*SPEC_MEDIA_EXTENSIONS.values())
        assert set(common.MEDIA_EXTENSION_CLASSES) == expected

    def test_lookup_is_case_insensitive_and_closed(self):
        assert common.class_for_extension(".PNG") == common.CLASS_IMAGE
        assert common.class_for_extension(".glb") is None
        assert common.class_for_extension("") is None
        assert common.class_for_extension(None) is None

    def test_table_values_are_media_classes(self):
        assert set(common.MEDIA_EXTENSION_CLASSES.values()) <= set(common.MEDIA_CLASSES)
        assert common.CLASS_TILES3D in common.MEDIA_CLASSES
        assert common.CLASS_OTHER not in common.MEDIA_CLASSES

    def test_media_extensions_are_viewer_extensions(self):
        with open(_VIEWER_CONFIG, encoding="utf-8") as handle:
            viewers = json.load(handle)["viewers"]
        # Control: the catalog read must be non-trivial, or the subset assertion below is vacuous.
        assert len(viewers) >= 10
        viewer_extensions = {
            extension.lower()
            for viewer in viewers
            if viewer.get("enabled", True)
            for extension in viewer.get("supportedExtensions", [])
            if extension != "*"
        }
        assert set(common.MEDIA_EXTENSION_CLASSES) - viewer_extensions == _NOT_A_VIEWER_EXTENSION | _OFFICE_EXTENSIONS

    def test_classifier_overrides_are_table_entries_with_a_reason(self):
        # The pipeline's `lambda/fileClassifier.py` suite diffs its MEDIA rows against this table minus these
        # entries; a new divergence is named here with its reason rather than widening the gap silently.
        assert set(common.CLASSIFIER_OVERRIDE_EXTENSIONS) == {".webp", ".json"}
        assert set(common.CLASSIFIER_OVERRIDE_EXTENSIONS) <= set(common.MEDIA_EXTENSION_CLASSES)
        assert _NOT_A_VIEWER_EXTENSION <= set(common.CLASSIFIER_OVERRIDE_EXTENSIONS)
        assert all(reason.strip() for reason in common.CLASSIFIER_OVERRIDE_EXTENSIONS.values())

    def test_office_rows_match_the_classifier_additional_extensions(self):
        """The office formats join the pipeline through the classifier's ADDITIONAL_EXTENSIONS; this table lists
        exactly those, under the classes the classifier gives them, and they are not classifier overrides
        (both sides agree on them)."""
        fc = _file_classifier()
        assert set(fc.ADDITIONAL_EXTENSIONS) == _OFFICE_EXTENSIONS
        for extension in _OFFICE_EXTENSIONS:
            file_class, branch = fc.EXTENSION_CLASSES[extension]
            assert branch == common.RENDER_BRANCH
            assert common.MEDIA_EXTENSION_CLASSES[extension] == file_class, extension
            assert extension not in common.CLASSIFIER_OVERRIDE_EXTENSIONS


@pytest.mark.unit
class TestTextHelpers:
    def test_truncate_keeps_text_within_the_limit(self):
        assert common.truncate_text("short", 10) == "short"

    def test_truncate_cuts_back_to_whitespace_inside_the_final_tenth(self):
        assert common.truncate_text("alpha beta gamma delta", 18) == "alpha beta gamma"

    def test_truncate_hard_cuts_when_the_window_has_no_whitespace(self):
        assert common.truncate_text("a" * 100, 40) == "a" * 40

    def test_truncate_zero_limit_is_empty(self):
        assert common.truncate_text("abc", 0) == ""

    def test_human_duration(self):
        assert common.human_duration(7) == "7 s"
        assert common.human_duration(92.48) == "1 min 32 s"
        assert common.human_duration(7500) == "2 h 05 min"
        assert common.human_duration(-3) == "0 s"

    def test_human_count(self):
        assert common.human_count(1, "page") == "1 page"
        assert common.human_count(1234, "page") == "1,234 pages"


@pytest.mark.unit
class TestResultShapes:
    def test_branch_result_defaults(self):
        result = common.BranchResult(file_class=common.CLASS_IMAGE)
        assert result.attributes == {}
        assert result.render_images == []
        assert result.text_excerpt == ""
        assert result.facts == {}
        assert result.warnings == []
        assert result.render_skipped is None
        assert result.full_text == ""
        assert result.full_text_truncated is False
        assert result.page_offsets == []

    def test_extract_context_capture_flag_defaults_off(self):
        # A caller that does not know about the capture never pays for a whole-document read.
        ctx = common.ExtractContext("doc.pdf", ".pdf", "application/pdf", 100, "/tmp/work")
        assert ctx.capture_full_text is False
        assert common.ExtractContext("doc.pdf", ".pdf", "application/pdf", 100, "/tmp/work", False, True).capture_full_text is True

    def test_two_results_do_not_share_mutable_defaults(self):
        first, second = common.BranchResult(file_class="a"), common.BranchResult(file_class="b")
        first.warnings.append("x")
        assert second.warnings == []

    def test_other_fallback(self):
        ctx = common.ExtractContext("blob.bin", ".bin", "application/octet-stream", 100, "/tmp")
        result = common.other_fallback(ctx, "not decodable as text")
        assert result.file_class == common.CLASS_OTHER
        assert result.render_skipped == common.RENDER_SKIPPED_UNSUPPORTED
        assert result.attributes == {}
        assert "blob.bin" in result.warnings[0] and "not decodable as text" in result.warnings[0]

    def test_extract_context_geo_flag_defaults_off(self):
        # The five positional fields keep working; the state flag is opt-in so a caller that does not know
        # about it never writes coordinates.
        ctx = common.ExtractContext("shot.jpg", ".jpg", "image/jpeg", 100, "/tmp")
        assert ctx.extract_geo_location is False
        assert common.ExtractContext("shot.jpg", ".jpg", "image/jpeg", 100, "/tmp", True).extract_geo_location is True


@pytest.mark.unit
def test_promotion_source_contract_is_the_registry():
    # The names WP06d's metadataCatalog.py reads (master §3.6). A rename in an extractor must land here AND in
    # the registry; a rename here alone fails this test, a rename in the extractor alone fails Task 12's guard.
    assert common.PROMOTION_SOURCE_KEYS == REGISTRY_PROMOTION_SOURCES
    for path in common.PROMOTION_SOURCE_KEYS:
        assert path.split(".")[0] in _MEDIA_ATTRIBUTE_GROUPS, path
    # Control: a nested path hangs off a key its parent lists.
    for path in common.PROMOTION_SOURCE_KEYS:
        if "." in path:
            parent, _, leaf = path.rpartition(".")
            assert leaf in common.PROMOTION_SOURCE_KEYS[parent], path


@pytest.mark.unit
def test_year_number():
    assert common.year_number("2026") == 2026
    assert common.year_number("2026-03-01") == 2026
    assert common.year_number(" 1999/12") == 1999
    assert common.year_number(1999) == 1999
    assert common.year_number("unknown") is None
    assert common.year_number("") is None
    assert common.year_number(None) is None
    assert common.year_number(True) is None


@pytest.mark.unit
def test_budgets_match_the_spec():
    assert common.VISION_MAX_LONG_EDGE_PX == 1568
    assert common.VISION_MAX_BYTES == 3_750_000
    assert common.DEFAULT_MAX_TEXT_CHARS == 12_000
    assert common.VIDEO_KEYFRAME_COUNT == 4
    assert common.PDF_RASTER_PAGES == 2
    assert common.RENDER_BRANCH == "MEDIA"
