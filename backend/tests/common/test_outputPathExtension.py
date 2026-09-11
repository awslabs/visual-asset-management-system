# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Placement of the output base path extension within an output file's relative path.

The extension lands immediately BEFORE the final filename, not at the start of the path, so a
pipeline's own output folder structure survives it. The authored trailing slash is meaningful: with
one the extension is a folder, without one it is glued onto the filename.
"""

import pytest

from common.workflows.outputPathExtension import (
    NO_EXTENSION,
    apply_output_path_extension,
    normalize_output_path_extension,
)


@pytest.mark.unit
class TestNormalize:
    def test_absent_forms_all_mean_no_extension(self):
        for raw in (None, "", "   ", "/", " / "):
            assert normalize_output_path_extension(raw) == NO_EXTENSION

    def test_a_trailing_slash_is_preserved_because_it_distinguishes_folder_from_glue(self):
        """The old normalizer forced a trailing slash, collapsing these two into one value and making
        the non-folder form unexpressible."""
        assert normalize_output_path_extension("/YOLO/") == "/YOLO/"
        assert normalize_output_path_extension("YOLO") == "/YOLO"
        assert normalize_output_path_extension("YOLO/") == "/YOLO/"
        assert normalize_output_path_extension("/YOLO") == "/YOLO"

    def test_leading_slash_and_duplicate_separators_are_canonicalized(self):
        assert normalize_output_path_extension("//a///b//") == "/a/b/"
        assert normalize_output_path_extension("a//b") == "/a/b"

    def test_template_tags_pass_through_untouched(self):
        """The stored workflow default holds tags verbatim; they are substituted at launch."""
        assert normalize_output_path_extension("/{{jobName}}/") == "/{{jobName}}/"
        assert normalize_output_path_extension("{{executionId}}") == "/{{executionId}}"
        assert (normalize_output_path_extension("run-{{jobName}}/{{executionId}}/")
                == "/run-{{jobName}}/{{executionId}}/")

    def test_a_non_string_is_coerced_rather_than_raising(self):
        assert normalize_output_path_extension(123) == "/123"


@pytest.mark.unit
class TestApplyPlacement:
    def test_the_folder_form_inserts_a_level_above_the_filename(self):
        assert (apply_output_path_extension("/path1/path2/file.txt", "/YOLO/")
                == "path1/path2/YOLO/file.txt")
        assert apply_output_path_extension("/a/b/c/d.glb", "/j-123/") == "a/b/c/j-123/d.glb"

    def test_the_non_folder_form_concatenates_onto_the_filename(self):
        assert (apply_output_path_extension("/path1/path2/file.txt", "YOLO")
                == "path1/path2/YOLOfile.txt")

    def test_a_file_at_the_root_gets_the_extension_as_its_only_folder(self):
        assert apply_output_path_extension("file.txt", "/YOLO/") == "YOLO/file.txt"
        assert apply_output_path_extension("/file.txt", "YOLO") == "YOLOfile.txt"

    def test_a_multi_segment_extension_inserts_every_level(self):
        assert (apply_output_path_extension("/a/file.txt", "/x/y/")
                == "a/x/y/file.txt")

    def test_no_extension_leaves_the_path_alone(self):
        for extension in (None, "", "/"):
            assert apply_output_path_extension("/a/b/file.txt", extension) == "a/b/file.txt"

    def test_an_empty_path_has_no_filename_to_place_before(self):
        for relative in (None, "", "/"):
            assert apply_output_path_extension(relative, "/YOLO/") == ""

    def test_placement_is_idempotent_per_call_not_cumulative(self):
        """Both the write path and the provenance record apply the extension to the SAME original
        relative path, independently — so one application each, never stacked."""
        once = apply_output_path_extension("/a/file.txt", "/YOLO/")
        assert once == "a/YOLO/file.txt"
        assert apply_output_path_extension(once, "/YOLO/") == "a/YOLO/YOLO/file.txt"

    def test_a_trailing_slash_path_is_treated_as_having_no_filename(self):
        """Output listings skip directory placeholder keys, so this shape should not appear; assert
        the behavior rather than leave it undefined."""
        assert apply_output_path_extension("/a/b/", "/YOLO/") == "a/b/YOLO/"
