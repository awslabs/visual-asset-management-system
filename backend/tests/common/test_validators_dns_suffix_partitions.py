#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""`aws_dns_suffix_group` covers every DNS suffix the CDK layer can hand a queue URL from.

`aws_partition_group` and `aws_dns_suffix_group` are two independent axes. The partition group is
matched against the partition string inside an ARN; the suffix group is matched against the endpoint
hostname inside a URL, and the ISO partitions each serve from their own non-amazonaws domain. So
naming a partition in the ARN axis does nothing for its URL axis: `aws-iso-f` can pass the ARN rules
while its `csp.hci.ic.gov` endpoint is refused by the SQS rule.

The authoritative suffix list is SERVICE_LOOKUP in infra/lib/helper/const.ts. This asserts the SQS
rule against one real regional endpoint per suffix — behaviorally, not by substring-matching the
pattern, since a suffix present in the string but unreachable through the assembled regex would still
reject the URL. Negative controls are included because a suffix group widened until the positives pass
is not evidence of correctness.
"""
import pytest

from common.validators import validate


# One real regional SQS endpoint per DNS suffix in SERVICE_LOOKUP, keyed by the partition that
# serves it. Update alongside infra/lib/helper/const.ts.
SQS_ENDPOINTS = {
    "aws": "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    "aws-us-gov": "https://sqs.us-gov-west-1.amazonaws.com/123456789012/my-queue",
    "aws-cn": "https://sqs.cn-north-1.amazonaws.com.cn/123456789012/my-queue",
    "aws-eusc": "https://sqs.eusc-de-east-1.amazonaws.eu/123456789012/my-queue",
    "aws-iso": "https://sqs.us-iso-east-1.c2s.ic.gov/123456789012/my-queue",
    "aws-iso-b": "https://sqs.us-isob-east-1.sc2s.sgov.gov/123456789012/my-queue",
    "aws-iso-e": "https://sqs.eu-isoe-west-1.cloud.adc-e.uk/123456789012/my-queue",
    "aws-iso-f": "https://sqs.us-isof-south-1.csp.hci.ic.gov/123456789012/my-queue",
}


def _ok(value, rule):
    return validate({"field": {"value": value, "validator": rule}})[0]


@pytest.mark.unit
class TestEveryPartitionEndpointHostnameIsAccepted:
    """One case per suffix, so a failure names the partition whose SQS pipelines cannot be created."""

    @pytest.mark.parametrize("partition,url", sorted(SQS_ENDPOINTS.items()))
    def test_the_queue_url_rule_accepts_it(self, partition, url):
        assert _ok(url, "SQS_QUEUE_URL"), (
            f"{partition}'s regional SQS endpoint must be accepted — an SQS-type pipeline cannot be "
            f"created in that partition otherwise")

    @pytest.mark.parametrize("partition,url", sorted(SQS_ENDPOINTS.items()))
    def test_the_fips_form_of_the_same_endpoint_is_accepted(self, partition, url):
        # Every ISO/GovCloud deployment resolves FIPS hostnames, which differ only in the service
        # label; a suffix reachable through one form but not the other is still broken there.
        assert _ok(url.replace("//sqs.", "//sqs-fips."), "SQS_QUEUE_URL"), (
            f"{partition}'s FIPS SQS endpoint must be accepted")

    @pytest.mark.parametrize("partition,url", sorted(SQS_ENDPOINTS.items()))
    def test_the_vpc_endpoint_form_of_the_same_endpoint_is_accepted(self, partition, url):
        # Air-gapped deployments reach SQS through an interface endpoint, so the vpce shape has to
        # work against the same suffix.
        host, _, tail = url[len("https://"):].partition("/")
        service, region, suffix = host.split(".", 2)
        vpce = f"https://vpce-0abc123def456.{service}.{region}.vpce.{suffix}/{tail}"
        assert _ok(vpce, "SQS_QUEUE_URL"), f"{partition}'s VPC endpoint URL must be accepted"


@pytest.mark.unit
class TestTheSuffixGroupStillRefusesLookAlikes:
    """Negative controls for the suffix that was added, in the shapes a widened group tends to leak."""

    @pytest.mark.parametrize("value,why", [
        ("https://sqs.us-isof-south-1.csp.hci.ic.gov.evil.com/123456789012/q",
         "the real suffix used as a PREFIX of an attacker domain — the anchor is what stops this"),
        ("https://sqs.us-isof-south-1.csp.hci.ic.govv/123456789012/q", "a typo suffix"),
        ("https://sqs.us-isof-south-1.csp.hci.gov/123456789012/q", "a suffix with a label dropped"),
        ("https://sqs.us-isof-south-1.hci.ic.gov/123456789012/q",
         "a suffix with the leading label dropped"),
        ("http://sqs.us-isof-south-1.csp.hci.ic.gov/123456789012/q", "http, not https"),
        ("https://sqs.us-isof-south-1.csp.hci.ic.gov/1234/q",
         "an account id that is not 12 digits"),
    ])
    def test_a_bad_iso_f_queue_url_is_refused(self, value, why):
        assert not _ok(value, "SQS_QUEUE_URL"), f"the SQS rule must refuse {value!r} ({why})"
