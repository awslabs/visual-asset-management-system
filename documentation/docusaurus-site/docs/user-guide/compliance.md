# Compliance

Compliance management enables you to enforce structural and content standards on assets using schema-driven evaluation. When the Federated Model Management (FMM) feature is enabled, you can register compliance schemas, evaluate assets against those schemas, quarantine non-compliant assets, and propagate compliance actions through asset relationships.

:::info[Feature availability]
Compliance is only available when FMM is enabled in your VAMS deployment configuration. If you do not see the Compliance navigation items, contact your administrator.
:::

---

## Viewing compliance status

### Asset compliance tab

Each asset detail view includes a **Compliance** tab showing:

- **Current state** -- the compliance state of the asset displayed as a colored indicator: green (compliant), yellow (non-compliant/warning), red (quarantined), or blue (pending evaluation).
- **Schema binding** -- which schema governs the asset and whether it was inherited from the database or set directly.
- **Evaluate Now** button -- triggers immediate evaluation and cascade propagation to child assets.
- **Evaluation history** -- a table of past evaluations with timestamps, verdicts, and violations.

:::info[Evaluation history]
The evaluation history table displays the result of each evaluation, including a violations list showing which rules failed. If the table appears empty after evaluation, verify that the compliance schema uses the `vams-rules-v1` format.
:::

### Database compliance overview

1. Navigate to **Databases** in the left sidebar.
2. In the databases table, choose the **Compliance** link for the database you want to inspect.
3. The compliance overview page shows:
   - A summary row with counts for each state: Compliant, Non-Compliant, Quarantined, Pending Evaluation, and Unknown.
   - A compliance rate percentage (compliant divided by total tracked).
   - A table of individual assets showing Asset Name, Asset ID, compliance state, schema name, and last evaluation time.

---

## Binding schemas to databases

### Assigning a compliance schema

1. Navigate to **Databases** and choose the database you want to configure.
2. Choose **Edit** to open the database editor.
3. In the **Compliance Schema** dropdown, select the schema you want to bind.
4. Choose **Update Database**.

All assets in the database are marked `pending_evaluation`. Use **Evaluate Now** on individual assets or **Sweep** on the schema to trigger evaluation.

### Removing a compliance schema

1. Navigate to **Databases** and choose the database.
2. Choose **Edit** to open the database editor.
3. In the **Compliance Schema** dropdown, select **None (no compliance schema)**.
4. Choose **Update Database**.

All database-inherited compliance records are removed. The compliance overview will show "No Compliance Schema Bound" until a new schema is assigned.

:::warning[Irreversible action]
Removing a compliance schema deletes all compliance state history for assets that inherited from the database. Assets with explicit asset-level schema overrides are not affected.
:::

---

## Managing schemas

Compliance schemas define the rules that assets are evaluated against. VAMS supports two schema formats: the recommended `vams-rules-v1` format for rule-based evaluation with enforcement levels, and the legacy JSON Schema (draft-07) format for simple metadata validation.

### Registering a schema (vams-rules-v1)

The `vams-rules-v1` format enables pipeline-connected evaluation, metadata validation, and relationship checks — each with configurable enforcement levels.

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
                    "workflowId": "coord-validate-workflow"
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
                    "databaseId": "my-database",
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

### Registering a schema (legacy JSON Schema)

For simple metadata structure validation, schemas can use JSON Schema (draft-07) format:

```bash
POST /compliance/schemas
```

```json
{
    "schemaName": "cad-model-standard",
    "description": "Standard for CAD model metadata requirements",
    "schemaBody": {
        "type": "object",
        "properties": {
            "format": { "type": "string", "enum": ["step", "iges", "stl"] },
            "units": { "type": "string" },
            "tolerance": { "type": "number", "minimum": 0 }
        },
        "required": ["format", "units"]
    }
}
```

:::tip[Schema format selection]
Use `vams-rules-v1` for new schemas — it supports pipeline-connected validation, enforcement levels, and tolerance comparisons. Legacy JSON Schema is retained for backward compatibility but does not support automated pipeline evaluation or graduated enforcement.
:::

### Updating a schema

Updates create a new version of the schema. Assets evaluated against the previous version retain their result until re-evaluated.

```bash
PUT /compliance/schemas/{schemaName}
```

:::warning[System schemas]
Schemas registered by the system (marked `isSystem: true`) cannot be modified by regular users.
:::

### Sample schemas

#### Coordinate transform validation

