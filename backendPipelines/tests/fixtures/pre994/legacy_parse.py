#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""How the Batch images built before the input-version field (#994) read their command.

The ``*_objects.py`` files beside this one are byte-for-byte copies of each image's
``utils/pipeline/objects.py`` at development ``ebc5f9d9ffd7e14047ebeaf58a446f425d38283b`` (their git blob
ids are pinned in ``backendPipelines/tests/test_pipeline_input_version_upgrade_window.py``). During an
upgrade from that code, a run the new ``constructPipeline`` launches can still reach one of those images,
so the new command has to parse the way they parse it. ``parse_like_development`` replays that path at
the same sha:

- ``__main__.py``: outside ``localTest`` the image reads ``run(json.loads(sys.argv[1]))``, the command's
  first argument and nothing else. ``sys.argv[2]`` is read only when ``sys.argv[1] == "localTest"``.
  (3dThumbnail ``preview_pipeline/__main__.py`` lines 47 and 75; pcPotreeViewer ``__main__.py`` 26/36;
  coordinateTransform ``coord_transform_pipeline/__main__.py`` 27/55; metadata3dLabeling
  ``main/__main__.py`` 26/36.)
- ``core.run``: ``PipelineDefinition(**params)``, then ``PipelineStage(**definition.stages.pop(0))``.
- the stage: ``StageInput(**stage.inputFile)`` and ``StageOutput(**stage.<output>)`` (3dThumbnail
  ``core.py`` 204-205; pcPotreeViewer ``pipelines/pdal/pipeline.py`` and ``pipelines/potree/pipeline.py``
  37-38; coordinateTransform ``core.py`` 203-205; metadata3dLabeling
  ``pipelines/blenderRenderer/pipeline.py`` 77-78).

Each of those is a plain ``@dataclass``, so an unknown key anywhere in the definition raises ``TypeError``.
"""

import importlib.util
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# pipeline -> (snapshot file, stage output fields the stage code builds a StageOutput from)
SNAPSHOTS = {
    "3dThumbnail": ("thumbnail_objects.py", ("outputFiles",)),
    "pcPotreeViewer": ("potree_objects.py", ("outputFiles",)),
    "coordinateTransform": ("coord_transform_objects.py", ("outputFiles", "outputMetadata")),
    "metadata3dLabeling": ("metadata3d_labeling_objects.py", ("outputFiles",)),
}


def load_objects(pipeline):
    """The development ``objects`` module of ``pipeline``'s image."""
    file_name = SNAPSHOTS[pipeline][0]
    spec = importlib.util.spec_from_file_location(f"pre994_{pipeline}_objects", os.path.join(_HERE, file_name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_like_development(pipeline, command):
    """Parse a Batch ``command`` (the ``definition`` list constructPipeline returns) the way the
    development image of ``pipeline`` does, returning ``(definition, stage, stage_input)``. Raises what
    that image raises."""
    objects = load_objects(pipeline)
    if command[0] == "localTest":
        raise AssertionError("a constructPipeline command never starts with localTest")
    params = json.loads(command[0])
    definition = objects.PipelineDefinition(**params)
    stage = objects.PipelineStage(**definition.stages.pop(0))
    stage_input = objects.StageInput(**stage.inputFile)
    for field in SNAPSHOTS[pipeline][1]:
        objects.StageOutput(**getattr(stage, field))
    return definition, stage, stage_input
