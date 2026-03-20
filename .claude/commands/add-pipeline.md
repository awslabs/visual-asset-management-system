# Add VAMS Processing Pipeline

Scaffold a new VAMS processing pipeline with all required files following established patterns. This creates the complete pipeline structure including Lambda functions, container code, CDK infrastructure, and Step Functions integration.

## Instructions

You are scaffolding a new VAMS processing pipeline. VAMS pipelines process assets through AWS Step Functions state machines with Lambda orchestration and either Lambda containers or ECS/Batch for heavy processing.

### Step 1: Gather Requirements

Ask the user for:

-   **Pipeline name**: A descriptive name in camelCase (e.g., `meshOptimizer`, `imageClassifier`, `pointCloudProcessor`)
-   **Pipeline category**: One of `conversion`, `preview`, `genAi`, `multi`, `3dRecon`, `simulation` (determines folder location)
-   **Input file types**: Which file extensions the pipeline processes (e.g., `.obj, .fbx, .stl`)
-   **Processing type**: `lambdaContainer` (for short tasks < 15min) or `ecsBatch` (for long-running tasks, GPU needed)
-   **Description**: What the pipeline does
-   **GPU required**: Whether the container needs GPU access (affects Batch vs Fargate construct)
-   **Output file types**: What files the pipeline produces

### Step 2: Understand the Pipeline Architecture

Every VAMS pipeline follows this Step Functions flow:

```
openPipeline (Lambda) -> constructPipeline (Lambda) -> [Container Task] -> pipelineEnd (Lambda)
```

-   **openPipeline**: Called by the workflow execution system. Standard across all pipelines. NOT in the pipeline's own code -- it is in `backend/backend/handlers/pipelines/`.
-   **constructPipeline**: Pipeline-specific Lambda that prepares the container task input (S3 paths, parameters). Located in `backendPipelines/{category}/{pipelineName}/lambda/`.
-   **Container Task**: The actual processing -- either a Lambda container or ECS/Batch task. Located in `backendPipelines/{category}/{pipelineName}/container/`.
-   **pipelineEnd**: Pipeline-specific Lambda that handles post-processing (uploading results back to VAMS). Located in `backendPipelines/{category}/{pipelineName}/lambda/`.

Additionally, each pipeline has:

-   **vamsExecute Lambda**: The VAMS-facing Lambda that gets invoked by workflow execution. Located in `backendPipelines/{category}/{pipelineName}/lambda/`.

### Step 3: Create Backend Pipeline Files

Create the following directory structure:

```
backendPipelines/
  {category}/
    {pipelineName}/
      lambda/
        __init__.py
        constructPipeline.py
        pipelineEnd.py
        vamsExecute{PipelineName}Pipeline.py
        customLogging/
          __init__.py
          logger.py
      container/
        __main__.py
        pipeline_vams.py
        Dockerfile
        requirements.txt
        utils/
          __init__.py
          aws/
            s3.py
            sfn.py
          logging/
            log.py
          pipeline/
            extensions.py
            objects.py
```

#### constructPipeline.py Pattern

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import json
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="constructPipeline-{pipelineName}")

s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions')

