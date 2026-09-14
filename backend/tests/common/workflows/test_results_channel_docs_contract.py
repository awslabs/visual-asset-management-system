# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The results channel is documented where the steering says it is, under the names the code uses.

Three places describe the results prefix and its reserved status object: the pipeline steering file
(`backendPipelines/CLAUDE.md`), the custom-pipelines guide, and the code
(`common/s3PathPatterns.EXECUTION_STATUS_RESULTS_FILENAME`; the `outputS3AssetResultsPath` field of the
vendored `manifestHelper.py`). A pipeline author reads the first two and writes against the third, so the
literal names have to agree. The code's constant is the source the assertions read, so renaming it turns
this red until both documents follow.

Durable (root CLAUDE.md Rule 13): every one of these documents is edited routinely, and a row or section
can be dropped by any later rewrite.
"""

import os

import pytest

from backend.backend.common.s3PathPatterns import EXECUTION_STATUS_RESULTS_FILENAME

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
STEERING = os.path.join(REPO_ROOT, "backendPipelines", "CLAUDE.md")
GUIDE = os.path.join(REPO_ROOT, "documentation", "docusaurus-site", "docs", "pipelines",
                     "custom-pipelines.md")
CANONICAL_HELPER = os.path.join(REPO_ROOT, "backendPipelines", "preview", "3dThumbnail", "lambda",
                                "manifestHelper.py")
IDENTITY_TEST_REL = "backendPipelines/tests/test_manifest_helper_byte_identity.py"

RESULTS_PATH_VARIABLE = "outputS3AssetResultsPath"
RESULTS_SECTION = f"## Reporting a Post-Write-Back Failure (`results/{EXECUTION_STATUS_RESULTS_FILENAME}`)"
EMBEDDING_SECTION = "## Publishing an Embedding for Vector Search (`vector.embedding.ready`)"


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _rows_whose_first_cell_is(text, name):
    """Markdown table rows whose FIRST cell is the backticked ``name``."""
    return [line for line in text.splitlines()
            if line.startswith("|") and f"`{name}`" in line.split("|")[1]]


@pytest.mark.unit
class TestSteeringFile:
    def test_the_output_path_table_has_one_results_row_naming_the_reserved_object(self):
        rows = _rows_whose_first_cell_is(_read(STEERING), RESULTS_PATH_VARIABLE)
        assert len(rows) == 1, rows
        assert EXECUTION_STATUS_RESULTS_FILENAME in rows[0], rows[0]

    def test_the_results_channel_section_names_the_constant(self):
        text = _read(STEERING)
        assert RESULTS_SECTION in text
        assert "common.s3PathPatterns.EXECUTION_STATUS_RESULTS_FILENAME" in text
        assert "test_processOutput_execution_status_file.py" in text

    def test_the_embedding_event_contract_section_states_the_contract(self):
        text = _read(STEERING)
        assert EMBEDDING_SECTION in text
        for term in ("vector.embedding.ready", "documentS3Location", "embeddingModelId",
                     "embeddingDimensions", "orchestrationEventPrefix", "events:PutEvents",
                     "segmentKey", "segmentKind", "segmentCount", "SEGMENT_KEY_MAX_BYTES"):
            assert term in text, term

    def test_the_byte_identity_test_it_names_exists(self):
        assert IDENTITY_TEST_REL in _read(STEERING)
        assert os.path.isfile(os.path.join(REPO_ROOT, *IDENTITY_TEST_REL.split("/")))


@pytest.mark.unit
class TestCustomPipelinesGuide:
    def test_the_output_path_table_has_one_results_row_naming_the_reserved_object(self):
        rows = _rows_whose_first_cell_is(_read(GUIDE), RESULTS_PATH_VARIABLE)
        assert len(rows) == 1, rows
        assert EXECUTION_STATUS_RESULTS_FILENAME in rows[0], rows[0]

    def test_the_writing_outputs_results_row_names_the_variable_and_the_reserved_object(self):
        rows = [line for line in _read(GUIDE).splitlines() if line.startswith("| Results")]
        assert len(rows) == 1, rows
        assert f"`{RESULTS_PATH_VARIABLE}`" in rows[0], rows[0]
        assert EXECUTION_STATUS_RESULTS_FILENAME in rows[0], rows[0]

    def test_the_status_object_contract_is_stated(self):
        text = _read(GUIDE)
        assert '"status": "FAILED"' in text
        assert "<error>: <cause>" in text

    def test_the_manifest_reading_example_lists_the_field(self):
        assert f'resolved["{RESULTS_PATH_VARIABLE}"]' in _read(GUIDE)


@pytest.mark.unit
class TestTheDocumentedNamesAreTheCodesNames:
    def test_the_canonical_manifest_helper_resolves_the_documented_field(self):
        assert f'"{RESULTS_PATH_VARIABLE}"' in _read(CANONICAL_HELPER)

    def test_the_reserved_name_is_a_bare_json_object_name(self):
        assert "/" not in EXECUTION_STATUS_RESULTS_FILENAME
        assert EXECUTION_STATUS_RESULTS_FILENAME.endswith(".json")
