# Compliance

Compliance (also known as federated model management) adds schema-driven compliance enforcement to VAMS. Assets are evaluated against registered compliance schemas, non-compliant assets can be quarantined, and a change to a parent asset can propagate re-evaluation through the asset relationship graph.

Compliance is part of every VAMS deployment: five Amazon DynamoDB tables, eight AWS Lambda functions and the `/compliance/*` API routes are always created, and the compliance pages of the web interface appear to a user whose role grants them like every other page. Two configuration settings tune it:

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

| Setting                                   | Default | Effect                                                                                                                                                                                |
| ----------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.compliance.autoLoadDefaultSchema`    | `true`  | Seeds the `GLOBAL` schema `default-compliance-schema` at deployment: one `warn`-level metadata rule that validates a bound asset against the `GLOBAL` `defaultAsset` metadata schema. |
| `app.compliance.quarantineBlocksDownload` | `false` | When `true`, the asset download and stream endpoints refuse a quarantined asset. When `false`, quarantine is visible in the interface and the API but does not block access.          |

See the [Configuration Reference](../deployment/configuration-reference.md) for the settings and [Compliance (User Guide)](../user-guide/compliance.md) for the interface walkthrough.

## Compliance schemas

A compliance schema is a named, versioned set of rules. Every registration or update writes the next version of the schema and evaluation always reads the highest version, so the version history is retained. A schema is scoped to a database or to `GLOBAL`; a database-scoped schema can only be bound within its own database, while a `GLOBAL` schema can be bound anywhere.

A schema whose `isSystem` flag is set is a system schema. Only the system user can register one or write a new version of it.

### Schema formats

| Format                     | Description                                                                                                                                                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **vams-rules-v1**          | Named rules of three types, each with an enforcement level; pipeline rules compare measurements against tolerances. The format evaluation understands.                                                                               |
| **JSON Schema (draft-07)** | A JSON Schema subset validating asset metadata structure. A body in this format is accepted and stored, but an evaluation against it records an `error` status and leaves the asset `unknown`; write new schemas in `vams-rules-v1`. |

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

A pipeline rule names a workflow and the pipeline within that workflow whose output the checks read:

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
| `inputParameters`                | Optional. Values handed to the pipeline as its template tag values                                                                     |
| `checks[].outputField`           | Key of the `measurements` object in the pipeline's output file                                                                         |
| `checks[].tolerance`             | The comparison — see [Tolerance operators](#tolerance-operators)                                                                       |

The evaluation launches one workflow execution per pipeline rule through the standard execute-workflow request: the asset is the single input, `pipelineExecutionParameters` carries the template and tag values under the `pipelineId`, and the trigger type is `manual`. The execution runs as the system user and is listed with the workflow's other executions. The evaluation records the `executionId` of each execution and stays `pending_pipeline` (asset state `pending_evaluation`) until every execution has completed. A rule whose workflow or pipeline does not exist, or whose execution cannot be launched, fails at once.

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

A workflow that writes no compliance output file can still back a pipeline rule. Two measurements are derived for every execution and used when the file is absent:

| Measurement                   | Value                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `execution_success`           | `1.0` when the execution succeeded, `0.0` otherwise                            |
| `processing_duration_seconds` | Wall-clock duration of the execution, from its start and completion timestamps |

A rule can therefore require that an existing workflow succeeds and finishes within a time budget without any change to its pipelines.

#### Workflow completion

When a workflow execution reaches a terminal status — including an abort — the workflow end-state puts a `workflow.execution.completed` event on the orchestration bus. The compliance callback subscribes to that event, looks the `executionId` up in the evaluation table's `ExecutionIdIndex`, and completes the pipeline rule it belongs to: on a `SUCCEEDED` execution it reads the pipeline's `compliance-output.json` from the execution's recorded output results and runs the rule's checks against the measurements; any other terminal status (`FAILED`, `ABORTED`, `TIMED_OUT`) fails every check of the rule. Once every pipeline rule of the evaluation has reported, the verdict is determined, the asset state is written and the audit entry is recorded. The event contract is documented in the [workflow execution data model](../developer/workflow-execution-data-model-handoff.md#workflow-completion-event).

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

| State                | Meaning                                                                             | Indicator |
| -------------------- | ----------------------------------------------------------------------------------- | --------- |
| `unknown`            | No evaluation has produced a verdict (also the answer for an asset with no record)  | —         |
| `pending_evaluation` | The asset is bound but not yet evaluated, or a pipeline rule's execution is awaited | Blue      |
| `compliant`          | Every rule passed, or only `inform`-level rules failed                              | Green     |
| `non_compliant`      | A `warn`-level rule failed and no `quarantine`-level rule did                       | Yellow    |
| `quarantined`        | A `quarantine`-level rule failed                                                    | Red       |

## Evaluation

An evaluation checks one asset against one schema and produces a verdict. Evaluations start in four ways:

1. **Automatically** — the compliance trigger subscribes to the asset indexer's Amazon SNS topic and evaluates an asset of a database with `complianceAutoEval` on when the asset is created or updated. An asset without a record is registered under the database binding first.
2. **On demand** — the **Evaluate Now** action on the asset's Compliance tab, or `POST /compliance/evaluate/{databaseId}/{assetId}`.
3. **Sweep** — the **Sweep** action on the Compliance Schemas page, or `POST /compliance/sweep/{schemaName}`, evaluates every asset bound to the schema (200 per call).
4. **Cascade** — an approved cascade evaluates the downstream assets of a parent in dependency order.

An evaluation resolves the schema (merging inherited rules), runs the metadata and relationship rules against the asset's metadata and links, launches a workflow execution for each pipeline rule, and writes an evaluation record, the asset's compliance state and a `compliance_check` audit entry. With no pipeline rules the verdict is final at once; otherwise the evaluation stays `pending_pipeline` until the [workflow completion](#workflow-completion) events arrive.

The verdict follows the highest-severity failure: any `quarantine`-level failure → `quarantined`; otherwise any `warn`-level failure → `non_compliant`; otherwise `compliant`. Each rule's outcome is kept on the evaluation record as a rule result (`ruleName`, `ruleType`, `enforcement`, `passed`, `message`, and for pipeline rules the `measured` and `expected` values), and the messages of the failed rules as `violations`.

## Quarantine

An asset that fails a `quarantine`-level rule enters the `quarantined` state and appears on the Quarantine page with its asset name. Subscribers of the asset are notified through its Amazon SNS topic. Whether quarantine blocks the asset's download and stream endpoints is set by `app.compliance.quarantineBlocksDownload`.

A quarantined asset leaves quarantine in three ways:

-   **Re-evaluation** — an evaluation in which every `quarantine`-level rule passes moves the asset to the state its verdict maps to; a return to `compliant` records a `quarantine_released` audit entry.
-   **Release** — sets the asset to `compliant` without recording an exception. The next failing evaluation quarantines it again.
-   **Exception** — sets the asset to `compliant` and records the exception on its compliance record and in the audit trail with the reason and the user who granted it, documenting why the asset remains available despite the failure.

## Cascades

A cascade re-evaluates the downstream assets of a parent: every descendant reachable through `parentChild` asset links, ordered so that parents are evaluated before their children (up to 500 assets). A descendant without a bound schema is skipped.

A cascade opens in two ways. After an evaluation of an asset that has children — from the trigger, an on-demand evaluation or a sweep — a cascade is opened in the `pending_approval` state and the parent's subscribers are notified. A cascade created through the API or CLI waits for approval by default, or executes at once when created with `requireApproval: false`.

| State              | Meaning                                         |
| ------------------ | ----------------------------------------------- |
| `pending_approval` | Waiting for an approval or rejection            |
| `executing`        | Evaluating the downstream assets                |
| `completed`        | Every downstream asset was evaluated or skipped |
| `aborted`          | Rejected                                        |

A pending cascade records an `approvalTimeoutAt` timestamp 24 hours after its creation, shown in the approval queue. Approving a cascade executes it within the request and records the per-asset verdicts on the cascade; rejecting it aborts it.

## Audit trail

Every compliance action writes an entry to the compliance audit table: evaluations (`compliance_check`), quarantine releases and exceptions, schema bindings and unbindings at both levels, schema deletions, and cascade creation, approval, rejection and completion. An entry carries the actor, the affected `databaseId` and `assetId`, JSON-encoded details, and — where the action changed an asset's state — the previous and new compliance state. The trail is queried per asset or across the deployment, filtered by event type and time window; see [Compliance API — Event types](../api/compliance.md#event-types).

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

| Table                              | Primary key                               | Global secondary indexes                                                                      | Holds                                                                   |
| ---------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `ComplianceSchemaStorageTable`     | `schemaName` (PK), `internalVersion` (SK) | `DatabaseIdIndex` (`databaseId`, `schemaName`)                                                | Every version of every schema                                           |
| `ComplianceAssetStateStorageTable` | `databaseId` (PK), `assetId` (SK)         | `SchemaNameIndex` (`schemaName`, `complianceState`)                                           | The compliance record of each bound asset                               |
| `ComplianceEvaluationStorageTable` | `evaluationId` (PK)                       | `AssetIndex` (`databaseId:assetId`, `evaluatedAt`); `ExecutionIdIndex` (`executionId`)        | Evaluation records and the tracking row of each pipeline-rule execution |
| `ComplianceCascadeStorageTable`    | `cascadeId` (PK)                          | `StateIndex` (`state`, `createdAt`)                                                           | Cascades with their per-node progress                                   |
| `ComplianceAuditStorageTable`      | `entryId` (PK)                            | `AssetIndex` (`databaseId:assetId`, `timestamp`); `EventTypeIndex` (`eventType`, `timestamp`) | The audit trail                                                         |

The table names are resolved through AWS Systems Manager Parameter Store like every other VAMS table; see [AWS Resources](../architecture/aws-resources.md) and the [Data Model](../architecture/data-model.md).
