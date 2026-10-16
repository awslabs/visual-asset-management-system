#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""One supported-extension list, checked everywhere it is written out (#471).

``core._all_extensions()`` -- the union of the format handlers' ``SUPPORTED_EXTENSIONS`` -- is the list.
The pipeline's ``vamsSchema`` ``inputFileFilters`` and description, the upload trigger's filters, the
CDK ``allowedInputFileExtensions`` string, the README's Supported Formats table and the documentation
page's format tables must name exactly those extensions. The tests fail when one drifts.

The CDK construct and the documentation page live outside this pipeline's directory; their checks
skip where the repository is not checked out around it (inside the pipeline image).
"""

import json
import os
import re

import pytest

from preview_pipeline import core

PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(PIPELINE_DIR)))
CONSTRUCT = os.path.join(REPO, "infra", "lib", "nestedStacks", "pipelines", "preview", "3dThumbnail",
                         "constructs", "preview3dThumbnail-construct.ts")
DOCS_PAGE = os.path.join(REPO, "documentation", "docusaurus-site", "docs", "pipelines", "3d-thumbnail.md")
README = os.path.join(PIPELINE_DIR, "README.md")

EXTENSIONS = core._all_extensions()
# A file-name form the splat handler recognises inside ".ply", written out in the tables.
COMPOUND_FORMS = {".compressed.ply"}
_EXTENSION = re.compile(r"(?<![\w.])(\.compressed\.ply|\.[a-z0-9]+)(?![\w])")


def _schema(name):
    with open(os.path.join(PIPELINE_DIR, "vamsSchema", name)) as handle:
        return json.load(handle)


def _filters(patterns):
    assert all(pattern.startswith("*.") for pattern in patterns), patterns
    assert len(patterns) == len(set(patterns)), f"duplicate filters: {patterns}"
    return {pattern[1:].lower() for pattern in patterns}


def _table_extensions(path, start_heading, stop_heading_level="## "):
    """Extensions named in the markdown table rows below ``start_heading``, up to the next heading of
    the same level."""
    with open(path) as handle:
        lines = handle.read().splitlines()
    found, inside = set(), False
    for line in lines:
        if line.startswith(start_heading):
            inside = True
            continue
        if inside and line.startswith(stop_heading_level) and not line.startswith(stop_heading_level + "#"):
            break
        if inside and line.startswith("|"):
            found |= set(_EXTENSION.findall(line.lower()))
    return found - COMPOUND_FORMS


@pytest.mark.unit
class TestOneExtensionList:
    def test_the_list_is_well_formed(self):
        assert EXTENSIONS and all(re.fullmatch(r"\.[a-z0-9]+", ext) for ext in EXTENSIONS), EXTENSIONS
        assert not {".fls", ".fws", ".mjs"} & EXTENSIONS

    def test_the_pipeline_filters_and_description(self):
        pipeline = _schema("pipeline.json")
        assert _filters(pipeline["systemConfig"]["inputFileFilters"]["allow"]) == EXTENSIONS
        described = set(_EXTENSION.findall(pipeline["description"].split(" - ", 1)[1]))
        assert described == EXTENSIONS

    def test_the_workflow_and_upload_trigger_filters(self):
        workflow = _schema("workflow.json")
        assert _filters(workflow["systemConfig"]["inputFileFilters"]["allow"]) == EXTENSIONS
        for trigger in workflow["triggers"]:
            assert _filters(trigger["inputFileFilters"]["allow"]) == EXTENSIONS

    def test_the_readme_supported_formats_table(self):
        assert _table_extensions(README, "## Supported Formats") == EXTENSIONS

    @pytest.mark.skipif(not os.path.isfile(CONSTRUCT), reason="the CDK construct is not checked out here")
    def test_the_cdk_allowed_input_file_extensions(self):
        with open(CONSTRUCT) as handle:
            match = re.search(r"allowedInputFileExtensions\s*=\s*\"([^\"]+)\"", handle.read())
        assert match, "allowedInputFileExtensions not found in the construct"
        listed = match.group(1).split(",")
        assert len(listed) == len(set(listed)) and set(listed) == EXTENSIONS

    @pytest.mark.skipif(not os.path.isfile(DOCS_PAGE), reason="the documentation is not checked out here")
    def test_the_documentation_format_tables(self):
        assert _table_extensions(DOCS_PAGE, "## Supported Formats") == EXTENSIONS
