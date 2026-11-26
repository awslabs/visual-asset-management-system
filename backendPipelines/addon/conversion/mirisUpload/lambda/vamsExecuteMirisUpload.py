#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""
Lambda Function called by VAMS workflows for Miris auto-upload execution.
Note: function name must start with "vams" to allow invoke permissioning from VAMS.
"""
import json
import os

import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

import assetNesting
import manifestHelper
import mirisClaim

logger = safeLogger(service="VamsExecuteMirisUpload")
retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
lambda_client = boto3.client("lambda", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
sfn_client = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "us-east-1"), config=retry_config)
dynamodb = boto3.resource("dynamodb", config=retry_config)

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
MIRIS_UPLOAD_GATE_FUNCTION_NAME = os.environ.get("MIRIS_UPLOAD_GATE_FUNCTION_NAME", "")
ASSET_STORAGE_TABLE_NAME = os.environ.get("ASSET_STORAGE_TABLE_NAME", "")
# The deployment's quarantine block (app.compliance.quarantineBlocksDownload) and the table it reads.
COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD = os.environ.get(
    "COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD", "false").lower() == "true"
COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME = os.environ.get(
    "COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME", "")

# The stored trigger type of a user-launched run, rendered into the configuration as {{triggerType}}.
MANUAL_TRIGGER_TYPE = "Manual"
# The identity a trigger-launched run executes as.
SYSTEM_USER = "SYSTEM_USER"
# The asset metadata key naming the root file of an automatic run.
ROOT_FILE_METADATA_KEY = "miris_root_file"


def _asset_table():
    """The asset storage table resource. Wrapped so tests can patch it."""
    return dynamodb.Table(ASSET_STORAGE_TABLE_NAME)


def _compliance_asset_state_table():
    """The compliance asset state table resource. Wrapped so tests can patch it."""
    return dynamodb.Table(COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME)


def is_quarantined(item):
    """Whether a compliance asset state row blocks the asset's content: the condition
    `check_quarantine_block` in the backend's `common/compliance/quarantineGuard.py` refuses on.
    `tests/test_miris_upload_quarantine_rule_matches_core.py` holds the two to the same expression.
    """
    return bool(item and item.get("complianceState") == "quarantined" and not item.get("exceptionGranted"))


def read_distributable_asset(database_id, asset_id):
    """The asset record, once it passes the rule the VAMS file routes apply before serving an
    asset's content (`common/downloadEligibility.py` and `common/compliance/quarantineGuard.py` in
    the backend): the asset exists and is distributable and, while the deployment's quarantine block
    is on, is not quarantined without a granted exception. Raises otherwise, so a refused run fails
    before anything is claimed or sent to Miris. A record that cannot be read fails the run too.
    """
    key = {"databaseId": database_id, "assetId": asset_id}
    asset = _asset_table().get_item(Key=key).get("Item")
    if not asset:
        raise Exception("The asset was not found, so its files are not sent to Miris.")
    if not asset.get("isDistributable", False):
        raise Exception("The asset is not distributable, so its files are not sent to Miris.")
    if COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD:
        if is_quarantined(_compliance_asset_state_table().get_item(Key=key).get("Item")):
            raise Exception("The asset is quarantined, so its files are not sent to Miris.")
    return asset


def resolve_asset_root(resolved):
    """(bucket, bucket-relative asset root ending in '/') of the input file's asset.

    The root is the asset's base location key, so it already carries the bucket's base assets
    prefix or an existing-key location.
    """
    input_files = resolved.get("inputFiles") or []
    first = input_files[0] if input_files else {}
    bucket = first.get("bucket", "")
    root = mirisClaim.asset_root_prefix(first.get("assetRootS3Key", ""))
    if not bucket or not root:
        raise Exception(
            "The workflow manifest does not locate the input file's asset root "
            "(inputFiles[0].bucket and assetRootS3Key).")
    return bucket, root


def is_manual_run(data, resolved):
    """Whether a user launched this run, rather than a file-upload trigger.

    The rendered configuration's triggerType decides when the template carries it; otherwise a run
    executing as anyone but the system user is a user's.
    """
    try:
        configuration = manifestHelper.fetch_input_configuration(
            s3_client, resolved.get("inputConfigurationS3Location", ""))
    except Exception as e:
        logger.warning(f"Could not read the input configuration: {e}")
        configuration = {}
    trigger_type = configuration.get("triggerType")
    if isinstance(trigger_type, str) and trigger_type and "{{" not in trigger_type:
        return trigger_type == MANUAL_TRIGGER_TYPE
    executing_user = data.get("executingUserName", "") or ""
    return bool(executing_user) and executing_user != SYSTEM_USER


def read_input_metadata(location):
    """The run's input metadata payload ({} when the run has no input metadata file). Unlike
    manifestHelper.fetch_metadata, a file that cannot be read raises, so a miris_root_file it holds
    is never silently ignored."""
    if not location:
        return {}
    bucket, key = manifestHelper.parse_s3_uri(location)
    try:
        body = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("not a JSON object")
    except Exception as e:
        raise Exception(
            f"The run's input metadata file could not be read, so its asset metadata "
            f"{ROOT_FILE_METADATA_KEY} cannot be checked: {e}") from e
    if "metadata" in body and "schemaVersion" in body:
        return body.get("metadata") or {}
    return body


def resolve_root_file(resolved):
    """The asset metadata miris_root_file from the run's input metadata file, or '' when it is
    unset. Raises when the file cannot be read. The container checks it names one of the asset's
    USD files."""
    metadata = read_input_metadata(resolved.get("inputMetadataS3Location", ""))
    value = manifestHelper.asset_metadata_for(
        metadata, resolved.get("databaseId", ""), resolved.get("assetId", "")).get(ROOT_FILE_METADATA_KEY)
    return value.strip() if isinstance(value, str) else ""


def same_region_reason(resolved, region=None):
    """The reason a run whose input or output bucket is outside the deployment Region is refused.

    Kept within the 256-character cause the task failure carries, for any Region names.
    """
    region = region or os.environ.get("AWS_REGION", "")
    where = []
    remote_inputs = manifestHelper.remote_input_regions(resolved, region)
    if remote_inputs:
        where.append(f"the input bucket is in {', '.join(remote_inputs)}")
    output_region = (resolved or {}).get("outputBucketRegion") or ""
    if output_region and output_region != region:
        where.append(f"the output bucket is in {output_region}")
    return (f"The input and output buckets must be in {region}: the Miris upload container reads and "
            f"writes Amazon S3 with a client for {region}, but {' and '.join(where)}.")


def abort_external_workflow(error, task_token):
    """Fail the workflow's waitForCallback token so the task does not wait out its taskTimeout."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="MirisUploadError",
            cause=str(error)[:256],
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def invoke_gate(resolved, asset_location, manual, miris_asset_name, task_token, nested_locations_uri):
    """Claim the asset's current content. Returns (proceed, claim fingerprint)."""
    if not MIRIS_UPLOAD_GATE_FUNCTION_NAME:
        return True, ""
    asset_bucket, asset_root = asset_location
    payload = {
        "body": json.dumps({
            "databaseId": resolved["databaseId"],
            "assetId": resolved["assetId"],
            "assetBucket": asset_bucket,
            "assetRootS3Key": asset_root,
            # The object listing the assets stored inside the asset root, whose files are left out of
            # the fingerprint; "" when there are none.
            "nestedLocationsS3Uri": nested_locations_uri,
            "manual": manual,
            "mirisAssetName": miris_asset_name,
            "inputOutputS3AssetAuxiliaryFilesPath":
                resolved["inputOutputS3AssetAuxiliaryFilesPath"],
            "sfnExternalTaskToken": task_token,
        })
    }
    response = lambda_client.invoke(
        FunctionName=MIRIS_UPLOAD_GATE_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    if response.get("FunctionError"):
        raise Exception(f"Invoke gate lambda failed: {response.get('FunctionError')}")
    result = json.loads(response["Payload"].read().decode("utf-8"))
    return result.get("gate") != "skip", result.get("claimFingerprint", "") or ""


def execute_pipeline(resolved, asset_location, claim_fingerprint, miris_asset_name,
                     external_task_token, executing_userName, executing_requestContext,
                     manual=False, root_file="", nested_locations_uri=""):
    """Invoke openPipeline with the full pipeline payload."""
    asset_bucket, asset_root = asset_location
    message_payload = {
        "inputS3AssetFilePath": resolved["inputS3AssetFilePath"],
        # The S3 version of the selected file recorded at launch ("" for none); the container reads it.
        "inputVersionId": resolved["inputVersionId"],
        "outputS3AssetFilesPath": resolved["outputS3AssetFilesPath"],
        "outputS3AssetPreviewPath": resolved["outputS3AssetPreviewPath"],
        "outputS3AssetMetadataPath": resolved["outputS3AssetMetadataPath"],
        # The container reports tied roots there for the failure reason.
        "outputS3AssetResultsPath": resolved.get("outputS3AssetResultsPath", ""),
        "inputOutputS3AssetAuxiliaryFilesPath": resolved["inputOutputS3AssetAuxiliaryFilesPath"],
        "inputMetadataS3Location": resolved["inputMetadataS3Location"],
        "inputConfigurationS3Location": resolved["inputConfigurationS3Location"],
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": resolved["orchestrationEventPrefix"],
        "assetId": resolved["assetId"],
        "databaseId": resolved["databaseId"],
        "assetRootS3Path": f"s3://{asset_bucket}/{asset_root}",
        # Carried through the whole chain so any downstream failure can release the gate's claim.
        "claimFingerprint": claim_fingerprint,
        "mirisAssetName": miris_asset_name,
        # A manual run's root is the selected file; an automatic run's is root_file when it is set.
        "manualRun": manual,
        "mirisRootFile": root_file,
        # The object listing the assets stored inside the asset root, whose files the container
        # leaves out; "" when there are none.
        "nestedLocationsS3Uri": nested_locations_uri,
    }

    logger.info("Invoking openPipeline")
    response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(message_payload).encode("utf-8"),
    )

    if response.get("StatusCode") != 200:
        raise Exception("Invoke openPipeline failed.")

    # A handled invocation still returns 200 when the invoked function raised; the failure is
    # reported via FunctionError. Without this check the workflow's callback task blocks until
    # taskTimeout.
    if response.get("FunctionError"):
        raise Exception(
            f"Invoke openPipeline failed: {response.get('FunctionError')}")


