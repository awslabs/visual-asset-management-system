# Compliance API

The Compliance API registers compliance schemas, binds them to databases and assets, evaluates assets against them, and manages the quarantine, cascade and audit records that evaluation produces. For the concepts behind these endpoints — rule types, enforcement levels, compliance states and the pipeline output contract — see [Compliance](../concepts/compliance.md).

All compliance endpoints live under the `/compliance` prefix. Identifiers follow the VAMS conventions: a `databaseId` or `schemaName` is 3-63 characters of letters, digits, hyphens and underscores (`GLOBAL` is accepted wherever a database is named), and an `assetId` follows the asset identifier rules.

:::info[Authorization]
All compliance endpoints require a valid credential in the `Authorization` header and are subject to two-tier authorization: API-level access to the `/compliance/*` route is checked first, followed by object-level Casbin enforcement on the compliance object types. Schema and sweep operations are enforced on a `complianceSchema` object (`complianceSchemaName`, and `databaseId`: the schema's scope, a database ID or `GLOBAL`); binding and unbinding on the `complianceBinding` object of the database (`databaseId`; an asset override uses the asset's database) and on a `GET` of the `complianceSchema` object of the schema bound or removed; evaluation, state, quarantine and audit operations on a `complianceEvaluation` object (`databaseId`, `complianceState`); cascade operations on a `complianceCascade` object (`cascadeId`, and `databaseId`: the database of the cascade's parent asset). Operations that reach into a database or asset are additionally enforced on that target: binding, unbinding and reading a database's bindings on a `GET` of the `database` object, binding and unbinding an asset override on a `GET` of the `asset` object, a sweep on the `complianceEvaluation` object of each bound database (assets in databases the caller may not evaluate are skipped and counted), and creating, approving, rejecting or reading a cascade on the `complianceEvaluation` object of the triggering asset's database. Every read or action on an asset's compliance data (its state, evaluations, quarantine, exception, audit entries and cascades, and the listings and counts that show them) also needs a `GET` of the `asset` object as stored, the check the asset routes make, for a caller whose permissions have any constraint on the `asset` object type; a caller with none (the `compliance-global-admin` template alone) is not asked it. A caller refused that check gets `403` on the asset's records and actions, the listings and counts leave the asset out, a cascade counts it in `unauthorizedNodes`, and a sweep or **Evaluate All** skips it (`skippedNotAuthorized`). The object action mirrors the HTTP method, except on the `database` and `asset` targets of a binding, which are always checked with `GET`: a binding changes compliance records only. Listings return only the items the caller may `GET`: the global audit trail and the pending-cascade listing return only rows whose database the caller may read, so a page can be empty while `NextToken` is present. The schema, cascade and quarantine listings carry a `permissions` object on each item: the actions the caller may take on it, computed by the service, so a client can hide what the caller cannot do.
:::

:::note[Error responses]
A compliance handler reports every rejection it makes itself as `400` with a short message — an invalid identifier, a missing schema, database, asset or cascade, a schema that is still bound, an asset that is not quarantined, or a cascade that is not pending approval. `403` is returned when either authorization tier denies the request, and `500` for an unexpected error.

While the compliance data of a deleted database is being moved under `<databaseId>#deleted`, every compliance action on that database id (a schema save or delete in its scope, a binding change, an evaluation, a release, an exception, a cascade action) answers `409` with `The compliance data of a deleted database with this id is still being moved. Try again in a few minutes.`, and reads of the id return no compliance data. See [Deleted databases](../concepts/compliance.md#deleted-databases).
:::

:::note[The history of a deleted database]
A deleted database's compliance data is read under `<databaseId>#deleted`, written `%23deleted` in a path or query value: `GET /compliance/state/{databaseId}` and `/compliance/state/{databaseId}/{assetId}`, the evaluation and audit history of an asset, and the `databaseId` filter of the audit, cascade and schema listings take it. Each read is authorized on that id as for any database id. Nothing under it can be changed: a schema scoped to `<databaseId>#deleted` is refused on update and delete with `400` `The schema belongs to a deleted database and cannot be changed`.
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
            "isSystem": false,
            "permissions": { "edit": false, "delete": false, "bind": true }
        }
    ],
    "createScopes": ["survey-project"]
}
```

Each schema carries `permissions`: `edit` (`PUT` on the schema in its scope), `delete` (`DELETE`), and `bind` (a `vams-rules-v1` schema the caller may read, and `PUT` on the `complianceBinding` of the database named by `databaseId`, or of the schema's own database for a database-scoped schema; for a `GLOBAL` schema listed without `databaseId`, whether a given database may bind it is checked when it is bound). A system schema is never editable or deletable by a caller other than the system. Without `databaseId`, the response also carries `createScopes`: the scopes (`GLOBAL` and database IDs) the caller may register a schema in; a deleted database is not one.

The listing is not paged; a schema appears once, at its highest version. The top-level `schemaFormat` is derived from the body: `vams-rules-v1`, or `legacy` for a row whose body is not a `vams-rules-v1` document. A legacy schema cannot be bound (see [Bind a schema to a database](#bind-a-schema-to-a-database)), updated or evaluated; it is listed so that it can be found and [deleted](#delete-a-compliance-schema).

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid `databaseId`  |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Register a compliance schema

Registers a schema body under a name. Registering a name that already exists writes the next version of that schema; the highest version is the one evaluation reads. The request is authorized on `POST` of the `complianceSchema` object in the target scope (`databaseId`, `GLOBAL` when omitted); re-registering an existing name also needs `PUT` on it in its current scope.

:::note[Schema names are unique across all databases]
Schema names form one name space across `GLOBAL` and every database: a name that one scope uses cannot be registered as a second schema in another scope. Registering it again writes the next version of the existing schema (moving it to the scope the request names), and so needs `PUT` on that schema in its current scope; a caller who may not change it there gets `403`. Choose a name that is unique for the whole deployment, for example prefixed with the database ID.

The routes that name a schema (`GET`, `PUT` and `DELETE /compliance/schemas/{schemaName}`) authorize the caller before they look the name up, as the asset routes do. A name used by a schema in a scope the caller may not read answers `403` (`Not Authorized`). A name no scope uses is checked as a `GLOBAL` schema: it answers `400` (`Schema not found`) to a caller who may read `GLOBAL` schemas (or may update or delete one, for `PUT` and `DELETE`), and `403` to one who may not.
:::

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
                "metadataSchemaRef": { "schemaName": "defaultAsset" },
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
            },
            "deliverable-layout": {
                "ruleType": "fileStructure",
                "enforcement": "warn",
                "checks": [
                    {
                        "name": "model-and-no-temp-files",
                        "requiredPaths": ["^models/", "\\.glb$"],
                        "forbiddenPaths": ["\\.tmp$"],
                        "maxFiles": 500
                    }
                ]
            },
            "files-versioned": {
                "ruleType": "versionCoverage",
                "enforcement": "warn",
                "checks": [
                    { "name": "current-files-versioned", "excludePaths": ["\\.previewFile\\."] }
                ]
            },
            "naming-and-tags": {
                "ruleType": "assetAttributes",
                "enforcement": "inform",
                "checks": [
                    {
                        "name": "name-convention",
                        "field": "assetName",
                        "matches": "^[A-Z][A-Za-z0-9-]*$"
                    },
                    {
                        "name": "tagged",
                        "field": "tags",
                        "requiredTags": ["survey"],
                        "forbiddenTags": ["draft"]
                    }
                ]
            }
        }
    }
}
```

The rule types and what each check reads:

