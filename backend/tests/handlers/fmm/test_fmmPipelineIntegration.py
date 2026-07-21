# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.models.fmm import (
    EnforcementLevel,
    PipelineCheck,
    PipelineRef,
    PipelineRule,
    RuleResult,
    Tolerance,
    ToleranceOperator,
)


class TestProcessPipelineResults:
    """Tests for _process_pipeline_results with the new input-based approach."""

    def test_failed_execution_marks_all_rules_failed(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _process_pipeline_results,
        )

        pending_rules = [
            {
                "ruleName": "coord-check",
                "rule": {
                    "ruleType": "pipeline",
                    "enforcement": "quarantine",
                    "pipelineRef": {"databaseId": "db", "workflowId": "wf"},
                    "checks": [
                        {
                            "name": "residual",
                            "outputField": "residual_error_mm",
                            "tolerance": {"operator": "lte", "value": 1.0},
                        }
                    ],
                },
            }
        ]

        results = _process_pipeline_results(
            execution_status="FAILED",
            execution_input={"evaluationId": "eval-1", "bucketAsset": "bucket"},
            execution_output={},
            pending_rules=pending_rules,
            database_id="db1",
            asset_id="asset1",
        )

        assert len(results) == 1
        assert results[0].passed is False
        assert "FAILED" in results[0].message

    def test_succeeded_with_compliance_output(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _process_pipeline_results,
        )

        pending_rules = [
            {
                "ruleName": "residual-check",
                "rule": {
                    "ruleType": "pipeline",
                    "enforcement": "quarantine",
                    "pipelineRef": {"databaseId": "db", "workflowId": "wf"},
                    "checks": [
                        {
                            "name": "residual_error",
                            "outputField": "residual_error_mm",
                            "tolerance": {"operator": "lte", "value": 1.0},
                        }
                    ],
                },
            }
        ]

        compliance_output = {
            "complianceOutput": True,
            "status": "success",
            "measurements": {"residual_error_mm": 0.5},
            "errors": [],
        }

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback._read_compliance_output"
        ) as mock_read:
            mock_read.return_value = compliance_output

            results = _process_pipeline_results(
                execution_status="SUCCEEDED",
                execution_input={"evaluationId": "eval-1", "bucketAsset": "bucket"},
                execution_output={},
                pending_rules=pending_rules,
                database_id="db1",
                asset_id="asset1",
            )

        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].measured["residual_error_mm"] == 0.5

    def test_succeeded_measurement_exceeds_tolerance(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _process_pipeline_results,
        )

        pending_rules = [
            {
                "ruleName": "scale-check",
                "rule": {
                    "ruleType": "pipeline",
                    "enforcement": "quarantine",
                    "pipelineRef": {"databaseId": "db", "workflowId": "wf"},
                    "checks": [
                        {
                            "name": "scale_deviation",
                            "outputField": "scale_deviation_ppm",
                            "tolerance": {"operator": "lte", "value": 1.0},
                        }
                    ],
                },
            }
        ]

        compliance_output = {
            "complianceOutput": True,
            "status": "success",
            "measurements": {"scale_deviation_ppm": 2.5},
            "errors": [],
        }

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback._read_compliance_output"
        ) as mock_read:
            mock_read.return_value = compliance_output

            results = _process_pipeline_results(
                execution_status="SUCCEEDED",
                execution_input={"evaluationId": "eval-1", "bucketAsset": "bucket"},
                execution_output={},
                pending_rules=pending_rules,
                database_id="db1",
                asset_id="asset1",
            )

        assert len(results) == 1
        assert results[0].passed is False
        assert "2.5" in results[0].message

    def test_succeeded_no_compliance_output_fails_rules(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _process_pipeline_results,
        )

        pending_rules = [
            {
                "ruleName": "check",
                "rule": {
                    "ruleType": "pipeline",
                    "enforcement": "warn",
                    "pipelineRef": {"databaseId": "db", "workflowId": "wf"},
                    "checks": [
                        {
                            "name": "test",
                            "outputField": "field",
                            "tolerance": {"operator": "gte", "value": 1.0},
                        }
                    ],
                },
            }
        ]

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback._read_compliance_output"
        ) as mock_read:
            mock_read.return_value = None

            results = _process_pipeline_results(
                execution_status="SUCCEEDED",
                execution_input={"evaluationId": "eval-1", "bucketAsset": "bucket"},
                execution_output={},
                pending_rules=pending_rules,
                database_id="db1",
                asset_id="asset1",
            )

        assert len(results) == 1
        assert results[0].passed is False
        assert "No compliance output" in results[0].message

    def test_pipeline_error_status_fails_rules(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _process_pipeline_results,
        )

        pending_rules = [
            {
                "ruleName": "check",
                "rule": {
                    "ruleType": "pipeline",
                    "enforcement": "quarantine",
                    "pipelineRef": {"databaseId": "db", "workflowId": "wf"},
                    "checks": [
                        {
                            "name": "test",
                            "outputField": "field",
                            "tolerance": {"operator": "lte", "value": 5.0},
                        }
                    ],
                },
            }
        ]

        compliance_output = {
            "complianceOutput": True,
            "status": "error",
            "measurements": {},
            "errors": ["Processing failed"],
        }

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback._read_compliance_output"
        ) as mock_read:
            mock_read.return_value = compliance_output

            results = _process_pipeline_results(
                execution_status="SUCCEEDED",
                execution_input={"evaluationId": "eval-1", "bucketAsset": "bucket"},
                execution_output={},
                pending_rules=pending_rules,
                database_id="db1",
                asset_id="asset1",
            )

        assert len(results) == 1
        assert results[0].passed is False
        assert "error" in results[0].message.lower()


