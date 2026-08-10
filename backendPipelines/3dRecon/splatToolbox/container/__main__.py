# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
import subprocess
import boto3
from vams_utils import manifest_io

def resolve_output_env(bucket_name: str, object_dir: str, job_name: str) -> tuple:
    """The (S3_OUTPUT, UUID) pair for an output-files prefix, as `main.py` expects them.

    `main.py` writes every output to "{S3_OUTPUT}/{UUID}/..." and rejects an empty UUID, so the pair
    is split to recompose to exactly the given prefix: UUID takes its last segment and S3_OUTPUT
    everything above. Outputs then land at the prefix root, leaving the workflow's output path prefix
    as the only thing that nests them — an execution's output folder is the workflow's choice, not
    the container's. `main.py` interpolates the pair at ~15 sites and is upstream-synced and
    gitignored, so this is the only durable place to fix the layout. UUID is read nowhere else here:
    the DynamoDB metrics writes it keys are all gated on DDB_TABLE_NAME, which VAMS does not set.
    """
    trimmed = str(object_dir or "").strip("/")
    parent, _, leaf = trimmed.rpartition("/")
    return f"s3://{bucket_name}/{parent}", (leaf or job_name)


METADATA_SCHEMA_VERSION_GROUPED = 2


def resolve_asset_metadata(metadata_obj: dict) -> dict:
    """The asset-level metadata of an input-metadata envelope, as a flat {key: value} config map.

    The envelope is grouped by asset (`{"schemaVersion": 2, "assets": [...]}`) and holds asset-level
    metadata as each group's `fileKey` "/" record, with database metadata in its own top-level
    section; the legacy `{"VAMS": {...}}` view carries the same values under `assetMetadata`. This
    mirrors `manifestHelper`'s projection rule: the asset scope resolves only from an envelope naming
    exactly ONE asset, since several assets leave no way to tell which one a setting belongs to.
    Anything the envelope cannot supply is reported rather than left to look like an empty asset.
    """
    if not isinstance(metadata_obj, dict):
        return {}

    if metadata_obj.get('schemaVersion') == METADATA_SCHEMA_VERSION_GROUPED and 'assets' in metadata_obj:
        assets = metadata_obj.get('assets') or []
        if len(assets) != 1:
            print(f"No asset metadata applied: the input metadata names {len(assets)} assets, "
                  f"so no single asset's settings can be selected")
            return {}
        for record in (assets[0] or {}).get('files') or []:
            if (record or {}).get('fileKey') == '/':
                return record.get('metadata') or {}
        print("No asset metadata applied: the input metadata carries no asset-level record")
        return {}

    if 'VAMS' in metadata_obj:
        return (metadata_obj.get('VAMS') or {}).get('assetMetadata') or {}

    return metadata_obj