Validates that point cloud assets meet positional accuracy requirements after coordinate transformation:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "coord-transform-accuracy": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "coord-validate-workflow"
            },
            "inputParameters": { "expected_crs": "EPSG:27700" },
            "checks": [
                {
                    "name": "residual_error",
                    "description": "Positional residual must be under 1mm",
                    "outputField": "residual_error_mm",
                    "tolerance": { "operator": "lte", "value": 1.0 }
                },
                {
                    "name": "scale_deviation",
                    "description": "Scale factor deviation within 1ppm",
                    "outputField": "scale_deviation_ppm",
                    "tolerance": { "operator": "lte", "value": 1.0 }
                }
            ]
        }
    }
}
```

#### Metadata completeness with relationship requirements

Validates that scan assets have required metadata fields and are linked to a control point parent:

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

Validates reconstruction quality with multiple tolerance types:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "reconstruction-quality": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "reconstruction-validate"
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

#### Using an existing pipeline with default metrics

Any existing VAMS workflow can be used for compliance without modifying its containers. When no custom `compliance-output.json` is produced, the pipeline callback automatically provides two default metrics: `execution_success` (1.0 on success, 0.0 on failure) and `processing_duration_seconds` (wall-clock execution time).

This example uses the Coordinate Transform workflow to validate that processing completes successfully within a 60-second time budget:

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
                "templateId": "coordinate-transform-wgs84-to-osgb36-laz"
            },
            "checks": [
                {
                    "name": "must_succeed",
                    "description": "Coordinate transform must complete successfully",
                    "outputField": "execution_success",
                    "tolerance": { "operator": "gte", "value": 1.0 }
                },
                {
                    "name": "time_budget",
                    "description": "Must complete within 60 seconds",
                    "outputField": "processing_duration_seconds",
                    "tolerance": { "operator": "lte", "value": 60.0 }
                }
            ]
        }
    }
}
```