def lambda_handler(event, context):
    logger.info("VamsExecuteMirisUpload received event")
    external_task_token = None
    # Set once the gate has written a claim, so a later failure can release it again.
    claim = None
    try:
        body = event.get("body")
        if not body:
            return {"statusCode": 400, "body": json.dumps({"message": "Request body is required"})}
        data = json.loads(body) if isinstance(body, str) else body

        # Capture the token BEFORE resolving the manifest: a manifest that cannot be read raises,
        # and that raise must reach send_task_failure rather than leaving the task pending.
        if "TaskToken" not in data:
            raise Exception(
                "VAMS Workflow TaskToken not found in pipeline input. "
                "Make sure this pipeline is registered with waitForCallback=Enabled."
            )
        external_task_token = data["TaskToken"]

        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        manifestHelper.enforce_single_input_file(resolved)
        # The container reads the asset and writes the stream metadata with a client for the
        # deployment Region, so a bucket in another Region is rejected before the gate claims it.
        try:
            manifestHelper.enforce_same_region_io(resolved)
        except Exception as e:
            raise Exception(same_region_reason(resolved)) from e
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved.get('manifestUsed')})")

        # Both identities become the Miris asset's binding tags, so neither may be missing.
        if not resolved.get("assetId") or not resolved.get("databaseId"):
            raise Exception("The workflow manifest does not name the input file's asset and database.")
        # The workflow manifest carries asset IDs but no asset NAME, so the Miris asset name is
        # read from the same record.
        asset_name = read_distributable_asset(
            resolved["databaseId"], resolved["assetId"]).get("assetName", "") or ""
        asset_location = resolve_asset_root(resolved)
        # Files of another asset stored inside this one's location are that asset's, so the upload
        # leaves them out. They are written under the run's auxiliary path, which keeps the job's
        # command small; a failed lookup or write fails the run before anything is claimed.
        nested_uri = assetNesting.write_nested_locations(
            s3_client, resolved["inputOutputS3AssetAuxiliaryFilesPath"],
            assetNesting.nested_locations(
                _asset_table(), asset_location[0], asset_location[1],
                resolved["databaseId"], resolved["assetId"]))
        manual = is_manual_run(data, resolved)
        root_file = "" if manual else resolve_root_file(resolved)

        proceed, claim_fingerprint = invoke_gate(
            resolved, asset_location, manual, asset_name, external_task_token, nested_uri)
        if not proceed:
            logger.info("Gate returned skip; this execution is a no-op")
            return {"statusCode": 200, "body": "Skipped (gate)"}

        claim = (resolved["inputOutputS3AssetAuxiliaryFilesPath"], resolved["databaseId"],
                 resolved["assetId"], claim_fingerprint, mirisClaim.owner_id(external_task_token))

        execute_pipeline(
            resolved,
            asset_location,
            claim_fingerprint,
            asset_name,
            external_task_token,
            data.get("executingUserName", ""),
            data.get("executingRequestContext", ""),
            manual,
            root_file,
            nested_uri,
        )
        return {"statusCode": 200, "body": "Success"}
    except Exception as e:
        logger.exception(e)
        # The claim is only released when the pipeline never got started. Leaving it behind would
        # skip this content on every later trigger.
        if claim:
            mirisClaim.release_claim(s3_client, logger, *claim)
        abort_external_workflow(e, external_task_token)
        return {"statusCode": 500, "body": json.dumps({"message": "Internal Server Error"})}
