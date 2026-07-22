---
title: Workflow / Pipeline / Execution Smoke Test Plan
description: An ordered, developer-executable smoke test plan for the overhauled V2 pipeline, workflow, and execution API — deploy, mock pipeline lambdas, the full CLI surface, an input/output combinations matrix, and DeadlineCloud backend-only validation.
---

# Workflow / Pipeline / Execution Smoke Test Plan

This is a developer-executable smoke test plan for the overhauled (V2) pipeline, workflow, and
execution code path — the single path that remains after the
[V1 to V2 consolidation](./workflow-pipeline-v1v2-consolidation-plan.md) and the
[Phase 2 overhaul](./workflow-pipeline-overhaul-phase2-plan.md). It exercises the backend handlers,
Step Functions definition generation, the S3 run-I/O + manifest contract, the V2 DynamoDB tables, and
the full `vamscli` command surface (`pipeline`, `workflow`, `execution`) against a fresh deployment,
using **mock pipeline lambdas** so no real use-case pipeline needs to be enabled.

:::note[This is a plan, not a run log]
This document describes the steps to execute later. It performs no deployment, AWS calls, or tests.
Every command is written with the real `vamscli` command names and options so the plan can be run
verbatim.
:::

:::warning[Keep everything after the run]
Do **not** tear down the mock lambdas, pipelines, templates, workflows, or their executions after this
round. They become the seed data for the later web front-end testing phase. See
[Persistence](#persistence-keep-the-seed-data).
:::

## Target environment

| Setting            | Value                                                      |
| ------------------ | ---------------------------------------------------------- |
| AWS profile        | `aws-pan-spatial-computing+vams-app-Admin`                 |
| Region             | `us-west-2`                                                |
| Deployment         | FRESH — no prior VAMS stack in the account/region          |
| Use-case pipelines | All OFF (no built-in pipeline containers/lambdas deployed) |
| Auth provider      | Cognito (as in `infra/config/config.json`)                 |
| Partition          | Commercial (`aws`)                                         |

The active `infra/config/config.json` is the deployment used for this first round. All work in this
plan runs against that fresh stack.

:::note[Verify the config before deploying]
The intended target has **every** use-case pipeline disabled. The active `infra/config/config.json`
currently still sets `app.pipelines.usePreviewPcPotreeViewer.enabled` to `true` and
`app.pipelines.deadlineCloudExecutionTypeEnabled` to `false`. For the pure mock round, set
`usePreviewPcPotreeViewer.enabled` to `false` (all use-case pipelines OFF), and set
`deadlineCloudExecutionTypeEnabled` to `true` so the DeadlineCloud backend-only checks in
[Phase 1 — DeadlineCloud backend-only](#phase-1--deadlinecloud-backend-only-no-real-execution) can
run. Committing/deploying config is the main agent's concern; this plan only states the target.
:::

## The pipeline-task contract the mock lambdas implement

The mock lambdas must mimic what a real VAMS pipeline task receives, so the smoke test proves the
whole contract end to end. Three artifacts define it.

### 1. The Step Functions task body

The workflow ASL builders (`backend/backend/common/workflows/stepfunctions_builder.py`) invoke each
pipeline step with a payload whose single top-level key is `body`. For a **Lambda** step the mock
lambda receives that object directly as its event; for an **SQS** step it is the message body; for an
**EventBridge** step it is the event `detail`. The body carries:

```json
{
    "body": {
        "workflowDatabaseId": "...",
        "workflowId": "...",
        "workflowExecutionId": "...",
        "workflowExecutionS3InputOutputBucket": "<default run bucket>",
        "executingUserName": "...",
        "executingRequestContext": { "...": "..." },
        "inputManifestS3Location": "s3://<run-bucket>/pipelines/.../manifest.json",
        "inputConfigurationS3Location": "s3://<run-bucket>/pipelines/.../config.json",
        "TaskToken": "<present only when waitForCallback=Enabled>"
    }
}
```

`inputManifestS3Location` and `inputConfigurationS3Location` are S3 pointers — the pipeline reads the
manifest and config from S3, it does not receive them inline. `TaskToken` is present only when the
pipeline's `executionConfig.waitForCallback` is `Enabled`.

### 2. The manifest envelope

The manifest (fetched from `inputManifestS3Location`) is the same envelope the built-in pipelines'
`manifestHelper.py` reads:

```json
{
    "schemaVersion": 1,
    "inputFiles": [
        {
            "relativePath": "/a.glb",
            "databaseId": "...",
            "assetId": "...",
            "assetRootS3Key": "...",
            "auxPreviewPrefix": "...",
            "bucket": "...",
            "key": "...",
            "versionId": ""
        }
    ],
    "inputMetadataS3Location": "s3://<run-bucket>/.../metadata.json",
    "outputs": {
        "bucket": "<default run bucket>",
        "files": "...",
        "previews": "...",
        "metadata": "...",
        "results": "..."
    },
    "auxBucket": "<auxiliary bucket>",
    "auxTempPrefix": "pipelines/{pipelineName}/{executionId}/",
    "auxPreviewPipelineSuffix": "",
    "systemConfig": { "orchestrationBusArn": "...", "orchestrationEventPrefix": "..." }
}
```

`outputs` pairs one `bucket` (the default run bucket) with bucket-relative prefixes; each input file
carries its own source `bucket`/`key` (input files are read from their own asset buckets). Output keys
must **preserve the input file's relative path** within the asset.

### 3. The grouped-by-asset metadata envelope (schemaVersion 2)

The shared metadata file (fetched from `manifest.inputMetadataS3Location`) is the v2 grouped envelope:

```json
{
    "schemaVersion": 2,
    "assets": [
        {
            "databaseId": "db1",
            "assetId": "xid1",
            "assetData": { "assetName": "...", "description": "...", "tags": [] },
            "files": [
                { "fileKey": "/", "metadata": { "...": "..." } },
                {
                    "fileKey": "/a.glb",
                    "metadata": { "...": "..." },
                    "attributes": { "...": "..." }
                }
            ]
        }
    ]
}
```

One `assets[]` entry per involved asset; the `fileKey: "/"` record is asset-level metadata; per-file
records appear only for selected files. The workflow's `metadataInputs` gate
(`assetMetadata` / `fileMetadata` / `fileAttributes`) controls which of these are populated.

---

## Phase 0 — Deploy the stack and connect the CLI

Goal: a fresh, use-case-pipeline-free deployment with the V2 API reachable and the CLI authenticated.

-   [ ] Confirm no prior VAMS stack exists in `aws-pan-spatial-computing+vams-app-Admin` / `us-west-2`.
-   [ ] Confirm `infra/config/config.json` has all `app.pipelines.use*` flags disabled (see the config
        note above) and `deadlineCloudExecutionTypeEnabled` set to `true`.
-   [ ] Deploy:

```bash
cd infra
npm install
AWS_PROFILE=aws-pan-spatial-computing+vams-app-Admin AWS_REGION=us-west-2 npx cdk deploy --all --require-approval never
```

-   [ ] Verify the nested stacks created (core, storage, resourceNames, auth, api, apiBuilder,
        apiBuilder2, staticWeb, search) — **no** pipeline nested stacks for any use-case pipeline.
-   [ ] Verify the SSM resource-name parameters are published under the deployment prefix
        (`/{config.name}-{baseStackName}/resourceNames/...`), including the six V2 pipeline/workflow
        definition tables and the workflow-execution V2 tables:

```bash
aws --profile aws-pan-spatial-computing+vams-app-Admin --region us-west-2 \
  ssm get-parameters-by-path --path "/vams-prod14/resourceNames" --recursive \
  --query "Parameters[].Name"
```

-   [ ] Confirm the V2 tables exist and are EMPTY (`PipelineStorageTableV2`, `PipelineTemplatesStorageTable`,
        `PipelineTemplateTagSchemaStorageTable`, `WorkflowStorageTableV2`, `WorkflowTriggersStorageTable`,
        the workflow-execution V2 tables, and `WorkflowExecutionOutputsIndex`) via `aws dynamodb scan
--select COUNT` on each resolved table name.
-   [ ] Confirm the API is reachable (the amplify-config endpoint returns the API URL).
-   [ ] Configure + authenticate the CLI:

```bash
cd tools/VamsCLI
pip install -e .
export PYTHONIOENCODING=utf-8
vamscli setup <api-gateway-url>
vamscli auth login -u scheurik@amazon.com
```

-   [ ] `vamscli features` — confirm the deployment's feature switches (no pipeline features enabled;
        `DEADLINECLOUD_PIPELINES` present because the type is enabled in config).

### Seed data (databases + assets + input files)

Executions need a database, one or more assets, and uploaded input files. Create enough to cover the
combinations matrix (single-asset and multi-asset, single-file and folder).

-   [ ] `vamscli database create` — a test database (e.g. `smoke-db`) and a second (`smoke-db-2`) for
        the cross-database / multi-asset cases.
-   [ ] Create assets and upload files with `vamscli file upload` so each asset has:
    -   a single file at the asset root (e.g. `/model.glb`),
    -   a folder with multiple files (e.g. `/scan/a.e57`, `/scan/b.e57`),
    -   at least one file with a second version (for the `versionId` case).
-   [ ] Record the databaseId / assetId / relative keys for use in `workflow execute` below.

---

## Phase 1 — Mock pipeline lambdas + the full CLI surface

Everything in Phase 1 runs against the fresh stack, using mock resources deployed **outside** the CDK
app.

### 1.1 Build the mock pipeline resources

Deploy these manually (a small boto3 script or the console), outside CDK. Each mock lambda emulates
the pipeline-task contract above:

1. **Log everything** (heavy debug): the full event, the resolved `body`, the fetched manifest, the
   fetched per-pipeline config, and the fetched grouped metadata envelope — all to its CloudWatch log
   group.
2. **Read the pointers** from `body`: `workflowExecutionS3InputOutputBucket`,
   `inputManifestS3Location`, `inputConfigurationS3Location`, and `TaskToken` (when present).
3. **Fetch** the manifest, the config JSON, and (from `manifest.inputMetadataS3Location`) the grouped
   metadata envelope.
4. **Write outputs** to the manifest's `outputs` prefixes on `outputs.bucket`, preserving each input
   file's relative path — write a small marker file under `outputs.files` (mirroring the input
   relative path), a `outputs.results` results file, and optionally a `outputs.metadata` file.
