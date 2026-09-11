#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The partition-aware validators accept every partition VAMS can deploy into, and nothing else.

VAMS deploys into 8 partitions. The authoritative list is the CDK layer's SERVICE_LOOKUP table
(`infra/lib/helper/const.ts`): aws, aws-us-gov, aws-cn, aws-iso, aws-iso-b, aws-iso-e, aws-iso-f, and
aws-eusc (EU Sovereign Cloud). The backend validators must accept an ARN or queue URL from any of them.

This class of bug is invisible in the partition you test in. A missing partition rejects a value the
deployment ITSELF produced — a pipeline registering its own sub-process state machine, a workflow
recording its own log group — so the failure appears only in that one partition, at run time, on a
resource nobody typed by hand. `aws-eusc` was missing for exactly this reason: it does not fit the
`-iso(-[a-z])?` family shape the pattern was built around, so widening for ISO did not cover it.

The URL-shaped values need a second axis: partitions do NOT share one DNS suffix. Commercial and
GovCloud use amazonaws.com, China amazonaws.com.cn, EU Sovereign amazonaws.eu, and the ISO partitions
use their own non-amazonaws domains. Accepting the partition in an ARN while rejecting its endpoint
hostname would leave SQS pipelines broken in the same partitions.

Both directions are asserted. Widening a regex is only correct if it did not also start accepting
values it should refuse, so every positive here is paired with negative controls: a fabricated
partition, a partition with an appended suffix, a look-alike DNS suffix, and plain http.
"""
import pytest

from common.validators import validate, aws_partition_group, aws_dns_suffix_group


# Mirrors SERVICE_LOOKUP in infra/lib/helper/const.ts. Update both together.
ALL_PARTITIONS = (
    "aws",
    "aws-us-gov",
    "aws-cn",
    "aws-iso",
    "aws-iso-b",
    "aws-iso-e",
    "aws-iso-f",
    "aws-eusc",
)

# One real regional endpoint hostname per partition that serves SQS, keyed by partition.
SQS_URLS = {
    "aws": "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    "aws-us-gov": "https://sqs.us-gov-west-1.amazonaws.com/123456789012/my-queue",
    "aws-cn": "https://sqs.cn-north-1.amazonaws.com.cn/123456789012/my-queue",
    "aws-eusc": "https://sqs.eusc-de-east-1.amazonaws.eu/123456789012/my-queue",
    "aws-iso": "https://sqs.us-iso-east-1.c2s.ic.gov/123456789012/my-queue",
    "aws-iso-b": "https://sqs.us-isob-east-1.sc2s.sgov.gov/123456789012/my-queue",
    "aws-iso-e": "https://sqs.eu-isoe-west-1.cloud.adc-e.uk/123456789012/my-queue",
}


def _ok(value, rule):
    return validate({"field": {"value": value, "validator": rule}})[0]


@pytest.mark.unit
@pytest.mark.parametrize("partition", ALL_PARTITIONS)
class TestEveryPartitionIsAccepted:
    """One test per partition per ARN-shaped rule, so a failure names the partition that broke."""

    def test_generic_arn(self, partition):
        arn = f"arn:{partition}:states:us-east-1:123456789012:execution:my-sm:my-exec"
        assert _ok(arn, "ARN"), f"{partition} must be accepted by the ARN rule"

    def test_eventbridge_bus_arn(self, partition):
        arn = f"arn:{partition}:events:us-east-1:123456789012:event-bus/my-bus"
        assert _ok(arn, "EVENTBRIDGE_BUS_ARN"), f"{partition} must be accepted"

    def test_cloudwatch_log_group_arn(self, partition):
        arn = f"arn:{partition}:logs:us-east-1:123456789012:log-group:/aws/vendedlogs/my-group"
        assert _ok(arn, "CLOUDWATCH_LOG_GROUP_ARN"), f"{partition} must be accepted"


@pytest.mark.unit
class TestSqsQueueUrlAcrossDnsSuffixes:
    """The URL rule keys on the DNS suffix, not the partition string, so it needs its own coverage."""

    @pytest.mark.parametrize("partition,url", sorted(SQS_URLS.items()))
    def test_each_partitions_endpoint_is_accepted(self, partition, url):
        assert _ok(url, "SQS_QUEUE_URL"), f"{partition}'s endpoint hostname must be accepted"

    def test_fips_and_vpc_endpoint_forms_still_work(self):
        # The two shapes the rule already supported; widening the suffix must not have dropped them.
        assert _ok("https://sqs-fips.us-east-1.amazonaws.com/123456789012/q", "SQS_QUEUE_URL")
        assert _ok(
            "https://vpce-0abc123.sqs.us-east-1.vpce.amazonaws.com/123456789012/q",
            "SQS_QUEUE_URL",
        )

    def test_a_vpc_endpoint_form_works_in_a_non_com_partition_too(self):
        assert _ok(
            "https://vpce-0abc123.sqs.eusc-de-east-1.vpce.amazonaws.eu/123456789012/q",
            "SQS_QUEUE_URL",
        )


@pytest.mark.unit
class TestWideningDidNotAcceptGarbage:
    """Negative controls. A regex widened until the positives pass is not evidence of correctness."""

    @pytest.mark.parametrize("value,why", [
        ("arn:notaws:states:us-east-1:123456789012:execution:sm:e", "fabricated partition"),
        ("arn:aws-eusc-evil:states:us-east-1:123456789012:execution:sm:e",
         "a real partition with an appended suffix"),
        ("arn:aws-eu:states:us-east-1:123456789012:execution:sm:e", "a truncated aws-eusc"),
        ("arn:AWS:states:us-east-1:123456789012:execution:sm:e", "uppercase partition"),
        ("arn:aws-iso-bb:states:us-east-1:123456789012:execution:sm:e",
         "two-letter ISO suffix (the family shape allows exactly one)"),
        ("notanarn", "not an ARN at all"),
        ("", "empty"),
    ])
    def test_bad_arns_are_refused(self, value, why):
        assert not _ok(value, "ARN"), f"the ARN rule must refuse {value!r} ({why})"

    @pytest.mark.parametrize("value,why", [
        ("https://sqs.us-east-1.evil.com/123456789012/q", "an attacker-controlled DNS suffix"),
        ("https://sqs.us-east-1.amazonaws.eu.evil.com/123456789012/q",
         "a real suffix used as a PREFIX of an attacker domain — the anchor is what stops this"),
        ("http://sqs.us-east-1.amazonaws.eu/123456789012/q", "http, not https"),
        ("https://sqs.us-east-1.amazonaws.euu/123456789012/q", "a typo suffix"),
        ("https://sqs.us-east-1.amazonaws.com/1234/q", "an account id that is not 12 digits"),
    ])
    def test_bad_queue_urls_are_refused(self, value, why):
        assert not _ok(value, "SQS_QUEUE_URL"), f"the SQS rule must refuse {value!r} ({why})"


@pytest.mark.unit
class TestThePatternConstantsThemselves:
    """Pins the shared building blocks, so a future edit that drops a partition fails here too —
    not only through whichever rule happens to be covered above."""

    def test_the_partition_group_names_every_partition(self):
        import re
        rx = re.compile(f"^{aws_partition_group}$")
        missing = [p for p in ALL_PARTITIONS if not rx.match(p)]
        assert not missing, f"aws_partition_group does not match: {missing}"

    def test_eusc_is_spelled_out_rather_than_folded_into_the_iso_shape(self):
        # aws-eusc is NOT an -iso partition; it was missed originally because the pattern only grew an
        # optional single-letter ISO suffix. Naming it explicitly is the fix.
        assert "-eusc" in aws_partition_group

    def test_the_dns_suffix_group_covers_every_non_com_partition(self):
        for suffix in ("amazonaws\\.com", "amazonaws\\.eu", "c2s\\.ic\\.gov",
                       "sc2s\\.sgov\\.gov", "cloud\\.adc-e\\.uk"):
            assert suffix in aws_dns_suffix_group, f"{suffix} missing from aws_dns_suffix_group"