| `ruleType`        | A check passes when                                                                                                                                                                                    | Reads                                                                                                                                                                                           |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline`        | The measurement in `outputField` is within the `tolerance` (asynchronous: a workflow execution runs first)                                                                                             | The pipeline's `compliance-output.json`                                                                                                                                                         |
| `metadata`        | The metadata schema's required fields are present (`validateRequired`), typed as declared (`validateTypes`), and every `additionalRequiredFields` key is present, for every unit of the rule's `scope` | By `scope`: asset metadata (`asset`, default), each current file's metadata (`files`) or attributes (`fileAttributes`), or each asset link's metadata (`links`); the referenced metadata schema |
| `relationship`    | The number of `direction` links of `relationshipType` is within `minCount` / `maxCount`                                                                                                                | Asset links                                                                                                                                                                                     |
| `fileStructure`   | Every `requiredPaths` pattern matches some file, no file matches a `forbiddenPaths` pattern, and the file count is within `minFiles` / `maxFiles`                                                      | The asset's current, non-archived files                                                                                                                                                         |
| `versionCoverage` | Every current file not matched by `excludePaths` has its current S3 version recorded in some asset version                                                                                             | The current files and the asset versions' file records                                                                                                                                          |
| `assetAttributes` | `assetName` / `description` matches `matches` or does not match `notMatches`; `tags` carries every `requiredTags` entry, no `forbiddenTags` entry and at least `minTags` tags                          | The asset record                                                                                                                                                                                |

Path, name and description patterns are Python regular expressions searched (`re.search`) in the value; a file path is matched without its leading `/`, so `^models/` anchors at the asset root. Every pattern is checked when the schema is saved — it must compile, may not repeat a group that itself repeats or alternates or use a backreference, and must stay within the backtracking budget (a path pattern, which runs against keys of up to 1024 characters, admits at most two unbounded quantifiers when it begins with `^` and one when it does not; a name or description pattern admits three and two) — and the `400` message names the field, never the pattern. Bounds: at most 25 checks per `metadata`, `fileStructure`, `versionCoverage` or `assetAttributes` rule; at most 25 patterns of 1-512 characters per `requiredPaths`, `forbiddenPaths` or `excludePaths` list, and per metadata rule `includePaths` or `excludePaths` list; at most 50 tags of 1-256 characters per `requiredTags` or `forbiddenTags` list. See [Compliance — The vams-rules-v1 format](../concepts/compliance.md#the-vams-rules-v1-format) for each type in full, including why `versionCoverage` fails after every upload until a version is created.

A metadata rule's `metadataSchemaRef` names a metadata schema by `schemaName` and, optionally, `databaseId`. Without `databaseId` the metadata schema is looked up in the database of the asset being evaluated, then in `GLOBAL`, so one compliance schema bound to several databases uses each database's own metadata schema of that name where it has one. `databaseId` pins the lookup to that database (then `GLOBAL`) and is limited to the compliance schema's scope: a GLOBAL compliance schema may name only `GLOBAL`, a database-scoped one `GLOBAL` or its own database. The same limit applies to a pipeline rule's `pipelineRef.databaseId` and `pipelineRef.pipelineDatabaseId`; another database is refused with `400`. Its `scope` selects what it validates, and its `metadataSchemaRef` must name a metadata schema of the matching entity type (`assetMetadata`, `fileMetadata`, `fileAttribute`, `assetLinkMetadata`); a schema that is not found under that type records the rule as `status: error`, never a pass. The schema is read whether or not it is enabled; its enabled setting governs metadata write validation only. `includePaths` / `excludePaths` narrow the `files` and `fileAttributes` scopes and are refused on the others; `linkDirection` (`parents`, `children`, `related`) and `relationshipType` (`parentChild`, `related`) narrow the `links` scope and are refused on the others. Every file or link in scope must pass:

```json
{
    "ruleType": "metadata",
    "enforcement": "warn",
    "scope": "files",
    "metadataSchemaRef": { "schemaName": "modelFileMetadata" },
    "includePaths": ["\\.(glb|stl)$"],
    "excludePaths": ["\\.previewFile\\."],
    "checks": [{ "name": "file-fields-present", "validateRequired": true, "validateTypes": true }]
}
```

Its result names the failing files, at most ten:

```json
{
    "ruleName": "model-file-metadata",
    "ruleType": "metadata",
    "enforcement": "warn",
    "status": "evaluated",
    "passed": false,
    "message": "1 of 3 files fail: /models/bracket.glb: file-fields-present: missing required schema fields: ['units']",
    "measured": {
        "scope": "files",
        "evaluatedCount": 3,
        "failingCount": 1,
        "failing": [
            {
                "path": "/models/bracket.glb",
                "message": "file-fields-present: missing required schema fields: ['units']"
            }
        ]
    },
    "expected": { "schemaRef": "GLOBAL/modelFileMetadata", "scope": "files" }
}
```

See [Metadata rules](../concepts/compliance.md#metadata-rules) for the scopes in full.

:::note[Only vams-rules-v1 documents are accepted]
`schemaBody` must be a `vams-rules-v1` document: an object with `schemaFormat` set to `vams-rules-v1` and at least one rule under `rules`. A plain JSON Schema, a template file's envelope (`metadata`, `schemaName`, `schemaBody`) or any other object is rejected with `400` and the message `schemaBody must be a vams-rules-v1 document`; the specific validation failure is written to the handler's log. A pipeline rule's `inputFiles` selects which of the asset's files the workflow receives (`matching`, `wholeAsset` or `explicit`); the modes and the arity rule are described in [Pipeline rules](../concepts/compliance.md#pipeline-rules).
:::

### Response

```json
{
    "message": "Schema 'survey-compliance' registered as v1",
    "schemaName": "survey-compliance",
    "databaseId": "GLOBAL",
    "internalVersion": 1,
    "warnings": []
}
```

`warnings` is present on every successful save and is empty when nothing applies; the save succeeded whatever it holds. A `pipelineRulesShareWorkflow` entry is returned for each workflow that two or more pipeline rules of the body name (the same `pipelineRef.databaseId` and `pipelineRef.workflowId`):

```json
{
    "code": "pipelineRulesShareWorkflow",
    "message": "Pipeline rules coord-accuracy, coord-tight run the same workflow. When the workflow restricts concurrency (perAsset, perInputFile or perInputFileVersion), they run one after another on an asset, so its evaluation takes as long as their runs together.",
    "workflowDatabaseId": "GLOBAL",
    "workflowId": "coord-validate-workflow",
    "ruleNames": ["coord-accuracy", "coord-tight"]
}
```

The warning is computed from the schema body alone; the workflow is not read, so it is returned whatever the workflow's concurrency restriction, which can change after the save. See [Rules that wait for a running execution](../concepts/compliance.md#rules-that-wait-for-a-running-execution).

An `extendsSchemaNotFound` entry is returned when the body's `extends` names a parent that the evaluation would not find, or that the caller may not read:

```json
{
    "code": "extendsSchemaNotFound",
    "message": "The parent schema 'base-standard' was not found as a vams-rules-v1 schema you may read. If it does not exist or is not vams-rules-v1, this schema is evaluated with its own rules only.",
    "extends": "base-standard"
}
```

The parent is looked up as the evaluation looks it up (its latest version, which must be `vams-rules-v1`) and counts as found only when the caller may also read it through [Get a compliance schema](#get-a-compliance-schema): `GET` on the route `/compliance/schemas/{schemaName}` and on the parent schema in its scope. A parent that exists but the caller may not read gets the same entry and the same message as a missing one, so the answer does not say whether it exists. The save succeeds either way. Only the parent `extends` names is checked, not its own parent.

:::note[Saving a pipeline rule needs the right to run its workflow]
Each pipeline rule whose `pipelineRef` is new (on registration, every pipeline rule; when the name exists, every rule whose `pipelineRef` differs from the current version's) is authorized as running its workflow is: `POST` on the route `/workflows/{pipelineRef.databaseId}/{pipelineRef.workflowId}/execute` (Tier 1), and `GET` on the `workflow` object, on every `pipeline` object the workflow references and on the rule's own `pipeline` object (Tier 2). The first rule refused stops the save with `403`:

```json
{
    "message": "Not authorized to run the workflow of pipeline rule 'coord-accuracy'",
    "ruleName": "coord-accuracy"
}
```

The answer is the same when the workflow or a pipeline does not exist, and every refusal is recorded in the authorization audit. The system user is not checked. See [Authorization model](../concepts/compliance.md#authorization-model).
:::

### Error responses

| Status | Description                                                                                                                                                            |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, a body that is not a valid `vams-rules-v1` document, the scoping database is absent, or the name exists and belongs to a deleted database's schema |
| `403`  | Not authorized, or a new or changed pipeline rule names a workflow the caller may not run (`ruleName` names the rule)                                                  |
| `409`  | The compliance data of a deleted database with the scope's id is still being moved                                                                                     |
| `500`  | Internal server error                                                                                                                                                  |

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
    "internalVersion": 2,
    "warnings": []
}
```

