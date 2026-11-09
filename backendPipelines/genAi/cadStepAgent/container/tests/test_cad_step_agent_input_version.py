#  SPDX-License-Identifier: Apache-2.0

"""GenAI CAD STEP agent container: the input part is read at the version the run was launched on (#994).

constructPipeline puts the version on the definition's ``inputFile.versionId``; ``_download_input`` sends
it as ``VersionId``. A definition without one makes exactly the call it made before, and a version that
no longer exists ends the run as ``RunFailed`` naming the file and the version, with no model call."""

import os
import sys
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

from cad_step_agent import run  # noqa: E402

VERSION = "3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY"


def _definition(version_id=None):
    input_file = {"bucketName": "abkt", "objectKey": "xasset1/sub/part.stp", "fileExtension": ".stp"}
    if version_id is not None:
        input_file["versionId"] = version_id
    return {"inputFile": input_file}


@pytest.mark.unit
class TestTheInputIsReadAtItsVersion:
    def test_a_version_is_passed_as_extra_args(self, tmp_path):
        s3 = MagicMock()
        local = run._download_input(s3, _definition(VERSION), str(tmp_path))
        s3.download_file.assert_called_once_with(
            "abkt", "xasset1/sub/part.stp", local, ExtraArgs={"VersionId": VERSION})

    @pytest.mark.parametrize("version_id", [None, ""])
    def test_no_version_makes_the_call_it_made_before(self, tmp_path, version_id):
        s3 = MagicMock()
        local = run._download_input(s3, _definition(version_id), str(tmp_path))
        s3.download_file.assert_called_once_with("abkt", "xasset1/sub/part.stp", local)

    @pytest.mark.parametrize("code", ["404", "405", "NoSuchVersion", "NoSuchKey", "MethodNotAllowed"])
    def test_a_deleted_version_fails_the_run_naming_it(self, tmp_path, code):
        s3 = MagicMock()
        s3.download_file.side_effect = ClientError({"Error": {"Code": code}}, "HeadObject")
        with pytest.raises(run.RunFailed) as excinfo:
            run._download_input(s3, _definition(VERSION), str(tmp_path))
        assert "xasset1/sub/part.stp" in str(excinfo.value) and VERSION in str(excinfo.value)

    def test_other_errors_and_a_missing_current_object_are_raised_as_before(self, tmp_path):
        s3 = MagicMock()
        s3.download_file.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
        with pytest.raises(ClientError):
            run._download_input(s3, _definition(VERSION), str(tmp_path))
        s3.download_file.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with pytest.raises(ClientError):
            run._download_input(s3, _definition(), str(tmp_path))

    def test_a_definition_without_an_input_downloads_nothing(self, tmp_path):
        s3 = MagicMock()
        assert run._download_input(s3, {"inputFile": None}, str(tmp_path)) is None
        s3.download_file.assert_not_called()