def set_config_parameters(params: dict, metadata: dict):
    """
    Set environment variables for valid config parameters.
    Metadata takes priority over parameters if both exist.
    """
    # Load valid config parameters
    try:
        with open('config.json', 'r') as f:
            config_keys = set(json.load(f).keys())
    except:
        print("Warning: Could not load config.json")
        return

    params = params if isinstance(params, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    print(f"Input parameters: {params}")
    print(f"Input metadata: {metadata}")

    # Combine with metadata priority
    combined = {**params, **metadata}
    print(f"Combined parameters and metadata: {combined}")
    
    # Set environment variables for valid config keys only
    for key, value in combined.items():
        if key in config_keys:
            os.environ[key] = str(value)
            source = "metadata" if key in metadata else "parameters"
            print(f"Set config {key}={value} (from {source})")
        else:
            print(f"Skipping {key}={value} (not in config.json)")

def main():
    # Debug: Print all available inputs
    print(f"Command line arguments: {sys.argv}")
    print(f"Environment variables:")
    for key, value in os.environ.items():
        if key.startswith(('INPUT_', 'VAMS_', 'AWS_', 'TASK_')):
            print(f"  {key}={value}")
    
    # Try to get pipeline definition from command line or environment
    pipeline_json = None
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        arg = sys.argv[1].strip()
        # Check if it's a file path
        if arg.startswith('/') and arg.endswith('.json'):
            print(f"Reading pipeline definition from file: {arg}")
            try:
                with open(arg, 'r') as f:
                    pipeline_json = f.read()
                print(f"Successfully read pipeline definition from file")
            except Exception as e:
                print(f"Error reading pipeline file {arg}: {e}")
                sys.exit(1)
        else:
            pipeline_json = arg
            print(f"Using pipeline definition from command line argument")
    elif os.environ.get('PIPELINE_DEFINITION'):
        pipeline_json = os.environ['PIPELINE_DEFINITION']
        print(f"Using pipeline definition from PIPELINE_DEFINITION environment variable")
    
    if not pipeline_json:
        print("Error: No pipeline definition provided in arguments or environment")
        sys.exit(1)
    
    # Parse the VAMS pipeline JSON
    try:
        pipeline_def = json.loads(pipeline_json)
        print(f"Successfully parsed pipeline definition")
    except json.JSONDecodeError as e:
        print(f"Failed to parse pipeline definition as JSON: {e}")
        print(f"Raw content (first 200 chars): '{pipeline_json[:200]}'")
        sys.exit(1)
    
    # The pipeline definition carries the metadata + input-configuration S3 locations; read each from S3
    input_metadata_s3_location = pipeline_def.get('inputMetadataS3Location', '')
    input_configuration_s3_location = pipeline_def.get('inputConfigurationS3Location', '')
    print(f"Input metadata S3 location: {input_metadata_s3_location}")
    print(f"Input configuration S3 location: {input_configuration_s3_location}")

    metadata_obj = manifest_io.fetch_metadata(input_metadata_s3_location)
    input_parameters_obj = manifest_io.fetch_input_configuration(input_configuration_s3_location)

    # Config settings come from the envelope's asset-level metadata (see resolve_asset_metadata).
    metadata_config = resolve_asset_metadata(metadata_obj)
    print(f"Asset metadata settings: {metadata_config}")

    # Store for main.py access
    if metadata_obj:
        os.environ['VAMS_INPUT_METADATA'] = json.dumps(metadata_obj)
    if input_parameters_obj:
        os.environ['VAMS_INPUT_PARAMETERS'] = json.dumps(input_parameters_obj)

    # Set config parameters from metadata and parameters (metadata takes priority)
    set_config_parameters(input_parameters_obj, metadata_config)
    
    # Extract the input file information from the first stage
    if not pipeline_def.get('stages') or len(pipeline_def['stages']) == 0:
        print("Error: No stages found in pipeline definition")
        sys.exit(1)
    
    stage = pipeline_def['stages'][0]
    input_file = stage.get('inputFile', {})
    output_files = stage.get('outputFiles', {})
    
    if not input_file or not output_files:
        print("Error: Missing inputFile or outputFiles in stage")
        sys.exit(1)
    
    # Set environment variables that main.py expects
    os.environ['S3_INPUT'] = f"s3://{input_file['bucketName']}/{input_file['objectKey']}"
    os.environ['FILENAME'] = input_file['objectKey'].split('/')[-1]

    # S3_OUTPUT + UUID recompose to the output-files prefix (see resolve_output_env), so outputs land
    # at its root and only the workflow's output path prefix nests them.
    os.environ['S3_OUTPUT'], os.environ['UUID'] = resolve_output_env(
        output_files['bucketName'], output_files['objectDir'],
        pipeline_def.get('jobName', 'pipeline-job'))

    # Force the correct paths for Batch environment
    os.environ['AWS_BATCH_JOB_ID'] = 'vams-batch-job'
    os.environ['DATASET_PATH'] = '/tmp/input/train'
    os.environ['MODEL_PATH'] = '/tmp/input/model'
    
    # Don't set MODEL_INPUT - this will skip model download in main.py
    # The container has pre-built models that should work
    
    # Create required directories
    os.makedirs('/tmp/input/train', exist_ok=True)
    os.makedirs('/tmp/input/model', exist_ok=True)
    
    # Create empty models.tar.gz so untar_gz doesn't fail
    import tarfile
    models_path = '/tmp/input/model/models.tar.gz'
    # Check if the file already exists to avoid creating it twice
    if not os.path.exists(models_path):
        with tarfile.open(models_path, 'w:gz') as tar:
            pass  # Create empty tar.gz file
        print(f"Created empty models.tar.gz at {models_path}")
    else:
        print(f"models.tar.gz already exists at {models_path}, skipping creation")
    
    print(f"Starting Splat Toolbox pipeline for: {os.environ['FILENAME']}")
    print(f"Model path: {os.environ['MODEL_PATH']}")
    print(f"Dataset path: {os.environ['DATASET_PATH']}")
    print(f"S3_INPUT: {os.environ['S3_INPUT']}")
    print(f"S3_OUTPUT: {os.environ['S3_OUTPUT']}")
    print(f"UUID: {os.environ['UUID']}")
    
    # Get task token for callback
    task_token = pipeline_def.get('externalSfnTaskToken', '')
    
    # Add the code path to Python path so main.py can import pipeline
    env = os.environ.copy()
    env['PYTHONPATH'] = '/opt/ml/code'
    
    # Call the existing main.py from the directory
    try:
        print("Starting main.py with real-time output...")
        result = subprocess.run([sys.executable, 'main.py'], # nosemgrep: dangerous-subprocess-use-audit
                              cwd='/opt/ml/code',
                              env=env,
                              check=True)
        print("Pipeline completed successfully")
        
        # Send success callback if task token exists
        if task_token:
            print(f"Sending success callback with task token")
            region = os.environ.get('AWS_REGION', 'us-east-1')
            sfn_client = boto3.client('stepfunctions', region_name=region)
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps({'status': 'Pipeline Success'})
            )
            print("Success callback sent")
        
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print(f"Pipeline failed: {e}")
        
        # Send failure callback if task token exists
        if task_token:
            print(f"Sending failure callback with task token")
            region = os.environ.get('AWS_REGION', 'us-east-1')
            sfn_client = boto3.client('stepfunctions', region_name=region)
            sfn_client.send_task_failure(
                taskToken=task_token,
                error='Pipeline Failure',
                cause=f'Pipeline execution failed with error: {str(e)}'
            )
            print("Failure callback sent")
        
        sys.exit(1)


if __name__ == "__main__":
    main()