`warnings` is as for [registering a schema](#register-a-compliance-schema), computed from the body of the new version. A pipeline rule whose `pipelineRef` is new or differs from the current version's needs the right to run its workflow, as on [registration](#register-a-compliance-schema); a rule the update leaves as it was is not checked again, so changing the description or another rule needs no rights on the workflow it names.

### Error responses

| Status | Description                                                                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters or body, the schema or scoping database does not exist, the schema is a system schema, or the schema belongs to a deleted database |
| `403`  | Not authorized, or a new or changed pipeline rule names a workflow the caller may not run (`ruleName` names the rule)                                 |
| `409`  | The compliance data of a deleted database with the schema's current or new scope id is still being moved                                              |
| `500`  | Internal server error                                                                                                                                 |

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

| Status | Description                                                                                                                                                   |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `schemaName`, the schema does not exist, the schema is bound to a database or asset, or the schema belongs to a deleted database (or one being moved) |
| `403`  | Not authorized                                                                                                                                                |
| `500`  | Internal server error                                                                                                                                         |

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
    "assetOverrideCount": 1,
    "applyStatus": "applied",
    "apply": {
        "applyId": "5b0e6a52-2f1c-4c8e-9d0a-0d6a3f1e2b44",
        "operation": "change",
        "status": "applied",
        "schemaName": "survey-compliance",
        "previousSchema": "survey-compliance-v1",
        "assetCount": 128,
        "assetsProcessed": 128,
        "assetsPendingEvaluation": 126,
        "assetsKeptQuarantined": 2,
        "removedComplianceRecords": 0,
        "evaluationsQueued": 128,
        "startedAt": "2026-03-15T10:30:00+00:00",
        "lastProgressAt": "2026-03-15T10:30:04+00:00",
        "finishedAt": "2026-03-15T10:30:04+00:00"
    }
}
```

`databaseSchema` is `null` when the database has no binding. `assetOverrideCount` counts every asset-level override of the database; `assetOverrides` is one page of them in `assetId` order, and `NextToken` is present while more remain. The request is authorized on `GET` of the database and of the bound schema's name (an empty name when there is none).

`apply` is the database's last binding change, which is applied to the asset records in the background (see [Bind a schema to a database](#bind-a-schema-to-a-database)); `null` when the database has had none. `applyStatus` repeats its `status`:

| `apply` field              | Description                                                                                                                                                                                                                                                                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `applyId`                  | The change's id                                                                                                                                                                                                                                                                                                                          |
| `operation`                | `bind` (a first binding), `change` (another schema), `unbind` (the binding removed), or `evaluatePending` (automatic evaluation turned on for the schema in force)                                                                                                                                                                       |
| `status`                   | `applying` while its records are written; `stalled` when it really stopped (see `reason`; repeat the same change to resume it); `applied` once every record carries the binding; `failed` (see `reason`)                                                                                                                                 |
| `schemaName`               | The schema the change applies; absent for `unbind`                                                                                                                                                                                                                                                                                       |
| `previousSchema`           | The schema bound before; absent for a first binding                                                                                                                                                                                                                                                                                      |
| `assetCount`               | How many assets (for `unbind`, inherited records; for `evaluatePending`, pending records) the change goes through, counted when it began                                                                                                                                                                                                 |
| `assetsProcessed`          | How many of them it has gone through so far                                                                                                                                                                                                                                                                                              |
| `assetsPendingEvaluation`  | Records now `pending_evaluation` under the schema                                                                                                                                                                                                                                                                                        |
| `assetsKeptQuarantined`    | Quarantined records kept quarantined and marked awaiting re-evaluation                                                                                                                                                                                                                                                                   |
| `removedComplianceRecords` | Records an `unbind` removed                                                                                                                                                                                                                                                                                                              |
| `evaluationsQueued`        | Evaluations queued with automatic evaluation on                                                                                                                                                                                                                                                                                          |
| `startedAt`                | When the change was made                                                                                                                                                                                                                                                                                                                 |
| `lastProgressAt`           | When it last saved progress                                                                                                                                                                                                                                                                                                              |
| `finishedAt`               | When it ended (`applied` or `failed`)                                                                                                                                                                                                                                                                                                    |
| `reason`                   | Why it `failed` or is `stalled`: its last step failed on every delivery, its next step could not be queued, a step was not delivered within the queue's retention period, or it ran more steps than a change of its size needs                                                                                                           |
| `evaluationStatus`         | With `complianceAutoEval` on: `queueing` while the evaluations of the records it reached are queued (after `applied`, holding nothing), `stalled` when that stopped (`evaluationReason`; the same request resumes it), `queued`, or `cancelled` (a later change, or automatic evaluation turned off). Absent when the change queues none |
| `evaluationsToQueue`       | How many evaluations it queues                                                                                                                                                                                                                                                                                                           |
| `evaluationReason`         | Why its evaluations are `stalled` or `cancelled`                                                                                                                                                                                                                                                                                         |

### Error responses

| Status | Description                                                              |
| ------ | ------------------------------------------------------------------------ |
| `400`  | Invalid `databaseId` or pagination token, or the database does not exist |
| `403`  | Not authorized                                                           |
| `500`  | Internal server error                                                    |

---

## Bind a schema to a database

Binds a `GLOBAL` or database-scoped schema to a database, or replaces the schema bound to it. The request saves the binding on the database, writes one `schema_bound_to_database` audit entry naming the caller, and answers `202` at once; the binding is then **applied to the asset records in the background**, page by page, however many assets the database has. Every asset in the database that has no asset-level override becomes `pending_evaluation` under the schema, except a quarantined one: it stays `quarantined` (its reason and download block with it), marked awaiting re-evaluation under the schema, until an evaluation decides it. Assets with an override are left as they are. Each asset the binding reaches gets a `schema_binding_applied_to_asset` audit entry with its previous and new state, naming the person who made the change; when every record is reached, one `schema_binding_apply_finished` entry records the totals. With `complianceAutoEval` on, an evaluation of each of them is queued on the compliance trigger's queue (`evaluationsQueued`); otherwise run a [sweep](#sweep-the-assets-bound-to-a-schema), **Evaluate All** or individual evaluations to produce verdicts once the change is applied. Follow the change with [Get the schema bindings of a database](#get-the-schema-bindings-of-a-database) (`apply`). While it is applied, another bind or removal of the database, and **Evaluate All** of it, answer `409`; once it is `stalled`, the same request (same schema and `complianceAutoEval`) resumes it from where it stopped and answers `202` with `resumed: true`. Binding the schema already bound changes nothing when `complianceAutoEval` is unchanged (`changed: false`, no audit entry, `200`); when only `complianceAutoEval` changes, the flag is updated without touching the asset records (one `schema_binding_auto_eval_changed` entry): turning it off answers `200`, and turning it on queues the assets still pending or awaiting re-evaluation in the background (`202`, `operation` `evaluatePending`), or answers `200` when there are none. The request is authorized on `PUT` of the database's `complianceBinding` object, `GET` of the schema in its scope, and `GET` of the database: a database's compliance administrator may bind a `GLOBAL` schema it may read but not edit.

```
PUT /compliance/bind/{databaseId}
```

### Request body

| Field                | Type    | Required | Description                                                                                                                                                                                                            |
| -------------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schemaName`         | string  | Yes      | A `GLOBAL` schema or a schema scoped to this database                                                                                                                                                                  |
| `complianceAutoEval` | boolean | No       | Evaluate assets of this database automatically when they are created or updated. Omitted: `true` for a new binding; a database that is already bound keeps its stored value, so changing the schema does not change it |

```json
{ "schemaName": "survey-compliance", "complianceAutoEval": true }
```

### Response

`202`, the binding saved and being applied:

```json
{
    "message": "Schema 'survey-compliance' bound to database 'survey-project'; the binding is being applied to 128 assets",
    "schemaName": "survey-compliance",
    "databaseId": "survey-project",
    "complianceAutoEval": true,
    "changed": true,
    "applyStatus": "applying",
    "applyId": "5b0e6a52-2f1c-4c8e-9d0a-0d6a3f1e2b44",
    "assetCount": 128,
    "apply": {
        "applyId": "5b0e6a52-2f1c-4c8e-9d0a-0d6a3f1e2b44",
        "operation": "change",
        "status": "applying",
        "schemaName": "survey-compliance",
        "previousSchema": "survey-compliance-v1",
        "assetCount": 128,
        "assetsProcessed": 0,
        "assetsPendingEvaluation": 0,
        "assetsKeptQuarantined": 0,
        "removedComplianceRecords": 0,
        "evaluationsQueued": 0,
        "startedAt": "2026-03-15T10:30:00+00:00",
        "lastProgressAt": "2026-03-15T10:30:00+00:00"
    }
}
```

`assetCount` is the number of the database's assets the change goes through, counted when it began. The counts grow on the binding read while the change runs (see the `apply` fields there): `assetsPendingEvaluation` counts the assets now `pending_evaluation` and `assetsKeptQuarantined` the quarantined ones awaiting re-evaluation. `evaluationsQueued` is 0 with `complianceAutoEval` off; with it on, the evaluations are queued once every record carries the binding (`evaluationStatus`), each asset evaluated once under the new binding. Queuing is best effort, and an asset not queued stays pending until its next change, a sweep or **Evaluate All**. A bind of the binding in force answers `200` with `changed` and the counts at 0, as before.

### Error responses

| Status | Description                                                                                                                                                                                                                                                                                                                                 |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the schema or database does not exist, the schema is scoped to another database, or the schema is a `legacy` row — its body is not a `vams-rules-v1` document (`Schema body must be a vams-rules-v1 document`)                                                                                                          |
| `403`  | Not authorized                                                                                                                                                                                                                                                                                                                              |
| `409`  | The compliance data of a deleted database with this id is still being moved, or a binding change of the database is still being applied (`The compliance schema binding of this database is still being applied; try again when it finishes`; a stalled one: `... stopped while it was being applied; repeat the same change to resume it`) |
| `500`  | Internal server error                                                                                                                                                                                                                                                                                                                       |

---

## Remove the schema binding of a database

Removes the database binding. The request removes it from the database, writes one `schema_unbound_from_database` audit entry naming the caller, and answers `202` at once; the removal is then applied to the asset records in the background, as a bind is: it deletes the compliance records of the assets that inherited the binding, except a quarantined one: it stays `quarantined`, marked awaiting re-evaluation with no schema, until an evaluation under a binding set later or a **Release** decides it. Assets with an asset-level override keep their records. Each asset the change reaches gets a `schema_binding_applied_to_asset` audit entry naming the caller, and its end one `schema_binding_apply_finished` entry. While a change of the database is being applied another one answers `409`; a stalled removal resumes when it is requested again. The request is authorized on `DELETE` of the database's `complianceBinding` object, `GET` of the database, and `GET` of the bound schema in its scope (the database stands in when there is none).

