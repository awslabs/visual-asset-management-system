#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A run launched during an upgrade still starts on an image built before the input-version field (#994).

The Batch images of 3dThumbnail, pcPotreeViewer (PDAL and Potree), coordinateTransform and
metadata3dLabeling parse their definition into plain dataclasses, so an image built before the field
rejects a definition carrying ``inputFile.versionId`` (``TypeError: ... unexpected keyword argument
'versionId'``). During an upgrade the new ``constructPipeline`` can run while the state machine still
submits the old job definition, so those pipelines name the launch version in a second Batch command
argument, which the old images never read, and keep the definition itself byte-identical to the one
written before the field. Each pipeline's lambda suite feeds its new command to the old parsing in
``fixtures/pre994``; this module pins those snapshots to development and keeps the rule durable for
pipelines added later.
"""

import hashlib
import os
import re

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES = os.path.join(_REPO_ROOT, "backendPipelines", "tests", "fixtures", "pre994")

# Git blob ids of each image's utils/pipeline/objects.py at development
# ebc5f9d9ffd7e14047ebeaf58a446f425d38283b (`git ls-tree ebc5f9d9f <path>`).
SNAPSHOT_BLOBS = {
    "thumbnail_objects.py": "66776a07f94b6fa43a4308af313d3825eee1c003",
    "potree_objects.py": "fac33bc5e06a723254a32851769b2ed60ae6a3ae",
    "coord_transform_objects.py": "7e42fc3c442da95afd66ba19ac9ed97ff27485d8",
    "metadata3d_labeling_objects.py": "9f380758a47fed65e7571f94923464a55eaf71a4",
}

# Container objects.py -> (the container's entry module, the pipeline's constructPipeline)
STRICT_IMAGES = {
    "backendPipelines/preview/3dThumbnail/container/preview_pipeline/utils/pipeline/objects.py": (
        "backendPipelines/preview/3dThumbnail/container/preview_pipeline/__main__.py",
        "backendPipelines/preview/3dThumbnail/lambda/constructPipeline.py"),
    "backendPipelines/preview/pcPotreeViewer/container/utils/pipeline/objects.py": (
        "backendPipelines/preview/pcPotreeViewer/container/__main__.py",
        "backendPipelines/preview/pcPotreeViewer/lambda/constructPipeline.py"),
    "backendPipelines/conversion/coordinateTransform/container/coord_transform_pipeline/utils/pipeline/objects.py": (
        "backendPipelines/conversion/coordinateTransform/container/coord_transform_pipeline/__main__.py",
        "backendPipelines/conversion/coordinateTransform/lambda/constructPipeline.py"),
    "backendPipelines/genAi/metadata3dLabeling/container/main/utils/pipeline/objects.py": (
        "backendPipelines/genAi/metadata3dLabeling/container/main/__main__.py",
        "backendPipelines/genAi/metadata3dLabeling/lambda/constructPipeline.py"),
}


def _read(relative):
    with open(os.path.join(_REPO_ROOT, *relative.split("/")), "r", encoding="utf-8") as f:
        return f.read()


def _git_blob_id(path):
    with open(path, "rb") as f:
        content = f.read()
    return hashlib.sha1(b"blob %d\x00" % len(content) + content).hexdigest()  # nosec B324 - git object id, not a security use


def _versioned_stage_inputs():
    """Every container objects.py whose StageInput dataclass declares a versionId field."""
    found = []
    for root, _, names in os.walk(os.path.join(_REPO_ROOT, "backendPipelines")):
        if "objects.py" not in names or os.sep + "tests" in root:
            continue
        relative = os.path.relpath(os.path.join(root, "objects.py"), _REPO_ROOT).replace(os.sep, "/")
        match = re.search(r"class StageInput\b.*?(?=\n@dataclass|\nclass |\Z)", _read(relative), re.S)
        if match and re.search(r"^\s+versionId\s*:", match.group(0), re.M):
            found.append(relative)
    return sorted(found)


@pytest.mark.unit
class TestTheSnapshotsAreDevelopmentsCode:
    @pytest.mark.parametrize("file_name", sorted(SNAPSHOT_BLOBS))
    def test_each_snapshot_is_byte_identical_to_development(self, file_name):
        assert _git_blob_id(os.path.join(_FIXTURES, file_name)) == SNAPSHOT_BLOBS[file_name]


@pytest.mark.unit
class TestTheVersionTravelsBesideTheDefinition:
    def test_the_strict_images_are_the_ones_listed(self):
        # A new image whose StageInput takes a version must be added here, and so to the rule below.
        assert _versioned_stage_inputs() == sorted(STRICT_IMAGES)

    @pytest.mark.parametrize("objects_path", sorted(STRICT_IMAGES))
    def test_construct_pipeline_writes_no_version_into_the_definition(self, objects_path):
        source = _read(STRICT_IMAGES[objects_path][1])
        assert not re.search(r"['\"]versionId['\"]\s*:", source), \
            "the version goes in the command's second argument (definition_command), not the definition"
        assert re.search(r"['\"]definition['\"]\s*:\s*definition_command\(", source)

    @pytest.mark.parametrize("objects_path", sorted(STRICT_IMAGES))
    def test_the_image_reads_the_second_argument(self, objects_path):
        assert re.search(r"apply_input_versions\(json\.loads\(sys\.argv\[1\]\),\s*sys\.argv\[2:\]\)",
                         _read(STRICT_IMAGES[objects_path][0]))
