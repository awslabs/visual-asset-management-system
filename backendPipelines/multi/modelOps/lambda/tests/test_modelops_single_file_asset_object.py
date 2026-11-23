#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""ModelOps and the object of a single-file asset.

A single-file asset (location ``p/model.glb``, no trailing ``/``) holds its object and its companion
folder ``p/model.glb/``, not the other keys of ``p/`` (``p/other/``, ``p/model.glb.previewFile.png/``).
The ModelOps state block addresses its input by the folder that holds it, which for that object is
``p``. The run is therefore refused before the pipeline starts, and its task token is failed with the
reason; nothing names ``p``. A file of the companion folder, and any input of a folder asset, starts as
before."""

import json
import os
import shlex
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_manifest_refactor as refactor_tests  # noqa: E402

LOCATION = "p/model.glb"


def _run(key, root):
    """Run vamsExecute on a manifest whose one input is ``key`` in an asset whose location is ``root``;
    return the response, the openPipeline invoke mock and the Step Functions client mock."""
    base = refactor_tests.TestVamsExecute()
    mod = base._load()
    manifest = base._manifest()
    entry = manifest["inputFiles"][0]
    entry["key"] = key
    if root is None:
        entry.pop("assetRootS3Key", None)
    else:
        entry["assetRootS3Key"] = root
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    invoke = MagicMock(return_value={"StatusCode": 200})
    sfn = MagicMock()
    with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
            patch.object(mod.lambda_client, "invoke", invoke):
        resp = mod.lambda_handler({"body": json.dumps(base._body())}, MagicMock())
    return mod, resp, invoke, sfn


@pytest.mark.unit
class TestASingleFileAssetsObject:
    @pytest.mark.parametrize("root", [LOCATION, "/" + LOCATION])
    def test_the_object_is_refused_before_the_pipeline_starts(self, root):
        mod, resp, invoke, sfn = _run(LOCATION, root)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        assert sfn.send_task_failure.call_count == 1
        call = sfn.send_task_failure.call_args.kwargs
        assert call["taskToken"] == "tok-123"
        assert call["cause"] == mod.SINGLE_FILE_ASSET_OBJECT_MESSAGE[:256]

    @pytest.mark.parametrize("key, root", [
        (LOCATION + "/parts/a.glb", LOCATION),  # a file of the companion folder
        (LOCATION + "/model.glb", LOCATION),    # the companion entry named like the object
        (LOCATION, "p/"),                       # a folder asset: its own folder is the asset
        (LOCATION, "p/other.glb"),              # a root that names another object
        (LOCATION, None),                       # a manifest without an asset root
    ])
    def test_any_other_input_starts_as_before(self, key, root):
        _, resp, invoke, sfn = _run(key, root)
        assert resp["statusCode"] == 200
        sfn.send_task_failure.assert_not_called()
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputS3AssetFilePath"] == f"s3://abkt/{key}"

    def test_a_companion_folder_file_is_addressed_inside_the_asset(self):
        # The state block of a companion-folder file names a folder of the asset.
        construct = refactor_tests.TestConstructPipelineReadsConfigFromS3()
        mod = construct._load()
        event = construct._event()
        event["inputS3AssetFilePath"] = f"s3://abkt/{LOCATION}/parts/a.glb"
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps({"outputType": ".glb"}).encode("utf-8"))}
        with patch.object(mod, "s3", s3):
            out = mod.lambda_handler(event, MagicMock())
        config = json.loads(shlex.split(out["commands"][2].split("|", 1)[0])[-1])
        assert (config["state"]["bucket"], config["state"]["prefix"]) == ("abkt", LOCATION + "/parts")
