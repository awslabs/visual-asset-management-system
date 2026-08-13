# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The `executionConfig` resource targets are validated at parse time.

Each of these values is baked into the deployed Step Functions definition as the sendMessage
QueueUrl, the putEvents EventBusName/Source/DetailType, or the invoke FunctionName, so a malformed
one is not caught until every execution of the pipeline fails at runtime. Deleting a check from
`_validate_execution_config` must fail here, which requires the dispatcher these cases resolve to be
the real one — a stand-in that no-ops an unimplemented validator name makes the whole file vacuous.
"""

import pytest

from backend.backend.models import pipelines as pl

VALID_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue"
VALID_BUS_ARN = "arn:aws:events:us-east-1:123456789012:event-bus/my-bus"


def _create(execution_config):
    return pl.CreatePipelineRequestModel(
        databaseId="mydb1", pipelineName="a pipeline", executionConfig=execution_config)


def _update(execution_config):
    return pl.UpdatePipelineRequestModel(executionConfig=execution_config)


@pytest.mark.unit
class TestSqsQueueUrlTarget:
    """queueUrl becomes the sendMessage task's QueueUrl."""

    @pytest.mark.parametrize("queue_url", [
        "ftp://evil.example/queue",
        "https://evil.example/123456789012/my-queue",
        "https://sqs.us-east-1.amazonaws.com/1/my-queue",
        "https://sqs.us-east-1.amazonaws.com/123456789012/my queue",
        "not-a-url",
    ])
    def test_malformed_queue_url_is_rejected(self, queue_url):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "SQS", "sqs": {"queueUrl": queue_url}})
        assert "queueUrl" in str(exc.value)

    def test_wellformed_queue_url_is_accepted(self):
        model = _create({"executionType": "SQS", "sqs": {"queueUrl": VALID_QUEUE_URL}})
        assert model.executionConfig["sqs"]["queueUrl"] == VALID_QUEUE_URL

    @pytest.mark.parametrize("queue_url", [
        "https://sqs.us-gov-west-1.amazonaws.com/123456789012/my-queue",
        "https://sqs.cn-north-1.amazonaws.com.cn/123456789012/my-queue",
        "https://sqs.us-isof-south-1.csp.hci.ic.gov/123456789012/my-queue",
        "https://vpce-0abc123.sqs.us-east-1.vpce.amazonaws.com/123456789012/my-queue",
    ])
    def test_other_partitions_and_vpc_endpoints_are_accepted(self, queue_url):
        assert _create({"executionType": "SQS", "sqs": {"queueUrl": queue_url}})

    def test_missing_queue_url_is_rejected(self):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "SQS", "sqs": {}})
        assert "queueUrl" in str(exc.value)

    def test_update_path_validates_the_target_too(self):
        with pytest.raises(Exception) as exc:
            _update({"executionType": "SQS", "sqs": {"queueUrl": "ftp://evil.example/queue"}})
        assert "queueUrl" in str(exc.value)
        assert _update({"executionType": "SQS", "sqs": {"queueUrl": VALID_QUEUE_URL}})


@pytest.mark.unit
class TestEventBridgeTargets:
    """busArn / source / detailType become the putEvents entry."""

    @pytest.mark.parametrize("bus_arn", [
        "not-an-arn",
        "arn:aws:s3:::my-bucket",
        "arn:aws:events:us-east-1:123456789012:rule/my-rule",
    ])
    def test_bus_arn_that_is_not_an_event_bus_is_rejected(self, bus_arn):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "EventBridge", "eventBridge": {"busArn": bus_arn}})
        assert "busArn" in str(exc.value)

    @pytest.mark.parametrize("bus_arn", [
        VALID_BUS_ARN,
        "arn:aws-us-gov:events:us-gov-west-1:123456789012:event-bus/my-bus",
        "arn:aws-cn:events:cn-north-1:123456789012:event-bus/my-bus",
    ])
    def test_wellformed_bus_arn_is_accepted(self, bus_arn):
        assert _create({"executionType": "EventBridge", "eventBridge": {"busArn": bus_arn}})

    @pytest.mark.parametrize("source", ["aws.reserved", "aws.s3", "has space", "x" * 257])
    def test_reserved_or_malformed_source_is_rejected(self, source):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "EventBridge",
                     "eventBridge": {"busArn": VALID_BUS_ARN, "source": source}})
        assert "source" in str(exc.value)

    def test_caller_owned_source_is_accepted(self):
        assert _create({"executionType": "EventBridge",
                        "eventBridge": {"busArn": VALID_BUS_ARN, "source": "com.example.vams"}})

    def test_over_long_detail_type_is_rejected(self):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "EventBridge",
                     "eventBridge": {"busArn": VALID_BUS_ARN, "detailType": "x" * 257}})
        assert "detailType" in str(exc.value)

    def test_absent_bus_resolves_to_the_default_bus(self):
        assert _create({"executionType": "EventBridge", "eventBridge": {}})


@pytest.mark.unit
class TestLambdaResourceIdTarget:
    """resourceId becomes the invoke task's FunctionName."""

    @pytest.mark.parametrize("resource_id", [
        "arn:not-a-partition:lambda:us-east-1:123456789012:function:f",
        "arn:aws:lambda",
        "has space",
        "!!",
    ])
    def test_malformed_target_is_rejected(self, resource_id):
        with pytest.raises(Exception) as exc:
            _create({"executionType": "Lambda", "lambda": {"resourceId": resource_id}})
        assert "resourceId" in str(exc.value)

    @pytest.mark.parametrize("resource_id", [
        "arn:aws:lambda:us-east-1:123456789012:function:my-function",
        "arn:aws-us-gov:lambda:us-gov-west-1:123456789012:function:my-function",
        "my-function",
    ])
    def test_arn_or_bare_function_name_is_accepted(self, resource_id):
        assert _create({"executionType": "Lambda", "lambda": {"resourceId": resource_id}})

    def test_empty_target_on_create_requests_auto_provisioning(self):
        assert _create({"executionType": "Lambda", "lambda": {}})

    def test_update_path_validates_a_supplied_target(self):
        with pytest.raises(Exception) as exc:
            _update({"executionType": "Lambda", "lambda": {"resourceId": "has space"}})
        assert "resourceId" in str(exc.value)
        # An absent target is left to the handler, which carries the prior row's function forward or
        # provisions one, so the model accepts it here.
        assert _update({"executionType": "Lambda", "lambda": {}})
