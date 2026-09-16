# Compliance API

The Compliance API registers compliance schemas, binds them to databases and assets, evaluates assets against them, and manages the quarantine, cascade and audit records that evaluation produces. For the concepts behind these endpoints — rule types, enforcement levels, compliance states and the pipeline output contract — see [Compliance](../concepts/compliance.md).

All compliance endpoints live under the `/compliance` prefix. Identifiers follow the VAMS conventions: a `databaseId` or `schemaName` is 3-63 characters of letters, digits, hyphens and underscores (`GLOBAL` is accepted wherever a database is named), and an `assetId` follows the asset identifier rules.

:::info[Authorization]
All compliance endpoints require a valid credential in the `Authorization` header and are subject to two-tier authorization: API-level access to the `/compliance/*` route is checked first, followed by object-level Casbin enforcement on one of three object types. Schema, binding and sweep operations are enforced on a `complianceSchema` object (`complianceSchemaName`); evaluation, state, quarantine and audit operations on a `complianceEvaluation` object (`databaseId`, `complianceState`); cascade operations on a `complianceCascade` object (`cascadeId`). Operations that reach into a database or asset are additionally enforced on that target: binding, unbinding and reading a database's bindings on the `database` object, binding and unbinding an asset override on the `asset` object, a sweep on the `complianceEvaluation` object of each bound database (assets in databases the caller may not evaluate are skipped and counted), and creating, approving, rejecting or reading a cascade on the `complianceEvaluation` object of the triggering asset's database. The object action mirrors the HTTP method. Listings return only the items the caller may `GET`: the global audit trail and the pending-cascade listing return only rows whose database the caller may read, so a page can be empty while `NextToken` is present.
:::

:::note[Error responses]
A compliance handler reports every rejection it makes itself as `400` with a short message — an invalid identifier, a missing schema, database, asset or cascade, a schema that is still bound, an asset that is not quarantined, or a cascade that is not pending approval. `403` is returned when either authorization tier denies the request, and `500` for an unexpected error.
:::

---

## List compliance schemas

Retrieves the latest version of every registered schema the caller may read.

```
GET /compliance/schemas
```

### Query parameters

| Parameter    | Type   | Required | Description                                                                                            |
| ------------ | ------ | -------- | ------------------------------------------------------------------------------------------------------ |
| `databaseId` | string | No       | Restrict the listing to `GLOBAL` schemas plus the schemas scoped to this database (`GLOBAL` accepted). |

### Response

```json
{
    "schemas": [
        {
            "schemaName": "survey-compliance",
            "databaseId": "GLOBAL",
            "description": "Compliance rules for survey data assets",
            "schemaBody": {
                "schemaFormat": "vams-rules-v1",
                "rules": { "...": "..." }
            },
            "schemaFormat": "vams-rules-v1",
            "version": 2,
            "createdAt": "2026-03-15T10:30:00+00:00",
            "isSystem": false
        }
    ]
}
```

