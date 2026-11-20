#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from upload_config import resolve_upload_config

pytestmark = pytest.mark.unit


def _s3_returning(body):
    client = MagicMock()
    client.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(body).encode("utf-8"))}
    return client


STAGE = {"inputConfigurationS3Location": "s3://b/cfg.json", "mirisAssetName": "Pump Housing"}


@pytest.mark.parametrize("stage, config, name, tags", [
    (STAGE, {"mirisAssetName": "Custom Name", "mirisTags": ["scan", "wip"]}, "Custom Name", ["scan", "wip"]),
    (STAGE, {"mirisAssetName": ""}, "Pump Housing", []),
    (dict(STAGE, mirisAssetName=""), {}, "model", []),
    (dict(STAGE, inputConfigurationS3Location=""), None, "Pump Housing", []),
], ids=["template-wins", "blank-template-name", "file-name-fallback", "no-config-location"])
def test_the_name_and_tags_resolve_from_the_template_then_the_asset(stage, config, name, tags):
    cfg = resolve_upload_config(stage, "model.usdz", MagicMock() if config is None else _s3_returning(config))
    assert (cfg.asset_name, cfg.tags) == (name, tags)


def test_reserved_binding_tags_are_dropped_with_a_warning(caplog):
    reserved = ["vams-asset-x86487e0b", "VAMS-ASSET-x", " vams-database-db2"]
    lookalikes = ["vams-assets", "my-vams-asset-1", "vams-database"]
    cfg = resolve_upload_config(STAGE, "model.usdz", _s3_returning({"mirisTags": ["scan", *reserved, *lookalikes]}))

    assert cfg.tags == ["scan", *lookalikes]
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert all(tag in warned for tag in reserved)
