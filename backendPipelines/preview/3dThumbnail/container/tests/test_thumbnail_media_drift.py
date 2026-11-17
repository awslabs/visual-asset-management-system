#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""One media extension list and one preview size limit, checked everywhere they are written (#1550).

The media list is the union of the image and video handlers' ``SUPPORTED_EXTENSIONS``. The media
vamsSchema bundle's filters and description, the CDK constant, the README's Media Formats table and the
documentation page's table must name exactly those extensions. The preview size limit and the list of
images the web shows as their own preview are one definition in the container, the backend and the web.
The CDK, backend and web files live outside this directory; their checks skip where the repository is not
checked out around it (inside the pipeline image)."""

import json
import os
import re

import pytest

from preview_pipeline import core, media_preview
from preview_pipeline.format_handlers import image_handler, video_handler
from preview_pipeline.utils import image_utils

PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(PIPELINE_DIR)))
CONSTRUCT = os.path.join(REPO, "infra", "lib", "nestedStacks", "pipelines", "preview", "3dThumbnail",
                         "constructs", "preview3dThumbnail-construct.ts")
BACKEND = os.path.join(REPO, "backend", "backend", "common", "s3PathPatterns.py")
WEB = os.path.join(REPO, "web", "src", "common", "constants", "fileFormats.ts")
DOCS_PAGE = os.path.join(REPO, "documentation", "docusaurus-site", "docs", "pipelines", "3d-thumbnail.md")
README = os.path.join(PIPELINE_DIR, "README.md")

MEDIA = media_preview.media_extensions()
_EXTENSION = re.compile(r"(?<![\w.])(\.[a-z0-9]+)(?![\w])")


def _schema(name):
    with open(os.path.join(PIPELINE_DIR, "vamsSchemaMedia", name)) as handle:
        return json.load(handle)


def _filters(patterns):
    assert all(pattern.startswith("*.") for pattern in patterns), patterns
    assert len(patterns) == len(set(patterns)), f"duplicate filters: {patterns}"
    return {pattern[1:].lower() for pattern in patterns}


def _quoted_extensions(text):
    return re.findall(r'"(\.[a-z0-9]+)"', text)


def _table_extensions(path, heading):
    with open(path) as handle:
        lines = handle.read().splitlines()
    found, inside = set(), False
    for line in lines:
        if line.startswith(heading):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.startswith("|"):
            found |= set(_EXTENSION.findall(line.lower()))
    return found


@pytest.mark.unit
class TestOneMediaList:
    def test_the_list_is_well_formed_and_apart_from_3d(self):
        assert MEDIA and all(re.fullmatch(r"\.[a-z0-9]+", ext) for ext in MEDIA)
        assert not MEDIA & core._all_extensions()
        assert image_handler.SUPPORTED_EXTENSIONS.isdisjoint(video_handler.SUPPORTED_EXTENSIONS)

    def test_the_issue_lists(self):
        assert image_handler.SUPPORTED_EXTENSIONS == {
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tga", ".tif", ".tiff"}
        assert not {".svg", ".heic", ".heif", ".avif", ".exr", ".hdr", ".psd", ".dds", ".mdl"} & MEDIA

    def test_the_pipeline_filters_and_description(self):
        pipeline = _schema("pipeline.json")
        assert _filters(pipeline["systemConfig"]["inputFileFilters"]["allow"]) == MEDIA
        assert set(_EXTENSION.findall(pipeline["description"].split(" - ", 1)[1])) == MEDIA

    def test_the_workflow_and_trigger_filters(self):
        workflow = _schema("workflow.json")
        assert _filters(workflow["systemConfig"]["inputFileFilters"]["allow"]) == MEDIA
        for trigger in workflow["triggers"]:
            assert _filters(trigger["inputFileFilters"]["allow"]) == MEDIA
            assert trigger["enabled"] is False

    def test_the_bundle_ids_do_not_collide_with_the_3d_bundle(self):
        template = json.load(open(os.path.join(PIPELINE_DIR, "vamsSchemaMedia", "templates",
                                               "preview-media-thumbnail-default.json")))
        three_d = json.load(open(os.path.join(PIPELINE_DIR, "vamsSchema", "templates",
                                              "preview-3d-thumbnail-default.json")))
        assert template["templateId"] != three_d["templateId"]
        trigger = _schema("workflow.json")["triggers"][0]
        assert trigger["defaultTemplateIds"] == {
            "GLOBAL:preview-media-thumbnail": template["templateId"]}

    def test_the_template_tags_are_all_used_by_its_config_body(self):
        template = json.load(open(os.path.join(PIPELINE_DIR, "vamsSchemaMedia", "templates",
                                               "preview-media-thumbnail-default.json")))
        used = set(re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", template["configBody"]))
        assert used == {tag["tagKey"] for tag in template["tagSchema"]}

    def test_the_readme_media_formats_table(self):
        assert _table_extensions(README, "## Media Formats") == MEDIA

    @pytest.mark.skipif(not os.path.isfile(DOCS_PAGE), reason="the documentation is not checked out here")
    def test_the_documentation_media_format_tables(self):
        assert _table_extensions(DOCS_PAGE, "## Media Formats") == MEDIA

    @pytest.mark.skipif(not os.path.isfile(CONSTRUCT), reason="the CDK construct is not checked out here")
    def test_the_cdk_constant(self):
        with open(CONSTRUCT) as handle:
            match = re.search(r"PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS\s*=\s*\"([^\"]+)\"", handle.read())
        assert match, "PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS not found in the construct"
        listed = match.group(1).split(",")
        assert len(listed) == len(set(listed)) and set(listed) == MEDIA


@pytest.mark.unit
class TestOnePreviewRule:
    def test_the_limit_is_5_mib(self):
        assert image_utils.MAX_FILE_SIZE_BYTES == 5 * 1024 * 1024 == 5242880

    @pytest.mark.skipif(not os.path.isfile(BACKEND), reason="the backend is not checked out here")
    def test_the_backend_agrees(self):
        with open(BACKEND) as handle:
            text = handle.read()
        limit = re.search(r"PREVIEW_FILE_SIZE_LIMIT_BYTES: int = (\d+) \* 1024 \* 1024", text)
        assert limit and int(limit.group(1)) * 1024 * 1024 == image_utils.MAX_FILE_SIZE_BYTES
        block = re.search(r"ALLOWED_PREVIEW_FILE_EXTENSIONS: Tuple\[str, \.\.\.\] = \(([^)]*)\)", text, re.S)
        backend = set(_quoted_extensions(block.group(1)))
        assert image_handler.SELF_PREVIEWABLE_EXTENSIONS == backend - {".svg"}
        assert not {".tga", ".tif", ".tiff"} & backend

    @pytest.mark.skipif(not os.path.isfile(WEB), reason="the web app is not checked out here")
    def test_the_web_agrees(self):
        with open(WEB) as handle:
            text = handle.read()
        limit = re.search(r"PREVIEW_FILE_SIZE_LIMIT_BYTES = (\d+) \* 1024 \* 1024", text)
        assert limit and int(limit.group(1)) * 1024 * 1024 == image_utils.MAX_FILE_SIZE_BYTES
        block = re.search(r"export const previewFileFormats = \[([^\]]*)\]", text)
        web = set(_quoted_extensions(block.group(1)))
        assert image_handler.SELF_PREVIEWABLE_EXTENSIONS == web - {".svg"}
        assert not {".tga", ".tif", ".tiff"} & web

    def test_tga_and_tiff_are_never_self_previewable(self):
        assert not {".tga", ".tif", ".tiff"} & image_handler.SELF_PREVIEWABLE_EXTENSIONS
        assert image_handler.SELF_PREVIEWABLE_EXTENSIONS <= image_handler.SUPPORTED_EXTENSIONS