```
DELETE /compliance/bind/{databaseId}
```

### Response

`202`, the binding removed from the database and the removal being applied:

```json
{
    "message": "Schema binding removed from database 'survey-project'; the removal is being applied to 128 assets",
    "databaseId": "survey-project",
    "previousSchema": "survey-compliance",
    "changed": true,
    "applyStatus": "applying",
    "applyId": "0c7d2e19-7a4b-4f5e-8b6a-1e2d3c4b5a69",
    "assetCount": 128,
    "apply": {
        "applyId": "0c7d2e19-7a4b-4f5e-8b6a-1e2d3c4b5a69",
        "operation": "unbind",
        "status": "applying",
        "previousSchema": "survey-compliance",
        "assetCount": 128,
        "assetsProcessed": 0,
        "assetsPendingEvaluation": 0,
        "assetsKeptQuarantined": 0,
        "removedComplianceRecords": 0,
        "evaluationsQueued": 0,
        "startedAt": "2026-03-15T10:30:00+00:00",
        "lastProgressAt": "2026-03-15T10:30:00+00:00"
    }
}
```

`assetCount` counts the records that inherit the binding. Once the removal is `applied`, the binding read reports `removedComplianceRecords` and `assetsKeptQuarantined`. When the database has no binding the response is `200` with only `message` and `databaseId`.

### Error responses

| Status | Description                                                                                                                             |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `databaseId`, or the database does not exist                                                                                    |
| `403`  | Not authorized                                                                                                                          |
| `409`  | The compliance data of a deleted database with this id is still being moved, or a binding change of the database is still being applied |
| `500`  | Internal server error                                                                                                                   |

---

## Bind a schema to an asset

Binds a schema directly to one asset as an override of the database binding. The asset becomes `pending_evaluation` under the schema with `schemaSource` `asset`; a quarantined asset stays `quarantined`, marked awaiting re-evaluation under the schema (`awaitingReevaluationReason` `overrideSet`). When the database's `complianceAutoEval` is on, an evaluation of the asset is queued (`evaluationsQueued` 1). Binding the override the asset already has changes nothing and answers with `changed: false`. The request is authorized on `PUT` of the `complianceBinding` object of the asset's database, `PUT` of the schema, and `GET` of the asset.

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
    "schemaSource": "asset",
    "changed": true,
    "complianceState": "pending_evaluation",
    "evaluationsQueued": 1
}
```

### Error responses

| Status | Description                                                                                                                                                                                                                     |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the schema or asset does not exist, the schema is scoped to another database, or the schema is a `legacy` row — its body is not a `vams-rules-v1` document (`Schema body must be a vams-rules-v1 document`) |
| `403`  | Not authorized                                                                                                                                                                                                                  |
| `409`  | The compliance data of a deleted database with this id is still being moved                                                                                                                                                     |
| `500`  | Internal server error                                                                                                                                                                                                           |

---

## Remove the schema override of an asset

Removes an asset-level override. When the database has a binding the asset falls back to it as `pending_evaluation`; otherwise the asset's compliance record is deleted and the asset leaves compliance tracking. A quarantined asset stays `quarantined` either way, marked awaiting re-evaluation (`overrideCleared`) under the database binding or, when there is none, with no schema. When the database is bound with `complianceAutoEval` on, an evaluation of the asset is queued. The request is authorized on `DELETE` of the `complianceBinding` object of the asset's database, `GET` of the asset, and `DELETE` of the name of the schema the asset is bound to (an empty name when it has no compliance record).

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
    "schemaSource": "database",
    "complianceState": "pending_evaluation",
    "evaluationsQueued": 1
}
```

`fallbackSchema` and `schemaSource` are `null` when the database has no binding, and `complianceState` is then `unknown` (the record is deleted) or `quarantined`. When the asset has no override the response is `200` with only `message`, `databaseId` and `assetId`.

### Error responses

| Status | Description                                                                 |
| ------ | --------------------------------------------------------------------------- |
| `400`  | Invalid parameters                                                          |
| `403`  | Not authorized                                                              |
| `409`  | The compliance data of a deleted database with this id is still being moved |
| `500`  | Internal server error                                                       |

---

## Evaluate an asset

Evaluates an asset against the named schema, or against its bound schema when the body names none: the asset's own binding, else its database's binding, under which an asset that has no compliance record yet is registered first. Naming a schema evaluates against it without changing the asset's binding; the evaluation record carries the `schemaName` used. Metadata, relationship, file-structure, version-coverage and asset-attribute rules complete within the request. Each pipeline rule selects the asset files its `inputFiles` describes, launches a workflow execution and the evaluation stays `pending_pipeline` (asset state `pending_evaluation`, or `quarantined` for an asset that was quarantined: its quarantine and download block hold until the verdict) until the execution completes — see [Pipeline rules](../concepts/compliance.md#pipeline-rules). When the asset has children linked by `parentChild` asset links, a cascade awaiting approval is opened after the evaluation, unless one for this asset is already pending approval.

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
    "pipelineRulesWaiting": 0,
    "exceptionApplied": false,
    "hasRuleErrors": false
}
```

`verdict` is one of `compliant`, `non_compliant`, `quarantined`, `pending_pipeline` or `error`; `complianceState` is the asset state the verdict maps to — `exception` when the verdict failed while an exception is active. `schemaVersion` is the version of the schema the evaluation read. `ruleResults` holds the rules that completed in the request, each with a `status` of `evaluated` or `error`, and `pipelineRulesPending` counts the pipeline rules whose execution is awaited. `pipelineRulesWaiting` counts those of them that wait for a running execution of their workflow: the workflow restricts concurrency and another execution of it already holds the asset, file or file version, so the rule launches when that execution ends (see [Rules that wait for a running execution](../concepts/compliance.md#rules-that-wait-for-a-running-execution)). The holding execution is not named. `exceptionApplied` is `true` when an active exception held the asset released, and `hasRuleErrors` is `true` when at least one rule result has `status` `error`.

### Error responses

| Status | Description                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset does not exist, no schema is named or bound, or the evaluation could not run |
| `403`  | Not authorized                                                                                             |
| `500`  | Internal server error                                                                                      |

---

## Evaluate every tracked asset of a database

Evaluates every asset of the database that has a compliance record, each against the schema its record binds — the database binding, or the asset's own override — and nothing in any other database. This is what **Evaluate All Assets** on the database compliance overview runs. In a bound database, an asset that has no compliance record yet is first registered under the binding and counted in `registered`. Assets in `pending_evaluation` are evaluated first, then the least recently evaluated, with the bounds, the continuation and the removal of deleted assets' records of a [sweep](#sweep-the-assets-bound-to-a-schema): at most 200 per call, no new evaluation after about 20 seconds, `assetsRemaining` counting the tracked assets still to evaluate, and a `NextToken` while it is above 0 that the next call sends as `startingToken`. An asset whose record names no schema (one evaluated on demand against a named schema in a database without a binding) is counted in `withoutSchema` and not evaluated. An archived asset is not evaluated either: its record is left as it is and it is counted in `skipped` and `skippedArchived`. Only the archived assets the caller may see are counted (the caller is allowed on the archived record `<databaseId>#deleted`, as the asset listing with archived assets decides it); for any other caller the database's archived assets are counted nowhere in the response, `withoutSchema` and `assetsRemaining` included, and `skippedArchived` is 0. An asset the caller may not read (a caller whose permissions have asset constraints needs `GET` on the asset, as the asset routes check it) is not evaluated and is counted in `skipped` and `skippedNotAuthorized`; `skipped` is the sum of `skippedNotAuthorized` and `skippedArchived`. A `superseded` entry means a newer evaluation of the asset had already started, as in a sweep. The request is authorized on `POST` of the database's `complianceEvaluation` object.

```
POST /compliance/evaluate/{databaseId}
```

### Request body

| Field           | Type   | Required | Description                                                                     |
| --------------- | ------ | -------- | ------------------------------------------------------------------------------- |
| `startingToken` | string | No       | The previous call's `NextToken`, to continue that evaluation; omit to start one |

### Response

```json
{
    "message": "Evaluation triggered for 2 assets",
    "databaseId": "survey-project",
    "assetsTriggered": [
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0042",
            "schemaName": "survey-compliance",
            "evaluationId": "3f6c1a2e-0b7d-4f7e-9d1c-5a2b8c9d0e1f",
            "verdict": "compliant"
        },
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0043",
            "schemaName": "strict-scan",
            "evaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "verdict": "quarantined"
        }
    ],
    "assetsRemaining": 0,
    "superseded": 0,
    "registered": 0,
    "withoutSchema": 0,
    "skipped": 0,
    "skippedNotAuthorized": 0,
    "skippedArchived": 0,
    "removedOrphans": 0
}
```

### Error responses

