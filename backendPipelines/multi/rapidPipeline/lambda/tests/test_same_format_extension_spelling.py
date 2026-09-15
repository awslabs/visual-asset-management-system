#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The same-format guard must not be defeated by how `outputType` is spelled.

`output_relative_subdir` adds a folder when the conversion does not change the extension, because
the output otherwise keeps both the input's subdirectory and its file name — so its asset-relative
path equals the input's, and the workflow's write-back lands a new version of the operator's source
object rather than a sibling file.

The two extensions it compares arrive from different places and in different shapes: the input's is
derived from the S3 key and carries its dot, while the output's is whatever `outputType` holds. That
is caller data — the shipped templates write ".glb", a caller may write "glb", and the emitted
state/output blocks strip the dot themselves. Compared raw, "obj" against ".obj" reads as a format
CHANGE, the folder is skipped, and the destructive placement returns through a value the operator
controls.

Driven directly against the helper rather than through the three-lambda chain: the chain tests
already cover placement end to end, and the property here is a comparison, so a direct call states
it without a fixture that could mask it.
"""

import os
import sys
import types
import importlib
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for _k, _v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"}.items():
    os.environ.setdefault(_k, _v)


def _module():
    if "constructPipeline" in sys.modules:
        return importlib.reload(sys.modules["constructPipeline"])
    return importlib.import_module("constructPipeline")


@pytest.mark.unit
class TestSameFormatGuardIgnoresExtensionSpelling:
    SUBDIR = "parts/housing"

    @pytest.mark.parametrize("output_spelling", ["obj", ".obj", "OBJ", ".OBJ"])
    def test_every_spelling_of_the_same_format_takes_the_folder(self, output_spelling):
        mod = _module()
        result = mod.output_relative_subdir(self.SUBDIR, ".obj", output_spelling)
        assert result == f"{self.SUBDIR}/{mod.SAME_FORMAT_OUTPUT_SUBDIR}", (
            f"outputType={output_spelling!r} skipped the folder, so the write-back resolves onto "
            f"the input's own key"
        )

    @pytest.mark.parametrize("output_spelling", ["glb", ".glb", "GLB"])
    def test_a_format_change_still_takes_no_folder(self, output_spelling):
        """Positive control. Without it, a guard that always added the folder would satisfy the
        assertions above while changing where every conversion writes."""
        mod = _module()
        assert mod.output_relative_subdir(self.SUBDIR, ".obj", output_spelling) == self.SUBDIR

    def test_an_input_at_the_asset_root_still_gains_the_folder(self):
        """An empty subdirectory is the case where the folder is the ONLY thing separating the
        output from its source, so it must not collapse to an empty path."""
        mod = _module()
        assert mod.output_relative_subdir("", ".obj", "obj") == mod.SAME_FORMAT_OUTPUT_SUBDIR
