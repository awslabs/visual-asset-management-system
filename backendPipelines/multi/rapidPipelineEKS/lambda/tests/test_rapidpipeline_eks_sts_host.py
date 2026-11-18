#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""multi/rapidPipelineEKS: the EKS token's STS host follows the deployment's partition (#1593).

The Kubernetes token is a presigned STS ``GetCallerIdentity`` URL, and the host is one of the signed
headers. Every path that built it wrote ``sts.{region}.amazonaws.com``, which is not the STS endpoint
in the AWS European Sovereign Cloud (``amazonaws.eu``) or the China Regions (``amazonaws.com.cn``),
so a token minted there names a host that partition does not serve.

``sts_host_for_region`` takes the host from the endpoint botocore resolves for the Region. Endpoint
resolution is local (no AWS call), so it is exercised directly. The source check makes sure no token
path goes back to a hard-coded STS host.
"""

import os
import sys
from unittest.mock import patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import kubernetes_utils  # noqa: E402

_SOURCE_PATH = os.path.join(_LAMBDA_DIR, "kubernetes_utils.py")


@pytest.mark.unit
class TestStsHostForRegion:
    @pytest.mark.parametrize(
        "region, host",
        [
            ("us-east-1", "sts.us-east-1.amazonaws.com"),
            ("us-gov-west-1", "sts.us-gov-west-1.amazonaws.com"),
            ("cn-north-1", "sts.cn-north-1.amazonaws.com.cn"),
            ("eusc-de-east-1", "sts.eusc-de-east-1.amazonaws.eu"),
        ],
    )
    def test_host_uses_the_partition_dns_suffix(self, region, host):
        assert kubernetes_utils.sts_host_for_region(region) == host

    def test_legacy_global_endpoint_falls_back_to_the_regional_host(self):
        # The token is signed for the Region, so the global host would not match the credential scope.
        with patch.dict(os.environ, {"AWS_STS_REGIONAL_ENDPOINTS": "legacy"}):
            assert kubernetes_utils.sts_host_for_region("us-west-2") == "sts.us-west-2.amazonaws.com"

    @pytest.mark.parametrize(
        "variable, value",
        [
            ("AWS_USE_FIPS_ENDPOINT", "true"),
            ("AWS_USE_DUALSTACK_ENDPOINT", "true"),
            ("AWS_ENDPOINT_URL_STS", "https://sts.cn-north-1.example.invalid"),
        ],
    )
    def test_endpoint_settings_do_not_change_the_host(self, variable, value):
        # The token names the partition's Regional STS host whatever endpoint settings the environment carries.
        with patch.dict(os.environ, {variable: value}):
            assert kubernetes_utils.sts_host_for_region("cn-north-1") == "sts.cn-north-1.amazonaws.com.cn"

    def test_resolution_failure_falls_back_to_the_regional_host(self):
        with patch.object(kubernetes_utils.boto3, "client", side_effect=RuntimeError("no endpoint data")):
            assert kubernetes_utils.sts_host_for_region("us-west-2") == "sts.us-west-2.amazonaws.com"


@pytest.mark.unit
def test_no_token_path_hard_codes_the_sts_host():
    with open(_SOURCE_PATH, encoding="utf-8") as fh:
        source = fh.read()
    assert "https://sts." not in source
    assert "host:sts." not in source