| Status | Description                                                             |
| ------ | ----------------------------------------------------------------------- |
| `400`  | Invalid `databaseId` or `startingToken`, or the database does not exist |
| `403`  | Not authorized                                                          |
| `500`  | Internal server error                                                   |

---

## Sweep the assets bound to a schema

Evaluates every asset whose compliance record is bound to the schema and whose database the caller may evaluate. In each database bound to the schema that the caller may evaluate, an asset with no compliance record yet is first registered under the binding (`pending_evaluation`). Assets in `pending_evaluation` are evaluated first, then the least recently evaluated. One call evaluates at most 200 assets and stops launching evaluations after about 20 seconds so that it returns within the API Gateway integration window. A sweep of more assets takes several calls: while assets remain, the response carries `NextToken` and `assetsRemaining` counts the assets still to evaluate; send the token back as `startingToken` and the call evaluates only the assets not evaluated since the sweep started (by an earlier call, or by an automatic evaluation meanwhile). Repeat until the response carries no `NextToken`: each asset is then evaluated once. A malformed token is refused with `400` `Invalid pagination token`. A compliance record whose asset no longer exists (no row in the database or its archive) is not evaluated: it is removed, as the asset's permanent deletion removes it, with an `asset_deleted` audit entry, and counted in `removedOrphans`. For a caller whose asset constraints read an asset's own fields (its name, type or tags) the record cannot be decided and is counted in `skippedNotAuthorized` instead, and left for the trigger or another caller's sweep to remove; the same holds for **Evaluate All**. The caller needs `GET` on the schema in its scope, so a database's compliance administrator may sweep a `GLOBAL` schema; the sweep then evaluates the assets of the databases the caller may evaluate only. Bound assets the caller is not authorized to evaluate, and the assets the caller may not read (`GET` on the asset, for a caller whose permissions have asset constraints), are counted in `skipped` and `skippedNotAuthorized` and never listed; no asset is evaluated with the system identity instead. An archived asset is not evaluated (its row cannot be read, so the evaluation could only record an error): its record is left as it is and it is counted in `skipped` and `skippedArchived`. `skipped` is the sum of the two. Only the archived assets the caller may see are counted (the caller is allowed on the archived record `<databaseId>#deleted`, as the asset listing with archived assets decides it); an archived asset the caller may not see is counted nowhere in the response, `skippedNotAuthorized` and `assetsRemaining` included. An asset whose evaluation found a newer evaluation of it already started (for example, a trigger evaluation running at the same time) did not run: its entry carries `verdict` `superseded` and `superseded: true`, its `evaluationId` names no evaluation record, and `superseded` counts these entries. The asset's state is the newer evaluation's; a superseded entry is not an error.

```
POST /compliance/sweep/{schemaName}
```

### Request body

| Field           | Type   | Required | Description                                                                |
| --------------- | ------ | -------- | -------------------------------------------------------------------------- |
| `startingToken` | string | No       | The previous call's `NextToken`, to continue that sweep; omit to start one |

### Response

```json
{
    "message": "Sweep triggered for 3 assets",
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
        },
        {
            "databaseId": "survey-project",
            "assetId": "x1a2b3c4-scan-0044",
            "evaluationId": "c4d5e6f7-8a9b-4c0d-9e1f-2a3b4c5d6e7f",
            "verdict": "superseded",
            "superseded": true
        }
    ],
    "skipped": 0,
    "skippedNotAuthorized": 0,
    "skippedArchived": 0,
    "superseded": 1,
    "removedOrphans": 0,
    "assetsRemaining": 0
}
```

### Error responses

| Status | Description                                                           |
| ------ | --------------------------------------------------------------------- |
| `400`  | Invalid `schemaName` or `startingToken`, or the schema does not exist |
| `403`  | Not authorized                                                        |
| `500`  | Internal server error                                                 |

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

`status` is `completed`, `pending_pipeline`, `error` or `failed`. `ruleResults` is a JSON-encoded list of rule results, each with a `status` of `evaluated` or `error`; `exceptionApplied` is `true` on an evaluation that ran while an exception against its schema version was active (the violations are recorded, the asset stayed released); `hasRuleErrors` is `true` when at least one rule result has `status` `error` — the rule's tooling failed, it produced no verdict and its enforcement did not apply — and `errorRules` names those rules, which are absent from `violations`. An evaluation with `status` `error` produced no verdict at all (every rule errored, or the schema could not be loaded) and left the asset's state unchanged. An evaluation closed before it could finish also has `status` and `verdict` `error`, with `closedReason` and a fixed `errorMessage`: `superseded` (`Not completed: a newer evaluation of the asset started`: a newer evaluation began while one of its pipeline rules waited), `assetArchived` (`Not completed: the asset was archived`), `assetDeleted` (`Not completed: the asset was deleted`) or `databaseDeleted` (`Not completed: the database was deleted`); its `violations` are empty, every `pipelineExecutions` entry is `completed`, and it never changed the asset's state. `pipelineExecutions`, `executionId` and `pipelineRuleName` are present on evaluations with pipeline rules, and `executionId` is the value the workflow completion event is correlated by. The evaluation row is written before any execution is launched, so each `pipelineExecutions` entry carries a `status` of `starting` (the launch has not been attempted yet), `pending` (launched, awaiting its completion event), `waiting` (the workflow's concurrency restriction holds the launch back while another execution of the workflow runs on the asset; no `executionId` yet), `launching` (a waiting rule's launch is under way), `completed` (also for a waiting rule whose wait ended without a run: its result on the row has `status` `error` with `Waited too long for a running execution of the workflow`, or `Not started: a newer evaluation of the asset began while the rule waited for its workflow`), or `not_started` (the launch failed; that rule's result on the row has `status` `error`). `NextToken` is absent on the last page.

An evaluation whose verdict a release or exception grant made after it began outranked carries `notApplied` (`released` or `exceptionGranted`) and `notAppliedMessage` (`Not applied: the asset was released after this evaluation began`, or `Not applied: an exception was granted after this evaluation began`): its verdict and rule results are recorded as computed, but it did not change the asset's state, quarantine fields or exception fields. The synchronous evaluate answer carries the same two fields when the decision landed while the evaluation ran, with `complianceState` the state the decision left.

### Execution failure reasons

A pipeline rule's result names the workflow execution it ran in (`executionId`). When that execution did not succeed, the result's `message` is `Pipeline execution FAILED` (or `ABORTED`, `TIMED_OUT`), the rule's entry in `violations` and the quarantine reason say the same, and `executionFailureReason` holds why the execution failed: the final line of the error the execution recorded (a stack trace contributes only its exception line), with credentials redacted, control characters removed, at most 1,000 characters — for example `ValueError: latin1.obj could not be read as OBJ text: byte 0xE8 at offset 5 is not UTF-8`.

The reason is what the execution itself discloses, so every compliance read that returns it — the evaluation history, the evaluate response, the asset's compliance state, the database overview and the quarantine listing — returns it only to a caller allowed to read that execution: the check `GET /workflows/executions/{executionId}/details` makes (the route itself, the execution's workflow, every asset the run read and the asset it wrote to, and the databases it captured metadata from). Any other caller gets the result without `executionFailureReason`: the generic `message` and the `executionId`. A role that reads compliance but not workflows or executions — the shipped Compliance Admin, Compliance Global Admin and Compliance Readonly templates on their own — therefore never sees it. One request decides at most 50 distinct executions; past that the reason is left out, and the asset's own compliance state shows it. A result recorded before these fields existed has neither.

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

`complianceState` is one of `compliant`, `non_compliant`, `quarantined`, `exception`, `pending_evaluation`, `unknown` or `archived`. An asset with no compliance record is reported with `complianceState` `unknown` and `null` for `schemaName` and `schemaSource`. An archived asset is not evaluated while it is archived: its record is returned with `complianceState` `archived` and the state the record holds as `recordedComplianceState` (`unknown` when it has no record), which applies again once the asset is unarchived. `archived` is never stored and is not a filter or a bucket of the database overview, which leaves archived assets out. A role refused the archived asset itself gets `403`, as for its other compliance records. `lastEvaluationStatus` is the status of the evaluation `lastEvaluationId` names — `completed`, `pending_pipeline` or `error`; an evaluation that has started and not yet completed reads `pending_pipeline`, since it records itself on the asset before it evaluates any rule. When it is `error` that evaluation produced no verdict (every rule of it errored, or its schema could not be loaded), so `complianceState` is the state the asset held before it. An asset holding an active exception is in state `exception` and carries `exceptionGranted: true`, `exceptionReason`, `exceptionGrantedBy`, `exceptionGrantedAt`, and `exceptionSchemaName` / `exceptionSchemaVersion` — the schema name and version the exception is scoped to; revoking or superseding the exception sets `exceptionGranted` to `false` and clears the other exception fields. `quarantinedAt` is when the asset's current quarantine started: set by the evaluation (or exception revoke) that quarantined it, kept while the asset stays quarantined (a re-evaluation that quarantines it again, a binding change that keeps the quarantine), and `null` once it is released, granted an exception or evaluated to another state; `updatedAt` is the time of the record's last change of any kind (each write stamps its own time, so it never moves backwards); `lastEvaluatedAt` is when the last evaluation began. The rule failures behind a quarantine are summarised in `quarantineReason` while the asset is quarantined and listed in full on the last evaluation's `violations` (see [List the evaluations of an asset](#list-the-evaluations-of-an-asset)). A failed pipeline rule contributes its generic message (`Pipeline execution FAILED`) there; why its workflow execution failed is listed beside it in `quarantineExecutionFailures`, one `{ruleName, executionId, executionFailureReason}` entry per failed pipeline rule whose execution recorded an error, cleared with `quarantineReason`. The reason is returned only to a caller allowed to read that execution — see [Execution failure reasons](#execution-failure-reasons) — and any other caller's entry names the rule and the execution only.

