---
sidebar_label: Migrating pipelines (v2.5 → v2.6)
title: Migrating custom pipelines from v2.5 to v2.6
---

# Migrating custom pipelines from v2.5 to v2.6

The v2.6 pipeline and workflow overhaul changes how a pipeline is defined, how it receives its work,
and how it reports progress back. Built-in pipelines and stored pipeline/workflow **definitions** are
migrated for you by the [v2.5 to v2.6 data migration](../deployment/update-the-solution.md#v25-to-v26).
This page covers what that migration cannot do for you: updating the **code** of a custom pipeline you
wrote against v2.5 so it runs correctly under the new model.

Read it alongside [Building custom pipelines](custom-pipelines.md), which is the reference for the
current contract. This page is the delta and the porting order.

:::info[Who needs this]
Only authors of custom pipelines — a Lambda, queue consumer, event consumer, or container that VAMS
invokes. If every pipeline in your deployment is a VAMS built-in, the data migration is sufficient and
nothing here applies.
:::

## What the data migration already did

Before changing any code, know what is already true after the migration runs:

| Migrated for you    | Result                                                                                                                                                                                                                                              |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pipeline definition | The loose v2.5 fields become the typed v2.6 shape: `pipelineExecutionType` + `waitForCallback` + `taskTimeout` + the `userProvidedResource` JSON become `executionConfig`, with your function name / queue URL / bus ARN in the matching sub-block. |
| Accepted file types | The old `assetType` becomes `systemConfig.inputFileFilters.allow` (`.all` becomes allow-all).                                                                                                                                                       |
| Default parameters  | The old per-pipeline `inputParameters` JSON is preserved as a single template named `migrated-default`, so the pipeline keeps its defaults as a selectable template.                                                                                |
| Workflow definition | The old `specifiedPipelines.functions` list becomes the v2.6 `specifiedPipelines` reference list. A reference to a consolidated `GLOBAL` built-in is rewritten to its new id; your own pipelines are never remapped.                                |

Your pipeline therefore still appears, is still referenced by its workflow, and still points at your
resource. What changes is the **payload it receives** and **what it is expected to report**.

## Step 1: Read inputs from the manifest, not from the payload

This is the single largest change, and the one that breaks a v2.5 pipeline silently rather than loudly.

In v2.5 the payload carried the input and output paths directly. In v2.6 an execution can span many
files across several assets, so the payload carries a **pointer to a manifest** instead of a single
file path. The paths a v2.5 pipeline read are simply absent.

| v2.5 payload                            | v2.6 equivalent                                                                                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A single input file path on the payload | `inputManifestS3Location` — a manifest listing every selected input file with its bucket, key, and the concrete S3 `versionId` the run reads.                |
| Output paths on the payload             | Resolved from the manifest as `outputS3AssetFilesPath`, `outputS3AssetPreviewPath`, `outputS3AssetMetadataPath`, and `inputOutputS3AssetAuxiliaryFilesPath`. |
| Parameters inline on the payload        | `inputConfigurationS3Location` — the rendered configuration body for this step, produced from the chosen template.                                           |
| (nothing)                               | `inputMetadataS3Location` — the asset/file metadata the workflow was configured to pass through.                                                             |

Use the `manifestHelper` module that ships in every pipeline's `lambda/` directory rather than parsing
the manifest yourself; it resolves all of the above in one call:

```python
resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
```

:::warning[Pass every output path through]
Resolve the paths once in your `vamsExecute` handler and forward **all** of them to whatever does the
work. Never hardcode an empty string for one you do not use: the workflow's process-output step looks
for results at those locations, so an empty path silently produces an execution that succeeds with no
outputs. See [Amazon S3 output path conventions](custom-pipelines.md#amazon-s3-output-path-conventions).
:::

Two further consequences worth checking in your code:

-   **`assetId` is resolved from the manifest, not read off the payload.** The same
    `manifestHelper.resolve_pipeline_inputs()` call returns it as `resolved["assetId"]`, falling back to
    the manifest's `outputTarget` block when the pipeline's `inputFileArity` is `none`. The v2.6 task body
    does not carry the field at all, and a v2.5 pipeline that reverse-engineered the asset ID from S3
    path segments is wrong whenever the asset uses a custom base prefix. Thread the resolved value from
    your `vamsExecute` handler through the rest of your chain. See
    [Threading assetId through the pipeline](custom-pipelines.md#threading-assetid-through-the-pipeline).
-   **Your pipeline may receive more than one file.** If it was written assuming exactly one input, either
    handle the list or declare `systemConfig.inputFileArity: "one"` so VAMS rejects a multi-file selection
    up front instead of handing you a list you ignore.

### Per-execution-type differences

The manifest contract above is the same for every execution type. What differs is only how the payload
reaches you:

| Execution type    | How the payload arrives                                                  | Change from v2.5                                                                                                   |
| ----------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| **Lambda**        | The invocation event. Read `event["body"]` (a JSON string or an object). | Same delivery; the body's contents changed as described above.                                                     |
| **SQS**           | The message body of each record in `event["Records"]`.                   | Same delivery; the body's contents changed. Asynchronous only, so a task token is required to report completion.   |
| **EventBridge**   | The event `detail`.                                                      | Same delivery; the detail's contents changed. Asynchronous only, so a task token is required to report completion. |
| **DeadlineCloud** | Job parameters on the submitted job.                                     | **New in v2.6** — no v2.5 equivalent to migrate. Asynchronous, and the callback is always required.                |

See [Per-type envelopes](custom-pipelines.md#per-type-envelopes) for the exact shape of each.

## Step 2: Return the task token

If your pipeline is asynchronous — every `SQS`, `EventBridge`, and `DeadlineCloud` pipeline, and any
`Lambda` pipeline with `waitForCallback` enabled — it must report its own completion. The workflow waits
on an AWS Step Functions task token that arrives with the payload.

```python
# On success
sfn.send_task_success(taskToken=task_token, output=json.dumps({"status": "complete"}))

# On failure — ALWAYS send this
sfn.send_task_failure(taskToken=task_token, error="PipelineFailure", cause="See pipeline logs.")
```

:::danger[Always report failure]
A pipeline that returns nothing does not fail the workflow — it hangs until the task timeout, which may
be hours. Send `SendTaskFailure` on every error path, including the ones you consider impossible.
:::

Set `taskTimeout` to something your work can actually finish within, and use `taskHeartbeatTimeout` for
long-running work so a dead job is detected in minutes rather than at the timeout. See
[Callbacks](custom-pipelines.md#callbacks).

## Step 3: Register your sub-processes and logs

New in v2.6, and easy to overlook because a pipeline runs correctly without it — until someone tries to
abort a run or read its logs.

VAMS can only stop, and only read logs from, resources it knows about. A pipeline reports its own by
publishing a **registration event** to the orchestration bus. Registration is best-effort by design: a
failure to register never fails the pipeline.

```python
events_client.put_events(Entries=[{
    "EventBusName": ORCHESTRATION_BUS_NAME,          # injected by the CDK
    "Source": orchestration_event_prefix,            # arrives on the payload
    "DetailType": "pipeline.execution.register",
    "Detail": json.dumps({
        "pipelineExecutionId": pipeline_execution_id,
        "subExecution": {"resourceType": "stepFunctionsExecution",
                         "stateMachineArn": state_machine_arn,
                         "executionArn": sub_execution_arn},
        "logs": [{"logGroupArn": log_group_arn, "logGroupName": log_group_name}],
    }),
}])
```

### What to register

| Register this                          | So that                                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| A nested Step Functions execution      | Aborting the VAMS execution stops your sub-workflow, and its history appears in the execution's logs.   |
| A log group your process writes to     | Its events appear under the step's logs, without the operator needing to know where your pipeline logs. |
| A long-running compute job (see below) | Aborting the VAMS execution terminates the job instead of leaving it running — and billing.             |

The `resourceType` field is what makes this extensible: the registration path validates and stores
whichever locator keys you report (`executionArn`, `jobId`, `jobArn`, `taskArn`, `clusterArn`, `farmId`,
`queueId`, or a generic `arn`), so a resource type VAMS cannot yet stop is still recorded and reported
as "left running" on abort rather than being silently forgotten.

:::tip[AWS Batch: check which integration you use]
If your nested state machine submits its Batch job through the Step Functions `.sync` Batch integration
(`IntegrationPattern.RUN_JOB`), Step Functions owns the job's lifecycle — registering the sub-execution
is enough, because stopping it stops the job.

If instead you submit the job yourself (for example from a Lambda under `WAIT_FOR_TASK_TOKEN`), nothing
stops the job when the state machine stops. Register the job explicitly so an abort can terminate it:

```python
"subExecution": {"resourceType": "batchJob", "jobId": response["jobId"]}
```

This distinction is the difference between an abort that stops a GPU job and one that leaves it running
for hours.
:::

## Step 4: Move your definition into a vamsSchema bundle

Your migrated pipeline exists as a stored record. That is enough to run, but the record is the only copy —
a redeployment of your solution does not recreate or update it, and nothing in version control describes it.

Moving the definition into a `vamsSchema/` bundle makes registration **idempotent and versioned**: the
bundle is plain JSON you commit, and re-registering on every deployment overwrites the stored record and
clears its archived flag, so a redeploy neither duplicates the pipeline nor leaves a previously archived
one hidden.

```
vamsSchema/
    pipeline.json                  # required
    workflow.json                  # one runnable workflow for the pipeline
    templates/{templateId}.json    # optional — one file per configuration template
```

Ship the workflow file: a pipeline is launchable only through a workflow, and the
`VamsSchemaRegistration` construct (option 3 below) fails synth when it is absent.

The bundle carries **no account identifiers or ARNs**: the execution target is injected at registration
time, so the same file works in any account, Region, and partition. Full field reference:
[Registration with the vamsSchema bundle](custom-pipelines.md#registration-with-the-vamsschema-bundle).

Two migration notes specific to porting a v2.5 pipeline:

-   **Your `migrated-default` template is the starting point.** Export it and commit it as a
    `templates/*.json` entry rather than re-authoring your parameters from scratch:

    ```bash
    vamscli pipeline template get -d GLOBAL -p my-pipeline -t migrated-default --json-output
    ```

-   **Declare only what differs from the defaults.** Registration fills every `systemConfig` field you
    omit with its documented default and fills nested maps key by key, so you do not need to restate the
    whole block — and a `systemConfig` field added in a later release will not change the meaning of a
    bundle written today.

### Registering from your own stack

If your pipeline is deployed by a separate CDK stack, you have three options, in increasing order of
coupling to the VAMS deployment:

1.  **Call the APIs.** Create the pipeline, templates, and workflow through the standard endpoints with
    your own resource identifiers. No VAMS deployment artifacts needed. See
    [External pipelines](custom-pipelines.md#external-pipelines-registering-outside-the-vams-cdk-solution).

2.  **Invoke the import function with a bundle.** Keeps your definition as versioned JSON and registers it
    idempotently on each of your own deployments. Requires permission to invoke the import function in the
    target VAMS deployment, and its function name.

3.  **Use the `VamsSchemaRegistration` construct.** If your stack is CDK and can import from the VAMS
    repository, this is the same construct the built-ins use — it uploads your bundle to the artefacts
    bucket and drives the import as a custom resource. It needs the import function name and the artefacts
    bucket from the VAMS deployment (both available as stack outputs / SSM parameters), the absolute path
    to your `vamsSchema/` directory, and your deploy-time resource values as the resource overrides:

    ```typescript
    new VamsSchemaRegistration(this, "MyPipelineRegistration", {
        importFunctionName: vamsImportFunctionName,
        artefactsBucket: vamsArtefactsBucket,
        vamsSchemaDir: path.join(__dirname, "../vamsSchema"),
        resourceOverrides: { lambdaName: myFunction.functionName },
    });
    ```

    `resourceOverrides` is a **flat** map keyed by the override name for your execution type —
    `lambdaName`, `sqsQueueUrl`, `eventBridgeBusArn` / `eventBridgeSource` / `eventBridgeDetailType`, or
    `deadlineFarmId` / `deadlineQueueId` / `deadlineStorageProfileId`. The importer reads only those flat
    keys, so a nested object is ignored and the pipeline registers with an empty resource identifier —
    a deployment that reports success and a pipeline whose every execution then fails at the invoke
    state.

    Options 2 and 3 use the same importer, so the bundle is identical either way — option 3 only saves you
    from wiring the upload and the custom resource yourself.

:::warning[Verify the registration landed]
A malformed bundle can fail to import while the deployment still reports success. Always confirm after
deploying:

```bash
vamscli pipeline get -d GLOBAL -p my-pipeline --json-output
vamscli pipeline template list -d GLOBAL -p my-pipeline
```

:::

## Porting checklist

-   [ ] Inputs resolved from `inputManifestS3Location` via `manifestHelper`, not read off the payload
-   [ ] All four output paths forwarded, none hardcoded empty
-   [ ] `assetId` taken from `resolved["assetId"]`, never off the payload or derived from S3 path segments
-   [ ] Multi-file input either handled or excluded by declaring `inputFileArity: "one"`
-   [ ] `SendTaskSuccess` on completion and `SendTaskFailure` on **every** error path
-   [ ] `taskTimeout` realistic; `taskHeartbeatTimeout` set for long-running work
-   [ ] Nested state machines, log groups, and self-submitted compute jobs registered
-   [ ] For self-submitted AWS Batch jobs: registered as `resourceType: "batchJob"` so abort terminates them
-   [ ] Definition committed as a `vamsSchema/` bundle and re-registered on each deployment
-   [ ] Registration verified with the CLI after deploying
-   [ ] `inputFileFilters.allow` matches what your pipeline actually accepts

## Related pages

-   [Building custom pipelines](custom-pipelines.md) — the full current reference
-   [Update the solution](../deployment/update-the-solution.md#v25-to-v26) — the data migration steps
-   [Pipelines and workflows](../concepts/pipelines-and-workflows.md) — the concepts behind the new model