5. **Call back** the Step Functions task token when `TaskToken` is present:
   `stepfunctions.send_task_success(taskToken=..., output=json.dumps({...}))`.

The mock lambda's IAM role needs: CloudWatch Logs; `s3:GetObject`/`s3:PutObject`/`s3:ListBucket` on
the default run bucket, the asset buckets, and the auxiliary bucket; and
`states:SendTaskSuccess`/`states:SendTaskFailure`.

Build these mock variants:

| Variant | Backing resource                  | executionConfig                                                                                                                                            |
| ------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (a)     | Sync Lambda, no callback          | `{"executionType":"Lambda","waitForCallback":"Disabled","lambda":{"resourceId":"<fn-name>"}}`                                                              |
| (b)     | Callback Lambda + task token      | `{"executionType":"Lambda","waitForCallback":"Enabled","taskTimeout":"3600","lambda":{"resourceId":"<fn-name>"}}`                                          |
| (c)     | SQS queue + mock consumer lambda  | `{"executionType":"SQS","waitForCallback":"Enabled","taskTimeout":"3600","sqs":{"queueUrl":"<url>"}}`                                                      |
| (d)     | EventBridge bus + rule + consumer | `{"executionType":"EventBridge","waitForCallback":"Enabled","taskTimeout":"3600","eventBridge":{"busArn":"<arn>","source":"<src>","detailType":"<type>"}}` |

