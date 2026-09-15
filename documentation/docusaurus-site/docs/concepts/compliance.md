# Compliance 

Compliance adds schema-driven compliance enforcement to VAMS. It ensures that engineering datasets across federated environments conform to defined standards by evaluating assets against registered JSON schemas, quarantining non-compliant assets, and propagating compliance changes through asset relationship graphs.

## Enabling Compliance

Compliance is an optional feature controlled by the deployment configuration. To enable it, set the following in your `config.json`:

```json
{
    "app": {
        "compliance": {
            "enabled": true
        }
    }
}
```

When enabled, VAMS deploys five additional Amazon DynamoDB tables, six AWS Lambda functions, and the `/compliance/*` API routes. When disabled (the default), no Compliance resources are created.

## Core concepts

### Compliance schemas

A compliance schema defines the structural and content requirements that an asset must satisfy. Schemas can be bound to databases or individual assets and support versioning — each update creates a new version, and the system retains the full history for audit purposes.

VAMS supports two schema formats:

| Format | Description |
| --- | --- |
| **vams-rules-v1** | Native compliance rules format with typed rule evaluation, enforcement levels, and tolerance comparison. Recommended for new schemas. |
| **JSON Schema (draft-07)** | Legacy format for simple metadata structure validation. Retained for backward compatibility. |

#### vams-rules-v1 format

