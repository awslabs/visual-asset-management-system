# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A macro-enabled Office file among a workflow's outputs stops the output step; macro-free Office files
and JavaScript files are ingested.

``verify_get_path_objects`` heads every listed output object and runs the shared blocklist helper on its
key and the content type the pipeline wrote it with. ``_verify`` from
``test_processOutput_stored_docx_js_content_types`` runs it with the real helper and the real merged
lists.
"""

import pytest

from backend.tests.handlers.workflows.test_processOutput_stored_docx_js_content_types import (
    OUTPUT_PREFIX,
    REFUSAL,
    _verify,
)
from tests.handlers.assets.test_macro_enabled_office_types_refused import MACRO_ENABLED_TYPES, OCTET

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

REFUSED = [
    case
    for ext, mime in MACRO_ENABLED_TYPES
    for case in (
        pytest.param(f"macro{ext}", OCTET, id=f"{ext[1:]}-extension"),
        pytest.param("renamed.dat", mime, id=f"{ext[1:]}-type"),
    )
]


@pytest.mark.unit
class TestOutputIngestMacroEnabledOfficeTypes:
    @pytest.mark.parametrize("name, content_type", REFUSED)
    def test_a_macro_enabled_office_output_stops_the_step(self, name, content_type):
        with pytest.raises(Exception, match=REFUSAL):
            _verify({"report.docx": DOCX_TYPE, name: content_type})

    def test_macro_free_office_and_javascript_outputs_are_ingested(self):
        outputs = {"report.docx": DOCX_TYPE, "sheet.xlsx": XLSX_TYPE, "deck.pptx": PPTX_TYPE,
                   "viewer.js": "application/javascript", "module.mjs": "text/javascript"}
        result = _verify(outputs)
        assert {obj["Key"][len(OUTPUT_PREFIX):]: obj["ContentType"] for obj in result["Contents"]} == outputs