def lambda_handler(event, context):
    """
    Construct the pipeline task input for {pipelineName}.

    Receives the pipeline execution context from openPipeline and
    prepares the container/task input with S3 paths and parameters.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Extract pipeline context
        pipeline_id = event.get('pipelineId')
        database_id = event.get('databaseId')
        asset_id = event.get('assetId')
        execution_id = event.get('executionId')
        bucket_name = event.get('bucketName')
        asset_key = event.get('assetKey')
        output_bucket = event.get('outputBucket', bucket_name)

        # Build container input
        container_input = {
            'pipelineId': pipeline_id,
            'databaseId': database_id,
            'assetId': asset_id,
            'executionId': execution_id,
            'inputBucket': bucket_name,
            'inputKey': asset_key,
            'outputBucket': output_bucket,
            'outputPrefix': f"{asset_key}/{pipeline_id}/",
            # Add pipeline-specific parameters here
        }

        logger.info(f"Constructed pipeline input: {json.dumps(container_input)}")
        return container_input

    except Exception as e:
        logger.exception(f"Error constructing pipeline: {e}")
        raise
```

#### pipelineEnd.py Pattern

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import json
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="pipelineEnd-{pipelineName}")

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Handle pipeline completion for {pipelineName}.

    Process output files and update asset metadata as needed.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Extract results from container output
        status = event.get('status', 'SUCCEEDED')
        output_bucket = event.get('outputBucket')
        output_prefix = event.get('outputPrefix')

        if status != 'SUCCEEDED':
            logger.error(f"Pipeline failed with status: {status}")
            return {
                'status': 'FAILED',
                'message': event.get('error', 'Unknown error')
            }

        # Process output files
        # Upload results back to VAMS asset location if needed

        return {
            'status': 'SUCCEEDED',
            'message': f'Pipeline {event.get("pipelineId")} completed successfully'
        }

    except Exception as e:
        logger.exception(f"Error in pipeline end: {e}")
        return {
            'status': 'FAILED',
            'message': str(e)
        }
```

#### vamsExecute{PipelineName}Pipeline.py Pattern

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import json
import boto3
from customLogging.logger import safeLogger

logger = safeLogger(service="vamsExecute{PipelineName}")

sfn_client = boto3.client('stepfunctions')

STATE_MACHINE_ARN = os.environ.get('STATE_MACHINE_ARN', '')

def lambda_handler(event, context):
    """
    VAMS execution entry point for {pipelineName} pipeline.

    Called by the workflow execution system to start the pipeline
    Step Functions state machine.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Start Step Functions execution
        execution_input = json.dumps(event)

        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=execution_input
        )

        logger.info(f"Started state machine execution: {response['executionArn']}")

        return {
            'statusCode': 200,
            'body': {
                'executionArn': response['executionArn'],
                'message': 'Pipeline execution started'
            }
        }

    except Exception as e:
        logger.exception(f"Error starting pipeline: {e}")
        raise
```

#### Container pipeline_vams.py Pattern

```python
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import json
import boto3
import tempfile
from utils.logging.log import get_logger
from utils.aws.s3 import download_file, upload_file, upload_directory
from utils.aws.sfn import send_task_success, send_task_failure
from utils.pipeline.extensions import SUPPORTED_EXTENSIONS

logger = get_logger(__name__)

s3_client = boto3.client('s3')


def run_pipeline(event):
    """
    Main pipeline processing logic for {pipelineName}.

    Downloads input files from S3, processes them, and uploads results.
    """
    logger.info(f"Starting pipeline with event: {json.dumps(event)}")

    input_bucket = event['inputBucket']
    input_key = event['inputKey']
    output_bucket = event['outputBucket']
    output_prefix = event['outputPrefix']

    with tempfile.TemporaryDirectory() as work_dir:
        # Download input files
        input_path = os.path.join(work_dir, 'input')
        os.makedirs(input_path, exist_ok=True)
        download_file(s3_client, input_bucket, input_key, input_path)

        # Process files
        output_path = os.path.join(work_dir, 'output')
        os.makedirs(output_path, exist_ok=True)

        # TODO: Add pipeline-specific processing logic here

        # Upload results
        upload_directory(s3_client, output_path, output_bucket, output_prefix)

    return {
        'status': 'SUCCEEDED',
        'outputBucket': output_bucket,
        'outputPrefix': output_prefix
    }


if __name__ == '__main__':
    # Entry point for container execution
    event = json.loads(os.environ.get('PIPELINE_INPUT', '{}'))
    task_token = os.environ.get('TASK_TOKEN', '')

    try:
        result = run_pipeline(event)
        if task_token:
            send_task_success(task_token, result)
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        if task_token:
            send_task_failure(task_token, str(e))
        sys.exit(1)
```

#### Container Utils Pattern

Copy the standard utility files from an existing pipeline (e.g., `backendPipelines/3dRecon/splatToolbox/container/utils/`). These provide:

-   `utils/aws/s3.py` - S3 download/upload helpers
-   `utils/aws/sfn.py` - Step Functions task token helpers
-   `utils/logging/log.py` - Logging configuration
-   `utils/pipeline/extensions.py` - Supported file extension definitions
-   `utils/pipeline/objects.py` - Pipeline data object definitions

#### Dockerfile Pattern

```dockerfile
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies
# RUN yum install -y <packages>

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . /app
WORKDIR /app