For (a): the SFN Lambda-invoke returns when the mock returns; the workflow proceeds. No token.

For (b): the SFN uses `lambda:invoke.waitForTaskToken`; the mock reads `body.TaskToken` and calls
`send_task_success`. The workflow blocks until the callback.

For (c): the SFN uses `sqs:sendMessage.waitForTaskToken`; the payload lands as the message body. A
**mock consumer lambda** (event-source-mapped to the queue) parses the message, reads `body.TaskToken`,
does the mock work (fetch/log/write outputs), and calls `send_task_success`.

For (d): the SFN uses `events:putEvents.waitForTaskToken` and puts an event on `busArn` with the
configured `source`/`detailType`, its `Detail` being the payload. A **mock rule** matching that
`source`/`detailType` targets a **mock consumer lambda** that reads `detail.body.TaskToken`, does the
mock work, and calls `send_task_success`.

-   [ ] Also exercise the **auto-provision** path once: create a Lambda-type pipeline **without** a
        `lambda.resourceId` and confirm VAMS provisions a sample Lambda (requires the deploy-time
        `ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE` + sample-package env on the pipeline service Lambda). Confirm
        the returned pipeline's `executionConfig.lambda.resourceId` is set to the newly-created function.

### 1.2 Register pipelines, templates, tag schemas, and workflows via the CLI

