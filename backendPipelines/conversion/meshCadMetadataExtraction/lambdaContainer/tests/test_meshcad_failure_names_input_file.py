#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""An extraction failure names the input file, not the local copy (#1188).

The handler downloads each input to ``input<ext>`` in a working directory of its own, so an
extractor that reported the path it was given produced "Could not extract CAD metadata from
input.step" whatever the operator selected, and an asset holding several files gave no way to tell
which one failed. Both extractors take the name to report as an argument, as ``check_input_text``
already did, and the handler passes the input's path within its asset.

``cq`` and ``trimesh`` are shared module objects — the real ones wherever they are installed — so
every importer and loader here is patched in a context rather than assigned, and nothing leaks into
the test files that use real trimesh and cadquery in this same process.
"""

import importlib.util
import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "cadquery" not in sys.modules:
    sys.modules["cadquery"] = MagicMock()
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_RELATIVE_PATH = "parts/bracket-v2.step"
_TEMP_FILE = os.path.join("/tmp", "tmp1234", "input.step")
_OUTPUT_PATH = "s3://obkt/pipelines/metaExtract/JOB/output/E1/metadata/"


def _load_extractor(name):
    """Load one extractor module directly, so the package's siblings are not imported."""
    spec = importlib.util.spec_from_file_location(
        "meshcad_" + name,
        os.path.join(_LAMBDA_DIR, "metadata_extractors", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "meshcad_pipeline_lambda_failure_names", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    extractors = types.ModuleType("metadata_extractors")
    extractors.extract_cad_metadata = MagicMock(return_value={"AB_x": {"a": 1}})
    extractors.extract_mesh_metadata = MagicMock(return_value={"AB_x": {"a": 1}})
    extractors.get_handler_for_format = MagicMock(return_value="cad")
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {"metadata_extractors": extractors}))
        stack.enter_context(patch("boto3.client", MagicMock()))
        stack.enter_context(patch("boto3.resource", MagicMock()))
        spec.loader.exec_module(module)
    return module


def _run_extraction(mod, relative_path, handler_type):
    """Run the handler's extraction with S3 and the file system stubbed out."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(
            mod, "download", MagicMock(side_effect=lambda b, k, p, **kw: p)))
        stack.enter_context(patch.object(mod, "check_input_text", MagicMock()))
        stack.enter_context(patch.object(
            mod, "upload", MagicMock(side_effect=lambda b, k, p: k)))
        stack.enter_context(patch.object(
            mod, "get_handler_for_format", MagicMock(return_value=handler_type)))
        stack.enter_context(patch("builtins.open", MagicMock()))
        stack.enter_context(patch("json.dump", MagicMock()))
        mod.extract_metadata(
            relative_path, "s3://abkt/xd130a6d6/" + relative_path, _OUTPUT_PATH)


@pytest.mark.unit
class TestTheExtractorsReportTheNameTheyAreGiven:
    def test_cad_failure_names_the_input_and_not_the_local_copy(self):
        mod = _load_extractor("cad_extractor")
        refused = MagicMock(side_effect=ValueError("the STEP file contains no shapes"))

        with patch.object(mod.cq.importers, "importStep", refused):
            with pytest.raises(RuntimeError) as failure:
                mod.extract_cad_metadata(_TEMP_FILE, _RELATIVE_PATH)

        assert _RELATIVE_PATH in str(failure.value)
        assert "input.step" not in str(failure.value)
        assert "the STEP file contains no shapes" in str(failure.value)

    def test_mesh_failure_names_the_input_and_not_the_local_copy(self):
        mod = _load_extractor("mesh_extractor")
        refused = MagicMock(side_effect=ValueError("not a mesh"))

        with patch.object(mod.trimesh, "load", refused):
            with pytest.raises(RuntimeError) as failure:
                mod.extract_mesh_metadata(
                    os.path.join("/tmp", "tmp1234", "input.stl"), "parts/housing.stl")

        assert "parts/housing.stl" in str(failure.value)
        assert "input.stl" not in str(failure.value)

    def test_the_format_is_still_read_from_the_real_file(self):
        # The name to report and the file to open are different values now, so the extension that
        # selects the importer must keep coming from the file. A display name with no extension,
        # or with a different one, must not change which importer runs.
        mod = _load_extractor("cad_extractor")
        mod.extract_step_metadata = MagicMock(return_value={"AB_x": {}})
        mod.step_shape = MagicMock(side_effect=lambda imported: imported)
        step = MagicMock(return_value=MagicMock())
        dxf = MagicMock(side_effect=AssertionError("the DXF importer ran for a .step file"))

        with patch.object(mod.cq.importers, "importStep", step):
            with patch.object(mod.cq.importers, "importDXF", dxf):
                mod.extract_cad_metadata(_TEMP_FILE, "parts/bracket")

        step.assert_called_once_with(_TEMP_FILE)

    def test_without_a_name_the_local_file_is_reported_as_before(self):
        # A direct or local invocation of the extractor passes no name.
        mod = _load_extractor("cad_extractor")
        refused = MagicMock(side_effect=ValueError("boom"))
        expected = r"^Could not extract CAD metadata from input\.step"

        with patch.object(mod.cq.importers, "importStep", refused):
            with pytest.raises(RuntimeError, match=expected):
                mod.extract_cad_metadata(_TEMP_FILE)


@pytest.mark.unit
class TestTheHandlerPassesTheAssetRelativePath:
    def test_the_cad_extractor_is_given_the_input_s_path_within_its_asset(self):
        mod = _load_handler()
        _run_extraction(mod, _RELATIVE_PATH, "cad")

        (_path, name), _kwargs = mod.extract_cad_metadata.call_args
        assert name == _RELATIVE_PATH

    def test_the_mesh_extractor_is_given_the_input_s_path_within_its_asset(self):
        mod = _load_handler()
        _run_extraction(mod, "parts/housing.stl", "mesh")

        (_path, name), _kwargs = mod.extract_mesh_metadata.call_args
        assert name == "parts/housing.stl"
