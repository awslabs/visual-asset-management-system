# Compliance

Compliance management enforces structural and content standards on assets through schema-driven evaluation. You register compliance schemas, bind them to databases or individual assets, evaluate assets against them, quarantine assets that fail, and propagate re-evaluation through asset relationships. This page walks through the web interface and the API calls behind it; the rule format, states and architecture are described in [Compliance (Concepts)](../concepts/compliance.md).

:::info[Navigation]
The **Compliance** section of the left sidebar — **Compliance Schemas**, **Quarantine**, **Cascade Approvals** and **Audit Log** — and the database compliance overview appear to users whose role grants the `/compliance` and `/databases` web routes. If you do not see them, ask your administrator to apply one of the compliance [permission templates](#permissions).
:::

---

## Viewing compliance status

### Asset compliance tab

Each asset detail view includes a **Compliance** tab showing:

-   **Current state** -- the compliance state as a colored indicator: green (compliant), yellow (non-compliant), red (quarantined), or blue (pending evaluation).
-   **Schema binding** -- which schema governs the asset and whether it is inherited from the database or set on the asset.
-   **Evaluate Now** -- evaluates the asset and, when it has child assets, opens a cascade awaiting approval.
-   **Evaluation history** -- past evaluations with their timestamps, verdicts and violations.

:::info[Evaluation history]
Each row lists the rules that failed as violations. An evaluation against a schema that is not in the `vams-rules-v1` format records an `error` status rather than a verdict.
:::

### Database compliance overview

1. Navigate to **Databases** in the left sidebar.
2. In the databases table, choose the **Compliance** link for the database you want to inspect.
3. The overview page shows:
    - A summary row with counts for each state: Compliant, Non-Compliant, Quarantined, Pending Evaluation, and Unknown.
    - A compliance rate (compliant assets divided by tracked assets).
    - A table of tracked assets with Asset Name, Asset ID, compliance state, schema name, and last evaluation time.

Only assets with a compliance record are counted; an asset that has never been bound does not appear.

---

## Binding schemas to databases

### Assigning a compliance schema

1. Navigate to **Databases** and choose the database you want to configure.
2. Choose **Edit** to open the database editor.
3. In the **Compliance Schema** field, select the schema to bind. `GLOBAL` schemas and schemas scoped to this database are offered.
4. Choose **Update Database**.

Every asset in the database without an asset-level override is marked `pending_evaluation`. Use **Evaluate Now** on individual assets or **Sweep** on the schema to produce verdicts. With automatic evaluation on (the default for a binding), an asset is also evaluated whenever it is created or updated.

The same binding is made with the API:

```bash
PUT /compliance/bind/{databaseId}
```

```json
{ "schemaName": "survey-compliance", "complianceAutoEval": true }
```

### Removing a compliance schema

1. Navigate to **Databases** and choose the database.
2. Choose **Edit** to open the database editor.
3. In the **Compliance Schema** field, select **None (no compliance schema)**.
4. Choose **Update Database**.

The compliance records of every asset that inherited the database schema are removed, and the overview shows **No Compliance Schema Bound** until a schema is assigned again.

:::warning[Irreversible action]
Removing a database's compliance schema deletes the compliance state of every asset that inherited it. Assets with an asset-level schema override keep their state.
:::

### Overriding the schema of one asset

An asset can be bound to a schema of its own, which takes precedence over the database binding:

```bash
PUT /compliance/bind/{databaseId}/{assetId}
```

```json
{ "schemaName": "strict-survey-compliance" }
```

Removing the override (`DELETE /compliance/bind/{databaseId}/{assetId}`) returns the asset to the database schema as `pending_evaluation`. `GET /compliance/bind/{databaseId}` lists the database binding and every override in the database.

---

## Managing schemas

Compliance schemas define the rules assets are evaluated against. Write schemas in the `vams-rules-v1` format: named rules of the `pipeline`, `metadata` and `relationship` types, each with a `quarantine`, `warn` or `inform` enforcement level. A JSON Schema draft-07 body is accepted at registration but is not evaluated — an evaluation against it records an `error` status.

The **Compliance Schemas** page lists every schema you may read with its name, description and version. **Create Schema** opens the schema editor, which builds a `vams-rules-v1` body rule by rule; **Edit** opens an existing schema in the same editor and writes its next version; **Sweep** evaluates every asset bound to the schema.

### Registering a schema

```bash
POST /compliance/schemas
```

```json
{
    "schemaName": "survey-compliance",
    "description": "Compliance rules for survey data assets",
    "schemaBody": {
        "schemaFormat": "vams-rules-v1",
        "rules": {
            "coord-accuracy": {
                "ruleType": "pipeline",
                "enforcement": "quarantine",
                "pipelineRef": {
                    "databaseId": "GLOBAL",
                    "workflowId": "coord-validate-workflow",
                    "pipelineDatabaseId": "GLOBAL",
                    "pipelineId": "coord-validate"
                },
                "checks": [
                    {
                        "name": "residual_check",
                        "outputField": "residual_error_mm",
                        "tolerance": { "operator": "lte", "value": 1.0 }
                    }
                ]
            },
            "required-metadata": {
                "ruleType": "metadata",
                "enforcement": "quarantine",
                "metadataSchemaRef": {
                    "databaseId": "survey-project",
                    "schemaName": "scan-capture-metadata"
                },
                "checks": [
                    {
                        "name": "required_fields",
                        "validateRequired": true,
                        "validateTypes": true
                    }
                ]
            },
            "has-control-parent": {
                "ruleType": "relationship",
                "enforcement": "warn",
                "checks": [
                    {
                        "name": "control_point_link",
                        "direction": "parents",
                        "relationshipType": "parentChild",
                        "minCount": 1
                    }
                ]
            }
        }
    }
}
```

A schema is `GLOBAL` unless the request names a `databaseId`, in which case it can only be bound within that database.

### Updating a schema

An update writes a new version of the schema; assets keep the verdict of their last evaluation until they are evaluated again. Fields omitted from the request carry over from the current version.

```bash
PUT /compliance/schemas/{schemaName}
```

:::warning[System schemas]
A schema registered by the system (`isSystem: true`) is updated or deleted only by the system user.
:::

### Deleting a schema

A schema that no database or asset is bound to can be deleted through the API or the CLI (`vamscli compliance schema delete`); every version is removed and a `schema_deleted` entry is written to the audit log. Remove the schema's bindings first — the request is refused while a binding remains.

```bash
DELETE /compliance/schemas/{schemaName}
```

### Sample schemas

#### Coordinate transform validation

Quarantines point cloud assets whose positional accuracy after coordinate transformation is outside tolerance. The pipeline's template tag `expected_crs` is set from `inputParameters`:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "coord-transform-accuracy": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "coord-validate-workflow",
                "pipelineDatabaseId": "GLOBAL",
                "pipelineId": "coord-validate"
            },
            "inputParameters": { "expected_crs": "EPSG:27700" },
            "checks": [
                {
                    "name": "residual_error",
                    "description": "Positional residual under 1 mm",
                    "outputField": "residual_error_mm",
                    "tolerance": { "operator": "lte", "value": 1.0 }
                },
                {
                    "name": "scale_deviation",
                    "description": "Scale factor deviation within 1 ppm",
                    "outputField": "scale_deviation_ppm",
                    "tolerance": { "operator": "lte", "value": 1.0 }
                }
            ]
        }
    }
}
```

#### Metadata completeness with relationship requirements

Quarantines scan assets that lack required metadata, warns when a scan has no control point parent, and records whether related imagery is linked:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "scan-metadata": {
            "ruleType": "metadata",
            "enforcement": "quarantine",
            "metadataSchemaRef": {
                "databaseId": "survey-project",
                "schemaName": "scan-capture-metadata"
            },
            "checks": [
                {
                    "name": "required_fields",
                    "validateRequired": true,
                    "validateTypes": true,
                    "additionalRequiredFields": ["capture_date", "scanner_model", "operator"]
                }
            ]
        },
        "has-control-point": {
            "ruleType": "relationship",
            "enforcement": "warn",
            "checks": [
                {
                    "name": "parent_control_point",
                    "direction": "parents",
                    "relationshipType": "parentChild",
                    "minCount": 1
                }
            ]
        },
        "has-related-imagery": {
            "ruleType": "relationship",
            "enforcement": "inform",
            "checks": [
                {
                    "name": "linked_imagery",
                    "direction": "related",
                    "relationshipType": "related",
                    "minCount": 1
                }
            ]
        }
    }
}
```

