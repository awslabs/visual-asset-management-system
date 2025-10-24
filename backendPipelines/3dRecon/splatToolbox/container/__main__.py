# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
import subprocess
import boto3

def set_config_parameters(input_parameters: str, input_metadata: str):
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
    
    # Parse parameters
    params = {}
    if input_parameters:
        try:
            # Handle double-encoded JSON strings
            if isinstance(input_parameters, str):
                params = json.loads(input_parameters)
            else:
                params = input_parameters
            print(f"Parsed input parameters: {params}")
        except json.JSONDecodeError as e:
            print(f"Failed to parse input parameters: {e}")
            print(f"Raw input_parameters: {repr(input_parameters)}")
    
    # Parse metadata (takes priority)
    # VAMS wraps metadata in {"VAMS": {"assetMetadata": {...}}}
    metadata = {}
    if input_metadata:
        try:
            # Handle double-encoded JSON strings
            if isinstance(input_metadata, str):
                metadata_obj = json.loads(input_metadata)
            else:
                metadata_obj = input_metadata
            print(f"Parsed metadata object type: {type(metadata_obj)}")
            print(f"Parsed metadata object: {metadata_obj}")
            
            # Extract from VAMS wrapper if present
            if isinstance(metadata_obj, dict) and 'VAMS' in metadata_obj:
                metadata = metadata_obj.get('VAMS', {}).get('assetMetadata', {})
                print(f"Extracted VAMS.assetMetadata: {metadata}")
            else:
                metadata = metadata_obj
                print(f"Using metadata as-is (no VAMS wrapper): {metadata}")
        except json.JSONDecodeError as e:
            print(f"Failed to parse input metadata: {e}")
            print(f"Raw input_metadata: {repr(input_metadata)}")
        except Exception as e:
            print(f"Unexpected error parsing metadata: {e}")
            print(f"Raw input_metadata: {repr(input_metadata)}")
    
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
    
    # Extract metadata and parameters from pipeline definition
    input_metadata = pipeline_def.get('inputMetadata', '')
    input_parameters = pipeline_def.get('inputParameters', '')
    
    print(f"Raw input metadata type: {type(input_metadata)}")
    print(f"Raw input metadata: {input_metadata}")
    print(f"Raw input parameters type: {type(input_parameters)}")
    print(f"Raw input parameters: {input_parameters}")
    
    # Store for main.py access
    if input_metadata:
        os.environ['VAMS_INPUT_METADATA'] = input_metadata
    if input_parameters:
        os.environ['VAMS_INPUT_PARAMETERS'] = input_parameters
    
    # Set config parameters from metadata and parameters
    set_config_parameters(input_parameters, input_metadata)
    
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
    os.environ['UUID'] = pipeline_def.get('jobName', 'pipeline-job')
    os.environ['S3_INPUT'] = f"s3://{input_file['bucketName']}/{input_file['objectKey']}"
    os.environ['FILENAME'] = input_file['objectKey'].split('/')[-1]
    
    # Set S3_OUTPUT to point to the assets output location (not aux assets)
    # This ensures final asset files (.ply, .spz, .sog, .mp4) are written to the correct location
    os.environ['S3_OUTPUT'] = f"s3://{output_files['bucketName']}/{output_files['objectDir']}"
    
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
        result = subprocess.run([sys.executable, 'main.py'], 
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