For each mock resource, create a pipeline whose `executionConfig` references it, then one or more
templates (with tag schemas), then a workflow referencing the pipeline(s).

-   [ ] Create pipelines (one per mock variant) — pass `--execution-config` referencing the mock resource:

```bash
# (b) callback Lambda-backed pipeline in the GLOBAL scope
vamscli pipeline create -d GLOBAL -n "Mock Callback Lambda" -p mock-cb-lambda \
  --category mock \
  --execution-config '{"executionType":"Lambda","waitForCallback":"Enabled","taskTimeout":"3600","lambda":{"resourceId":"mock-callback-fn"}}' \
  --system-config '{"inputFileArity":"one","assetScope":{"singleAssetOnly":true},"metadataInputs":{"assetMetadata":true,"fileMetadata":true,"fileAttributes":true},"requireTemplate":false,"allowCustomTemplateOverride":true}'

# (c) SQS-backed pipeline
vamscli pipeline create -d GLOBAL -n "Mock SQS" -p mock-sqs \
  --execution-config '{"executionType":"SQS","waitForCallback":"Enabled","taskTimeout":"3600","sqs":{"queueUrl":"https://sqs.us-west-2.amazonaws.com/<acct>/mock-queue"}}'

# (d) EventBridge-backed pipeline
vamscli pipeline create -d GLOBAL -n "Mock EventBridge" -p mock-eb \
  --execution-config '{"executionType":"EventBridge","waitForCallback":"Enabled","taskTimeout":"3600","eventBridge":{"busArn":"arn:aws:events:us-west-2:<acct>:event-bus/mock-bus","source":"vams.mock","detailType":"mock.pipeline"}}'
```

-   [ ] Create **multiple templates per pipeline**, each with a distinct tag schema and `configBody`:

```bash
# a JSON-config template with a tag schema (templateId must match the ID pattern
# ^[-_a-zA-Z0-9]{3,63}$ — minimum 3 characters)
vamscli pipeline template create -d GLOBAL -p mock-cb-lambda -n "High quality" -t hqt \
  --config-format json \
  --config-body '{"quality":"{{quality}}","format":"{{outFormat}}"}' \
  --tag-schema '[{"tagKey":"quality","type":"enum","enumValues":["low","high"],"required":true,"default":"high"},{"tagKey":"outFormat","type":"string","default":"glb"}]' \
  --allow-custom-edit --input-instructions "Pick a quality preset."

# a second template on the same pipeline
vamscli pipeline template create -d GLOBAL -p mock-cb-lambda -n "Low quality" -t lqt \
  --config-body '{"quality":"low"}'
```

-   [ ] Exercise `pipeline tag-schema set` / `get` on a template independently:

```bash
vamscli pipeline tag-schema set -d GLOBAL -p mock-cb-lambda -t lqt \
  --fields '[{"tagKey":"note","type":"string"}]'
vamscli pipeline tag-schema get -d GLOBAL -p mock-cb-lambda -t lqt
```

-   [ ] Create workflows referencing one or more pipelines (`--pipeline databaseId:pipelineId[:defaultTemplateId]`):

```bash
# single-pipeline workflow, default template selected via the ref
vamscli workflow create -d GLOBAL -n "Mock single" -w mock-single \
  --pipeline GLOBAL:mock-cb-lambda:hqt \
  --system-config '{"inputFileArity":"one","assetScope":{"singleAssetOnly":true},"metadataInputs":{"assetMetadata":true,"fileMetadata":true,"fileAttributes":true},"concurrencyRestriction":"none","outputTarget":{"locationType":"asset","allowOverride":false}}'

# multi-pipeline workflow (chains the mock lambda + SQS + EventBridge pipelines)
vamscli workflow create -d GLOBAL -n "Mock multi" -w mock-multi \
  --pipeline GLOBAL:mock-cb-lambda:hqt --pipeline GLOBAL:mock-sqs --pipeline GLOBAL:mock-eb
```

