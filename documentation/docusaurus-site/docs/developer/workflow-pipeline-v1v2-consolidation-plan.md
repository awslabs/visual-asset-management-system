---
title: Workflow / Pipeline / Execution V1→V2 Consolidation
description: Consolidation of the pre-overhaul (V1) pipeline/workflow/execution handlers, models, routes, and CDK wiring into the single V2 code path.
---

# Workflow / Pipeline / Execution V1→V2 Consolidation

This document records the consolidation that removes the pre-overhaul ("V1") pipeline, workflow, and
execution code paths in favor of the single V2 path introduced by the Phase-2 overhaul
(see `workflow-pipeline-overhaul-phase2-plan.md`). The overhaul deliberately shipped V2 handlers,
models, and DynamoDB tables **alongside** V1 and deferred the removal of V1 to a later cutover. This
consolidation performs that cutover: the V2 handlers become the only pipeline/workflow/execution API,
and the redundant V1 handlers, models, routes, CDK builders, and the V1 SQS auto-execute path are
removed.

The API contract for pipelines, workflows, and executions is **not** held backward-compatible with the
pre-overhaul shape — the V2 shape is authoritative.

## Why V1 and V2 co-existed

The overhaul introduced, per domain, a V2 handler + model + table set and wired only the **new,
non-colliding** API verbs to V2 while leaving the pre-existing verbs on V1:

-   `POST` / `PUT` create+update on the database-scoped collection routes were wired to
    `pipelineServiceV2` / `workflowServiceV2`.
-   `GET` / list / `DELETE` on the same paths continued to be served by the V1 `pipelineService` /
    `workflowService`, even though the V2 handlers already implement list/get/archive.
-   File-upload auto-execute ran on **two** parallel paths: the V1 SQS path
    (`sqsBucketSync` → `WorkflowAutoExecuteQueue` → `sqsAutoExecuteWorkflow` → `executeWorkflow`) and the
    V2 EventBridge path (`sqsBucketSync` → orchestration bus → `workflowTriggerDispatch` →
    `executeWorkflowV2`).

The V2 handlers are complete; the CDK simply had not been repointed. This consolidation finishes that.

## Decisions

These decisions govern the consolidation:

1. **Retain V1 tables + the v2.5→v2.6 data migration.** Only the V1 _code / APIs_ are removed. The V1
   DynamoDB tables and the `v2.5_to_v2.6` upgrade migration are kept so existing deployments' pre-overhaul
   pipelines / workflows / executions still upgrade into the V2 tables. The migration is the supported
   data upgrade path.
2. **`DELETE` is soft-archive only.** The consolidated V2 `DELETE` sets `archived = true` (and
   `enabled = false`), preserving execution history and allowing re-register / unarchive. The Step
   Functions state machine is left in place (re-deployed on the next update). This matches the V2
   idempotent-reregister design; the V1 behavior of tearing down the state machine / auto-created Lambda
   on delete is intentionally dropped.
3. **Database-scoped ID uniqueness.** V2 pipelines and workflows are database-scoped (`PK databaseId`,
   `SK id`). The same id may legitimately exist in two different databases. The V1 global
   cross-database uniqueness scan (`find_conflicting_database`) is dropped — it does not fit the V2
   data model, and templates / `defaultTemplateIds` are keyed by `databaseId:id`.
4. **Salvage auto-Lambda creation into V2 create.** `pipelineServiceV2.create_pipeline` provisions a new
   Lambda function for a Lambda-type pipeline when the request does not reference an existing execution
   resource (mirroring the V1 `createPipeline` auto-Lambda behavior). Built-in pipelines continue to
   inject their Lambda name at import time via `resourceOverrides`, so they never auto-create. See the
   **Future backend + website work** note below.

## CDK: single-stack consolidation (apiBuilder2)

All pipeline / workflow / execution Lambda **builds and route attachments** live in
`apiBuilder2-nestedStack.ts`. Before this change, `apiBuilder-nestedStack.ts` built and shared several of
these functions cross-stack (execution service, and the now-removed V1 services). After the change,
`apiBuilder` no longer builds or shares any pipeline/workflow/execution function — `apiBuilder2` owns the
whole domain, reducing cross-nested-stack function sharing.

Removed CDK builders (with the V1 handlers they built): `buildPipelineService`,
`buildCreatePipelineFunction`, `buildEnablePipelineFunction`, `buildWorkflowService`,
`buildCreateWorkflowFunction`, `buildExecuteWorkflowFunction`, `buildSqsAutoExecuteWorkflowFunction`
(and its SQS event source + `WorkflowAutoExecuteQueue` consume grant). `buildExecutionServiceFunction`
is **moved** (not removed) into `apiBuilder2`.

The GET / list / DELETE pipeline + workflow routes are repointed to `pipelineServiceV2` /
`workflowServiceV2`, which already implement those operations.