class TestReadComplianceOutput:
    """Tests for _read_compliance_output S3 lookup logic."""

    def test_reads_from_metadata_path_key(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _read_compliance_output,
        )

        compliance_json = json.dumps({
            "complianceOutput": True,
            "status": "success",
            "measurements": {"field": 1.0},
        })

        mock_body = MagicMock()
        mock_body.read.return_value = compliance_json.encode("utf-8")

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback.s3_client"
        ) as mock_s3:
            mock_s3.get_object.return_value = {"Body": mock_body}

            result = _read_compliance_output(
                execution_input={
                    "evaluationId": "eval-123",
                    "bucketAsset": "my-bucket",
                },
                execution_output={
                    "body": {
                        "metadataPathKey": "pipelines/test/job1/output/exec1/metadata/",
                    }
                },
                database_id="db1",
                asset_id="asset1",
            )

        assert result is not None
        assert result["measurements"]["field"] == 1.0
        mock_s3.get_object.assert_called_once_with(
            Bucket="my-bucket",
            Key="pipelines/test/job1/output/exec1/metadata/compliance-output.json",
        )

    def test_falls_back_to_evaluation_id_path(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _read_compliance_output,
        )

        compliance_json = json.dumps({
            "complianceOutput": True,
            "status": "success",
            "measurements": {"x": 2.0},
        })
        mock_body = MagicMock()
        mock_body.read.return_value = compliance_json.encode("utf-8")

        no_such_key_error = type("NoSuchKey", (Exception,), {})

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback.s3_client"
        ) as mock_s3:
            mock_s3.exceptions.NoSuchKey = no_such_key_error
            mock_s3.get_object.side_effect = [
                no_such_key_error("not found"),
                {"Body": mock_body},
            ]

            result = _read_compliance_output(
                execution_input={
                    "evaluationId": "eval-456",
                    "bucketAsset": "my-bucket",
                },
                execution_output={},
                database_id="db1",
                asset_id="asset1",
            )

        assert result is not None
        assert result["measurements"]["x"] == 2.0
        calls = mock_s3.get_object.call_args_list
        assert calls[1][1]["Key"] == "compliance/db1/asset1/eval-456/compliance-output.json"

    def test_returns_none_when_no_output_found(self):
        from backend.backend.handlers.fmm.fmmPipelineCallback import (
            _read_compliance_output,
        )

        no_such_key_error = type("NoSuchKey", (Exception,), {})

        with patch(
            "backend.backend.handlers.fmm.fmmPipelineCallback.s3_client"
        ) as mock_s3:
            mock_s3.exceptions.NoSuchKey = no_such_key_error
            mock_s3.get_object.side_effect = no_such_key_error("not found")

            result = _read_compliance_output(
                execution_input={
                    "evaluationId": "eval-789",
                    "bucketAsset": "my-bucket",
                },
                execution_output={},
                database_id="db1",
                asset_id="asset1",
            )

        assert result is None


class TestGetAssetInfo:
    """Tests for _get_asset_info in the evaluation engine."""

    def test_returns_asset_info_on_success(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import _get_asset_info

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.asset_table"
        ) as mock_asset_table, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.s3_asset_buckets_table"
        ) as mock_buckets_table:
            mock_asset_table.query.return_value = {
                "Items": [
                    {
                        "databaseId": "db1",
                        "assetId": "asset1",
                        "assetLocation": {"Key": "asset1/model.glb"},
                        "bucketId": "bucket-001",
                    }
                ]
            }
            mock_buckets_table.query.return_value = {
                "Items": [{"bucketId": "bucket-001", "bucketName": "vams-assets-prod"}]
            }

            result = _get_asset_info("db1", "asset1")

        assert result is not None
        assert result["bucketName"] == "vams-assets-prod"
        assert result["assetFileKey"] == "asset1/model.glb"

    def test_returns_none_when_asset_not_found(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import _get_asset_info

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.asset_table"
        ) as mock_asset_table:
            mock_asset_table.query.return_value = {"Items": []}

            result = _get_asset_info("db1", "missing-asset")

        assert result is None

    def test_returns_none_when_bucket_not_found(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import _get_asset_info

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.asset_table"
        ) as mock_asset_table, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.s3_asset_buckets_table"
        ) as mock_buckets_table:
            mock_asset_table.query.return_value = {
                "Items": [
                    {
                        "databaseId": "db1",
                        "assetId": "asset1",
                        "assetLocation": {"Key": "asset1/file.obj"},
                        "bucketId": "bucket-gone",
                    }
                ]
            }
            mock_buckets_table.query.return_value = {"Items": []}

            result = _get_asset_info("db1", "asset1")

        assert result is None