-   [ ] (Optional, for auto-trigger coverage) set a fileUpload trigger:

```bash
vamscli workflow trigger set -d GLOBAL -w mock-single \
  --input-file-filters '{"allow":["*.glb"],"exclude":[]}' \
  --default-template-ids '{"GLOBAL:mock-cb-lambda":"hqt"}' --enable
```

### 1.3 Combinations matrix

Run `vamscli workflow execute` for each row, then run the [per-execution verification](#per-execution-verification)
against it. Vary the workflow's `systemConfig` (arity/scope/output override) to admit each case.

| #   | Input arity                 | Assets          | Pipelines             | Template selection                                   | Output target                              |
| --- | --------------------------- | --------------- | --------------------- | ---------------------------------------------------- | ------------------------------------------ |
| 1   | no input files              | n/a             | single (arity none)   | default                                              | results-only or explicit (rows 9-10)       |
| 2   | single input file           | single asset    | single                | run-selected `templateId` + tags                     | default (locked)                           |
| 3   | folder (`/folder/`)         | single asset    | single                | default template + tag values                        | default (locked)                           |
| 4   | whole asset (`/`)           | single asset    | single                | default                                              | default (locked)                           |
| 5   | multiple single files       | multiple assets | single                | per-run template + tags                              | explicit (honored)                         |
| 6   | single file                 | single asset    | multi (lambda+sqs+eb) | per-pipeline template ids + tags                     | default (locked)                           |
| 7   | multiple files              | multiple assets | multi                 | per-pipeline templates; one custom-template override | explicit (honored)                         |
| 8   | single file                 | single asset    | single                | `customTemplateOverride` (no templateId)             | default (locked)                           |
| 9   | no input files (arity none) | n/a             | single (results-only) | default                                              | none (results-only, `locationType` `none`) |
| 10  | multiple files              | multiple assets | single                | default                                              | explicit, `allowOverride` false (honored)  |

Example execute commands:

```bash
# Row 2 — single file, run-selected template + tags
vamscli workflow execute --workflow-database-id GLOBAL -w mock-single \
  --input-file smoke-db:asset1:/model.glb \
  --pipeline-parameters '{"mock-cb-lambda":{"templateId":"hqt","templateTags":[{"key":"quality","value":"low"},{"key":"outFormat","value":"stl"}]}}'

# Row 4 — whole asset
vamscli workflow execute --workflow-database-id GLOBAL -w mock-single \
  --input-file smoke-db:asset1:/

# Row 5 — multiple assets, output override
vamscli workflow execute --workflow-database-id GLOBAL -w mock-multi-crossasset \
  --input-file smoke-db:asset1:/model.glb --input-file smoke-db-2:asset9:/scan/a.e57 \
  --output-asset-id asset1 --output-database-id smoke-db

# Row 8 — custom template override, no templateId (requires allowCustomTemplateOverride + requireTemplate=false)
vamscli workflow execute --workflow-database-id GLOBAL -w mock-single \
  --input-file smoke-db:asset1:/model.glb \
  --pipeline-parameters '{"mock-cb-lambda":{"customTemplateOverride":"{\"quality\":\"{{q}}\"}","templateTags":[{"key":"q","value":"med"}]}}'

# Row 9 — results-only workflow (outputTarget.locationType "none" + inputFileArity "none"); no input files, no output ids
vamscli workflow execute --workflow-database-id GLOBAL -w mock-results-only

# Row 10 — multiple assets, explicit output honored with allowOverride false
vamscli workflow execute --workflow-database-id GLOBAL -w mock-multiasset-explicit \
  --input-file smoke-db:asset1:/model.glb --input-file smoke-db-2:asset9:/scan/a.e57 \
  --output-asset-id asset1 --output-database-id smoke-db
```

Rows 9-10 need two extra workflows created in [1.2](#12-register-pipelines-templates-tag-schemas-and-workflows-via-the-cli):

```bash
# results-only workflow — a mock pipeline that writes only a results file, no asset output
vamscli workflow create -d GLOBAL -n "Mock results only" -w mock-results-only \
  --pipeline GLOBAL:mock-cb-lambda:hqt \
  --system-config '{"inputFileArity":"none","assetScope":{"singleAssetOnly":false},"metadataInputs":{"assetMetadata":false,"fileMetadata":false,"fileAttributes":false},"concurrencyRestriction":"none","outputTarget":{"locationType":"none","allowOverride":false}}'

# multi-asset workflow that honors an explicit output target even though allowOverride is false
vamscli workflow create -d GLOBAL -n "Mock multiasset explicit" -w mock-multiasset-explicit \
  --pipeline GLOBAL:mock-cb-lambda:hqt \
  --system-config '{"inputFileArity":"multi","assetScope":{"crossAssetAllowed":true,"singleAssetOnly":false},"metadataInputs":{"assetMetadata":true,"fileMetadata":true,"fileAttributes":true},"concurrencyRestriction":"none","outputTarget":{"locationType":"asset","allowOverride":false}}'
```

Also confirm the **negative** cases return the expected validation error (not a launch):

-   [ ] Row 1 (arity `none`) is **not** an unconditional gap: it launches when the workflow is configured
        results-only (`outputTarget.locationType` `none`, row 9) or given an explicit output target (both
        `outputAssetId` and `outputDatabaseId`, row 10). Executed against a workflow whose pipeline
        requires a file, it still returns a cross-entity validation error (`executionValidationErrors`).
-   [ ] Multi-file input into a single-file (`inputFileArity: one`) workflow — expect an arity error.
-   [ ] A file that fails the workflow `inputFileFilters` — expect a filter error.
-   [ ] Inputs that resolve to zero or multiple assets with **no** explicit output target — expect the
        "does not resolve to a single input asset; supply an explicit output target … or configure the
        workflow as results-only" error. (An explicit output target for the 0/multi case is honored
        regardless of `allowOverride`, so it is not a negative case — see row 10.)
-   [ ] A results-only workflow (`outputTarget.locationType` `none`) executed with an `outputAssetId`/
        `outputDatabaseId` supplied — expect a contradiction error.
-   [ ] A `templateId` whose required tag has no value/default — expect a `templateResolutionErrors`.

### Per-execution verification

For each executed row, verify all four layers:

-   [ ] **(i) SFN definition correctness.** Resolve the workflow's `workflow_arn` (from `vamscli workflow
get`) and describe the state machine; confirm one task state per pipeline of the correct
        integration type:

```bash
aws --profile aws-pan-spatial-computing+vams-app-Admin --region us-west-2 \
  stepfunctions describe-state-machine --state-machine-arn <workflow_arn> \
  --query "definition" --output text | python -m json.tool
```

Confirm: Lambda no-callback uses `states:::lambda:invoke`; callback uses
`lambda:invoke.waitForTaskToken`; SQS uses `sqs:sendMessage.waitForTaskToken`; EventBridge uses
`events:putEvents.waitForTaskToken`; each task body carries `inputManifestS3Location`,
`inputConfigurationS3Location`, and (callback) `TaskToken.$`.

-   [ ] **(ii) Mock lambda CloudWatch debug logs.** In each mock lambda's log group, confirm the logged
        event, resolved `body`, fetched manifest, fetched per-pipeline config (the rendered template —
        tag values applied), and the grouped metadata envelope all reflect the run's inputs and the
        selected template/tags.
-   [ ] **(iii) S3 outputs + scratch/aux.** Confirm the mock wrote outputs under the manifest
        `outputs.files`/`previews`/`metadata`/`results` prefixes on the default run bucket, preserving the
        input relative path; confirm the per-execution input files exist (metadata file, per-pipeline
        `config.json`, pipeline 1 manifest) and the aux temp/preview prefixes.
-   [ ] **(iv) Execution records.** Via the CLI and a direct DynamoDB spot-check:

```bash
vamscli execution details <executionId>
vamscli execution logs <executionId>
vamscli execution logs <executionId> --mode full --limit 200
```

Spot-check the DynamoDB rows directly (resolve each table name from SSM first):

```bash
# main workflow-execution row (V2)
aws --profile aws-pan-spatial-computing+vams-app-Admin --region us-west-2 \
  dynamodb get-item --table-name <WorkflowExecutionsStorageTableV2> \
  --key '{"workflowExecutionId":{"S":"<executionId>"},"workflowDatabaseId:workflowId":{"S":"GLOBAL:mock-single"}}'
```

Confirm the presence and correctness of: the main workflow-execution row (status, trigger type,
group id when set); one workflow-input row per selected input; the workflow configuration row (output
target + specified-pipelines snapshot + metadata file key); one PipelineExecutions row per pipeline;
one input-configuration snapshot per pipeline (templateId, template/tag schema version, resolved
tags, `customTemplateOverrideUsed`, rendered config); the output-index row keyed on the output asset;
and, after completion, the per-pipeline output files/metadata/results + log rows.

-   [ ] Confirm each pipeline reports its status + logs into the execution records, and that a pipeline's
        sub-SFN execution ARN + CloudWatch log location are recorded (`registeredSubExecutions` /
        `registeredLogs` on the PipelineExecutions row) so `execution logs <id> --mode full` can pull from
        the sub-execution.
-   [ ] **Row 9 (results-only)** — confirm the workflow wrote **no** asset output: no output-index row,
        no asset files/metadata written back to an asset, and the main workflow-execution row records the
        results text + logs against the execution transaction. The mock pipeline writes a results file
        only.
-   [ ] **Row 10 (multi-asset explicit output)** — confirm the explicit `outputAssetId`/`outputDatabaseId`
        is honored even though `allowOverride` is `false` (two input assets, so no single input asset to
        lock to): the output-index row is keyed on the supplied output asset.

### 1.4 Update tests

-   [ ] `vamscli pipeline update` — change a pipeline's `--system-config`, toggle `--disable`/`--enable`,
        and (for a template-selection change) update a template with `vamscli pipeline template update`
        (change `--config-body` / `--tag-schema` / `--allow-custom-edit`). Re-run an execute and confirm
        the new config/tags flow through (mock lambda logs + config snapshot).
-   [ ] `vamscli workflow update` — change the pipeline set (`--pipeline` refs), the `--system-config`,
        and enable/disable. Confirm the workflow's Step Functions state machine is **redeployed** (the
        `workflow_arn`'s definition reflects the new pipeline set — describe-state-machine) and a fresh
        `vamscli workflow execute` still succeeds end to end.
-   [ ] Confirm a disabled pipeline referenced by a workflow blocks execution (disabled/archived gate),
        and re-enabling restores it.

### 1.5 API coverage checklist

Exercise every command in the three groups at least once, verifying logs are written and retrievable
and the DynamoDB records are correct:

-   [ ] `pipeline`: `list` (with and without `-d`, `--include-archived`), `get`, `create`, `update`,
        `delete` (archive).
-   [ ] `pipeline template`: `list`, `get`, `create`, `update`, `delete`.
-   [ ] `pipeline tag-schema`: `get`, `set`.
-   [ ] `workflow`: `list` (`--auto-paginate`), `get`, `create`, `update`, `delete` (archive).
-   [ ] `workflow trigger`: `list`, `get`, `set`, `delete`.
-   [ ] `workflow execute` — the matrix above.
-   [ ] `workflow list-executions` — per-asset history for a seeded asset.
-   [ ] `execution list` — global, with each filter (`-w`, `--workflow-database-id`, `--status`,
        `--trigger-type`, `--group-id`, `--triggered-by`) and `--auto-paginate`.
-   [ ] `execution details`, `execution logs` (`--mode truncated` and `--mode full`,
        `--pipeline-execution-id`).
-   [ ] `execution abort` (single) and `execution abort <memberId> --group-id <grp>` — run an execute
        batch under one `--execution-group-id` first so the group has active members.
-   [ ] `execution rerun` — confirm a NEW executionId is produced from the stored records and the re-run
        succeeds; `--execution-group-id` to group it.
-   [ ] `execution permanent-delete <id> --yes` — on a terminal execution; confirm the DynamoDB rows are
        gone across all sub-tables and Step Functions history is untouched.

### 1.6 CLI pytest suite

Part of this phase's validation is the CLI's own unit tests (they do not touch AWS):

```bash
cd tools/VamsCLI
python -m pytest -v
```

### Phase 1 — DeadlineCloud backend-only (no real execution)

With `app.pipelines.deadlineCloudExecutionTypeEnabled` set to `true` for this fresh commercial deploy,
validate the DeadlineCloud creation + Step Functions generation path **without running a real job**
(no Deadline farm exists yet). Use **mock** `farmId`/`queueId`/`fleetId` values.

-   [ ] Create a template whose `configBody` is an OpenJD job template, with `configFormat` `openjd`:

```bash
vamscli pipeline template create -d GLOBAL -p mock-deadline -n "OpenJD job" -t openjd-default \
  --config-format openjd --config-body-file openjd-template.yaml
```

-   [ ] Create a DeadlineCloud-type pipeline referencing mock farm/queue ids (callback is mandatory for
        DeadlineCloud):

```bash
vamscli pipeline create -d GLOBAL -n "Mock Deadline" -p mock-deadline \
  --execution-config '{"executionType":"DeadlineCloud","waitForCallback":"Enabled","taskTimeout":"3600","deadlineCloud":{"farmId":"farm-mock000000000000000000000000","queueId":"queue-mock00000000000000000000000","priority":50,"templateType":"YAML"}}'
```

-   [ ] Confirm creation is **accepted** (not rejected) because the type is enabled — a create while the
        type is disabled must be rejected with "DeadlineCloud execution type is not enabled".
-   [ ] Confirm the template stores and retrieves correctly (`vamscli pipeline template get`), OpenJD
        body intact.
-   [ ] Create a workflow referencing the DeadlineCloud pipeline, then confirm the generated SFN
        definition contains a `aws-sdk:deadline:createJob.waitForTaskToken` task state with the reserved
        OpenJD parameter injection (`VamsTaskToken`, `VamsPipelineExecutionId`, `VamsWorkflowExecutionId`)
        and a `ClientToken` — via `describe-state-machine`.
-   [ ] Do **NOT** execute the DeadlineCloud workflow. Real execution against a live Deadline farm is
        deferred to the [AWS Deadline Cloud Pipeline Test Plan](./deadline-cloud-test-plan.md).

---

## Phase 2 (later) — enable use-case pipelines

After the mock round passes, enable the use-case pipelines in config and redeploy.

-   [ ] Enable one or more `app.pipelines.use*` flags in `infra/config/config.json` (with
        `autoRegisterWithVAMS: true`), redeploy, and confirm each registers into the V2 tables via the
        `VamsSchemaRegistration` custom resource (a `PipelineStorageTableV2` + `WorkflowStorageTableV2`
        row appears with the built-in's id).
-   [ ] Execute each enabled built-in workflow against a real input file and verify the four layers (SFN
        definition, container/lambda logs, S3 outputs, DynamoDB records) as in Phase 1.
-   [ ] Change a use-case pipeline (toggle `autoRegisterAutoTriggerOnFileUpload`, or edit a bundled
        template), redeploy, and confirm the re-register unarchives/overwrites and redeploys the SFN, and
        a fresh execute still works.

---

## Phase 3 (later) — upgrade + data migration

Validate the `v2.5_to_v2.6` migration path.

-   [ ] Stand up a separate `release/2.6.0` (pre-overhaul) deployment; create V1 pipelines, workflows,
        and run executions on it.
-   [ ] Deploy the overhaul stack over it (or into a mirrored account) and run the `v2.5_to_v2.6`
        migration (`--steps pipelineWorkflowDefinitions` and `--steps workflowExecutions`, plus the
        others as applicable).
-   [ ] Confirm migrated user-database pipelines/workflows/executions appear correctly in the V2 tables
        (`migratedRecord: true`), that a `GLOBAL` built-in is **not** clobbered (recreated by the CDK
        importer), and that a migrated workflow re-executes successfully.

---

## Persistence: keep the seed data

:::warning
Keep **all** mock lambdas, SQS queues, EventBridge buses/rules, pipelines, templates, tag schemas,
workflows, triggers, and their executions after this round. They are the seed data for the later web
front-end testing phase — do not archive or permanent-delete them (except where a test step
explicitly exercises `delete`/`permanent-delete` on a throwaway record created for that purpose).
:::