A quarantined asset that a binding change reached is still `quarantined` and carries `awaitingReevaluationSince` (when the change reached the record), `awaitingReevaluationReason` (`schemaBound`, `schemaUnbound`, `overrideSet` or `overrideCleared`) and, when a schema applies, `awaitingReevaluationSchema`, the schema its next evaluation uses. Its `schemaName` and `schemaSource` name the schema that now applies; after a database unbind, or an override cleared in a database with no binding, no schema applies and the record carries neither. An evaluation that reaches a verdict removes the three fields, except a marker set after that evaluation began, which stays while the asset stays quarantined; one that ends in `error` leaves them. A release or an exception grant ends the quarantine and removes them too. The quarantine listing returns them.

`manualDecision` (`released` or `exceptionGranted`) and `manualDecisionAt` record a release or exception grant and when it was made: an evaluation that began before `manualDecisionAt` does not change the state, the quarantine fields or the exception fields, and an evaluation that began after it (or an exception revoke) removes them. `recordRevision` is a number every write of the record increments; a write computed from a read of the record is made only while the record still has the revision read. Clients should not depend on its value.

`assetName` is the asset's display name, kept on the record; a record written before names were kept has none. `registeredAt`, on a record an asset change or an evaluation created under its database's binding, is when that happened; it is informational. The record's other attributes, kept for the listings' name sort and text filter, are not returned.