#### Combined pipeline and range validation

Checks reconstruction quality with three tolerance operators:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "reconstruction-quality": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "reconstruction-validate",
                "pipelineDatabaseId": "GLOBAL",
                "pipelineId": "reconstruction-metrics"
            },
            "checks": [
                {
                    "name": "point_density",
                    "outputField": "points_per_sqm",
                    "tolerance": { "operator": "gte", "value": 100.0 }
                },
                {
                    "name": "coverage_ratio",
                    "outputField": "coverage_percent",
                    "tolerance": { "operator": "between", "min": 95.0, "max": 100.0 }
                },
                {
                    "name": "noise_level",
                    "outputField": "noise_sigma_mm",
                    "tolerance": { "operator": "lte", "value": 2.5 }
                }
            ]
        }
    }
}
```

#### Using an existing workflow with the derived measurements

Any workflow can back a pipeline rule without changes to its pipelines. When the pipeline writes no `compliance-output.json`, two measurements are derived from the execution itself: `execution_success` (`1.0` on success, `0.0` otherwise) and `processing_duration_seconds`. This schema requires the Coordinate Transform workflow to succeed within 60 seconds:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "coord-transform-execution": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "coordinate-transform",
                "pipelineDatabaseId": "GLOBAL",
                "pipelineId": "coordinate-transform",
                "templateId": "coordinate-transform-wgs84-to-osgb36-laz"
            },
            "checks": [
                {
                    "name": "must_succeed",
                    "description": "Coordinate transform completes successfully",
                    "outputField": "execution_success",
                    "tolerance": { "operator": "gte", "value": 1.0 }
                },
                {
                    "name": "time_budget",
                    "description": "Completes within 60 seconds",
                    "outputField": "processing_duration_seconds",
                    "tolerance": { "operator": "lte", "value": 60.0 }
                }
            ]
        }
    }
}
```