:::tip[Default metrics — no container changes required]
Default metrics make it possible to enforce compliance on any pipeline without writing a custom compliance output file. Use `execution_success` to ensure a workflow completes without error, and `processing_duration_seconds` to enforce time budgets. For domain-specific measurements (geometric accuracy, noise levels, coverage ratios), implement a custom `compliance-output.json` in the pipeline container — see [Compliance (Concepts)](../concepts/compliance.md#pipeline-compliance-output-contract).
:::

---

## Evaluating assets

### Single asset evaluation

1. Navigate to the asset detail view.
2. Open the **Compliance** tab.
3. Choose **Evaluate Now**.

The evaluation runs synchronously for metadata and relationship rules. If the asset has child assets linked via `parentChild` relationships, a cascade is automatically created in `pending_approval` state to propagate re-evaluation to children.

Alternatively, use the API:

```bash
POST /compliance/evaluate/{databaseId}/{assetId}
```

### Schema sweep

A sweep triggers evaluation for all assets governed by a specific schema. This is useful after registering or updating a schema.

1. Navigate to **Compliance > Schemas** in the left sidebar.
2. Locate the schema and choose **Sweep** in the Actions column.

Alternatively, use the API:

```bash
POST /compliance/sweep/{schemaName}
```

---

## Quarantine

When an asset fails a rule with `quarantine` enforcement level, it enters the quarantined state and appears in the quarantine list.

### Viewing quarantined assets

1. Navigate to **Compliance > Quarantine** in the left sidebar.
2. The page lists all currently quarantined assets with their Asset Name, Asset ID, Database ID, and quarantine reason.

### Releasing from quarantine

1. In the quarantine list, locate the asset you want to release.
2. Choose **Release**.
3. The asset state changes from `quarantined` to `compliant`.

:::tip[Auto-release]
If you fix the underlying compliance issue (for example, adding missing metadata or creating a required parent link) and re-evaluate the asset, quarantine is automatically cleared when all rules pass. You do not need to manually release in this case.
:::

### Granting an exception

If an asset must remain available despite non-compliance:

1. In the quarantine list, locate the asset.
2. Choose **Exception**.
3. Enter the reason for the exception in the confirmation dialog.
4. Choose **Confirm**.

The asset's compliance state changes to `compliant` and the exception is recorded in the audit log with the reason and the user who granted it.

:::note
Exceptions are visible in the database compliance overview. The asset will show as compliant until the next re-evaluation triggers.
:::

---

## Cascade approvals

Cascades propagate compliance changes through asset relationships. When a cascade requires approval, it appears in the approval queue.

### Reviewing pending cascades

1. Navigate to **Compliance > Cascade Approvals** in the left sidebar.
2. The page lists cascades in `pending_approval` state with their trigger details and timeout.

### Approving a cascade

1. Locate the cascade in the list.
2. Choose **Approve**.
3. Enter an optional reason.
4. The cascade proceeds with execution.

### Rejecting a cascade

1. Locate the cascade in the list.
2. Choose **Reject**.
3. Enter an optional reason.
4. The cascade is aborted.

:::warning[Approval timeout]
Cascades have a 24-hour approval window. If not approved within that period, the cascade expires automatically.
:::

---

## Audit log

The compliance audit log records every compliance action across the system.

### Viewing the audit log

1. Navigate to **Compliance > Audit Log** in the left sidebar.
2. Use the **Event Type** filter to narrow results (e.g., only show `quarantine_released` events).
3. The log shows entries ordered by most recent first.

### Event types

| Event type                    | Description                                            |
| ----------------------------- | ------------------------------------------------------ |
| `compliance_check`            | An asset was evaluated against a schema                |
| `state_change`                | An asset's compliance state changed                    |
| `quarantine_released`         | An asset was released from quarantine                  |
| `exception_granted`           | A compliance exception was granted                     |
| `cascade_triggered`           | A cascade propagation was initiated                    |
| `cascade_approved`            | A pending cascade was approved for execution           |
| `cascade_rejected`            | A pending cascade was rejected                         |
| `schema_registered`           | A new schema was registered                            |
| `schema_updated`              | An existing schema was updated (new version)           |
| `schema_bound_to_database`    | A schema was bound to a database                       |
| `schema_unbound_from_database`| A schema binding was removed from a database           |
| `schema_bound_to_asset`       | A schema was bound directly to an asset                |
| `schema_unbound_from_asset`   | An asset-level schema override was removed             |

---

## Permissions

Compliance operations are governed by the same two-tier role-based access control system as other VAMS features. Both tiers must allow access for an operation to succeed.

### Tier 1: API route access

Controls which users can call compliance API endpoints. To grant access, create a constraint with object type `api` that matches `/compliance` routes:

```json
{
    "objectType": "api",
    "criteriaOr": [
        { "field": "route__path", "operator": "starts_with", "value": "/compliance" }
    ],
    "groupPermissions": [
        { "permission": "GET", "permissionType": "allow" },
        { "permission": "POST", "permissionType": "allow" }
    ]
}
```

### Tier 2: Object-level access

Controls which specific compliance resources a user can access. Three object types govern compliance operations:

| Object Type | Controls | Constraint Fields | Use Case |
| --- | --- | --- | --- |
| `complianceSchema` | Schema CRUD, binding, sweep | `complianceSchemaName` | Restrict which schemas a user can view or manage |
| `complianceEvaluation` | Evaluate, quarantine, audit | `databaseId`, `complianceState` | Scope evaluations and quarantine actions to specific databases |
| `complianceCascade` | Cascade approve/reject | `cascadeId` | Control who can approve cascade propagations |

### Default admin access

The built-in admin role automatically receives full access (GET, PUT, POST, DELETE) to all three compliance object types with `contains .*` criteria (matches all values). No additional configuration is needed for administrators.

### Permission templates

VAMS includes two pre-built permission templates for compliance roles:

| Template | File | Description |
| --- | --- | --- |
| Compliance Admin | `compliance-admin.json` | Full management access: create/update schemas, trigger evaluations, release quarantine, approve cascades. Scoped to a specific database. |
| Compliance Readonly | `compliance-readonly.json` | View-only access: view schemas, evaluation results, quarantine list, cascade status, and audit logs. Cannot trigger evaluations or modify state. |

Both templates accept `DATABASE_ID` and `ROLE_NAME` variables. Apply them via **Admin > Permissions > Constraints > Import Template** or the CLI.

### Example: Database-scoped compliance admin

To grant a user full compliance management for a specific database:

1. Apply the `compliance-admin` template with `DATABASE_ID` set to your target database.
2. Assign the resulting role to the user.

The user can manage schemas globally but can only trigger evaluations and view compliance state within the scoped database.

### Example: Read-only compliance viewer

To grant a user view-only compliance access:

1. Apply the `compliance-readonly` template with `DATABASE_ID` set to your target database.
2. Assign the resulting role to the user.

The user can view schemas, evaluation history, quarantine status, and audit logs but cannot trigger evaluations, release quarantines, or approve cascades.

:::tip[Troubleshooting 403 errors]
If a user receives "Not Authorized" on compliance pages, verify they have both:
1. An `api` constraint allowing `/compliance` routes (Tier 1)
2. A `complianceSchema`, `complianceEvaluation`, or `complianceCascade` constraint matching the resource (Tier 2)

Missing either tier results in a 403 response.
:::

---

## Related topics

-   [Compliance (Concepts)](../concepts/compliance.md) -- architecture, schema format reference, tolerance operators, and API endpoints
-   [Metadata and Schemas](../concepts/metadata-and-schemas.md) -- metadata schemas referenced by `metadata` rule types
-   [Permissions Model](../concepts/permissions-model.md) -- role-based access control for compliance operations
-   [Databases](../concepts/databases.md) -- database-level configuration and schema binding