A quarantined asset whose evaluation is waiting for its pipeline rules (running, or waiting for a running execution of their workflow) is still `quarantined`, with its `quarantineReason`, `quarantineExecutionFailures`, `quarantinedAt` and download block unchanged and `lastEvaluationStatus` `pending_pipeline`. Unless a binding change marked it first, it carries `awaitingReevaluationSince` (when the evaluation began), `awaitingReevaluationReason` `evaluationPending` and `awaitingReevaluationSchema` (the schema evaluated), written with the evaluation's first write to the record, so a read never finds the record pending without them. A quarantined asset whose evaluation has no pipeline rule carries them for the moment that evaluation runs. The verdict decides the asset and removes the fields; no `quarantine_released` entry or notification is written while it is pending, and a verdict that keeps it quarantined sends no second notification. In general the asset's subscribers are notified when it enters quarantine from another state (an evaluation's verdict or an exception revoke), and never by a verdict that keeps a quarantined asset quarantined. An evaluation that ends with no verdict, or one closed because the asset was archived, removes an `evaluationPending` marker and leaves the asset quarantined. Any other asset reads `pending_evaluation` while its evaluation is pending.

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid parameters    |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Get the compliance overview of a database

Retrieves per-state counts covering every tracked asset of a database, and one page of the tracked assets, filtered and sorted on the server. Only assets with a compliance record are counted. An archived asset keeps its record but is not evaluated, so it is left out of the page, `totalAssets` and every bucket of `summary`, for every caller, as the asset list leaves archived assets out.

```
GET /compliance/state/{databaseId}
```

### Query parameters

| Parameter         | Type   | Required | Default   | Description                                                                                                          |
| ----------------- | ------ | -------- | --------- | -------------------------------------------------------------------------------------------------------------------- |
| `state`           | string | No       | `null`    | Only assets in this state: `compliant`, `non_compliant`, `pending_evaluation`, `quarantined`, `exception`, `unknown` |
| `schemaName`      | string | No       | `null`    | Only assets bound to this schema                                                                                     |
| `schemaSource`    | string | No       | `null`    | `database` (the database binding) or `asset` (an asset override)                                                     |
| `evaluationError` | string | No       | `null`    | `true`: only assets whose last evaluation errored; `false`: only those whose last evaluation did not                 |
| `text`            | string | No       | `null`    | Case-insensitive contains on the asset name or ID (at most 256 characters)                                           |
| `sort`            | string | No       | `assetId` | `assetId` or `name`                                                                                                  |
| `maxItems`        | number | No       | `100`     | Asset records per page. A larger value is reduced to 200; the remainder follows `NextToken`.                         |
| `startingToken`   | string | No       | `null`    | Continuation token from a previous response's `NextToken`, valid only with the filters it was issued for.            |

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

`totalAssets` and `summary` cover every tracked asset of the database regardless of the page and the filters; they are counted with `COUNT` queries on the state index, so no records are read back for them. `summary` holds one bucket per state (`compliant`, `non_compliant`, `pending_evaluation`, `quarantined`, `exception`, `unknown`) plus `error`, the number of assets whose `lastEvaluationStatus` is `error`. `error` is an overlay on the state buckets, not a state: an asset whose last evaluation produced no verdict is counted under the state it kept and again under `error`, so the state buckets sum to `totalAssets` without it. `assets` is one page of their records in `assetId` order (or by name with `sort=name`) — each a compliance record as returned by [Get the compliance state of an asset](#get-the-compliance-state-of-an-asset), plus the asset's display name as `assetName`, kept on the compliance record — and `NextToken` is absent on the last page.

A `state` filter reads the state index, `sort=name` reads the database's name index, and the other filters are applied to the records read. A filtered page reads a bounded amount of the table, so it can hold fewer records than `maxItems`, even none, while `NextToken` is present: keep paging until no token is returned. With both `state` and `sort=name`, each page is sorted by name, not the listing as a whole.

Right after an update that adds the name index to an existing table, Amazon DynamoDB builds the index in the background, and until it is active it cannot be read. A `sort=name` request (without `state`) is then answered in `assetId` order with `"nameSortUnavailable": true`, and its `NextToken` continues in that order; once the index is active the flag is absent.

A record written before asset names were kept on compliance records has no `assetName` until its next evaluation, which writes the asset's current name onto the record (`POST /compliance/evaluate/{databaseId}` evaluates every tracked asset of a database), or until the asset is next modified; until then it is absent from `sort=name` and matches `text` by its ID only.

### Error responses

| Status | Description                                                                                                                                                             |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `databaseId`, filter value or pagination token, or no database has this id, live or deleted (`Database not found`; answered only to a caller allowed on the id) |
| `403`  | Not authorized                                                                                                                                                          |
| `500`  | Internal server error                                                                                                                                                   |

---

## List quarantined assets

Retrieves one page of assets in the `quarantined` state across databases, each with its `assetName` and `permissions` (`release`, `exception`: whether the caller may release the asset or grant it an exception, a `POST` on the quarantined evaluation object of its database); an asset released under an exception (state `exception`) is not listed, nor is an archived asset, for any caller. The page is filtered to the databases the caller may read after it is read, so a page can be empty while `NextToken` is present — keep following the token until none is returned. With `databaseId`, the caller's access to that database is decided before any of it is read: a caller who may not read it gets one empty page without `NextToken`, the same answer as for an id that names no database.

```
GET /compliance/quarantine
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                  |
| --------------- | ------ | -------- | ------- | -------------------------------------------------------------------------------------------- |
| `databaseId`    | string | No       | `null`  | Only the quarantined assets of this database                                                 |
| `text`          | string | No       | `null`  | Case-insensitive contains on the asset name or ID                                            |
| `maxItems`      | number | No       | `100`   | Asset records per page. A larger value is reduced to 200; the remainder follows `NextToken`. |
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
            "quarantinedAt": "2026-03-15T10:30:00+00:00",
            "lastEvaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "lastEvaluatedAt": "2026-03-15T10:30:00+00:00",
            "updatedAt": "2026-03-16T08:12:40+00:00"
        }
    ],
    "NextToken": "example-page-token"
}
```

`quarantinedAt` is when the asset's current quarantine started; `updatedAt` is the record's last change (a re-evaluation, a binding change), so it can be later. `NextToken` is absent on the last page.

### Error responses

| Status | Description                                                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid pagination token, or `databaseId` names no database, live or deleted (`Database not found`; answered only to a caller allowed on the id) |
| `403`  | Not authorized                                                                                                                                   |
| `500`  | Internal server error                                                                                                                            |

---

## Release an asset from quarantine

Sets a quarantined asset to `compliant` without recording an exception, records the decision on its compliance record (`manualDecision` `released`, `manualDecisionAt`), and writes a `quarantine_released` audit entry. The asset is re-quarantined if an evaluation that begins after the release fails a `quarantine` rule again; an evaluation already running when the release is made records its verdict as not applied (`notApplied`) and leaves the asset released.

The release is decided on the compliance record as it is when the write is made: when another write (a passing verdict, an exception) changed the record since it was read, the record is read again, and an asset no longer quarantined answers `Asset is not quarantined`. When the record changes under three tries, the route answers `400` `The asset's compliance record changed while the request was made; try again` and writes nothing. The same holds for granting and revoking an exception.

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

| Status | Description                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset has no compliance record, the asset is not quarantined, or its record changed under every try |
| `403`  | Not authorized                                                                                                              |
| `500`  | Internal server error                                                                                                       |

---

## Grant an exception to a quarantined asset

Moves a quarantined asset to the `exception` state, records the exception on its compliance record (`exceptionGranted`, `exceptionReason`, `exceptionGrantedBy`, `exceptionGrantedAt`, and the bound schema's name and current version as `exceptionSchemaName` / `exceptionSchemaVersion`), clears `quarantineReason`, and writes an `exception_granted` audit entry. The grant is recorded as a decision (`manualDecision` `exceptionGranted`, `manualDecisionAt`), so an evaluation already running when it is made records its verdict as not applied (`notApplied`) and leaves the exception in place.

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

| Status | Description                                                                                                                                  |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, no reason given, the asset has no compliance record, the asset is not quarantined, or its record changed under every try |
| `403`  | Not authorized                                                                                                                               |
| `500`  | Internal server error                                                                                                                        |

---

## Revoke an exception

Ends an asset's active exception. The exception fields are cleared and the asset returns to the state its last evaluation's verdict maps to — `quarantined` again, with `quarantineReason` restored and the asset's subscribers notified, when that verdict was quarantined — or to `pending_evaluation` when the asset has no recorded evaluation. An `exception_revoked` audit entry is written. The request carries no body. The grant's decision (`manualDecision`) is removed with it, so an evaluation still running decides the asset normally when it ends.

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

| Status | Description                                                                                                           |
| ------ | --------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, the asset has no compliance record, no exception is active, or its record changed under every try |
| `403`  | Not authorized                                                                                                        |
| `500`  | Internal server error                                                                                                 |

---

## List cascades

Retrieves one page of the cascades in a state — `pending_approval` unless `state` names another — that the caller may read, newest first. A row is listed only when the caller may read both the cascade (in the parent asset's database) and the compliance evaluations of that database. Each row carries `permissions.approve`: whether the caller may approve or reject it.

Approving a cascade (or creating one with `requireApproval: false`) records the databases the caller may evaluate. The cascade evaluates only the descendants in those databases; the others are recorded `skipped_not_authorized`, never evaluated with the system identity. The record is not returned.

A cascade still pending at its `approvalTimeoutAt` (24 hours after creation) can no longer be approved. Whichever request reads it first aborts it: this listing (which then leaves it out), [Get a cascade](#get-a-cascade), an approve or reject (refused with `400`), or the next evaluation of the parent, which then opens a new cascade. The cascade is recorded `aborted` with `abortReason` `approval timed out` and a `cascade_timed_out` audit entry.

```
GET /compliance/cascades
```

### Query parameters

| Parameter       | Type   | Required | Default            | Description                                                                             |
| --------------- | ------ | -------- | ------------------ | --------------------------------------------------------------------------------------- |
| `state`         | string | No       | `pending_approval` | `pending_approval`, `executing`, `completed` or `aborted`                               |
| `databaseId`    | string | No       | `null`             | Only cascades whose trigger asset is in this database                                   |
| `text`          | string | No       | `null`             | Case-insensitive contains on the trigger asset's name or ID                             |
| `maxItems`      | number | No       | `100`              | Cascades per page. A larger value is reduced to 200; the remainder follows `NextToken`. |
| `startingToken` | string | No       | `null`             | Continuation token from a previous response's `NextToken`.                              |

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
            "assetName": "Control 0001",
            "triggerReason": "Parent asset updated; downstream re-evaluation needed",
            "createdAt": "2026-03-15T10:31:12+00:00",
            "actor": "SYSTEM_USER",
            "requireApproval": true,
            "approvalTimeoutAt": "2026-03-16T10:31:12+00:00",
            "nodes": "{}",
            "executionOrder": "[]"
        }
    ],
    "NextToken": "eyJjYXNjYWRlSWQiOiAiLi4uIn0="
}
```

Each row carries `databaseId` and `assetId` naming the trigger asset, copies of `triggeredByDatabaseId` and `triggeredByAssetId`, and `assetName`, the trigger asset's name when the cascade was created. A page can hold fewer rows than `maxItems` while `NextToken` is present; `NextToken` is absent on the last page.

### Error responses

| Status | Description                                                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `state`, filter value or pagination token, or `databaseId` names no database, live or deleted (`Database not found`; answered only to a caller allowed on the id) |
| `403`  | Not authorized                                                                                                                                                            |
| `500`  | Internal server error                                                                                                                                                     |

---

## Create a cascade

Creates a cascade that re-evaluates the downstream assets of an asset — every descendant reachable through `parentChild` asset links, in dependency order. With `requireApproval` (the default) the cascade waits in `pending_approval` for [approval](#approve-a-pending-cascade) and the response is `200`. With `requireApproval: false` the cascade is written in the `executing` state, its execution is started in the background and the response is `202`; the evaluations do not run inside the request. This route always opens a new cascade; only the cascade an evaluation opens automatically for an asset with children is de-duplicated — while one for the same parent is pending approval, further evaluations of that parent open no new one, including evaluations that finish at the same moment. A cascade created here neither waits for nor blocks that automatic one.

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
    "executionOrder": "[\"survey-project:x1a2b3c4-scan-0042\", \"survey-project:x1a2b3c4-scan-0043\"]",
    "completedAt": "2026-03-15T11:02:44+00:00"
}
```

`state` is `pending_approval`, `executing`, `completed` or `aborted`. `nodes` is a JSON-encoded map of `databaseId:assetId` to the node's status — `pending`, `evaluating`, the verdict the evaluation produced (`compliant`, `non_compliant`, `quarantined`, `pending_pipeline`, `error`), `skipped` for a descendant with no bound schema or that no longer exists (its compliance record is removed), `skipped_not_authorized` for a descendant in a database the caller who started the cascade (its approver, or the creator of a cascade without approval) may not evaluate, `skipped_archived` for a descendant that is archived when the cascade reaches it (not evaluated; its compliance record and history are left as they are), or `superseded` for a descendant whose evaluation found a newer one of it already started (that evaluation settles the asset's state; not an error) — and `executionOrder` the JSON-encoded list of the same `databaseId:assetId` keys in evaluation order. `nodeNames` is a JSON-encoded map of the same keys to each node asset's name, recorded when the cascade starts from the compliance record it reads for the node (a `skipped_not_authorized` node is not named); `assetName` is the parent (trigger) asset's name. A `completed` cascade also carries `results`, a JSON-encoded list of `{node, status, evaluationId}` with the same statuses. A skipped or superseded node does not abort the cascade. A cascade read is authorized on its parent's database, and its nodes can sit in other databases: a node is returned only to a caller who may read that asset's compliance record (the evaluation `GET` on the node's database, and for an archived asset the archived-record rule). A node in a database the caller may not read is left out of `nodes`, `executionOrder`, `nodeNames`, `results` and `totalNodes` and counted in `unauthorizedNodes` (0 when there are none), without its id, name or verdict, as asset links count the linked assets a reader may not see. A node that is archived, or that the cascade skipped as archived (`skipped_archived`), is returned only to a caller who may see that archived asset (allowed on the archived record `<databaseId>#deleted` and on reading the archived asset itself, as the asset list with archived assets decides it); for any other caller it is left out of the same attributes and not counted. Both rules hold in this response and in the cascade listing. The response carries `permissions.approve`: whether the caller may approve or reject it (a pending cascade, `POST` on the cascade and on the evaluation object of its parent's database). A rejected cascade carries `rejectedBy`, `rejectedAt` and `rejectionReason` instead of the approval fields. An `aborted` cascade that was rejected carries `rejectedBy`, `rejectedAt` and `rejectionReason` (the reason given, `rejected` when none was) and no `abortReason`; any other `aborted` cascade carries `abortReason`: `approval timed out`, or the failure that stopped it from starting or ended the run.

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

| Status | Description                                                                                                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid `cascadeId`, the cascade does not exist or is not in `pending_approval`, its approval timed out (`Cascade approval timed out; the cascade was aborted`), or the cascade could not be started (recorded `aborted`) |
| `403`  | Not authorized                                                                                                                                                                                                            |
| `500`  | Internal server error                                                                                                                                                                                                     |

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

| Status | Description                                                                                                                                                        |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid `cascadeId`, the cascade does not exist or is not in `pending_approval`, or its approval timed out (`Cascade approval timed out; the cascade was aborted`) |
| `403`  | Not authorized                                                                                                                                                     |
| `500`  | Internal server error                                                                                                                                              |

---

## Query the compliance audit trail

Retrieves audit entries across assets, newest first, one page per call; entries of different event types interleave by time. The continuation token is opaque: pass it back as `startingToken` with the same filters it was issued for. Entries are filtered to those whose database the caller may read; an entry that names no database is kept only for a caller whose `complianceEvaluation` permission does not restrict `databaseId`.

Only the entries a page can show are read. With `databaseId`, the listing reads that database's entries alone, once it has decided that the caller may read that database (and, with `assetId`, that asset). A caller who may not gets one empty page without `NextToken`, the same answer as for an id that names no database, and nothing of the database is read. Without it, a caller who may read only some databases (at most 25) reads each of them, merged by time, and a caller who may read none gets an empty page without `NextToken`. Any other caller reads the whole trail, by event type when `eventType` is given. So entries of databases the caller does not see never take a page's place.

Each request reads a bounded part of what it reads and answers within it, so a page can still hold fewer entries than `maxItems`, even none, while `NextToken` is present. This happens when the `text` or `eventType` filter, or the caller's asset permissions, leave out most of the entries read, or when a caller who may read more than 25 databases, but not every one, reads the whole trail. Keep following `NextToken` until none is returned.

Right after an update that adds the database index to an existing audit table, Amazon DynamoDB builds it in the background. Until it is active, a first page reads the whole trail as before, and its `NextToken` continues there.

```
GET /compliance/audit
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                                                           |
| --------------- | ------ | -------- | ------- | --------------------------------------------------------------------------------------------------------------------- |
| `eventType`     | string | No       | `null`  | Restrict the page to one event type — see [Event types](#event-types).                                                |
| `databaseId`    | string | No       | `null`  | Only entries of this database (a deleted database's history: `<databaseId>%23deleted`); only they are read.           |
| `assetId`       | string | No       | `null`  | Only entries of this asset; requires `databaseId`, and combines with `eventType`.                                     |
| `text`          | string | No       | `null`  | Case-insensitive contains on the asset name recorded with the entry, the asset ID or the actor.                       |
| `startDate`     | string | No       | `null`  | ISO-8601 timestamp; only entries at or after it are returned.                                                         |
| `endDate`       | string | No       | `null`  | ISO-8601 timestamp; only entries at or before it are returned.                                                        |
| `maxItems`      | number | No       | `100`   | Entries per page. A larger value is reduced to 200; the remainder follows `NextToken`. Takes precedence over `limit`. |
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
            "assetName": "Scan 0043",
            "actor": "user@example.com",
            "schemaName": "survey-compliance",
            "evaluationId": "8a1d2c3b-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "details": "{\"verdict\": \"quarantined\", \"ruleResultCount\": 2, \"pipelineRulesPending\": 0}",
            "previousState": "pending_evaluation",
            "newState": "quarantined"
        }
    ],
    "NextToken": "example-page-token"
}
```

`details` is a JSON-encoded object whose keys depend on the event type. `previousState` and `newState` are present on entries that changed an asset's compliance state; `schemaName`, `evaluationId` and `cascadeId` are present when the event concerns one. Entries about a schema rather than an asset (`schema_deleted`) carry `*` as `databaseId` and `assetId`. `assetName` is the asset's name when the entry was written; entries written before names were recorded have none. The `text` filter, and `eventType` together with `databaseId` or for a caller who may read only some databases, are applied to the entries read, a bounded amount per page, so such a page can hold fewer entries than `maxItems` while `NextToken` is present. `NextToken` is absent on the last page.

### Event types

| Event type                         | Written when                                                                                                                                                                                       |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `compliance_check`                 | An asset is evaluated                                                                                                                                                                              |
| `evaluation_error`                 | A rule's tooling failed in an evaluation, or the schema could not be loaded; `details` names the errored rules as `ruleNames`                                                                      |
| `quarantine_released`              | A quarantined asset is released, by request or by a re-evaluation that moves it from `quarantined` to `compliant` (see [Quarantine](../concepts/compliance.md#quarantine))                         |
| `exception_granted`                | An exception is granted to a quarantined asset                                                                                                                                                     |
| `exception_revoked`                | An active exception is revoked; the asset returns to its last verdict's state                                                                                                                      |
| `exception_superseded`             | An evaluation against another schema or a newer schema version clears an active exception                                                                                                          |
| `schema_bound_to_database`         | A schema is bound to a database                                                                                                                                                                    |
| `schema_unbound_from_database`     | A database binding is removed                                                                                                                                                                      |
| `schema_bound_to_asset`            | A schema is bound to an asset as an override                                                                                                                                                       |
| `schema_unbound_from_asset`        | An asset override is removed                                                                                                                                                                       |
| `schema_deleted`                   | A schema is deleted                                                                                                                                                                                |
| `cascade_triggered`                | A cascade is created through the API                                                                                                                                                               |
| `cascade_auto_triggered`           | A cascade is opened after the evaluation of an asset that has children; `details` carries `reason` and `childCount`, the direct children (see below)                                               |
| `cascade_approved`                 | A pending cascade is approved                                                                                                                                                                      |
| `cascade_rejected`                 | A pending cascade is rejected                                                                                                                                                                      |
| `cascade_timed_out`                | A pending cascade reached its `approvalTimeoutAt` unapproved and was aborted (`abortReason` `approval timed out`); written by the system actor                                                     |
| `schema_binding_applied_to_asset`  | A binding change (a database binding set or removed) reached one asset: `previousState`, `newState` (a quarantined asset stays `quarantined`, marked awaiting re-evaluation) and the schema        |
| `schema_binding_auto_eval_changed` | Re-binding the schema in force changed only automatic evaluation (`details.complianceAutoEval`, `details.evaluationsQueued`)                                                                       |
| `schema_binding_apply_finished`    | A database binding change applied in the background ended (`details.outcome` `applied` or `failed`, `details.reason`, the counts); actor: the person who made the change                           |
| `cascade_database_deleted`         | A cascade awaiting approval was aborted because its database was deleted (`abortReason` `The database was deleted`); written by the system actor                                                   |
| `database_deleted`                 | A database was deleted: its compliance data moves under `<databaseId>#deleted` (`details` counts the cascades and waiting pipeline rules closed); recorded under `<databaseId>#deleted`            |
| `database_compliance_moved`        | The move of a deleted database's compliance data under `<databaseId>#deleted` finished (`details.moved` counts the rows per kind); recorded under `<databaseId>#deleted`                           |
| `cascade_completed`                | A cascade finishes evaluating its downstream assets; `details` carries `summary`, the node count per status, and `nodesEvaluated`, how many of those nodes were evaluated to a verdict (see below) |
| `asset_deleted`                    | An asset is permanently deleted; its compliance record (state, binding override, quarantine) is removed                                                                                            |

A `cascade_completed` entry counts each descendant the cascade skipped as archived only for a reader who may see that archived asset (allowed on the archived record `<databaseId>#deleted` and on reading the archived asset itself). For such a reader, `summary` counts them under `skipped_archived` and `skippedArchivedByDatabase` maps each database to the number of them it holds. For any other reader they are in neither, on every path that returns the entry. They were not evaluated, so `nodesEvaluated` counts them for no reader: it counts the nodes evaluated to a verdict (`compliant`, `non_compliant`, `quarantined`, `pending_pipeline`), and the skipped, superseded and failed (`error`) nodes are in `summary` only, as in the cascade's progress on the Cascade Approvals page and in `vamscli compliance cascade get`. The subscriber notification of a finished cascade, one message for every subscriber, lists the node count per status without them.

A `cascade_completed` entry counts the results of the descendants in another database than the parent's only for a reader who may read that database (the evaluation `GET` on it): for such a reader they are in `summary`, and those evaluated to a verdict in `nodesEvaluated`; for any other reader they are counted in `unauthorizedNodes`, without their database, ids or results. `unauthorizedNodes` is present on an entry whose cascade reached another database. Within the databases the reader may read, an entry counts in `summary` and `nodesEvaluated` only the descendants the reader may read (for a reader whose permissions have asset constraints, `GET` on the asset as stored, as for the asset's own compliance records); one the reader may not read is counted in `unauthorizedNodes`, as the cascade's `GET` and listing count it, and `unauthorizedNodes` is then present. A descendant archived after the cascade reached it that the reader may not see is left out, as on the cascade. The entry is decided on the descendants it records by asset id, ids it never returns; an entry written by an earlier build has no such record and counts its descendants per database. The subscriber notification lists the node count per status of the parent's database only, and gives the number of downstream assets in other databases without their results.

A `cascade_auto_triggered` entry counts the direct children that are archived (archiving keeps an asset's links) the same way: for a reader who may see them, `childCount` includes them and `archivedChildrenByDatabase` maps each database to the number of them it holds; for any other reader `childCount` counts the other children only. `childCount` counts the children in every database; it names none. The approval notification, one message for every subscriber, counts the other children only.

### Error responses

| Status | Description                                                                                                                                                    |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters or pagination token, or `databaseId` names no database, live or deleted (`Database not found`; answered only to a caller allowed on the id) |
| `403`  | Not authorized                                                                                                                                                 |
| `500`  | Internal server error                                                                                                                                          |

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