CMD ["python", "-m", "__main__"]
```

### Step 4: Create CDK Infrastructure

Create the following CDK files:

#### Pipeline Construct

Create `infra/lib/nestedStacks/pipelines/{category}/{pipelineName}/constructs/{pipelineName}-construct.ts`:

This construct creates:

-   Step Functions state machine
-   Lambda functions (constructPipeline, pipelineEnd, vamsExecute)
-   Container task (Lambda container or Batch/Fargate)
-   IAM roles and permissions

Follow the pattern from an existing construct like `conversion3dBasic-construct.ts` or `splatToolbox-construct.ts`.

The construct should:

1. Create constructPipeline Lambda
2. Create pipelineEnd Lambda
3. Create container task definition (Lambda container or Batch)
4. Create Step Functions state machine linking them
5. Create vamsExecute Lambda that starts the state machine
6. Export the `pipelineVamsLambdaFunctionName` for pipeline registration

#### Lambda Builder Functions

Create `infra/lib/nestedStacks/pipelines/{category}/{pipelineName}/lambdaBuilder/{pipelineName}Functions.ts`:

Follow the pattern from existing pipeline lambda builders. Each function needs:

-   Standard signature with scope, layer, storageResources, config, vpc, subnets
-   Code path pointing to `backendPipelines/{category}/{pipelineName}/lambda`
-   The 4 security helper calls

#### Pipeline Nested Stack

Create `infra/lib/nestedStacks/pipelines/{category}/{pipelineName}/{pipelineName}Builder-nestedStack.ts`:

```typescript
import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { {PipelineName}Construct } from "./constructs/{pipelineName}-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";

export interface {PipelineName}NestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

const defaultProps: Partial<{PipelineName}NestedStackProps> = {};

export class {PipelineName}NestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: {PipelineName}NestedStackProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };

        const pipeline = new {PipelineName}Construct(this, "{PipelineName}Pipeline", {
            ...props,
        });

        this.pipelineVamsLambdaFunctionName = pipeline.pipelineVamsLambdaFunctionName;
    }
}
```

### Step 5: Register Pipeline in Pipeline Builder

Update `infra/lib/nestedStacks/pipelines/pipelineBuilder-nestedStack.ts`:

1. Add import for the new nested stack
2. Add config flag check: `if (props.config.app.pipelines.use{PipelineName}.enabled)`
3. Instantiate the nested stack with standard props
4. Add the `pipelineVamsLambdaFunctionName` to the `pipelineVamsLambdaFunctionNames` array

```typescript
// Import
import { {PipelineName}NestedStack } from "./{category}/{pipelineName}/{pipelineName}Builder-nestedStack";

// In constructor, after other pipeline registrations:
if (props.config.app.pipelines.use{PipelineName}.enabled) {
    const {pipelineName}NestedStack = new {PipelineName}NestedStack(
        this,
        "{PipelineName}NestedStack",
        {
            ...props,
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            pipelineSubnets: pipelineNetwork.isolatedSubnets.pipeline,
            pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowFunctionName:
                props.importGlobalPipelineWorkflowFunctionName,
        }
    );
    this.pipelineVamsLambdaFunctionNames.push(
        {pipelineName}NestedStack.pipelineVamsLambdaFunctionName
    );
}
```

### Step 6: Add Config Flag

Update `infra/config/config.ts` to add the pipeline enablement flag under `pipelines`:

```typescript
use{PipelineName}: { enabled: boolean };
```

Update `infra/config/config.json` to include the new pipeline flag:

```json
"pipelines": {
    "use{PipelineName}": { "enabled": false }
}
```

### Step 7: Validate

After creating all files, verify:

-   [ ] Lambda handler paths in CDK match actual file locations in `backendPipelines/`
-   [ ] Step Functions state machine references correct Lambda ARNs
-   [ ] Container Dockerfile builds successfully
-   [ ] Config flag name matches between config.json and pipelineBuilder check
-   [ ] Pipeline nested stack is imported and registered in pipelineBuilder-nestedStack.ts
-   [ ] `pipelineVamsLambdaFunctionName` is pushed to the array for pipeline registration

## Workflow

1. Gather requirements from the user (or parse from $ARGUMENTS)
2. Determine pipeline category and processing type
3. Create all backend pipeline files (lambda + container)
4. Create CDK infrastructure (construct, nested stack, lambda builder)
5. Register in pipelineBuilder-nestedStack.ts
6. Add config flag
7. Summarize created files and next steps

## User Request

$ARGUMENTS