:::tip[Domain-specific measurements]
For measurements of your own — geometric accuracy, noise levels, coverage ratios — the pipeline writes a `compliance-output.json` file under its results output prefix. The file format and the `write_compliance_output` helper are described in [Compliance (Concepts) — Pipeline output contract](../concepts/compliance.md#pipeline-output-contract).
:::

---

## Evaluating assets

### Single asset evaluation

1. Navigate to the asset detail view.
2. Open the **Compliance** tab.
3. Choose **Evaluate Now**.

Metadata and relationship rules complete at once. A pipeline rule launches a workflow execution — visible among the workflow's executions — and the asset stays `pending_evaluation` until the execution completes and its measurements are checked. If the asset has child assets linked by `parentChild` relationships, a cascade is opened in `pending_approval` so the children can be re-evaluated.

The same evaluation is started with the API:

```bash
POST /compliance/evaluate/{databaseId}/{assetId}
```

### Schema sweep

A sweep evaluates every asset bound to a schema — for example after registering or updating the schema.

1. Navigate to **Compliance > Compliance Schemas** in the left sidebar.
2. Locate the schema and choose **Sweep** in the Actions column.

A sweep evaluates up to 200 assets per request and reports how many remain; run it again to reach the rest.

```bash
POST /compliance/sweep/{schemaName}
```

---

## Quarantine

An asset that fails a rule with `quarantine` enforcement enters the `quarantined` state and appears in the quarantine list. Whether a quarantined asset can still be downloaded depends on the deployment's `app.compliance.quarantineBlocksDownload` setting; the asset's subscribers are notified either way.

### Viewing quarantined assets

1. Navigate to **Compliance > Quarantine** in the left sidebar.
2. The page lists every quarantined asset you may read with its Asset Name, Asset ID and Database ID.

### Releasing from quarantine

1. In the quarantine list, locate the asset you want to release.
2. Choose **Release**.
3. The asset state changes from `quarantined` to `compliant`.

:::tip[Release by re-evaluation]
Fixing the underlying issue — adding the missing metadata, creating the required parent link — and evaluating the asset again clears the quarantine when every `quarantine`-level rule passes. No manual release is needed in that case.
:::

### Granting an exception

If an asset must remain available despite the failure:

1. In the quarantine list, locate the asset.
2. Choose **Exception**.
3. Enter the reason for the exception in the confirmation dialog.
4. Choose **Confirm**.

The asset's compliance state changes to `compliant`, and the reason, the grantor and the time are recorded on the asset's compliance record and in the audit log.

:::note
An exception holds until the asset is evaluated again; a later evaluation that fails a `quarantine`-level rule quarantines the asset once more.
:::

---

## Cascade approvals

A cascade re-evaluates the downstream assets of a parent — every descendant linked through `parentChild` relationships, parents before children. Cascades opened after an evaluation wait for approval.

### Reviewing pending cascades

1. Navigate to **Compliance > Cascade Approvals** in the left sidebar.
2. The page lists cascades in `pending_approval` with the asset that triggered them, the reason, and the approval timeout timestamp recorded 24 hours after creation.

### Approving a cascade

1. Locate the cascade in the list.
2. Choose **Approve**.
3. Enter an optional reason.
4. The downstream assets are evaluated in dependency order and the cascade completes.

### Rejecting a cascade

1. Locate the cascade in the list.
2. Choose **Reject**.
3. Enter an optional reason.
4. The cascade is aborted.

A cascade can also be created directly — `POST /compliance/cascades` with the parent's `databaseId` and `assetId` — and executes at once when created with `requireApproval: false`.

---

## Audit log

The compliance audit log records every compliance action across the deployment.

### Viewing the audit log

1. Navigate to **Compliance > Audit Log** in the left sidebar.
2. Use the **Event Type** filter to narrow the list (for example, only `quarantine_released` events).
3. Entries are ordered newest first.

### Event types

| Event type                     | Description                                                         |
| ------------------------------ | ------------------------------------------------------------------- |
| `compliance_check`             | An asset was evaluated against a schema                             |
| `quarantine_released`          | An asset left quarantine, by release or by a passing re-evaluation  |
| `exception_granted`            | An exception was granted to a quarantined asset                     |
| `schema_bound_to_database`     | A schema was bound to a database                                    |
| `schema_unbound_from_database` | A database's schema binding was removed                             |
| `schema_bound_to_asset`        | A schema was bound directly to an asset                             |
| `schema_unbound_from_asset`    | An asset-level schema override was removed                          |
| `schema_deleted`               | A schema was deleted                                                |
| `cascade_triggered`            | A cascade was created through the API                               |
| `cascade_auto_triggered`       | A cascade was opened after the evaluation of an asset with children |
| `cascade_approved`             | A pending cascade was approved and executed                         |
| `cascade_rejected`             | A pending cascade was rejected                                      |
| `cascade_completed`            | A cascade finished evaluating its downstream assets                 |

---

## Permissions

Compliance operations are governed by the same two-tier access control as the rest of VAMS. Both tiers must allow a request.

### Tier 1: route access

Controls which users can call the compliance API and open the compliance pages. Grant the API routes with an `api` constraint and the pages with a `web` constraint, both matching `/compliance`:

```json
{
    "objectType": "api",
    "criteriaOr": [{ "field": "route__path", "operator": "starts_with", "value": "/compliance" }],
    "groupPermissions": [
        { "permission": "GET", "permissionType": "allow" },
        { "permission": "POST", "permissionType": "allow" }
    ]
}
```

### Tier 2: object-level access

Controls which compliance resources a user can act on. Three object types govern compliance operations:

| Object type            | Controls                                              | Constraint fields               | Use case                                                       |
| ---------------------- | ----------------------------------------------------- | ------------------------------- | -------------------------------------------------------------- |
| `complianceSchema`     | Schema registration, update, deletion, binding, sweep | `complianceSchemaName`          | Restrict which schemas a user can view or manage               |
| `complianceEvaluation` | Evaluate, state, quarantine, audit                    | `databaseId`, `complianceState` | Scope evaluations and quarantine actions to specific databases |
| `complianceCascade`    | Cascade create, approve, reject                       | `cascadeId`                     | Control who can approve cascade propagations                   |

### Default admin access

The built-in admin role holds full access (GET, PUT, POST, DELETE) to all three compliance object types. No additional configuration is needed for administrators.

### Permission templates

VAMS includes two permission templates for compliance roles:

| Template            | File                       | Description                                                                                                                                                                                  |
| ------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compliance Admin    | `compliance-admin.json`    | Full schema management, evaluation and quarantine actions within one database, cascade approval, plus the compliance web and API routes and read access to the database's assets.            |
| Compliance Readonly | `compliance-readonly.json` | View-only access: schemas, evaluation results and compliance state within one database, the quarantine list, pending cascades and the audit log. Cannot trigger evaluations or change state. |

Both templates take `DATABASE_ID` and `ROLE_NAME` variables. Apply them through **Admin > Permissions > Constraints > Import Template** or the CLI.

### Example: database-scoped compliance admin

1. Apply the `compliance-admin` template with `DATABASE_ID` set to the target database.
2. Assign the resulting role to the user.

The user manages schemas across the deployment but evaluates assets and acts on quarantine only within the scoped database.

### Example: read-only compliance viewer

1. Apply the `compliance-readonly` template with `DATABASE_ID` set to the target database.
2. Assign the resulting role to the user.

The user views schemas, evaluation history, quarantine status and the audit log but cannot evaluate, release, grant exceptions or approve cascades.

:::tip[Troubleshooting 403 errors]
If a user receives **Not Authorized** on compliance pages or calls, verify they hold both:

1. `api` and `web` constraints allowing the `/compliance` routes (Tier 1)
2. A `complianceSchema`, `complianceEvaluation`, or `complianceCascade` constraint matching the resource (Tier 2)

Missing either tier results in a 403 response.
:::

---

## Related topics

-   [Compliance (Concepts)](../concepts/compliance.md) -- rule format, pipeline output contract, states, cascades and the authorization model
-   [Compliance API](../api/compliance.md) -- endpoint reference
-   [CLI: Compliance commands](../cli/commands/compliance.md) -- `vamscli compliance` command group
-   [Metadata and Schemas](../concepts/metadata-and-schemas.md) -- metadata schemas referenced by `metadata` rules
-   [Permissions Model](../concepts/permissions-model.md) -- access control for compliance operations
-   [Databases](../concepts/databases.md) -- database configuration and schema binding