The listing is not paged; a schema appears once, at its highest version. The top-level `schemaFormat` is derived from the body: `vams-rules-v1`, or `legacy` for a row whose body is not a `vams-rules-v1` document. A legacy schema cannot be bound (see [Bind a schema to a database](#bind-a-schema-to-a-database)), updated or evaluated; it is listed so that it can be found and [deleted](#delete-a-compliance-schema).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid `databaseId`  |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Register a compliance schema

Registers a schema body under a name. Registering a name that already exists writes the next version of that schema; the highest version is the one evaluation reads.

```
POST /compliance/schemas
```

### Request body

| Field         | Type    | Required | Description                                                                                                                                                                                                                                       |
| ------------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemaName`  | string  | Yes      | Schema name (3-63 chars, alphanumeric, hyphens, underscores)                                                                                                                                                                                      |
| `schemaBody`  | object  | Yes      | The schema definition: a `vams-rules-v1` document (`schemaFormat` and `rules`) as described in [Compliance — The vams-rules-v1 format](../concepts/compliance.md#the-vams-rules-v1-format). Any other body is rejected with `400` (see the note). |
| `description` | string  | No       | Human-readable description (up to 1024 characters)                                                                                                                                                                                                |
| `databaseId`  | string  | No       | Database to scope the schema to (default `GLOBAL`). A database-scoped schema can only be bound within that database.                                                                                                                              |
| `isSystem`    | boolean | No       | Marks a system schema. Honoured only when the caller is the system user; any other caller's value is ignored.                                                                                                                                     |

```json
{
    "schemaName": "survey-compliance",
    "description": "Compliance rules for survey data assets",
    "schemaBody": {
        "schemaFormat": "vams-rules-v1",
        "rules": {
            "required-metadata": {
                "ruleType": "metadata",
                "enforcement": "quarantine",
                "metadataSchemaRef": { "databaseId": "GLOBAL", "schemaName": "defaultAsset" },
                "checks": [
                    { "name": "required_fields", "validateRequired": true, "validateTypes": true }
                ]
            },
            "converts-to-glb": {
                "ruleType": "pipeline",
                "enforcement": "quarantine",
                "pipelineRef": {
                    "databaseId": "GLOBAL",
                    "workflowId": "conversion-3d-basic",
                    "pipelineDatabaseId": "GLOBAL",
                    "pipelineId": "conversion-3d-basic",
                    "templateId": "convert-to-glb"
                },
                "inputFiles": {
                    "mode": "matching",
                    "filter": ["*.stl", "*.obj", "*.ply", "*.gltf", "*.xyz"]
                },
                "checks": [
                    {
                        "name": "conversion-succeeds",
                        "outputField": "execution_success",
                        "tolerance": { "operator": "eq", "value": 1 }
                    }
                ]
            }
        }
    }
}
```

:::note[Only vams-rules-v1 documents are accepted]
`schemaBody` must be a `vams-rules-v1` document: an object with `schemaFormat` set to `vams-rules-v1` and at least one rule under `rules`. A plain JSON Schema, a template file's envelope (`metadata`, `schemaName`, `schemaBody`) or any other object is rejected with `400` and the message `schemaBody must be a vams-rules-v1 document`; the specific validation failure is written to the handler's log. A pipeline rule's `inputFiles` selects which of the asset's files the workflow receives (`matching`, `wholeAsset` or `explicit`); the modes and the arity rule are described in [Pipeline rules](../concepts/compliance.md#pipeline-rules).
:::

### Response

```json
{
    "message": "Schema 'survey-compliance' registered as v1",
    "schemaName": "survey-compliance",
    "databaseId": "GLOBAL",
    "internalVersion": 1
}
```

### Error responses

| Status | Description                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, a body that is not a valid `vams-rules-v1` document, or the scoping database is absent |
| `403`  | Not authorized                                                                                             |
| `500`  | Internal server error                                                                                      |

---

## Get a compliance schema

Retrieves the latest version of a schema.

```
GET /compliance/schemas/{schemaName}
```

### Path parameters

| Parameter    | Type   | Required | Description |
| ------------ | ------ | -------- | ----------- |
| `schemaName` | string | Yes      | Schema name |

### Response

Same shape as an entry of [List compliance schemas](#list-compliance-schemas).

### Error responses

| Status | Description                                        |
| ------ | -------------------------------------------------- |
| `400`  | Invalid `schemaName`, or the schema does not exist |
| `403`  | Not authorized                                     |
| `500`  | Internal server error                              |

---

## Update a compliance schema

Writes the next version of a schema. Every body field is optional; a field that is omitted carries over from the latest version, so a request may change only the description. A replacement `schemaBody` is validated the same way as on registration and must be a `vams-rules-v1` document. Assets bound to the schema keep their state until their next evaluation, and that evaluation supersedes any exception granted against the previous version.

```
PUT /compliance/schemas/{schemaName}
```

### Request body

| Field         | Type   | Required | Description                                                |
| ------------- | ------ | -------- | ---------------------------------------------------------- |
| `schemaBody`  | object | No       | Replacement schema definition (a `vams-rules-v1` document) |
| `description` | string | No       | Replacement description                                    |
| `databaseId`  | string | No       | Replacement database scope, or `GLOBAL`                    |

A system schema (`isSystem: true`) is updated or deleted only by the system user; any other caller receives `400`.

### Response

```json
{
    "message": "Schema 'survey-compliance' registered as v2",
    "schemaName": "survey-compliance",
    "databaseId": "GLOBAL",
    "internalVersion": 2
}
```

### Error responses

| Status | Description                                                                                                 |
| ------ | ----------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters or body, the schema or scoping database does not exist, or the schema is a system schema |
| `403`  | Not authorized                                                                                              |
| `500`  | Internal server error                                                                                       |

---

## Delete a compliance schema

Deletes every version of a schema that no database or asset is bound to, and records a `schema_deleted` audit entry. Remove the schema's bindings first — the delete is refused while any database or asset compliance record still names it.

```
DELETE /compliance/schemas/{schemaName}
```

### Path parameters

| Parameter    | Type   | Required | Description |
| ------------ | ------ | -------- | ----------- |
| `schemaName` | string | Yes      | Schema name |

### Response

```json
{
    "message": "Schema deleted",
    "schemaName": "survey-compliance",
    "versionsDeleted": 2
}
```

### Error responses

| Status | Description                                                                                    |
| ------ | ---------------------------------------------------------------------------------------------- |
| `400`  | Invalid `schemaName`, the schema does not exist, or the schema is bound to a database or asset |
| `403`  | Not authorized                                                                                 |
| `500`  | Internal server error                                                                          |

---

## Get the schema bindings of a database

Returns the schema bound to a database, whether automatic evaluation is on for it, and every asset in the database that carries its own schema override.

```
GET /compliance/bind/{databaseId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                    |
| --------------- | ------ | -------- | ------- | ---------------------------------------------------------------------------------------------- |
| `maxItems`      | number | No       | `100`   | Asset overrides per page. A larger value is reduced to 500; the remainder follows `NextToken`. |
| `startingToken` | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                     |

### Response

```json
{
    "databaseId": "survey-project",
    "databaseSchema": "survey-compliance",
    "complianceAutoEval": true,
    "assetOverrides": [
        {
            "assetId": "x1a2b3c4-scan-0042",
            "schemaName": "strict-survey-compliance",
            "complianceState": "compliant"
        }
    ],
    "assetOverrideCount": 1
}
```

`databaseSchema` is `null` when the database has no binding. `assetOverrideCount` counts every asset-level override of the database; `assetOverrides` is one page of them in `assetId` order, and `NextToken` is present while more remain. The request is authorized on the database and on the bound schema's name (an empty name when there is none).

### Error responses

| Status | Description                                                              |
| ------ | ------------------------------------------------------------------------ |
| `400`  | Invalid `databaseId` or pagination token, or the database does not exist |
| `403`  | Not authorized                                                           |
| `500`  | Internal server error                                                    |

---

## Bind a schema to a database

Binds a `GLOBAL` or database-scoped schema to a database. Every asset in the database that has no asset-level override becomes `pending_evaluation` under the schema; assets with an override are left as they are. Run a [sweep](#sweep-the-assets-bound-to-a-schema) or evaluate assets individually to produce verdicts.

```
PUT /compliance/bind/{databaseId}
```

### Request body

| Field                | Type    | Required | Description                                                                                      |
| -------------------- | ------- | -------- | ------------------------------------------------------------------------------------------------ |
| `schemaName`         | string  | Yes      | A `GLOBAL` schema or a schema scoped to this database                                            |
| `complianceAutoEval` | boolean | No       | Evaluate assets of this database automatically when they are created or updated (default `true`) |

```json
{ "schemaName": "survey-compliance", "complianceAutoEval": true }
```

### Response

```json
{
    "message": "Schema 'survey-compliance' bound to database 'survey-project'",
    "schemaName": "survey-compliance",
    "databaseId": "survey-project",
    "assetsPendingEvaluation": 128
}
```

### Error responses

| Status | Description                                                                                                                                                                                                                        |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the schema or database does not exist, the schema is scoped to another database, or the schema is a `legacy` row — its body is not a `vams-rules-v1` document (`Schema body must be a vams-rules-v1 document`) |
| `403`  | Not authorized                                                                                                                                                                                                                     |
| `500`  | Internal server error                                                                                                                                                                                                              |

---

## Remove the schema binding of a database

Removes the database binding and deletes the compliance records of the assets that inherited it. Assets with an asset-level override keep their records.

```
DELETE /compliance/bind/{databaseId}
```

### Response

```json
{
    "message": "Schema binding removed from database 'survey-project'",
    "databaseId": "survey-project",
    "previousSchema": "survey-compliance",
    "removedComplianceRecords": 128
}
```

When the database has no binding the response is `200` with only `message` and `databaseId`.

### Error responses

| Status | Description                                          |
| ------ | ---------------------------------------------------- |
| `400`  | Invalid `databaseId`, or the database does not exist |
| `403`  | Not authorized                                       |
| `500`  | Internal server error                                |

---

## Bind a schema to an asset

Binds a schema directly to one asset as an override of the database binding. The asset becomes `pending_evaluation` under the schema with `schemaSource` `asset`.

```
PUT /compliance/bind/{databaseId}/{assetId}
```

### Request body

| Field        | Type   | Required | Description                                                  |
| ------------ | ------ | -------- | ------------------------------------------------------------ |
| `schemaName` | string | Yes      | A `GLOBAL` schema or a schema scoped to the asset's database |

### Response

```json
{
    "message": "Schema 'strict-survey-compliance' bound to asset",
    "databaseId": "survey-project",
    "assetId": "x1a2b3c4-scan-0042",
    "schemaName": "strict-survey-compliance",
    "schemaSource": "asset"
}
```

### Error responses

| Status | Description                                                                                                                                                                                                                     |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the schema or asset does not exist, the schema is scoped to another database, or the schema is a `legacy` row — its body is not a `vams-rules-v1` document (`Schema body must be a vams-rules-v1 document`) |
| `403`  | Not authorized                                                                                                                                                                                                                  |
| `500`  | Internal server error                                                                                                                                                                                                           |

---

## Remove the schema override of an asset

Removes an asset-level override. When the database has a binding the asset falls back to it as `pending_evaluation`; otherwise the asset's compliance record is deleted and the asset leaves compliance tracking.

```
DELETE /compliance/bind/{databaseId}/{assetId}
```

### Response

```json
{
    "message": "Asset schema override removed",
    "databaseId": "survey-project",
    "assetId": "x1a2b3c4-scan-0042",
    "fallbackSchema": "survey-compliance",
    "schemaSource": "database"
}
```

`fallbackSchema` and `schemaSource` are `null` when the database has no binding. When the asset has no override the response is `200` with only `message`, `databaseId` and `assetId`.

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid parameters    |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Evaluate an asset

Evaluates an asset against the named schema, or against its bound schema when the body names none. Naming a schema evaluates against it without changing the asset's binding; the evaluation record carries the `schemaName` used. Metadata and relationship rules complete within the request. Each pipeline rule selects the asset files its `inputFiles` describes, launches a workflow execution and the evaluation stays `pending_pipeline` (asset state `pending_evaluation`) until the execution completes — see [Pipeline rules](../concepts/compliance.md#pipeline-rules). When the asset has children linked by `parentChild` asset links, a cascade awaiting approval is opened after the evaluation, unless one for this asset is already pending approval.

While the asset holds an active exception against the schema version evaluated, the rule results and violations are recorded on the evaluation as computed, the evaluation carries `exceptionApplied: true`, and a failing verdict leaves the asset in the `exception` state rather than quarantining it; an evaluation against another schema or a newer version supersedes the exception and applies its verdict normally (see [Grant an exception](#grant-an-exception-to-a-quarantined-asset)).

A rule whose tooling fails — an input selection that is not accepted by the workflow, does not match exactly one file or names a file the asset does not have, or an execution that cannot be launched — is recorded as a rule result with `status: "error"`. It is not a verdict: `passed` is `false`, but the rule's enforcement does not apply, the verdict is computed from the remaining rules and the response carries `hasRuleErrors: true`. When every rule errored the `verdict` is `error` and the asset's compliance state is left unchanged (`lastEvaluationStatus: "error"` on its record). Either way an `evaluation_error` audit entry names the errored rules. See [Tooling failures](../concepts/compliance.md#tooling-failures).

```
POST /compliance/evaluate/{databaseId}/{assetId}
```

### Request body

| Field        | Type   | Required | Description                                                                             |
| ------------ | ------ | -------- | --------------------------------------------------------------------------------------- |
| `schemaName` | string | No       | Schema to evaluate against; defaults to the bound schema. The binding is left unchanged |

### Response

```json
{
    "message": "Evaluation completed",
    "evaluationId": "3f6c1a2e-0b7d-4f7e-9d1c-5a2b8c9d0e1f",
    "schemaName": "survey-compliance",
    "schemaVersion": 2,
    "verdict": "pending_pipeline",
    "complianceState": "pending_evaluation",
    "ruleResults": [
        {
            "ruleName": "required-metadata",
            "ruleType": "metadata",
            "enforcement": "quarantine",
            "status": "evaluated",
            "passed": true,
            "message": null,
            "measured": null,
            "expected": null
        }
    ],
    "pipelineRulesPending": 1,
    "exceptionApplied": false,
    "hasRuleErrors": false
}
```

`verdict` is one of `compliant`, `non_compliant`, `quarantined`, `pending_pipeline` or `error`; `complianceState` is the asset state the verdict maps to — `exception` when the verdict failed while an exception is active. `schemaVersion` is the version of the schema the evaluation read. `ruleResults` holds the rules that completed in the request, each with a `status` of `evaluated` or `error`, and `pipelineRulesPending` counts the pipeline rules whose execution is awaited. `exceptionApplied` is `true` when an active exception held the asset released, and `hasRuleErrors` is `true` when at least one rule result has `status` `error`.

### Error responses

| Status | Description                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset does not exist, no schema is named or bound, or the evaluation could not run |
| `403`  | Not authorized                                                                                             |
| `500`  | Internal server error                                                                                      |

---

## Sweep the assets bound to a schema

Evaluates every asset whose compliance record is bound to the schema and whose database the caller may evaluate. One call evaluates at most 200 assets; `assetsRemaining` reports how many bound assets were not reached, and repeated calls work through them. Bound assets the caller is not authorized to evaluate are counted in `skipped` and never listed.

```
POST /compliance/sweep/{schemaName}
```

### Response

```json
{
    "message": "Sweep triggered for 2 assets",
    "schemaName": "survey-compliance",
    "assetsTriggered": [
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0042",
            "evaluationId": "3f6c1a2e-0b7d-4f7e-9d1c-5a2b8c9d0e1f",
            "verdict": "compliant"
        },
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0043",
            "evaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "verdict": "quarantined"
        }
    ],
    "skipped": 0,
    "assetsRemaining": 0
}
```

### Error responses

| Status | Description                                        |
| ------ | -------------------------------------------------- |
| `400`  | Invalid `schemaName`, or the schema does not exist |
| `403`  | Not authorized                                     |
| `500`  | Internal server error                              |

---

## List the evaluations of an asset

Retrieves the evaluation history of an asset, newest first, one page per call.

```
GET /compliance/evaluations/{databaseId}/{assetId}
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                |
| --------------- | ------ | -------- | ------- | ------------------------------------------------------------------------------------------ |
| `maxItems`      | number | No       | `50`    | Evaluations per page. A larger value is reduced to 200; the remainder follows `NextToken`. |
| `startingToken` | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                 |

### Response

```json
{
    "evaluations": [
        {
            "evaluationId": "3f6c1a2e-0b7d-4f7e-9d1c-5a2b8c9d0e1f",
            "databaseId:assetId": "survey-project:x1a2b3c4-scan-0042",
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0042",
            "schemaName": "survey-compliance",
            "evaluatedAt": "2026-03-15T10:30:00+00:00",
            "status": "completed",
            "verdict": "compliant",
            "violations": [],
            "ruleResults": "[{\"ruleName\": \"required-metadata\", \"ruleType\": \"metadata\", \"enforcement\": \"quarantine\", \"status\": \"evaluated\", \"passed\": true}]",
            "exceptionApplied": false,
            "hasRuleErrors": false,
            "errorRules": [],
            "pipelineExecutions": [
                {
                    "ruleName": "coord-accuracy",
                    "executionId": "c9d8e7f6-a5b4-4c3d-2e1f-0a9b8c7d6e5f",
                    "status": "completed"
                }
            ],
            "executionId": "c9d8e7f6-a5b4-4c3d-2e1f-0a9b8c7d6e5f",
            "pipelineRuleName": "coord-accuracy",
            "actor": "user@example.com",
            "completedAt": "2026-03-15T10:31:12+00:00"
        }
    ],
    "NextToken": "eyJldmFsdWF0aW9uSWQiOiAiLi4uIn0="
}
```

`status` is `completed`, `pending_pipeline`, `error` or `failed`. `ruleResults` is a JSON-encoded list of rule results, each with a `status` of `evaluated` or `error`; `exceptionApplied` is `true` on an evaluation that ran while an exception against its schema version was active (the violations are recorded, the asset stayed released); `hasRuleErrors` is `true` when at least one rule result has `status` `error` — the rule's tooling failed, it produced no verdict and its enforcement did not apply — and `errorRules` names those rules, which are absent from `violations`. An evaluation with `status` `error` produced no verdict at all (every rule errored, or the schema could not be loaded) and left the asset's state unchanged. `pipelineExecutions`, `executionId` and `pipelineRuleName` are present on evaluations that launched a workflow execution, and `executionId` is the value the workflow completion event is correlated by. `NextToken` is absent on the last page.

### Error responses

| Status | Description                            |
| ------ | -------------------------------------- |
| `400`  | Invalid parameters or pagination token |
| `403`  | Not authorized                         |
| `500`  | Internal server error                  |

---

## Get the compliance state of an asset

Retrieves the compliance record of an asset: its state, bound schema and last evaluation.

```
GET /compliance/state/{databaseId}/{assetId}
```

### Response

```json
{
    "databaseId": "survey-project",
    "assetId": "x1a2b3c4-scan-0042",
    "complianceState": "compliant",
    "schemaName": "survey-compliance",
    "schemaSource": "database",
    "lastEvaluationId": "3f6c1a2e-0b7d-4f7e-9d1c-5a2b8c9d0e1f",
    "lastEvaluatedAt": "2026-03-15T10:30:00+00:00",
    "lastEvaluationStatus": "completed",
    "updatedAt": "2026-03-15T10:31:12+00:00",
    "exceptionGranted": false
}
```

`complianceState` is one of `compliant`, `non_compliant`, `quarantined`, `exception`, `pending_evaluation` or `unknown`. An asset with no compliance record is reported with `complianceState` `unknown` and `null` for `schemaName` and `schemaSource`. `lastEvaluationStatus` is the status of the evaluation `lastEvaluationId` names — `completed`, `pending_pipeline` or `error`; when it is `error` that evaluation produced no verdict (every rule of it errored, or its schema could not be loaded), so `complianceState` is the state the asset held before it. An asset holding an active exception is in state `exception` and carries `exceptionGranted: true`, `exceptionReason`, `exceptionGrantedBy`, `exceptionGrantedAt`, and `exceptionSchemaName` / `exceptionSchemaVersion` — the schema name and version the exception is scoped to; revoking or superseding the exception sets `exceptionGranted` to `false` and clears the other exception fields. The rule failures behind a quarantine are summarised in `quarantineReason` while the asset is quarantined and listed in full on the last evaluation's `violations` (see [List the evaluations of an asset](#list-the-evaluations-of-an-asset)).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid parameters    |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Get the compliance overview of a database

Retrieves per-state counts covering every tracked asset of a database, and one page of the tracked assets. Only assets with a compliance record are counted.

```
GET /compliance/state/{databaseId}
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                  |
| --------------- | ------ | -------- | ------- | -------------------------------------------------------------------------------------------- |
| `maxItems`      | number | No       | `100`   | Asset records per page. A larger value is reduced to 500; the remainder follows `NextToken`. |
| `startingToken` | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                   |

### Response

```json
{
    "databaseId": "survey-project",
    "totalAssets": 3,
    "summary": {
        "compliant": 2,
        "non_compliant": 0,
        "pending_evaluation": 0,
        "quarantined": 1,
        "exception": 0,
        "unknown": 0,
        "error": 1
    },
    "assets": [
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0042",
            "assetName": "Scan 0042",
            "complianceState": "compliant",
            "schemaName": "survey-compliance",
            "schemaSource": "database",
            "lastEvaluatedAt": "2026-03-15T10:30:00+00:00",
            "lastEvaluationStatus": "error"
        }
    ],
    "NextToken": "eyJvZmZzZXQiOiAxfQ=="
}
```

`totalAssets` and `summary` cover every tracked asset of the database regardless of the page; `summary` holds one bucket per state (`compliant`, `non_compliant`, `pending_evaluation`, `quarantined`, `exception`, `unknown`) plus `error`, the number of assets whose `lastEvaluationStatus` is `error`. `error` is an overlay on the state buckets, not a state: an asset whose last evaluation produced no verdict is counted under the state it kept and again under `error`, so the state buckets sum to `totalAssets` without it. `assets` is one page of their records in `assetId` order — each a compliance record as returned by [Get the compliance state of an asset](#get-the-compliance-state-of-an-asset), plus the asset's display name as `assetName` — and `NextToken` is absent on the last page.

### Error responses

| Status | Description                              |
| ------ | ---------------------------------------- |
| `400`  | Invalid `databaseId` or pagination token |
| `403`  | Not authorized                           |
| `500`  | Internal server error                    |

---

## List quarantined assets

Retrieves one page of assets in the `quarantined` state across databases, each with its `assetName`; an asset released under an exception (state `exception`) is not listed. The page is filtered to the databases the caller may read after it is read, so a page can be empty while `NextToken` is present — keep following the token until none is returned.

```
GET /compliance/quarantine
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                  |
| --------------- | ------ | -------- | ------- | -------------------------------------------------------------------------------------------- |
| `maxItems`      | number | No       | `100`   | Asset records per page. A larger value is reduced to 500; the remainder follows `NextToken`. |
| `startingToken` | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                   |

### Response

```json
{
    "quarantinedAssets": [
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0043",
            "assetName": "Scan 0043",
            "complianceState": "quarantined",
            "schemaName": "survey-compliance",
            "schemaSource": "database",
            "quarantineReason": "Required field 'owner' is missing",
            "lastEvaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "lastEvaluatedAt": "2026-03-15T10:30:00+00:00"
        }
    ],
    "NextToken": "eyJjb21wbGlhbmNlU3RhdGUiOiAicXVhcmFudGluZWQiLCAiZGF0YWJhc2VJZCI6ICIuLi4ifQ=="
}
```

`NextToken` is absent on the last page.

### Error responses

| Status | Description              |
| ------ | ------------------------ |
| `400`  | Invalid pagination token |
| `403`  | Not authorized           |
| `500`  | Internal server error    |

---

## Release an asset from quarantine

Sets a quarantined asset to `compliant` without recording an exception, and writes a `quarantine_released` audit entry. The asset is re-quarantined if a later evaluation fails a `quarantine` rule again.

```
POST /compliance/quarantine/{databaseId}/{assetId}/release
```

### Request body

| Field    | Type   | Required | Description                                                     |
| -------- | ------ | -------- | --------------------------------------------------------------- |
| `reason` | string | No       | Reason recorded in the audit entry (default `released via API`) |

### Response

```json
{ "message": "Asset survey-project:x1a2b3c4-scan-0043 released from quarantine" }
```

### Error responses

| Status | Description                                                                             |
| ------ | --------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset has no compliance record, or the asset is not quarantined |
| `403`  | Not authorized                                                                          |
| `500`  | Internal server error                                                                   |

---

## Grant an exception to a quarantined asset

Moves a quarantined asset to the `exception` state, records the exception on its compliance record (`exceptionGranted`, `exceptionReason`, `exceptionGrantedBy`, `exceptionGrantedAt`, and the bound schema's name and current version as `exceptionSchemaName` / `exceptionSchemaVersion`), clears `quarantineReason`, and writes an `exception_granted` audit entry.

The exception is scoped to that schema name and version and holds until it is revoked or superseded. A re-evaluation against the same schema version records its violations on the evaluation (`exceptionApplied: true`) and leaves the asset in `exception` on a failing verdict (`compliant` on a passing one); an evaluation against another schema or a newer version of the schema supersedes the exception — the exception fields are cleared, an `exception_superseded` audit entry is written and the verdict applies normally.

```
POST /compliance/quarantine/{databaseId}/{assetId}/exception
```

### Request body

| Field    | Type   | Required | Description                       |
| -------- | ------ | -------- | --------------------------------- |
| `reason` | string | Yes      | Justification (1-1024 characters) |

### Response

```json
{
    "message": "Exception granted for survey-project:x1a2b3c4-scan-0043",
    "reason": "Legacy capture; accuracy waived by the survey lead",
    "grantedBy": "user@example.com",
    "complianceState": "exception"
}
```

### Error responses

| Status | Description                                                                                              |
| ------ | -------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, no reason given, the asset has no compliance record, or the asset is not quarantined |
| `403`  | Not authorized                                                                                           |
| `500`  | Internal server error                                                                                    |

---

## Revoke an exception

Ends an asset's active exception. The exception fields are cleared and the asset returns to the state its last evaluation's verdict maps to — `quarantined` again, with `quarantineReason` restored and the asset's subscribers notified, when that verdict was quarantined — or to `pending_evaluation` when the asset has no recorded evaluation. An `exception_revoked` audit entry is written. The request carries no body.

```
DELETE /compliance/quarantine/{databaseId}/{assetId}/exception
```

### Response

```json
{
    "message": "Exception revoked",
    "databaseId": "survey-project",
    "assetId": "x1a2b3c4-scan-0043",
    "complianceState": "quarantined"
}
```

### Error responses

| Status | Description                                                                       |
| ------ | --------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset has no compliance record, or no exception is active |
| `403`  | Not authorized                                                                    |
| `500`  | Internal server error                                                             |

---

## List pending cascades

Retrieves every cascade in the `pending_approval` state that the caller may read, newest first. A row is listed only when the caller may read both the cascade and the compliance evaluations of the trigger asset's database.

```
GET /compliance/cascades
```

### Response

```json
{
    "cascades": [
        {
            "cascadeId": "7b2e4d6f-8a1c-4e3b-9f5d-2c4a6e8b0d1f",
            "state": "pending_approval",
            "triggeredByDatabaseId": "survey-project",
            "triggeredByAssetId": "x1a2b3c4-control-0001",
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-control-0001",
            "triggerReason": "Parent asset updated; downstream re-evaluation needed",
            "createdAt": "2026-03-15T10:31:12+00:00",
            "actor": "SYSTEM_USER",
            "requireApproval": true,
            "approvalTimeoutAt": "2026-03-16T10:31:12+00:00",
            "nodes": "{}",
            "executionOrder": "[]"
        }
    ]
}
```

The listing is not paged. Each row carries `databaseId` and `assetId` naming the trigger asset, copies of `triggeredByDatabaseId` and `triggeredByAssetId`.

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Create a cascade

Creates a cascade that re-evaluates the downstream assets of an asset — every descendant reachable through `parentChild` asset links, in dependency order. With `requireApproval` (the default) the cascade waits in `pending_approval` for [approval](#approve-a-pending-cascade) and the response is `200`. With `requireApproval: false` the cascade is written in the `executing` state, its execution is started in the background and the response is `202`; the evaluations do not run inside the request. This route always opens a new cascade; only the cascade an evaluation opens automatically for an asset with children is de-duplicated — while one for the same parent is pending approval, further evaluations of that parent open no new one.

```
POST /compliance/cascades
```

### Request body

| Field             | Type    | Required | Description                                               |
| ----------------- | ------- | -------- | --------------------------------------------------------- |
| `databaseId`      | string  | Yes      | Database of the source asset                              |
| `assetId`         | string  | Yes      | Asset whose downstream assets are re-evaluated            |
| `reason`          | string  | No       | Reason recorded on the cascade (default `manual trigger`) |
| `requireApproval` | boolean | No       | Wait for approval before executing (default `true`)       |

### Response

```json
{
    "message": "Cascade created",
    "cascadeId": "7b2e4d6f-8a1c-4e3b-9f5d-2c4a6e8b0d1f",
    "state": "pending_approval"
}
```

When `requireApproval` is `false`, the status is `202` and `state` is `executing`. The response carries no execution result: follow the cascade with [Get a cascade](#get-a-cascade) until `state` is `completed` or `aborted`.

### Error responses

| Status | Description                                                                                            |
| ------ | ------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid parameters, the asset does not exist, or the cascade could not be started (recorded `aborted`) |
| `403`  | Not authorized                                                                                         |
| `500`  | Internal server error                                                                                  |

---

## Get a cascade

Retrieves one cascade with its state and per-node progress. An executing cascade is followed by polling this route until `state` is `completed` or `aborted`.

```
GET /compliance/cascades/{cascadeId}
```

### Response

```json
{
    "cascadeId": "7b2e4d6f-8a1c-4e3b-9f5d-2c4a6e8b0d1f",
    "state": "completed",
    "triggeredByDatabaseId": "survey-project",
    "triggeredByAssetId": "x1a2b3c4-control-0001",
    "triggerReason": "manual trigger",
    "createdAt": "2026-03-15T10:31:12+00:00",
    "actor": "user@example.com",
    "requireApproval": true,
    "approvalTimeoutAt": "2026-03-16T10:31:12+00:00",
    "approvedBy": "user@example.com",
    "approvedAt": "2026-03-15T11:02:40+00:00",
    "approvalReason": "approved",
    "totalNodes": 2,
    "nodes": "{\"survey-project:x1a2b3c4-scan-0042\": \"compliant\", \"survey-project:x1a2b3c4-scan-0043\": \"quarantined\"}",
    "executionOrder": "[{\"databaseId\": \"survey-project\", \"assetId\": \"x1a2b3c4-scan-0042\"}, {\"databaseId\": \"survey-project\", \"assetId\": \"x1a2b3c4-scan-0043\"}]",
    "completedAt": "2026-03-15T11:02:44+00:00"
}
```

`state` is `pending_approval`, `executing`, `completed` or `aborted`. `nodes` is a JSON-encoded map of `databaseId:assetId` to the node's status — `pending`, `evaluating`, the verdict the evaluation produced (`compliant`, `non_compliant`, `quarantined`, `pending_pipeline`, `error`), or `skipped` for a descendant with no bound schema — and `executionOrder` the JSON-encoded evaluation order. A rejected cascade carries `rejectedBy`, `rejectedAt` and `rejectionReason` instead of the approval fields. An `aborted` cascade carries `abortReason`: the rejection, or the failure that ended the run.

### Error responses

| Status | Description                                        |
| ------ | -------------------------------------------------- |
| `400`  | Invalid `cascadeId`, or the cascade does not exist |
| `403`  | Not authorized                                     |
| `500`  | Internal server error                              |

---

## Approve a pending cascade

Moves a `pending_approval` cascade to `executing`, starts its execution in the background and records a `cascade_approved` audit entry. The response is `202`; the evaluations do not run inside the request. Follow the cascade with [Get a cascade](#get-a-cascade) until `state` is `completed` or `aborted`, and a `cascade_completed` audit entry is written when it finishes.

```
POST /compliance/cascades/{cascadeId}/approve
```

### Request body

| Field    | Type   | Required | Description                                                                |
| -------- | ------ | -------- | -------------------------------------------------------------------------- |
| `reason` | string | No       | Reason recorded on the cascade and in the audit entry (default `approved`) |

### Response

```json
{
    "message": "Cascade approved",
    "cascadeId": "7b2e4d6f-8a1c-4e3b-9f5d-2c4a6e8b0d1f",
    "state": "executing"
}
```

### Error responses

| Status | Description                                                                                                                               |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `cascadeId`, the cascade does not exist or is not in `pending_approval`, or the cascade could not be started (recorded `aborted`) |
| `403`  | Not authorized                                                                                                                            |
| `500`  | Internal server error                                                                                                                     |

---

## Reject a pending cascade

Moves a `pending_approval` cascade to `aborted` and records a `cascade_rejected` audit entry.

```
POST /compliance/cascades/{cascadeId}/reject
```

### Request body

| Field    | Type   | Required | Description                                                                |
| -------- | ------ | -------- | -------------------------------------------------------------------------- |
| `reason` | string | No       | Reason recorded on the cascade and in the audit entry (default `rejected`) |

### Response

```json
{
    "message": "Cascade rejected",
    "cascadeId": "7b2e4d6f-8a1c-4e3b-9f5d-2c4a6e8b0d1f"
}
```

### Error responses

| Status | Description                                                                        |
| ------ | ---------------------------------------------------------------------------------- |
| `400`  | Invalid `cascadeId`, or the cascade does not exist or is not in `pending_approval` |
| `403`  | Not authorized                                                                     |
| `500`  | Internal server error                                                              |

---

## Query the compliance audit trail

Retrieves audit entries across assets, newest first, one page per call. With `eventType` a single event type is read; without it the entries of every event type are read in turn, and the continuation token carries the position of that walk. Entries are filtered to those whose database the caller may read — an entry that names no database is kept only for a caller whose `complianceEvaluation` permission does not restrict `databaseId` — so a page can be empty while `NextToken` is present.

```
GET /compliance/audit
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                                           |
| --------------- | ------ | -------- | ------- | --------------------------------------------------------------------------------------------------------------------- |
| `eventType`     | string | No       | `null`  | Restrict the page to one event type — see [Event types](#event-types).                                                |
| `startDate`     | string | No       | `null`  | ISO-8601 timestamp; only entries at or after it are returned.                                                         |
| `endDate`       | string | No       | `null`  | ISO-8601 timestamp; only entries at or before it are returned.                                                        |
| `maxItems`      | number | No       | `100`   | Entries per page. A larger value is reduced to 500; the remainder follows `NextToken`. Takes precedence over `limit`. |
| `limit`         | number | No       | `100`   | Alias of `maxItems`, read when `maxItems` is absent.                                                                  |
| `startingToken` | string | No       | `null`  | Continuation token from a previous response's `NextToken`.                                                            |

### Response

```json
{
    "entries": [
        {
            "entryId": "0f1e2d3c-4b5a-4968-8776-655443322110",
            "databaseId:assetId": "survey-project:x1a2b3c4-scan-0043",
            "timestamp": "2026-03-15T10:30:00+00:00",
            "eventType": "compliance_check",
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0043",
            "actor": "user@example.com",
            "schemaName": "survey-compliance",
            "evaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "details": "{\"verdict\": \"quarantined\", \"ruleResultCount\": 2, \"pipelineRulesPending\": 0}",
            "previousState": "pending_evaluation",
            "newState": "quarantined"
        }
    ],
    "NextToken": "eyJwYXJ0aXRpb24iOiAiY29tcGxpYW5jZV9jaGVjayIsICJrZXkiOiB7Li4ufX0="
}
```

`details` is a JSON-encoded object whose keys depend on the event type. `previousState` and `newState` are present on entries that changed an asset's compliance state; `schemaName`, `evaluationId` and `cascadeId` are present when the event concerns one. Entries about a schema rather than an asset (`schema_deleted`) carry `*` as `databaseId` and `assetId`. `NextToken` is absent on the last page.

### Event types

| Event type                     | Written when                                                                                                                  |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `compliance_check`             | An asset is evaluated                                                                                                         |
| `evaluation_error`             | A rule's tooling failed in an evaluation, or the schema could not be loaded; `details` names the errored rules as `ruleNames` |
| `quarantine_released`          | A quarantined asset is released, by request or by a passing re-evaluation                                                     |
| `exception_granted`            | An exception is granted to a quarantined asset                                                                                |
| `exception_revoked`            | An active exception is revoked; the asset returns to its last verdict's state                                                 |
| `exception_superseded`         | An evaluation against another schema or a newer schema version clears an active exception                                     |
| `schema_bound_to_database`     | A schema is bound to a database                                                                                               |
| `schema_unbound_from_database` | A database binding is removed                                                                                                 |
| `schema_bound_to_asset`        | A schema is bound to an asset as an override                                                                                  |
| `schema_unbound_from_asset`    | An asset override is removed                                                                                                  |
| `schema_deleted`               | A schema is deleted                                                                                                           |
| `cascade_triggered`            | A cascade is created through the API                                                                                          |
| `cascade_auto_triggered`       | A cascade is opened after the evaluation of an asset that has children                                                        |
| `cascade_approved`             | A pending cascade is approved                                                                                                 |
| `cascade_rejected`             | A pending cascade is rejected                                                                                                 |
| `cascade_completed`            | A cascade finishes evaluating its downstream assets                                                                           |

### Error responses

| Status | Description                            |
| ------ | -------------------------------------- |
| `400`  | Invalid parameters or pagination token |
| `403`  | Not authorized                         |
| `500`  | Internal server error                  |

---

## Get the audit history of an asset

Retrieves the audit entries of one asset, newest first, one page per call.

```
GET /compliance/audit/{databaseId}/{assetId}
```

### Query parameters

Same as [Query the compliance audit trail](#query-the-compliance-audit-trail) without `eventType`: `startDate`, `endDate`, `maxItems` (or `limit`) and `startingToken`, under the same defaults and caps.

### Response

Same structure as [Query the compliance audit trail](#query-the-compliance-audit-trail).

### Error responses

| Status | Description                            |
| ------ | -------------------------------------- |
| `400`  | Invalid parameters or pagination token |
| `403`  | Not authorized                         |
| `500`  | Internal server error                  |