## V1 SQS auto-execute removal

The V1 file-upload auto-execute path is removed: the `publish_to_workflow_execution_sqs` producer call
in `sqsBucketSync`, the `WORKFLOW_AUTO_EXECUTE_SQS_URL` env wiring, the `sqsAutoExecuteWorkflow` handler,
and the `executeWorkflow` (V1 asset-scoped execute) handler that was its only target. File-upload
auto-execute is served solely by the V2 EventBridge trigger-dispatch path
(`workflowTriggerDispatch` → `executeWorkflowV2`).

## Triggers for built-in pipelines (unchanged, documented here)

A built-in pipeline's file-upload auto-trigger is controlled by **two** orthogonal config flags per
pipeline:

-   `autoRegisterWithVAMS` — whether the pipeline + its workflow + templates are registered into the V2
    tables at CDK deploy at all (gates whether `VamsSchemaRegistration` is instantiated).
-   `autoRegisterAutoTriggerOnFileUpload` — whether the registered workflow's file-upload trigger is
    **enabled** (auto-fires on upload).

`autoRegisterAutoTriggerOnFileUpload` is threaded as `triggerEnabled` into the `VamsSchemaRegistration`
construct → the import custom-resource property → `vamsSchemaImport._trigger_body`, which **overrides**
the bundled trigger's `enabled` flag. The override is opt-in (omitted = leave the schema value intact)
and toggles the `enabled` boolean; the trigger definition (`inputFileFilters`, `defaultTemplateIds`) is
always registered from the bundle. `triggerEnabled` participates in the registration content hash, so a
toggle re-runs the import on redeploy.

A pipeline with `autoRegisterWithVAMS = true` and `autoRegisterAutoTriggerOnFileUpload = false` is
registered and available for manual / on-demand runs, but does not auto-fire on upload.

## EventBridge VPC interface endpoint

In-VPC Lambdas reach the EventBridge orchestration bus (`storageResources.eventBridge.orchestrationBus`)
through the Amazon EventBridge (`events`) interface VPC endpoint. It is created by the VPC builder
whenever `useGlobalVpc.addVpcEndpoints` is true (a common endpoint, not gated by any pipeline/feature
flag or partition). This is documented in `architecture/networking.md` (Common Interface Endpoints).

## Future backend + website work (auto-Lambda note)

When a pipeline is created through the API as a Lambda-type pipeline without referencing an existing
Lambda, VAMS **provisions a new Lambda function** for it (seeded from the sample pipeline package). The
upcoming pipelines/workflows/executions website overhaul must surface this to the user at create time —
the create UI should state that a new Lambda will be created for the pipeline, and the documentation
should describe the provisioned function (naming, role, VPC placement).

This is groundwork for a planned future backend upgrade that will let the API additionally **specify the
Lambda code** (and potentially other pipeline components) to deploy as part of pipeline creation, rather
than provisioning only from the sample package.

## Future phase: API-driven pipeline component deployment (post-web)

Scheduled **after** the web overhaul and its testing. Today the current stage only supports the
dummy-Lambda-if-no-external-Lambda behavior above (as pre-overhaul); everything below is future scope.

The goal is to let a pipeline be fully designed and created through the VAMS API, including deploying
the components it runs on, with the correct least-privilege roles, provisioned by VAMS:

-   **User-supplied Lambda code** on pipeline create/update (a zip/image reference or inline package),
    deployed as the pipeline's function instead of the sample package. Direct Lambda, SQS, and
    EventBridge execution types would all be creatable end-to-end via the API.
-   **Other components over time** — SQS queues, EventBridge rules/targets, and potentially
    Batch/ECS/Fargate compute with container image code — deployed and wired with scoped roles.

This requires **new authorization permission fields** (a distinct capability from creating a pipeline
record): only roles granted the new "deploy pipeline components / supply code" permission may submit
code or request component deployment. Design constraints to work through before building:

-   **Security / exploit-limiting.** Running user-supplied code is a privilege-escalation and
    data-exfiltration surface. Scope every provisioned role to the minimum (the pipeline's own
    buckets/queues, no broad `iam:PassRole`, no wildcard resource access); consider a code-review or
    approval gate, per-deployment resource tagging + quotas, network isolation (VPC/no-egress options),
    and blocking or sandboxing components that can reach VAMS control-plane resources.
-   **Permission model.** New constraint fields gate who can (a) supply Lambda code, (b) request other
    component types, (c) target specific databases. These are additive to the existing pipeline
    create/update permissions — a normal pipeline author cannot deploy code without them.
-   **Component lifecycle.** How VAMS-provisioned components are updated on pipeline update, cleaned up
    on archive/delete, and reconciled if a deployment partially fails.

Until this phase lands, keep the current behavior: a Lambda-type pipeline created without an external
`lambda.resourceId` gets a dummy Lambda that a developer builds out in the backend.