The `vams-rules-v1` format defines compliance as a set of named rules, each with a type and enforcement level:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "rule-name": {
            "ruleType": "pipeline | metadata | relationship",
            "enforcement": "quarantine | warn | inform",
            "...rule-type-specific fields..."
        }
    }
}
```

**Rule types:**

| Type | Purpose | Evaluation |
| --- | --- | --- |
| `pipeline` | Invoke a VAMS workflow and compare output measurements to tolerances | Asynchronous (via AWS Step Functions) |
| `metadata` | Validate asset metadata fields against a VAMS metadata schema (`validateRequired` checks all required fields are present) | Synchronous |
| `relationship` | Validate that required asset links exist (for example, `must-have-parent` checks that at least one `parentChild` link exists) | Synchronous |

:::info[Relationship type values]
The `relationshipType` field in relationship rule checks must use the exact value stored in Amazon DynamoDB: `"parentChild"` (camelCase) or `"related"`. Do not use uppercase or snake_case variants.
:::

**Enforcement levels:**

| Level | Effect on verdict |
| --- | --- |
| `quarantine` | Failure sets asset to `quarantined` state |
| `warn` | Failure sets asset to `non_compliant` state |
| `inform` | Failure is logged but asset remains `compliant` |

#### Schema inheritance

Schemas support inheritance through the `extends` field. A child schema inherits all rules from its parent and can override specific rules by redefining them with the same key name. Rules defined only in the parent are inherited unchanged; rules defined in both parent and child use the child's definition.

```json
{
    "schemaFormat": "vams-rules-v1",
    "extends": "parent-schema-name",
    "rules": {
        "overridden-rule": { "...child definition takes precedence..." }
    }
}
```

### Schema binding and precedence

Schemas can be bound at two levels:

| Binding level | Scope | Precedence |
| --- | --- | --- |
| Database | All assets in the database inherit the schema | Lower |
| Asset | Only that specific asset uses the schema | Higher (overrides database) |

**Precedence rule:** An asset-level binding always overrides a database-level binding.

When a database schema is changed or first applied:

- Assets with an explicit asset-level binding (`schemaSource: "asset"`) are **untouched**.
- Assets inheriting from the database (`schemaSource: "database"`) are marked `pending_evaluation`.
- A secondary action (sweep or manual evaluation) is required to actually run the evaluations.

When an asset-level binding is removed, the asset falls back to the database schema and is marked `pending_evaluation`.

When a database schema is **unbound** (removed entirely):

- All compliance records with `schemaSource: "database"` are deleted.
- Assets with explicit asset-level bindings (`schemaSource: "asset"`) are untouched.
- The database compliance overview will show no tracked assets until a new schema is bound.

:::tip[Removing a compliance schema]
To remove a compliance schema from a database, edit the database and select "None (no compliance schema)" in the schema dropdown. This clears all database-inherited compliance states.
:::

### Compliance states

Every tracked asset has a compliance state:

| State                       | Meaning                                                       | UI Indicator |
| --------------------------- | ------------------------------------------------------------- | ------------ |
| `unknown`                   | Asset has not been evaluated                                  | --           |
| `pending_evaluation`        | Evaluation has been triggered but not yet completed           | Blue (in-progress) |
| `compliant`                 | Asset satisfies its assigned schema                           | Green (success) |
| `non_compliant`             | A `warn`-level rule failed but no `quarantine`-level failures | Yellow (warning) |
| `quarantined`               | A `quarantine`-level rule failed                              | Red (error) |
| `pending_parent_resolution` | Asset is blocked because a parent asset is quarantined        | --           |

### Evaluation

An evaluation checks an asset against its assigned compliance schema. Evaluations can be triggered in four ways:

1. **On upload** -- when an asset is created or updated, Compliance automatically checks whether a schema is registered and triggers evaluation via an Amazon SNS subscription.
2. **On demand (UI)** -- the **Evaluate Now** button on the asset's Compliance tab triggers evaluation and cascade propagation to child assets.
3. **On demand (API)** -- a call to `POST /compliance/evaluate/\{databaseId\}/\{assetId\}` triggers evaluation for a specific asset.
4. **Sweep** -- a call to `POST /compliance/sweep/\{schemaName\}` or the **Sweep** button on the Compliance Schemas page triggers evaluation for all assets governed by a specific schema.

#### Evaluation flow (vams-rules-v1)

For schemas using the `vams-rules-v1` format, evaluation proceeds as follows:

1. Load the schema and resolve inheritance (merge parent rules if `extends` is set).
2. Evaluate **metadata rules** synchronously — fetch asset metadata and validate against the referenced VAMS metadata schema.
3. Evaluate **relationship rules** synchronously — query asset links and compare counts against minCount/maxCount requirements.
4. If **pipeline rules** exist, invoke the referenced VAMS workflow asynchronously. The evaluation enters `pending_pipeline` state until the workflow completes.
5. When all rules have been evaluated, determine the **final verdict** based on enforcement levels.

#### Verdict determination

The final compliance state is determined by the highest-severity failure:

1. Any `quarantine`-level rule failure → asset is `quarantined`
2. Any `warn`-level rule failure (with no quarantine failures) → asset is `non_compliant`
3. Only `inform`-level failures → asset remains `compliant` (failures logged in audit)
4. All rules pass → asset is `compliant`

#### Pipeline rule execution (async)

Pipeline rules invoke VAMS workflows (AWS Step Functions) to perform complex validation that cannot be done synchronously — for example, geometric accuracy checks, file format validation, or AI-based quality assessment. The execution is fully asynchronous:

```
Evaluation Engine                   Step Functions                    EventBridge
     |                                    |                                |
     |-- start_execution(input) --------->|                                |
     |   (evaluationId in input)          |                                |
     |                                    |-- runs workflow steps --------->|
     |   evaluation status:               |   (containers, lambdas)        |
     |   "pending_pipeline"               |                                |
     |                                    |-- execution complete ---------> |
     |                                    |                                |
     |<----------------------------------------- EventBridge rule triggers--|
     |   complianceWorkflowCallback                                               |
     |   reads compliance-output.json                                      |
     |   compares measurements vs tolerances                               |
     |   merges with metadata/relationship results                         |
     |   determines final verdict                                          |