class TestInvokePipelineRules:
    """Tests for _invoke_pipeline_rules workflow execution launch."""

    def test_starts_execution_with_correct_input(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import (
            _invoke_pipeline_rules,
        )

        rule = PipelineRule(
            ruleType="pipeline",
            enforcement=EnforcementLevel.quarantine,
            pipelineRef=PipelineRef(databaseId="GLOBAL", workflowId="coord-wf"),
            checks=[
                PipelineCheck(
                    name="residual",
                    outputField="residual_error_mm",
                    tolerance=Tolerance(operator=ToleranceOperator.lte, value=1.0),
                )
            ],
        )

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine._get_asset_info"
        ) as mock_info, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine._get_workflow_arn"
        ) as mock_arn, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.sfn_client"
        ) as mock_sfn:
            mock_info.return_value = {
                "bucketName": "vams-bucket",
                "assetLocationKey": "asset1/",
                "assetFileKey": "asset1/scan.e57",
            }
            mock_arn.return_value = "arn:aws:states:us-east-1:123:stateMachine:vams-coord"
            mock_sfn.start_execution.return_value = {
                "executionArn": "arn:aws:states:us-east-1:123:execution:vams-coord:exec-1"
            }

            _invoke_pipeline_rules(
                pipeline_rules=[("coord-check", rule)],
                evaluation_id="eval-abc",
                database_id="db1",
                asset_id="asset1",
            )

        mock_sfn.start_execution.assert_called_once()
        call_args = mock_sfn.start_execution.call_args
        input_json = json.loads(call_args[1]["input"])

        assert input_json["bucketAsset"] == "vams-bucket"
        assert input_json["databaseId"] == "db1"
        assert input_json["assetId"] == "asset1"
        assert input_json["evaluationId"] == "eval-abc"
        assert input_json["inputAssetFileKey"] == "asset1/scan.e57"

        input_metadata = json.loads(input_json["inputMetadata"])
        assert "fmmContext" in input_metadata
        assert input_metadata["fmmContext"]["evaluationId"] == "eval-abc"
        assert input_metadata["fmmContext"]["ruleName"] == "coord-check"

    def test_skips_when_asset_not_found(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import (
            _invoke_pipeline_rules,
        )

        rule = PipelineRule(
            ruleType="pipeline",
            enforcement=EnforcementLevel.warn,
            pipelineRef=PipelineRef(databaseId="db", workflowId="wf"),
            checks=[
                PipelineCheck(
                    name="test",
                    outputField="x",
                    tolerance=Tolerance(operator=ToleranceOperator.gte, value=0.0),
                )
            ],
        )

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine._get_asset_info"
        ) as mock_info, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.sfn_client"
        ) as mock_sfn:
            mock_info.return_value = None

            _invoke_pipeline_rules(
                pipeline_rules=[("test-rule", rule)],
                evaluation_id="eval-xyz",
                database_id="db1",
                asset_id="missing",
            )

        mock_sfn.start_execution.assert_not_called()

    def test_skips_rule_when_workflow_not_found(self):
        from backend.backend.handlers.fmm.fmmEvaluationEngine import (
            _invoke_pipeline_rules,
        )

        rule = PipelineRule(
            ruleType="pipeline",
            enforcement=EnforcementLevel.quarantine,
            pipelineRef=PipelineRef(databaseId="db", workflowId="missing-wf"),
            checks=[
                PipelineCheck(
                    name="test",
                    outputField="x",
                    tolerance=Tolerance(operator=ToleranceOperator.lte, value=5.0),
                )
            ],
        )

        with patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine._get_asset_info"
        ) as mock_info, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine._get_workflow_arn"
        ) as mock_arn, patch(
            "backend.backend.handlers.fmm.fmmEvaluationEngine.sfn_client"
        ) as mock_sfn:
            mock_info.return_value = {
                "bucketName": "bucket",
                "assetLocationKey": "key/",
                "assetFileKey": "key/file.obj",
            }
            mock_arn.return_value = None

            _invoke_pipeline_rules(
                pipeline_rules=[("rule", rule)],
                evaluation_id="eval-1",
                database_id="db1",
                asset_id="asset1",
            )

        mock_sfn.start_execution.assert_not_called()
