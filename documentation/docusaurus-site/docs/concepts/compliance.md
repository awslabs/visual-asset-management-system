# Compliance

Compliance (also known as federated model management) adds schema-driven compliance enforcement to VAMS. Assets are evaluated against registered compliance schemas, non-compliant assets can be quarantined, and a change to a parent asset can propagate re-evaluation through the asset relationship graph.

Compliance is part of every VAMS deployment: five Amazon DynamoDB tables, nine AWS Lambda functions and the `/compliance/*` API routes are always created, and the compliance pages of the web interface appear to a user whose role grants them like every other page. Two configuration settings tune it:

```json
{
    "app": {
        "compliance": {
            "autoLoadDefaultSchema": true,
            "quarantineBlocksDownload": false
        }
    }
}
```

| Setting                                   | Default | Effect                                                                                                                                                                                                                               |
| ----------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app.compliance.autoLoadDefaultSchema`    | `true`  | Seeds the `GLOBAL` schema `default-compliance-schema` at deployment: one `warn`-level metadata rule that validates a bound asset against the `GLOBAL` `defaultAsset` metadata schema.                                                |
| `app.compliance.quarantineBlocksDownload` | `false` | When `true`, the asset download, stream, export (presigned file URLs) and auxiliary-preview stream endpoints refuse a quarantined asset. When `false`, quarantine is visible in the interface and the API but does not block access. |

See the [Configuration Reference](../deployment/configuration-reference.md) for the settings and [Compliance (User Guide)](../user-guide/compliance.md) for the interface walkthrough.

## Compliance schemas

A compliance schema is a named, versioned set of rules. Every registration or update writes the next version of the schema and evaluation always reads the highest version, so the version history is retained. A schema is scoped to a database or to `GLOBAL`; a database-scoped schema can only be bound within its own database, while a `GLOBAL` schema can be bound anywhere.

A schema whose `isSystem` flag is set is a system schema. Only the system user can register one, write a new version of it, or delete it. The default schema seeded at deployment is a system schema.

### Schema format

A schema body is a `vams-rules-v1` document: named rules of three types, each with an enforcement level, whose pipeline rules compare measurements against tolerances. Registration and update accept only a valid `vams-rules-v1` document — an object with `schemaFormat` set to `vams-rules-v1` and at least one rule under `rules`. Any other body (a plain JSON Schema, a template file's envelope, an object without rules) is rejected with `400` and the message `schemaBody must be a vams-rules-v1 document`; the specific validation failure is written to the handler's log rather than returned.

Every schema record the listing and the single-schema read return carries a top-level `schemaFormat`, derived from its body: `vams-rules-v1`, or `legacy` for a row whose body is not a `vams-rules-v1` document. A legacy schema cannot be bound (the bind request is refused with `400` and `Schema body must be a vams-rules-v1 document`), updated or evaluated; it is listed — marked **Legacy** on the Compliance Schemas page and printed as `Format: legacy` by the CLI — only so that it can be found and deleted.

### Schema templates

Four ready-to-register schemas ship in `documentation/complianceSchemaTemplates/`. Each file wraps a `vams-rules-v1` body in an envelope (`metadata`, `schemaName`, `description`, `schemaBody`); register the `schemaBody` under the `schemaName` — the envelope itself is not a schema body and is rejected as one.

| Template                     | Rules                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `3d-model-quality`           | A `warn` metadata rule against the `GLOBAL` `defaultAsset` metadata schema (required fields, declared types, plus `polygon_count`, `coordinate_system` and `units`), and a `quarantine` pipeline rule that converts the model through the built-in 3D conversion workflow: `pipelineRef` `{"databaseId": "GLOBAL", "workflowId": "conversion-3d-basic", "pipelineDatabaseId": "GLOBAL", "pipelineId": "conversion-3d-basic", "templateId": "convert-to-glb"}`, `inputFiles` `{"mode": "matching", "filter": ["*.stl", "*.obj", "*.ply", "*.gltf", "*.xyz"]}`, with checks on `execution_success` and `processing_duration_seconds`. The filter names source formats only and excludes `*.glb`: the pipeline writes a `.glb` back into the asset, and a filter that admitted it would select the pipeline's own output. |
| `data-classification`        | A `quarantine` metadata rule requiring `classification`, `handling_instructions` and `data_steward`, and an `inform` metadata rule recording `dissemination_controls` and `originating_agency`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `engineering-asset-standard` | A `warn` metadata rule requiring `owner`, `classification` and `retention_days`, an `inform` metadata rule recording `department` and `review_date`, and a `warn` relationship rule requiring at least one `parentChild` parent.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `retention-policy`           | A `warn` metadata rule requiring `retention_days`, `disposal_method` and `data_owner`, and an `inform` metadata rule recording `last_access_review` and `next_review_date`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

The pipeline template's `filter` names the source formats the conversion workflow accepts, and the workflow takes exactly one input file, so a model asset that holds exactly one such file converts; an asset holding several records the rule as an `error` result — a [tooling failure](#tooling-failures) that applies no enforcement — until the selection is narrowed (a tighter `filter`, or `explicit` keys).

### The vams-rules-v1 format

A `vams-rules-v1` body is a set of named rules. Each rule has a `ruleType`, an `enforcement` level and at least one check:

```json
{
    "schemaFormat": "vams-rules-v1",
    "extends": "parent-schema-name",
    "rules": {
        "rule-name": {
            "ruleType": "pipeline | metadata | relationship",
            "enforcement": "quarantine | warn | inform",
            "checks": []
        }
    }
}
```

| Rule type      | Purpose                                                                                                                                                                                  | Evaluation                                       |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `metadata`     | Validate the asset's metadata against a VAMS metadata schema — required fields present, values of the declared type, and any `additionalRequiredFields`.                                 | Within the evaluation request                    |
| `relationship` | Validate that the asset carries the required asset links — `direction` (`parents`, `children`, `related`), `relationshipType` (`parentChild`, `related`) and a `minCount` or `maxCount`. | Within the evaluation request                    |
| `pipeline`     | Run a VAMS workflow and compare the measurements one of its pipelines reports against tolerances.                                                                                        | Asynchronous, completed by the workflow callback |

:::info[Relationship type values]
`relationshipType` takes the value stored on the asset link: `parentChild` or `related`.
:::

| Enforcement  | Effect of a failed rule on the verdict                                    |
| ------------ | ------------------------------------------------------------------------- |
| `quarantine` | The asset is `quarantined`                                                |
| `warn`       | The asset is `non_compliant` unless a `quarantine`-level rule also failed |
| `inform`     | The failure is recorded in the rule results; the asset stays `compliant`  |

#### Schema inheritance

A schema names a parent in `extends` and inherits every rule of the parent. A rule of the same name in the child replaces the parent's definition; rules defined only in the parent apply unchanged. Inheritance resolves through up to ten levels of parents.

#### Pipeline rules

A pipeline rule names a workflow, the pipeline within that workflow whose output the checks read, and which of the asset's files the execution receives:

```json
{
    "ruleType": "pipeline",
    "enforcement": "quarantine",
    "pipelineRef": {
        "databaseId": "GLOBAL",
        "workflowId": "coord-validate-workflow",
        "pipelineDatabaseId": "GLOBAL",
        "pipelineId": "coord-validate",
        "templateId": "coord-validate-osgb36"
    },
    "inputFiles": { "mode": "matching", "filter": ["*.las", "*.laz"] },
    "inputParameters": { "expected_crs": "EPSG:27700" },
    "checks": [
        {
            "name": "residual_check",
            "outputField": "residual_error_mm",
            "tolerance": { "operator": "lte", "value": 1.0 }
        }
    ]
}
```

| Field                            | Description                                                                                                                            |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `pipelineRef.databaseId`         | Database of the workflow (`GLOBAL` accepted)                                                                                           |
| `pipelineRef.workflowId`         | The workflow to execute                                                                                                                |
| `pipelineRef.pipelineDatabaseId` | Database of the pipeline (`GLOBAL` accepted)                                                                                           |
| `pipelineRef.pipelineId`         | The pipeline within the workflow whose measurements the checks read; the execution request keys the template and tag values by this id |
| `pipelineRef.templateId`         | Optional. The pipeline template to run; when omitted the pipeline's default template is used                                           |
| `inputFiles`                     | Optional. Which of the asset's files the execution receives — see [Input files](#input-files); defaults to `{"mode": "matching"}`      |
| `inputParameters`                | Optional. Values handed to the pipeline as its template tag values                                                                     |
| `checks[].outputField`           | Key of the `measurements` object in the pipeline's output file, or one of the two derived measurements                                 |
| `checks[].tolerance`             | The comparison — see [Tolerance operators](#tolerance-operators)                                                                       |

The evaluation launches one workflow execution per pipeline rule through the standard execute-workflow request: the selected files are the inputs, `pipelineExecutionParameters` carries the template and tag values under the `pipelineId`, and the trigger type is `manual`. The execution runs as the system user and is listed with the workflow's other executions. The evaluation records the `executionId` of each execution and stays `pending_pipeline` (asset state `pending_evaluation`) until every execution has completed. A rule whose workflow or pipeline does not exist, whose input selection cannot be satisfied, or whose execution cannot be launched, records an `error` result at once — a [tooling failure](#tooling-failures), not a verdict. Each execution is launched with the evaluation's id as its `executionGroupId`, so the executions of one evaluation form one group and their audit entries and completion events name the evaluation.

#### Input files

A workflow declares how many input files it takes (`inputFileArity`: one, several or none), whether it accepts the whole asset, and which file names it accepts (`inputFileFilters`); each of its pipelines can narrow those through its own configuration and the chosen template. `inputFiles` selects the asset files the rule hands to that contract, in one of three modes:

| Mode         | Selection                                                                                                                                                                                                                     | Fields                                                     |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `matching`   | The default. The asset's current files are listed, the workflow's input-file filters are applied, then each pipeline's effective filters, then the rule's own `filter` globs as an allow list; the matches are sorted by key. | `filter` — optional glob list (up to 32, 1-256 characters) |
| `wholeAsset` | The asset root (`/`) is sent. Accepted only when the workflow permits whole-asset selection.                                                                                                                                  | —                                                          |
| `explicit`   | The listed asset-relative keys are sent. Every key must exist among the asset's files.                                                                                                                                        | `keys` — required (1-64 keys, each starting with `/`)      |

The workflow's arity is honoured after the selection is made: a single-input workflow needs the selection to resolve to exactly one file, a multi-input workflow receives every match and needs at least one, and a workflow that takes no input files receives none. In `matching` mode a file that a workflow execution wrote into the asset (object metadata `vams-changesource` of `workflowExecution`) is never a candidate: such a file is a pipeline's output, not a source, so a rule's own output cannot be selected as its input on the next evaluation. `explicit` keys are sent as given. A selection that cannot be satisfied — a whole-asset selection the workflow refuses, a `matching` or `explicit` selection that does not resolve to the number of files the workflow takes, or an `explicit` key the asset does not have — is recorded as that rule's result with `passed: false`, `status: "error"` and a message that names the condition but no file names or filters; it is a [tooling failure](#tooling-failures) and applies no enforcement. `filter` is accepted only with `matching`, and `keys` only — and always — with `explicit`; a schema violating either is rejected at registration.

:::tip[Matching one file of many]
The built-in conversion workflows take exactly one input file. When an asset holds several files the workflow accepts, narrow the rule's `filter` (for example `["*.stl"]`) or name the file with `explicit` keys so the selection resolves to one.
:::

#### Tooling failures

A rule result carries a `status`: `evaluated` for a rule that produced a pass or fail, `error` for a rule whose tooling failed before it could — an input selection that was not accepted by the workflow, that did not match exactly one file, or that named a file the asset does not have, or a workflow execution that could not be launched. An `error` result is not a verdict. Its `passed` is `false` and its message names the condition, but the rule's enforcement does not apply: a `quarantine`-level rule that errored neither quarantines the asset nor opens a cascade, so a mistake in a schema's input selection cannot quarantine every asset it is bound to.

The evaluation's outcome follows from the rules that were evaluated. When some rules errored, the verdict is determined from the remaining rules and the evaluation is `completed` with `hasRuleErrors: true` and the errored rule names in `errorRules`; its violations list the failed rules only. When every rule errored, or no rule remains, the evaluation itself is an `error` with no verdict and the asset's compliance state is left unchanged. Either way the asset's compliance record is updated with `lastEvaluationId`, `lastEvaluatedAt` and `lastEvaluationStatus` (`completed`, `pending_pipeline` or `error`), and an `evaluation_error` audit entry naming the errored rules (`ruleNames`) is written. An evaluation whose schema cannot be loaded, or whose body is not a `vams-rules-v1` document, is recorded the same way: an `error` evaluation, `lastEvaluationStatus: error`, the state unchanged, and an `evaluation_error` entry.

The compliance engine holds this mapping behind one constant, `TOOLING_FAILURES_APPLY_ENFORCEMENT` (default `false`). An operator who wants a tooling failure treated as a failed rule — enforcement applied, the asset quarantined at `quarantine` level — sets it to `true` in the engine module and redeploys; every other behaviour described on this page is unchanged by the switch.

#### Pipeline output contract

A pipeline that backs a compliance rule reports its measurements by writing one file, `compliance-output.json`, under the execution's standard results output prefix — the `outputs.results` location of the workflow manifest, alongside the `files`, `previews` and `metadata` prefixes. The workflow end-state records every file under that prefix as an output result of the execution, and the compliance callback reads the document from there. The pipeline needs no evaluation identifier, asset reference or compliance table access; a pipeline written in Python uses the `write_compliance_output` helper in `backendPipelines/common/compliance_output.py`.

```json
{
    "complianceOutput": true,
    "status": "success",
    "measurements": {
        "residual_error_mm": 0.45,
        "coverage_percent": 98.2
    },
    "errors": [],
    "pipelineMetadata": {}
}
```

| Field              | Type    | Description                                                            |
| ------------------ | ------- | ---------------------------------------------------------------------- |
| `complianceOutput` | boolean | `true`; identifies the document as a compliance output file            |
| `status`           | string  | `success` or `error`. An `error` status fails every check of the rule  |
| `measurements`     | object  | Numeric values keyed by the `outputField` names the rule's checks read |
| `errors`           | array   | Error messages when `status` is `error`                                |
| `pipelineMetadata` | object  | Optional free-form information about the run                           |

`compliance-output.json` is optional. A workflow that writes none can still back a pipeline rule: two measurements are derived for every execution, and when the file is absent they are the only measurements the rule's checks can read — a check on any other `outputField` fails because the field is missing.

| Measurement                   | Value                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `execution_success`           | `1.0` when the execution succeeded, `0.0` otherwise                            |
| `processing_duration_seconds` | Wall-clock duration of the execution, from its start and completion timestamps |

A rule can therefore require that an existing workflow succeeds and finishes within a time budget without any change to its pipelines; the shipped `3d-model-quality` template does exactly that against the built-in 3D conversion workflow.

#### Workflow completion

When a workflow execution reaches a terminal status — including an abort — the workflow end-state puts a `workflow.execution.completed` event on the orchestration bus. The compliance callback subscribes to that event, looks the `executionId` up in the evaluation table's `ExecutionIdIndex`, and completes the pipeline rule it belongs to: on a `SUCCEEDED` execution it reads the pipeline's `compliance-output.json` from the execution's recorded output results and runs the rule's checks against the measurements; any other terminal status (`FAILED`, `ABORTED`, `TIMED_OUT`) fails every check of the rule. Once every pipeline rule of the evaluation has reported, the verdict is determined, the asset state is written and the audit entry is recorded. The callback processes only events whose `detail-type` is `workflow.execution.completed` and whose detail matches the completion contract; a rule's completion is recorded at most once, so a redelivered event is a no-op, and the evaluation is finalized exactly once when every pipeline rule has reported, however the completion events are ordered. The event contract is documented in the [workflow execution data model](../developer/workflow-execution-data-model-handoff.md#workflow-completion-event).

#### Tolerance operators

| Operator  | Passes when                           | Fields                                      |
| --------- | ------------------------------------- | ------------------------------------------- |
| `lte`     | measurement ≤ `value`                 | `value`                                     |
| `gte`     | measurement ≥ `value`                 | `value`                                     |
| `eq`      | \|measurement − `value`\| ≤ `epsilon` | `value`, optional `epsilon` (default 0.001) |
| `between` | `min` ≤ measurement ≤ `max`           | `min`, `max`                                |

A check whose `outputField` is missing from the measurements fails.

## Schema binding

A schema is bound at one of two levels. A database binding governs every asset in the database; an asset binding overrides it for one asset.

| Binding  | Scope                                           | `schemaSource` | Precedence |
| -------- | ----------------------------------------------- | -------------- | ---------- |
| Database | Every asset in the database without an override | `database`     | Lower      |
| Asset    | The one asset                                   | `asset`        | Higher     |

Binding a schema to a database marks every asset without an override `pending_evaluation` under it; a sweep or an individual evaluation then produces verdicts. A database binding also carries `complianceAutoEval` (default on), which evaluates an asset of the database automatically when it is created or updated. Binding a schema to an asset marks that asset `pending_evaluation` under the new schema. Removing an asset override returns the asset to the database binding as `pending_evaluation`, or deletes its compliance record when the database has none. Removing a database binding deletes the compliance records of the assets that inherited it and leaves asset overrides in place.

A schema that is bound to any database or asset cannot be deleted; remove its bindings first. Deleting a schema removes every version and records a `schema_deleted` audit entry.

:::tip[Binding from the interface]
The database editor's **Compliance Schema** field binds a schema to the database; selecting **None (no compliance schema)** removes the binding. See [Compliance (User Guide)](../user-guide/compliance.md#binding-schemas-to-databases).
:::

## Compliance states

Every asset with a compliance record has a compliance state:

| State                | Meaning                                                                                                             | Indicator |
| -------------------- | ------------------------------------------------------------------------------------------------------------------- | --------- |
| `unknown`            | No evaluation has produced a verdict (also the answer for an asset with no record)                                  | —         |
| `pending_evaluation` | The asset is bound but not yet evaluated, or a pipeline rule's execution is awaited                                 | Blue      |
| `compliant`          | Every rule passed, or only `inform`-level rules failed                                                              | Green     |
| `non_compliant`      | A `warn`-level rule failed and no `quarantine`-level rule did                                                       | Yellow    |
| `quarantined`        | A `quarantine`-level rule failed                                                                                    | Red       |
| `exception`          | A rule failed while an [exception](#exceptions) is active: the asset stays released and the violations are recorded | Info      |

Beside its state, a compliance record carries `lastEvaluationStatus` — `completed`, `pending_pipeline` or `error`. An `error` status means the last evaluation produced no verdict (every rule of it errored, or its schema could not be loaded — see [Tooling failures](#tooling-failures)), so the state shown is the one the asset held before that evaluation. The interface flags such a record with an **Evaluation error** indicator beside the state badge, and the database overview's `summary` carries an `error` count of these assets. That count is an overlay on the state buckets rather than a state of its own: an asset with an errored last evaluation is also counted under the state it kept, so the overlay is not part of their sum.

## Evaluation

An evaluation checks one asset against one schema and produces a verdict. Evaluations start in four ways:

1. **Automatically** — the compliance trigger evaluates an asset of a database with `complianceAutoEval` on when the asset is created or updated. Uploads are detected from the file indexer's events: the trigger subscribes to the indexer's Amazon SNS topic, unwraps each message to its Amazon S3 object records and queues one evaluation per asset a message touches; a change to the asset record itself (the asset table's stream) triggers the same way. Both paths report the same upload — the indexer emits one message per object and every upload stamps the asset record — so each skips a change the asset's last evaluation already covers: an evaluation that started after the upload's completion was recorded on the asset (`lastChangeAt`, written once every object of the upload was copied) has read every file of that upload, whichever path reported the change first and however many files it carried, so one upload produces one evaluation. Only an object written outside a recorded upload completion is judged against its own S3 event time, with a two-second margin that absorbs clock skew between the S3 and trigger clocks. A file that lands after that evaluation started is a new change and deliberately yields a second evaluation. An asset without a record is registered under the database binding first. Files a workflow execution wrote — the outputs a pipeline rule's own run puts back into the asset, carrying object metadata `vams-changesource` of `workflowExecution` and recorded on the asset as `lastChangeSource` — never trigger an evaluation, and are never counted as a pipeline rule's inputs, so a rule cannot re-evaluate and quarantine an asset on the strength of its own output. A change the in-flight evaluation has already read — one made at or before its start — is covered by that evaluation; a later change starts a new evaluation. When two evaluations overlap, an older evaluation's callback never overwrites the state a newer one has written: the asset-state row records the newest evaluation only, and an older one finalizes its own evaluation record alone.
2. **On demand** — the **Evaluate Now** action on the asset's Compliance tab, or `POST /compliance/evaluate/{databaseId}/{assetId}`. A request that names a `schemaName` evaluates against that schema without changing the asset's binding; the evaluation record carries the schema used, and the response carries the `schemaVersion` read, `exceptionApplied` and `hasRuleErrors`.
3. **Sweep** — the **Sweep** action on the Compliance Schemas page, or `POST /compliance/sweep/{schemaName}`, evaluates every asset bound to the schema (200 per call).
4. **Cascade** — an approved cascade evaluates the downstream assets of a parent in dependency order.

An evaluation resolves the schema (merging inherited rules), runs the metadata and relationship rules against the asset's metadata and links, launches a workflow execution for each pipeline rule, and writes an evaluation record, the asset's compliance state and a `compliance_check` audit entry. With no pipeline rules the verdict is final at once; otherwise the evaluation stays `pending_pipeline` until the [workflow completion](#workflow-completion) events arrive.

The verdict follows the highest-severity failure among the rules that were evaluated: any `quarantine`-level failure → `quarantined`; otherwise any `warn`-level failure → `non_compliant`; otherwise `compliant`. While the asset holds an active [exception](#exceptions) against the schema version evaluated, a failing verdict maps to the `exception` state instead. Each rule's outcome is kept on the evaluation record as a rule result (`ruleName`, `ruleType`, `enforcement`, `status`, `passed`, `message`, and for pipeline rules the `measured` and `expected` values), and the messages of the failed rules as `violations`. A rule result whose `status` is `error` is a [tooling failure](#tooling-failures): it is excluded from the verdict and from the violations, and the evaluation records it under `hasRuleErrors` / `errorRules`.

## Quarantine

An asset that fails a `quarantine`-level rule enters the `quarantined` state and appears on the Quarantine page with its asset name. Subscribers of the asset are notified through its Amazon SNS topic. Whether quarantine blocks the asset's download, stream, export and auxiliary-preview endpoints is set by `app.compliance.quarantineBlocksDownload`.

A quarantined asset leaves quarantine in three ways:

-   **Re-evaluation** — an evaluation in which every `quarantine`-level rule passes moves the asset to the state its verdict maps to; a return to `compliant` records a `quarantine_released` audit entry.
-   **Release** — sets the asset to `compliant` without recording an exception. The next failing evaluation quarantines it again.
-   **Exception** — moves the asset to the `exception` state and records the exception on its compliance record and in the audit trail with the reason and the user who granted it, documenting why the asset remains available despite the failure. An exception outlives re-evaluation; see [Exceptions](#exceptions).

### Exceptions

An exception is scoped to the schema the asset was bound to when it was granted, at that schema's version at the time: the compliance record stores them as `exceptionSchemaName` and `exceptionSchemaVersion`, beside `exceptionGranted`, `exceptionReason`, `exceptionGrantedBy` and `exceptionGrantedAt`. Granting an exception requires the asset to be `quarantined`; it moves the asset to `exception` and clears `quarantineReason`. The exception then holds until it is revoked or superseded:

-   **Re-evaluation against the same schema version** records the rule results and violations on the evaluation as computed and marks the evaluation `exceptionApplied`. The asset becomes `compliant` when the verdict is compliant and `exception` when any rule fails — it is never quarantined or marked `non_compliant`, no quarantine notification is sent, and the exception fields are retained. While pipeline rules are awaited the asset is `pending_evaluation` as for any evaluation.
-   **Superseding** — an evaluation against a different schema name, or against a newer version of the same schema (any registration or update writes one), clears the exception, records an `exception_superseded` audit entry and applies its verdict normally.
-   **Revoking** — `DELETE /compliance/quarantine/{databaseId}/{assetId}/exception`, the **Revoke exception** action on the asset's Compliance tab, or `vamscli compliance quarantine revoke-exception` clears the exception and returns the asset to the state its last evaluation's verdict maps to: quarantined again, with `quarantineReason` restored and its subscribers notified, when that verdict was quarantined; `pending_evaluation` when the asset has no recorded evaluation. An `exception_revoked` audit entry is written. Revoking takes no reason.

An asset in the `exception` state is not listed on the Quarantine page, and the download guard (`app.compliance.quarantineBlocksDownload`) never applies to it; the database overview counts it in the `exception` bucket of its summary.

## Cascades

A cascade re-evaluates the downstream assets of a parent: every descendant reachable through `parentChild` asset links, ordered so that parents are evaluated before their children (up to 500 assets). A descendant without a bound schema is skipped.

A cascade opens in two ways. After an evaluation of an asset that has children — from the trigger, an on-demand evaluation or a sweep — a cascade is opened in the `pending_approval` state and the parent's subscribers are notified; while a cascade for that parent is still pending approval, further evaluations of the parent open no new one, so the approval queue holds at most one automatic cascade per parent. A cascade created through the API or CLI always opens a new one; it waits for approval by default, or starts executing at once when created with `requireApproval: false`.

| State              | Meaning                                                |
| ------------------ | ------------------------------------------------------ |
| `pending_approval` | Waiting for an approval or rejection                   |
| `executing`        | Evaluating the downstream assets in the background     |
| `completed`        | Every downstream asset was evaluated or skipped        |
| `aborted`          | Rejected, or the run failed — `abortReason` says which |

A pending cascade records an `approvalTimeoutAt` timestamp 24 hours after its creation, shown in the approval queue. Approving a cascade moves it to `executing` and hands the run to a background executor; the approve request returns at once, and the per-asset verdicts are recorded on the cascade as the run progresses, so its state is followed by reading the cascade until it is `completed` or `aborted`. Rejecting a cascade aborts it.

## Audit trail

Every compliance action writes an entry to the compliance audit table: evaluations (`compliance_check`), evaluations in which a rule errored or whose schema could not be loaded (`evaluation_error`, naming the errored rules), quarantine releases, exceptions granted, revoked and superseded, schema bindings and unbindings at both levels, schema deletions, and cascade creation, approval, rejection and completion. An entry carries the actor, the affected `databaseId` and `assetId`, JSON-encoded details, and — where the action changed an asset's state — the previous and new compliance state. The trail is queried per asset or across the deployment, filtered by event type and time window; the deployment-wide listing returns only the entries whose database the caller may read. See [Compliance API — Event types](../api/compliance.md#event-types).

## API endpoints

All compliance endpoints are under the `/compliance` prefix. The [Compliance API](../api/compliance.md) reference documents parameters, request and response bodies.

| Method | Path                                                      | Description                                  |
| ------ | --------------------------------------------------------- | -------------------------------------------- |
| GET    | `/compliance/schemas`                                     | List schemas (optionally for one database)   |
| POST   | `/compliance/schemas`                                     | Register a schema, or write its next version |
| GET    | `/compliance/schemas/{schemaName}`                        | Get the latest version of a schema           |
| PUT    | `/compliance/schemas/{schemaName}`                        | Write the next version of a schema           |
| DELETE | `/compliance/schemas/{schemaName}`                        | Delete an unbound schema                     |
| GET    | `/compliance/bind/{databaseId}`                           | Get the database binding and asset overrides |
| PUT    | `/compliance/bind/{databaseId}`                           | Bind a schema to a database                  |
| DELETE | `/compliance/bind/{databaseId}`                           | Remove the database binding                  |
| PUT    | `/compliance/bind/{databaseId}/{assetId}`                 | Bind a schema to an asset (override)         |
| DELETE | `/compliance/bind/{databaseId}/{assetId}`                 | Remove the asset override                    |
| POST   | `/compliance/evaluate/{databaseId}/{assetId}`             | Evaluate an asset                            |
| POST   | `/compliance/sweep/{schemaName}`                          | Evaluate every asset bound to a schema       |
| GET    | `/compliance/evaluations/{databaseId}/{assetId}`          | List the evaluations of an asset (paged)     |
| GET    | `/compliance/state/{databaseId}/{assetId}`                | Get the compliance state of an asset         |
| GET    | `/compliance/state/{databaseId}`                          | Get the compliance overview of a database    |
| GET    | `/compliance/quarantine`                                  | List quarantined assets                      |
| POST   | `/compliance/quarantine/{databaseId}/{assetId}/release`   | Release an asset from quarantine             |
| POST   | `/compliance/quarantine/{databaseId}/{assetId}/exception` | Grant an exception to a quarantined asset    |
| DELETE | `/compliance/quarantine/{databaseId}/{assetId}/exception` | Revoke an active exception                   |
| GET    | `/compliance/cascades`                                    | List pending cascades                        |
| POST   | `/compliance/cascades`                                    | Create a cascade                             |
| GET    | `/compliance/cascades/{cascadeId}`                        | Get a cascade                                |
| POST   | `/compliance/cascades/{cascadeId}/approve`                | Approve and execute a pending cascade        |
| POST   | `/compliance/cascades/{cascadeId}/reject`                 | Reject a pending cascade                     |
| GET    | `/compliance/audit`                                       | Query the audit trail (paged, filterable)    |
| GET    | `/compliance/audit/{databaseId}/{assetId}`                | Get the audit history of an asset (paged)    |

## Authorization model

Compliance operations use the two-tier Casbin ABAC/RBAC system described in the [Permissions Model](../concepts/permissions-model.md); both tiers must allow a request.

**Tier 1** checks the caller's `api` constraints against the `/compliance/*` route, and `web` constraints against the compliance pages (`/compliance/*` and `/databases/*`), so the navigation entries and pages appear only to a role that grants them.

**Tier 2** enforces one of three object types, with the object action mirroring the HTTP method:

| Object type            | Constraint fields               | Governs                                                                                                                |
| ---------------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `complianceSchema`     | `complianceSchemaName`          | Schema registration, update and deletion, schema bindings at both levels, and sweeps                                   |
| `complianceEvaluation` | `databaseId`, `complianceState` | Evaluation, evaluation history, compliance state and overview, the quarantine listing and actions, and the audit trail |
| `complianceCascade`    | `cascadeId`                     | Cascade creation, listing, detail, approval and rejection                                                              |

Listings return only the items the caller may `GET`. The built-in admin role is seeded with an allow-all constraint on each of the three object types.

Two permission templates in `documentation/permissionsTemplates/` set up non-admin roles: `compliance-admin.json` (full schema management, evaluation and quarantine actions scoped to a `DATABASE_ID`, cascade approval, plus the web and API routes) and `compliance-readonly.json` (`GET` on the three object types, evaluations scoped to a `DATABASE_ID`). Constraint criteria narrow access further — for example `databaseId equals my-database` on a `complianceEvaluation` constraint, `complianceSchemaName equals my-schema` on a `complianceSchema` constraint, or `complianceState equals quarantined` to grant only the quarantine operations. See [Compliance (User Guide) — Permissions](../user-guide/compliance.md#permissions).

## Amazon DynamoDB tables

| Table                              | Primary key                               | Global secondary indexes                                                                                      | Holds                                                                   |
| ---------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `ComplianceSchemaStorageTable`     | `schemaName` (PK), `internalVersion` (SK) | `DatabaseIdIndex` (`databaseId`, `schemaName`)                                                                | Every version of every schema                                           |
| `ComplianceAssetStateStorageTable` | `databaseId` (PK), `assetId` (SK)         | `SchemaNameIndex` (`schemaName`, `complianceState`); `ComplianceStateIndex` (`complianceState`, `databaseId`) | The compliance record of each bound asset                               |
| `ComplianceEvaluationStorageTable` | `evaluationId` (PK)                       | `AssetIndex` (`databaseId:assetId`, `evaluatedAt`); `ExecutionIdIndex` (`executionId`)                        | Evaluation records and the tracking row of each pipeline-rule execution |
| `ComplianceCascadeStorageTable`    | `cascadeId` (PK)                          | `StateIndex` (`state`, `createdAt`)                                                                           | Cascades with their per-node progress                                   |
| `ComplianceAuditStorageTable`      | `entryId` (PK)                            | `AssetIndex` (`databaseId:assetId`, `timestamp`); `EventTypeIndex` (`eventType`, `timestamp`)                 | The audit trail                                                         |

The table names are resolved through AWS Systems Manager Parameter Store like every other VAMS table; see [AWS Resources](../architecture/aws-resources.md) and the [Data Model](../architecture/data-model.md).
