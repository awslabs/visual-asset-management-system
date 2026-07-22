---
title: AWS Deadline Cloud Pipeline Test Plan
description: The AWS account pre-setup and end-to-end test steps for running a real VAMS DeadlineCloud-type pipeline — farm, queue, fleet, IAM roles, the OpenJD job template, the job-status EventBridge rule, and the createJob.waitForTaskToken callback. Real execution is deferred from the initial smoke round.
---

# AWS Deadline Cloud Pipeline Test Plan

This plan covers standing up AWS Deadline Cloud and running a **real** VAMS DeadlineCloud-type pipeline
end to end. It follows the mock-only DeadlineCloud validation in the
[Workflow / Pipeline / Execution Smoke Test Plan](./workflow-pipeline-smoke-test-plan.md) (which
validates creation + Step Functions generation with mock ids, no real job). Here the mock farm/queue
ids are replaced with a real farm/queue/fleet and a job is actually submitted and completed.

:::danger[Real DeadlineCloud execution is DEFERRED]
Real DeadlineCloud execution is **not** part of the initial smoke round. The smoke round validates the
DeadlineCloud path backend-only (template stores/retrieves, pipeline registers, workflow builds a valid
`createJob.waitForTaskToken` state machine) with mock ids. The steps in this document run only after
the AWS Deadline Cloud [pre-setup](#required-aws-account-pre-setup) is complete.
:::

:::note[This is a plan, not a run log]
This document describes steps to execute later; it performs no deployment, AWS calls, or tests.
Commands use the real `vamscli` command names and options and the real config field names.
:::

## What AWS Deadline Cloud is in the VAMS pipeline context

AWS Deadline Cloud is a managed render/compute farm service. In VAMS it is a pipeline **execution
type** (`executionConfig.executionType = "DeadlineCloud"`), alongside `Lambda`, `SQS`, and
`EventBridge`. It is gated by `app.pipelines.deadlineCloudExecutionTypeEnabled` in config — when that is
`true`, VAMS deploys the `deadlineCloudJobCallback` lambda and (in-VPC) a `deadline.management`
interface VPC endpoint, and the workflow ASL builder is allowed to emit DeadlineCloud task states.

Key properties of the VAMS DeadlineCloud integration:

-   **Async-only, callback mandatory.** The workflow ASL uses the Step Functions AWS SDK integration
    `aws-sdk:deadline:createJob.waitForTaskToken`. `createJob` returns as soon as the job is **queued**,
    not when it completes, so the task **always** waits on a Step Functions task token. A DeadlineCloud
    pipeline with `waitForCallback` other than `Enabled` is rejected at ASL generation time.
-   **OpenJD job template.** The pipeline's template `configBody` is an OpenJD job template
    (`configFormat: openjd`, `templateType` JSON or YAML). The `createJob` task passes the template text
    as `Template` and injects the VAMS body envelope as reserved, string-typed OpenJD job parameters
    (all `Vams`-prefixed). The registered job template must declare every injected parameter.
-   **Reserved OpenJD parameters.** The task state injects `VamsTaskToken`, `VamsPipelineExecutionId`,
    and `VamsWorkflowExecutionId` (plus a `Vams`-prefixed parameter per shared body field). The large
    `executingRequestContext` is intentionally excluded — a Deadline string job parameter is capped at
    1024 characters, so it stays only in the Step Functions state for the process-output step.
-   **The callback lambda (`deadlineCloudJobCallback`).** Deadline Cloud publishes job status events to
    the account's **default** EventBridge bus (source `aws.deadline`). VAMS creates standing rules on the
    default bus routing terminal "Job Run Status Change" and failure "Job Lifecycle Status Change" events
    to `deadlineCloudJobCallback`. For each event it calls `deadline:GetJob`, reads the reserved
    `VamsTaskToken` job parameter (a job without it is not a VAMS job and is ignored), best-effort
    registers the Deadline job as the pipeline execution's sub-process on the orchestration bus, then
    resolves the token: task-run `SUCCEEDED` → `SendTaskSuccess`; task-run `FAILED`/`CANCELED`/
    `NOT_COMPATIBLE` or lifecycle `CREATE_FAILED`/`UPLOAD_FAILED` → `SendTaskFailure`. Duplicate/late
    events (`TaskDoesNotExist`/`TaskTimedOut`/`InvalidToken`) are swallowed.
-   **Idempotent createJob.** The task passes a `ClientToken` derived from the execution name + the state
    name so a retried `createJob` cannot submit a duplicate job. Retries are scoped to transient Deadline
    API errors only.

## Required AWS account pre-setup

These resources must exist before a real DeadlineCloud execution can run. The **Automatable** column
marks whether the main agent can create it (CLI/CDK/boto3) or the **user** must do it manually
(console/organization action). Deadline Cloud has an AWS CLI (`aws deadline ...`) and a boto3 client,
so most farm objects are scriptable; account-level and IAM/organization actions are the manual ones.

| #   | Resource                                       | Automatable?                                          | Notes                                                                                                          |
| --- | ---------------------------------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 1   | Deadline Cloud farm                            | Agent (`aws deadline create-farm`)                    | Needs a KMS key (Deadline can create/manage one, or supply the VAMS CMK). Farm is the top-level container.     |
| 2   | Queue                                          | Agent (`aws deadline create-queue`)                   | Bind to the farm; set its job-attachments S3 bucket + root prefix.                                             |
| 3   | Fleet                                          | Agent (`aws deadline create-fleet`)                   | Service-managed vs customer-managed — see [Fleet choice](#fleet-choice-service-managed-vs-customer-managed).   |
| 4   | Queue-to-fleet association                     | Agent (`aws deadline create-queue-fleet-association`) | Without this, queued jobs never dispatch to workers.                                                           |
| 5   | Budget / limits                                | Agent (`aws deadline create-budget`)                  | A budget/limit protects against runaway cost during testing; set a low cap.                                    |
| 6   | Queue IAM role (jobs' identity)                | User (IAM)                                            | The role Deadline assumes to run the queue's jobs; needs S3 job-attachments + any VAMS S3/CloudWatch access.   |
| 7   | Worker instance profile / role (CMF only)      | User (IAM)                                            | Customer-managed fleet workers assume this; the `deadline-worker` managed policy + CloudWatch/S3.              |
| 8   | Fleet service role (SMF)                       | User (IAM)                                            | Service-managed fleets need the Deadline service role to launch/manage workers.                                |
| 9   | OpenJD job template                            | Agent (via VAMS)                                      | Authored as the pipeline template `configBody` (`configFormat: openjd`); no separate registration step.        |
| 10  | EventBridge rule (default bus, `aws.deadline`) | Agent (VAMS/CDK)                                      | Created by VAMS when the type is enabled — see [confirm the rules](#confirm-the-eventbridge-job-status-rules). |
| 11  | Deadline monitor / identity center (optional)  | User                                                  | Only needed to browse jobs in the Deadline monitor UI; not required for the API-driven test.                   |
| 12  | Network / worker AMI (CMF only)                | User + Agent                                          | See [network + worker AMI](#network--worker-ami-considerations).                                               |

### Fleet choice: service-managed vs customer-managed

-   **Service-managed fleet (SMF)** — Deadline Cloud provisions and manages the worker EC2 instances for
    you (auto-scaling, patching, teardown). Simplest to stand up; uses the Deadline service role. **Use
    SMF for this testing** — it minimizes the IAM/network surface and removes the need to build and
    maintain a worker AMI.
-   **Customer-managed fleet (CMF)** — you run the worker hosts (EC2 or on-prem) with the Deadline worker
    agent installed, under your own instance profile and networking. More control (custom AMIs, GPU
    instance families, VPC placement) at the cost of building the worker instance profile, worker AMI,
    and network path yourself. Defer CMF unless a test specifically needs a custom worker environment.

### Network + worker AMI considerations

-   **SMF:** no VPC/AMI work is required for the fleet itself; Deadline manages worker networking. The
    only VAMS-side network requirement is the callback lambda's `deadline:GetJob` reachability (see the
    VPC endpoint note below).
-   **CMF:** the worker hosts need a network path to the Deadline Cloud endpoints and to any S3 buckets
    the job reads/writes (VAMS asset + default run buckets), plus a worker AMI with the Deadline worker
    agent and the job's runtime (e.g. the tools the OpenJD template invokes). Size the instance family to
    the workload.

### Partition and VPC-endpoint caveats

-   **DeadlineCloud is blocked in `aws-us-gov` and `aws-eusc`.** The execution type is only usable in
    commercial (`aws`) partitions. Do not enable it for GovCloud or EU Sovereign deployments.
-   **`deadline.management` VPC interface endpoint.** When `deadlineCloudExecutionTypeEnabled` is `true`
    **and** `app.useGlobalVpc.addVpcEndpoints` is `true`, the VPC builder creates a `deadline.management`
    interface endpoint so the in-VPC `deadlineCloudJobCallback` lambda can call `deadline:GetJob`. If the
    callback lambda runs in the VPC and this endpoint is absent (e.g. `addVpcEndpoints: false`), the
    operator must hand-create the equivalent endpoint or the token never resolves.

### Confirm the EventBridge job-status rules

VAMS creates the default-bus rules that drive the callback when the type is enabled. Confirm they
exist after the enabling deploy (the `deadlineCloudJobCallback` lambda wiring is the source of truth —
it expects "Job Run Status Change" filtered to terminal `taskRunStatus` values and "Job Lifecycle
Status Change" filtered to failure lifecycle states, both on the default bus, source `aws.deadline`,
targeting the callback lambda):

```bash
aws --profile aws-pan-spatial-computing+vams-app-Admin --region us-west-2 \
  events list-rules --event-bus-name default \
  --query "Rules[?contains(Name, 'eadline') || contains(Name, 'Deadline')]"
```

## Test steps once pre-setup is done

Replace the mock ids from the smoke plan with the real farm/queue/fleet ids and run a real job.

-   [ ] **Point the pipeline at the real farm/queue.** Either update the mock DeadlineCloud pipeline from
        the smoke plan, or create a fresh one:

```bash
vamscli pipeline update -d GLOBAL -p mock-deadline \
  --execution-config '{"executionType":"DeadlineCloud","waitForCallback":"Enabled","taskTimeout":"3600","deadlineCloud":{"farmId":"farm-<real>","queueId":"queue-<real>","priority":50,"templateType":"YAML"}}'
```

-   [ ] **Confirm the OpenJD template** on the pipeline declares every injected `Vams`-prefixed parameter
        (`VamsTaskToken`, `VamsPipelineExecutionId`, `VamsWorkflowExecutionId`, and a parameter per shared
        body field) as STRING, and that its steps do real (small) work and write to the manifest output
        prefixes. Update it if needed:

```bash
vamscli pipeline template update -d GLOBAL -p mock-deadline -t openjd-default \
  --config-format openjd --config-body-file openjd-template.yaml
vamscli pipeline template get -d GLOBAL -p mock-deadline -t openjd-default
```

-   [ ] **Confirm the workflow's SFN definition** still emits the `aws-sdk:deadline:createJob.waitForTaskToken`
        task with the reserved parameter injection and `ClientToken` (describe-state-machine).
-   [ ] **Execute the workflow** against a real input file:

```bash
vamscli workflow execute --workflow-database-id GLOBAL -w mock-deadline-wf \
  --input-file smoke-db:asset1:/model.glb \
  --pipeline-parameters '{"mock-deadline":{"templateId":"openjd-default","templateTags":[]}}'
```

-   [ ] **Verify the createJob call.** In the Deadline monitor (or `aws deadline list-jobs
--farm-id <farm> --queue-id <queue>`), confirm a job was created for the execution with the
        injected `Vams*` parameters.
-   [ ] **Verify the worker ran the OpenJD job.** Confirm the job reaches a terminal task-run status;
        inspect the job/step/task logs in Deadline.
-   [ ] **Verify the callback resolved the task token.** Confirm the `deadlineCloudJobCallback` lambda
        logged a `SendTaskSuccess` (or `SendTaskFailure`) for the job, and the workflow's Step Functions
        execution advanced past the DeadlineCloud task (no task-timeout).
-   [ ] **Verify outputs landed.** Confirm the OpenJD job wrote to the manifest output prefixes on the
        default run bucket (preserving the input relative path), and that the workflow's process-output
        step ingested them to the output asset.
-   [ ] **Verify DynamoDB records.** `vamscli execution details <id>` and `vamscli execution logs <id>`
        show the DeadlineCloud pipeline execution and its registered Deadline sub-process (farmId/queueId/
        jobId). Spot-check the PipelineExecutions row, the input-configuration snapshot, and the
        output/log rows as in the smoke plan's per-execution verification.
-   [ ] **Failure path.** Submit a job that fails (or cancel it) and confirm the callback resolves the
        token via `SendTaskFailure`, the workflow surfaces the failure, and the execution records the
        error.

## Teardown

The farm/queue/fleet, budget, and IAM roles created for this test can be removed after validation.
Keep the DeadlineCloud pipeline/template/workflow definitions and their executions as seed data (per
the smoke plan's [persistence rule](./workflow-pipeline-smoke-test-plan.md#persistence-keep-the-seed-data));
they simply become non-runnable once the real farm ids are removed.