```

**Pipeline rule definition:**

```json
{
    "ruleType": "pipeline",
    "enforcement": "quarantine",
    "pipelineRef": {
        "databaseId": "GLOBAL",
        "workflowId": "coord-validate-workflow"
    },
    "checks": [
        {
            "name": "residual_check",
            "outputField": "residual_error_mm",
            "tolerance": { "operator": "lte", "value": 1.0 }
        }
    ],
    "inputParameters": {
        "referenceFrame": "EPSG:27700"
    }
}
```

**Key fields:**

| Field | Description |
| --- | --- |
| `pipelineRef.databaseId` | Database containing the workflow (use `GLOBAL` for cross-database workflows) |
| `pipelineRef.workflowId` | The VAMS workflow ID to execute |
| `pipelineRef.templateId` | Optional. The workflow template to use. If omitted, the workflow's default template is selected (single-template workflows auto-promote their only template to default). |
| `checks[].name` | Human-readable check name |
| `checks[].outputField` | Key in the pipeline's output `measurements` object to evaluate |
| `checks[].tolerance` | Comparison criteria (operator + value/min/max) |
| `inputParameters` | Optional parameters passed to the workflow as `complianceContext.inputParameters` |

**Workflow input context:**

The evaluation engine passes an `complianceContext` object in the workflow's `inputMetadata` field:

```json
{
    "complianceContext": {
        "evaluationId": "eval-abc123",
        "ruleName": "coord-accuracy",
        "checks": [
            {
                "name": "residual_check",
                "outputField": "residual_error_mm",
                "tolerance": { "operator": "lte", "value": 1.0 }
            }
        ],
        "inputParameters": { "referenceFrame": "EPSG:27700" }
    }
}
```

Pipelines can read `inputParameters` to configure their processing (for example, which reference frame to compare against). The `evaluationId` is used by the callback to correlate results.

#### Pipeline compliance output contract

Pipeline rules support two modes of operation: **default metrics** (zero-modification) and **custom compliance output** (advanced).

##### Default metrics (zero-modification pipelines)

Any existing VAMS workflow can be used for pipeline rule compliance without modification. When a workflow completes and no `compliance-output.json` file is found, the pipeline callback automatically computes default metrics from the execution metadata:

| Default metric | Type | Description |
| --- | --- | --- |
| `execution_success` | float | `1.0` if the workflow succeeded, `0.0` otherwise |
| `processing_duration_seconds` | float | Wall-clock duration of the AWS Step Functions execution in seconds |

These default metrics are available as `outputField` values in the rule's `checks` array. For example, a rule can enforce that a workflow succeeds and completes within a time budget without requiring any changes to the pipeline containers.

##### Custom compliance output (advanced)

For domain-specific measurements (geometric accuracy, coverage ratios, noise levels), the pipeline container writes a `compliance-output.json` file to the metadata output path in Amazon S3. The file structure:

```json
{
    "complianceOutput": true,
    "measurements": {
        "residual_error_mm": 0.45,
        "coverage_percent": 98.2
    },
    "status": "success",
    "errors": []
}
```

| Field | Type | Description |
| --- | --- | --- |
| `complianceOutput` | boolean | Must be `true` — identifies this as a compliance output file |
| `measurements` | object | Key-value pairs where keys match `checks[].outputField` names |
| `status` | string | `"success"` or `"error"` |
| `errors` | array | Error messages if `status` is `"error"` |

The `measurements` keys must match the `outputField` values defined in the pipeline rule's `checks` array. The evaluation callback reads each measurement and compares it against the configured tolerance.

**Output file location fallback:** The callback first checks the Step Functions execution output for a metadata path key. If not found, it falls back to a well-known path: `compliance/\{databaseId\}/\{assetId\}/\{evaluationId\}/compliance-output.json` in the asset auxiliary bucket.

#### Pipeline execution failure handling

If the Step Functions execution fails, times out, or is aborted:

- All pending pipeline rule checks are marked as failed
- The failure reason includes the execution status (FAILED, TIMED_OUT, ABORTED)
- The evaluation proceeds to verdict determination using the failed results
- If any failed pipeline rule has `quarantine` enforcement, the asset is quarantined
- The `execution_success` default metric is set to `0.0`

If the pipeline succeeds but the custom `compliance-output.json` has `status: "error"`, all checks for that rule are marked as failed.

#### Tolerance operators

| Operator | Meaning | Required fields |
| --- | --- | --- |
| `lte` | value <= threshold | `value` |
| `gte` | value >= threshold | `value` |
| `eq` | value == target (within epsilon) | `value`, optional `epsilon` (default 0.001) |
| `between` | min <= value <= max | `min`, `max` |

### Quarantine

When an asset fails a rule with `quarantine` enforcement level, the asset enters the `quarantined` state. The quarantine page displays all quarantined assets across databases with their asset names for easy identification.

**Download blocking**: By default, quarantine is informational — the UI displays a red error indicator but downloads remain available. Download blocking enforcement is planned for a future release.

**Auto-release**: When an asset is re-evaluated and passes all rules, quarantine is automatically cleared and the asset returns to `compliant` state. This applies whether the re-evaluation was triggered by metadata updates, manual evaluation, or cascade execution.

**Release**: Manually removes quarantine and sets the asset to `compliant`.

**Exception**: Grants an exception for a quarantined asset. The asset's compliance state is set to `compliant`, and the exception is recorded in the audit log with the reason and the user who granted it. Exceptions allow assets to remain available despite non-compliance when there is a documented justification.

### Cascade execution

When a parent asset is evaluated, Compliance checks for child assets linked via `parentChild` relationships. If children exist, a cascade is created to propagate re-evaluation through the asset relationship DAG (directed acyclic graph) in topological order (parents before children).

Cascades are triggered by:

- **Evaluate Now** on a parent asset (via the UI or API)
- **Asset updates** that fire the SNS-triggered compliance check

Cascades support an approval gate: the cascade enters `pending_approval` and must be explicitly approved before execution proceeds. On approval, child assets are re-evaluated in dependency order.

### Audit log

Every compliance action is recorded in the audit log:

- Schema registrations and updates
- Compliance evaluations (triggered and completed)
- Quarantine entries and releases
- Exception grants and revocations
- Cascade triggers and approvals

The audit log can be queried by asset, by event type, or across the entire system.

## API endpoints

All Compliance endpoints are under the `/compliance` path prefix and require authentication.

### Schema management

| Method | Path                             | Description              |
| ------ | -------------------------------- | ------------------------ |
| GET    | `/compliance/schemas`            | List all schemas         |
| POST   | `/compliance/schemas`            | Register a new schema    |
| GET    | `/compliance/schemas/\{schemaName\}` | Get schema details       |
| PUT    | `/compliance/schemas/\{schemaName\}` | Update an existing schema |

### Schema binding

| Method | Path                                                    | Description                           |
| ------ | ------------------------------------------------------- | ------------------------------------- |
| PUT    | `/compliance/bind/\{databaseId\}`                       | Bind schema to database               |
| DELETE | `/compliance/bind/\{databaseId\}`                       | Remove database schema binding        |
| PUT    | `/compliance/bind/\{databaseId\}/\{assetId\}`           | Bind schema to asset (override)       |
| DELETE | `/compliance/bind/\{databaseId\}/\{assetId\}`           | Remove asset override (fall back)     |
| GET    | `/compliance/bind/\{databaseId\}`                       | Get database binding and overrides    |

### Evaluation

| Method | Path                                                | Description                  |
| ------ | --------------------------------------------------- | ---------------------------- |
| POST   | `/compliance/evaluate/\{databaseId\}/\{assetId\}`       | Evaluate a specific asset    |
| POST   | `/compliance/sweep/\{schemaName\}`                    | Sweep all assets for a schema |
| GET    | `/compliance/evaluations/\{databaseId\}/\{assetId\}`    | Get evaluation history       |
| GET    | `/compliance/state/\{databaseId\}/\{assetId\}`          | Get current compliance state |
| GET    | `/compliance/state/\{databaseId\}`                      | Get database compliance overview |

### Quarantine

| Method | Path                                                           | Description         |
| ------ | -------------------------------------------------------------- | ------------------- |
| GET    | `/compliance/quarantine`                                       | List quarantined assets |
| POST   | `/compliance/quarantine/\{databaseId\}/\{assetId\}/release`        | Release from quarantine |
| POST   | `/compliance/quarantine/\{databaseId\}/\{assetId\}/exception`      | Grant an exception  |

### Cascade execution

| Method | Path                                          | Description            |
| ------ | --------------------------------------------- | ---------------------- |
| GET    | `/compliance/cascades`                        | List pending cascades  |
| POST   | `/compliance/cascades`                        | Create a cascade       |
| GET    | `/compliance/cascades/\{cascadeId\}`              | Get cascade status     |
| POST   | `/compliance/cascades/\{cascadeId\}/approve`      | Approve a cascade      |
| POST   | `/compliance/cascades/\{cascadeId\}/reject`       | Reject a cascade       |

### Audit

| Method | Path                                            | Description          |
| ------ | ----------------------------------------------- | -------------------- |
| GET    | `/compliance/audit/\{databaseId\}/\{assetId\}`      | Get asset audit history |
| GET    | `/compliance/audit`                             | Query audit log      |

## Authorization model

Compliance compliance operations are protected by the same two-tier Casbin ABAC/RBAC system used across VAMS. Both tiers must allow access for any operation to succeed.

### Tier 1: API route access

All compliance endpoints are under the `/compliance` path prefix. A user's `api` object type constraint must match these routes for the request to proceed past the API Gateway authorizer.

### Tier 2: Object-level access

Three dedicated object types control access to compliance resources:

| Object Type | Constraint Fields | Controls |
| --- | --- | --- |
| `complianceSchema` | `complianceSchemaName` | Schema CRUD, schema binding (bind/unbind to databases and assets), and sweep operations |
| `complianceEvaluation` | `databaseId`, `complianceState` | Evaluation triggers, compliance state queries, quarantine list/release/exception, and audit log access |
| `complianceCascade` | `cascadeId` | Cascade creation, approval, rejection, and status queries |

### Default admin constraints

The built-in admin role is seeded with constraints for all three compliance object types at deploy time. Each constraint uses `contains .*` criteria (matches all values) with full GET/PUT/POST/DELETE permissions. No additional configuration is required for administrators to access compliance features.

### Scoped access patterns

Non-admin roles can be scoped to specific resources using constraint criteria:

- **Database-scoped evaluations**: Set `databaseId equals my-database` on a `complianceEvaluation` constraint to restrict evaluation access to a single database.
- **Schema-scoped management**: Set `complianceSchemaName equals my-schema` on a `complianceSchema` constraint to restrict schema management to specific schemas.
- **State-scoped quarantine**: Set `complianceState equals quarantined` on a `complianceEvaluation` constraint to grant access only to quarantined asset operations.

### Permission templates

Two pre-built templates simplify compliance role setup:

| Template | Access Level | Scope |
| --- | --- | --- |
| `compliance-admin.json` | Full CRUD on schemas; POST+GET on evaluations and cascades; read-only assets/databases | Scoped to `DATABASE_ID` variable for evaluations |
| `compliance-readonly.json` | GET-only on schemas, evaluations, cascades, and audit | Scoped to `DATABASE_ID` variable for evaluations |

For detailed permission configuration instructions, see [User Guide: Compliance > Permissions](../user-guide/compliance.md#permissions).

## System pipelines and workflows

Compliance introduces the `isSystem` flag for pipelines and workflows. When a pipeline or workflow is marked as `isSystem: true`, Casbin ABAC policies can restrict modification or deletion to administrators only. This protects compliance-critical processing from accidental changes.

## Amazon DynamoDB tables

Compliance creates five dedicated tables:

| Table                         | Primary key                       | GSI                            | Purpose                           |
| ----------------------------- | --------------------------------- | ------------------------------ | --------------------------------- |
| Compliance Schema Storage            | `schemaName` (PK), `internalVersion` (SK) | --                    | Schema definitions and versions   |
| Compliance Asset State Storage  | `databaseId` (PK), `assetId` (SK) | `SchemaNameIndex` (PK: schemaName) | Per-asset compliance state        |
| Compliance Evaluation Storage        | `evaluationId` (PK)              | `AssetIndex` (PK: databaseId:assetId) | Evaluation records with rule results and violations |
| Compliance Cascade Storage           | `cascadeId` (PK)                  | --                             | Cascade execution state           |
| Compliance Audit Storage             | `entryId` (PK)                    | `AssetIndex` (PK: databaseId:assetId, SK: timestamp) | Full audit trail |
