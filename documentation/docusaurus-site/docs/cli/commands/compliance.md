---
sidebar_label: Compliance
title: Compliance Commands
---

# Compliance Commands

Manage compliance schemas, bind them to databases and assets, evaluate assets against them, and handle the quarantine, cascade and audit trail that result.

A compliance schema is a rule set. Each rule has a type — `pipeline` (run a VAMS workflow and compare the pipeline's measurements to tolerances), `metadata` (validate the asset's metadata against a metadata schema) or `relationship` (check the asset's links) — and an enforcement level: a failed `quarantine` rule quarantines the asset, a failed `warn` rule marks it non-compliant, and an `inform` rule only reports. A schema is bound to a database, so every asset in it inherits the binding, or to a single asset as an override. See the [Compliance user guide](../../user-guide/compliance.md) for the rule format in full.

Every command below takes `--json-output`; the JSON is the API response unchanged.

---

## Schema Commands

A schema is addressed by name. Registering a name that already exists writes a new version of the schema; `schema get` and `schema list` always return the latest version.

---

## compliance schema list

List compliance schemas (the latest version of each).

```bash
vamscli compliance schema list [OPTIONS]
```

| Option                | Type | Required | Description                                                 |
| --------------------- | ---- | -------- | ----------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | No       | Only schemas scoped to this database, plus `GLOBAL` schemas |
| `--json-output`       | FLAG | No       | Output raw JSON response (`{"schemas": [...]}`)             |

```bash
vamscli compliance schema list
vamscli compliance schema list -d my-database
vamscli compliance schema list --json-output
```

:::note[The listing is returned whole]
The route returns every schema in one response and takes no paging options; there is no `--starting-token`.
:::

Each record carries `schemaFormat`, printed as `Format:` — `vams-rules-v1`, or `legacy` for a row whose body is not a `vams-rules-v1` document. A legacy schema cannot be bound (`compliance bind` is refused with `Schema body must be a vams-rules-v1 document`), updated or evaluated; it is listed so that it can be found and removed with `compliance schema delete`.

---

## compliance schema get

Get the latest version of a schema, including its body.

```bash
vamscli compliance schema get [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-n`, `--schema-name` | TEXT | Yes      | Schema name              |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli compliance schema get -n cad-quality
vamscli compliance schema get -n cad-quality --json-output
```

The output prints the record's `Format:` (`vams-rules-v1` or `legacy`, as on `schema list`) before the body.

---

## compliance schema create

Register a compliance schema.

```bash
vamscli compliance schema create [OPTIONS]
```

| Option                | Type | Required | Description                                                                               |
| --------------------- | ---- | -------- | ----------------------------------------------------------------------------------------- |
| `-n`, `--schema-name` | TEXT | Yes      | Schema name (3-63 characters; letters, digits, hyphens, underscores)                      |
| `--schema-file`       | TEXT | Yes      | Path to a JSON file holding the schema body, or the body as an inline JSON object         |
| `-d`, `--database-id` | TEXT | No       | Database the schema is scoped to; omit for a `GLOBAL` schema that every database can bind |
| `--description`       | TEXT | No       | Schema description                                                                        |
| `--json-output`       | FLAG | No       | Output raw JSON response                                                                  |

The schema body is a `vams-rules-v1` document. A pipeline rule names the workflow it executes and the pipeline whose measurements its checks read:

```json
{
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "wall-thickness": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {
                "databaseId": "GLOBAL",
                "workflowId": "cad-inspection",
                "pipelineDatabaseId": "GLOBAL",
                "pipelineId": "wall-thickness-check",
                "templateId": "default"
            },
            "checks": [
                {
                    "name": "minimum-wall",
                    "outputField": "minWallThicknessMm",
                    "tolerance": { "operator": "gte", "value": 1.5 }
                }
            ]
        },
        "has-owner": {
            "ruleType": "metadata",
            "enforcement": "warn",
            "metadataSchemaRef": { "databaseId": "GLOBAL", "schemaName": "ownership" },
            "checks": [{ "name": "required-fields", "validateRequired": true }]
        }
    }
}
```

`pipelineRef.databaseId` is the **workflow's** database and `pipelineDatabaseId` the pipeline's; both accept `GLOBAL`. `templateId` is optional. A pipeline rule's `inputFiles` selects the asset files the execution receives: `{"mode": "matching"}` (the default) lists the asset's files and applies the workflow's, the pipeline's and the rule's own `filter` globs — a single-input workflow must be left with exactly one file; `{"mode": "wholeAsset"}` sends the asset root where the workflow allows it; `{"mode": "explicit", "keys": ["/path/file.glb"]}` sends the listed asset-relative files. `compliance-output.json` is optional for the pipeline: without it the checks can read only `execution_success` and `processing_duration_seconds`. Only `vams-rules-v1` bodies are accepted; any other body is rejected with `schemaBody must be a vams-rules-v1 document`.

```bash
vamscli compliance schema create -n cad-quality --schema-file cad-quality.json
vamscli compliance schema create -n cad-quality --schema-file cad-quality.json -d my-database --description "CAD deliverable checks"
vamscli compliance schema create -n cad-quality --schema-file cad-quality.json --json-output
```

---

## compliance schema update

Write a new version of a schema. At least one of `--schema-file`, `--database-id` or `--description` is required.

```bash
vamscli compliance schema update [OPTIONS]
```

| Option                | Type | Required | Description                                                         |
| --------------------- | ---- | -------- | ------------------------------------------------------------------- |
| `-n`, `--schema-name` | TEXT | Yes      | Schema to update                                                    |
| `--schema-file`       | TEXT | No       | Path to a JSON file holding the new schema body, or the body inline |
| `-d`, `--database-id` | TEXT | No       | Re-scope the schema to this database, or `GLOBAL`                   |
| `--description`       | TEXT | No       | New description                                                     |
| `--json-output`       | FLAG | No       | Output raw JSON response                                            |

```bash
vamscli compliance schema update -n cad-quality --schema-file cad-quality-v2.json
vamscli compliance schema update -n cad-quality --description "Tightened tolerances"
```

:::note[The body is replaced, not merged]
`--schema-file` replaces the whole rule document. Assets bound to the schema keep their current state until their next evaluation; run `compliance sweep` to re-evaluate them against the new version.
:::

---

## compliance schema delete

Remove a schema that is not bound to any database or asset. Every version of the schema is removed.

```bash
vamscli compliance schema delete [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-n`, `--schema-name` | TEXT | Yes      | Schema to remove         |
| `--confirm`           | FLAG | Yes      | Confirm the removal      |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli compliance schema delete -n cad-quality --confirm
vamscli compliance schema delete -n cad-quality --confirm --json-output
```

:::warning[Bound schemas cannot be removed]
The API refuses to remove a schema that a database or asset is still bound to. Run `compliance unbind` for each binding first (`compliance bindings -d <database>` shows them). The removal is not reversible and is written to the audit trail as `schema_deleted`.
:::

---

## Binding Commands

---

## compliance bind

Bind a schema to a database, or to one asset as an override of the database binding.

```bash
vamscli compliance bind [OPTIONS]
```

| Option                | Type | Required | Description                                                                    |
| --------------------- | ---- | -------- | ------------------------------------------------------------------------------ |
| `-n`, `--schema-name` | TEXT | Yes      | Schema to bind                                                                 |
| `-d`, `--database-id` | TEXT | Yes      | Database to bind, or the database of `--asset-id`                              |
| `-a`, `--asset-id`    | TEXT | No       | Bind this asset instead, overriding the database binding                       |
| `--no-auto-eval`      | FLAG | No       | Do not re-evaluate the database's assets automatically (database binding only) |
| `--json-output`       | FLAG | No       | Output raw JSON response                                                       |

```bash
vamscli compliance bind -n cad-quality -d my-database
vamscli compliance bind -n cad-quality -d my-database --no-auto-eval
vamscli compliance bind -n strict-cad -d my-database -a my-asset
```

A database binding marks every asset in the database that has no override `pending_evaluation` and reports the count as `assetsPendingEvaluation`. Only a `GLOBAL` schema or one scoped to the database can be bound, and a `legacy` schema (see `schema list`) is refused with `Schema body must be a vams-rules-v1 document`. `--no-auto-eval` is rejected together with `--asset-id`: the asset route does not read that flag.

---

## compliance unbind

Remove a database's schema binding, or one asset's override.

```bash
vamscli compliance unbind [OPTIONS]
```

| Option                | Type | Required | Description                                                                |
| --------------------- | ---- | -------- | -------------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database to unbind, or the database of `--asset-id`                        |
| `-a`, `--asset-id`    | TEXT | No       | Remove this asset's override instead, falling back to the database binding |
| `--json-output`       | FLAG | No       | Output raw JSON response                                                   |

```bash
vamscli compliance unbind -d my-database
vamscli compliance unbind -d my-database -a my-asset
```

:::warning[Unbinding a database removes compliance records]
Unbinding a database deletes the compliance record (state, last evaluation, quarantine and exception fields) of every asset that inherited the binding, and reports the count as `removedComplianceRecords`. Asset overrides are kept, and the evaluation history and audit trail are not removed. Removing an asset's override reverts it to the database binding and marks it `pending_evaluation`. Both forms succeed when there was nothing to remove.
:::

---

## compliance bindings

Show a database's schema binding and one page of its asset-level overrides.

```bash
vamscli compliance bindings [OPTIONS]
```

| Option                | Type    | Required | Description                                                              |
| --------------------- | ------- | -------- | ------------------------------------------------------------------------ |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                                                              |
| `--max-items`         | INTEGER | No       | Asset overrides per page (the API applies 100 when omitted; at most 500) |
| `--starting-token`    | TEXT    | No       | Token for pagination (the previous page's `NextToken`)                   |
| `--json-output`       | FLAG    | No       | Output raw JSON response                                                 |

```bash
vamscli compliance bindings -d my-database
vamscli compliance bindings -d my-database --max-items 20 --starting-token "token123"
vamscli compliance bindings -d my-database --json-output
```

`assetOverrideCount` is the total number of asset-level overrides; `assetOverrides` is one page of them. The response carries a `NextToken` when more overrides exist; pass it back as `--starting-token`.

---

## Evaluation and State Commands

---

## compliance evaluate

Evaluate an asset against its bound schema.

```bash
vamscli compliance evaluate [OPTIONS]
```

| Option                | Type | Required | Description                                                    |
| --------------------- | ---- | -------- | -------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID                                                    |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset ID                                                       |
| `-n`, `--schema-name` | TEXT | No       | Schema to evaluate against (defaults to the asset's bound one) |
| `--json-output`       | FLAG | No       | Output raw JSON response                                       |

```bash
vamscli compliance evaluate -d my-database -a my-asset
vamscli compliance evaluate -d my-database -a my-asset -n cad-quality --json-output
```

Metadata and relationship rules are evaluated within the request and their results returned as `ruleResults`, with the `verdict`, `complianceState` and the `schemaVersion` evaluated. Pipeline rules start a workflow execution and complete asynchronously: `pipelineRulesPending` counts them, and `compliance state` shows the final verdict once the workflow finishes. A failed quarantine-level rule quarantines the asset at once. The response also carries `exceptionApplied` (an active exception held the asset released) and `hasRuleErrors`.

A rule whose tooling failed — its input selection did not resolve to the files the workflow takes, or the execution could not start — is a rule result with `status` `error`, printed as `! rule-name [enforcement] (error): message` apart from a failed rule's `✗`. It is not a verdict: its enforcement does not apply, the verdict comes from the remaining rules (`Rule Errors: yes: <names>`), and when every rule errored the evaluation is an `error` that leaves the asset's compliance state unchanged (`lastEvaluationStatus` `error` on its record).

---

## compliance sweep

Re-evaluate every asset bound to a schema — the usual follow-up to `compliance schema update`.

```bash
vamscli compliance sweep [OPTIONS]
```

| Option                | Type | Required | Description                        |
| --------------------- | ---- | -------- | ---------------------------------- |
| `-n`, `--schema-name` | TEXT | Yes      | Schema whose assets to re-evaluate |
| `--json-output`       | FLAG | No       | Output raw JSON response           |

```bash
vamscli compliance sweep -n cad-quality
vamscli compliance sweep -n cad-quality --json-output
```

:::note[A sweep runs one evaluation per bound asset]
A schema with pipeline rules starts one workflow execution per asset it is bound to. Check `compliance bindings` on the databases that use the schema before sweeping a large one.
:::

The response lists the assets triggered and counts the rest: `skipped` is the number of bound assets the caller is not authorized to evaluate (counted, never listed), and `assetsRemaining` the number beyond the per-call cap, which a repeated sweep works through.

---

## compliance state

Show compliance state: one asset's record, or a database's overview.

```bash
vamscli compliance state [OPTIONS]
```

| Option                | Type    | Required | Description                                                                                     |
| --------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                                                                                     |
| `-a`, `--asset-id`    | TEXT    | No       | One asset's record; omit for the database overview                                              |
| `--max-items`         | INTEGER | No       | Asset records per page of the database overview (the API applies 100 when omitted; at most 500) |
| `--starting-token`    | TEXT    | No       | Token for pagination of the database overview (the previous page's `NextToken`)                 |
| `--json-output`       | FLAG    | No       | Output raw JSON response                                                                        |

```bash
vamscli compliance state -d my-database
vamscli compliance state -d my-database --max-items 20 --starting-token "token123"
vamscli compliance state -d my-database -a my-asset
vamscli compliance state -d my-database --json-output
```

`complianceState` is one of `compliant`, `non_compliant`, `pending_evaluation`, `quarantined`, `exception` or `unknown`. An asset with no binding is reported as `unknown` rather than as an error. A record also carries `lastEvaluationStatus` (`completed`, `pending_pipeline` or `error`); `error` means the last evaluation produced no verdict, so `complianceState` is the state the asset held before it, and the CLI says so on the `Last Evaluation Status` line. The database overview carries a per-state `summary` and `totalAssets` covering every tracked asset, and one page of their records as `assets`, each with its `assetName`; only assets with a compliance record are counted. The summary's `error` count is the number of assets whose last evaluation errored — an overlay on the state buckets, not a state — and such a row is printed with `(evaluation error)` after its state. The response carries a `NextToken` when more records exist; pass it back as `--starting-token`. `--max-items` and `--starting-token` are rejected with `-a`, because the single-asset route is not paged.

---

## compliance evaluations

List an asset's evaluation history, most recent first.

```bash
vamscli compliance evaluations [OPTIONS]
```

| Option                | Type    | Required | Description                                                         |
| --------------------- | ------- | -------- | ------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                                                         |
| `-a`, `--asset-id`    | TEXT    | Yes      | Asset ID                                                            |
| `--max-items`         | INTEGER | No       | Evaluations per page (the API applies 50 when omitted; at most 200) |
| `--starting-token`    | TEXT    | No       | Token for pagination (the previous page's `NextToken`)              |
| `--json-output`       | FLAG    | No       | Output raw JSON response                                            |

```bash
vamscli compliance evaluations -d my-database -a my-asset
vamscli compliance evaluations -d my-database -a my-asset --max-items 10
vamscli compliance evaluations -d my-database -a my-asset --starting-token "token123" --json-output
```

Each evaluation carries its `evaluationId`, `schemaName`, `evaluatedAt`, the `verdict` and `ruleResults` once complete, and — for an evaluation with pipeline rules — the `executionId` of the workflow execution behind it. A row with `hasRuleErrors` names in `errorRules` the rules whose tooling failed; they are printed as `! rule-name [enforcement] (error)` apart from the failed rules and are absent from `violations`, because they produced no verdict. The response carries a `NextToken` when more evaluations exist; pass it back as `--starting-token`.

---

## Quarantine Commands

---

## compliance quarantine list

List quarantined assets across every database the caller may read, one page per call.

```bash
vamscli compliance quarantine list [OPTIONS]
```

| Option             | Type    | Required | Description                                                                 |
| ------------------ | ------- | -------- | --------------------------------------------------------------------------- |
| `--max-items`      | INTEGER | No       | Quarantined assets per page (the API applies 100 when omitted; at most 500) |
| `--starting-token` | TEXT    | No       | Token for pagination (the previous page's `NextToken`)                      |
| `--json-output`    | FLAG    | No       | Output raw JSON response (`{"quarantinedAssets": [...], "NextToken": ...}`) |

```bash
vamscli compliance quarantine list
vamscli compliance quarantine list --max-items 20 --starting-token "token123"
vamscli compliance quarantine list --json-output
```

The response carries a `NextToken` when more quarantined assets exist; pass it back as `--starting-token`. Each page is filtered to the caller's databases after it is read, so a page can be empty while a token is present — keep paging until no token is returned.

---

## compliance quarantine release

Release an asset from quarantine, returning it to the `compliant` state.

```bash
vamscli compliance quarantine release [OPTIONS]
```

| Option                | Type | Required | Description                        |
| --------------------- | ---- | -------- | ---------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID                        |
| `-a`, `--asset-id`    | TEXT | Yes      | Quarantined asset ID               |
| `--reason`            | TEXT | No       | Reason recorded in the audit trail |
| `--json-output`       | FLAG | No       | Output raw JSON response           |

```bash
vamscli compliance quarantine release -d my-database -a my-asset
vamscli compliance quarantine release -d my-database -a my-asset --reason "Source data corrected"
```

The next evaluation can quarantine the asset again; use `quarantine exception` to record a deliberate waiver. An asset that is not quarantined is refused.

---

## compliance quarantine exception

Grant a quarantined asset an exception. The asset moves to the `exception` state with the justification, the granting user and the schema name and version the exception was granted against recorded on its compliance record and in the audit trail. The exception holds until it is revoked or superseded by an evaluation against another schema or version; meanwhile re-evaluations record their violations but never re-quarantine the asset.

```bash
vamscli compliance quarantine exception [OPTIONS]
```

| Option                | Type | Required | Description                         |
| --------------------- | ---- | -------- | ----------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID                         |
| `-a`, `--asset-id`    | TEXT | Yes      | Quarantined asset ID                |
| `--reason`            | TEXT | Yes      | Justification recorded on the asset |
| `--json-output`       | FLAG | No       | Output raw JSON response            |

```bash
vamscli compliance quarantine exception -d my-database -a my-asset --reason "Legacy part, waived by engineering"
```

---

## compliance quarantine revoke-exception

Revoke an asset's active exception. The asset returns to the state of its last evaluation — back into quarantine, with its quarantine reason restored and its subscribers notified, when that evaluation failed a `quarantine`-level rule; `pending_evaluation` when it has no recorded evaluation. The revocation is recorded as `exception_revoked`. An asset without an active exception is refused.

```bash
vamscli compliance quarantine revoke-exception [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database ID              |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset ID                 |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli compliance quarantine revoke-exception -d my-database -a my-asset
```

---

## Cascade Commands

A cascade re-evaluates the dependents of a changed asset. By default it waits in `pending_approval` for `cascade approve` or `cascade reject`, and expires after the approval timeout. The cascade an evaluation opens automatically for an asset with children is not duplicated: while one for that parent is pending approval, further evaluations of it open no new one. `cascade create` always opens a new cascade.

---

## compliance cascade list

List cascades awaiting approval, each with the `databaseId` and `assetId` of the asset that triggered it. Only cascades whose trigger asset's database the caller may read are listed. Cascades in any other state are read individually with `cascade get`.

```bash
vamscli compliance cascade list [OPTIONS]
```

| Option          | Type | Required | Description                                      |
| --------------- | ---- | -------- | ------------------------------------------------ |
| `--json-output` | FLAG | No       | Output raw JSON response (`{"cascades": [...]}`) |

```bash
vamscli compliance cascade list
vamscli compliance cascade list --json-output
```

---

## compliance cascade get

Get a cascade's record and state (`pending_approval`, `executing`, `completed` or `aborted`).

```bash
vamscli compliance cascade get [OPTIONS]
```

| Option               | Type | Required | Description              |
| -------------------- | ---- | -------- | ------------------------ |
| `-c`, `--cascade-id` | TEXT | Yes      | Cascade ID               |
| `--json-output`      | FLAG | No       | Output raw JSON response |

```bash
vamscli compliance cascade get -c 3f0c1b2e-7d4a-4f0e-9b1c-2a6d8e5f4c71
```

---

## compliance cascade create

Create a cascade for an asset's dependents.

```bash
vamscli compliance cascade create [OPTIONS]
```

| Option                | Type | Required | Description                                             |
| --------------------- | ---- | -------- | ------------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database of the triggering asset                        |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset whose dependents to re-evaluate                   |
| `--reason`            | TEXT | No       | Trigger reason recorded on the cascade                  |
| `--no-approval`       | FLAG | No       | Start executing at once instead of waiting for approval |
| `--json-output`       | FLAG | No       | Output raw JSON response                                |

```bash
vamscli compliance cascade create -d my-database -a my-asset
vamscli compliance cascade create -d my-database -a my-asset --reason "Geometry revised" --no-approval
```

:::note[--no-approval starts the cascade in the background]
With `--no-approval` the cascade is created in the `executing` state and the command returns its `cascadeId` and `state` at once; the evaluations run in the background and the response carries no result. Poll `compliance cascade get -c <id>` until the state is `completed` or `aborted` (an aborted cascade carries its `abortReason`). Each dependent is evaluated as by `compliance evaluate`, so a schema with pipeline rules starts one workflow execution per dependent.
:::

---

## compliance cascade approve

Approve a pending cascade and start its execution. The cascade moves to `executing` and the command returns its `cascadeId` and `state` at once; the evaluations run in the background, so poll `compliance cascade get -c <id>` until the state is `completed` or `aborted`.

```bash
vamscli compliance cascade approve [OPTIONS]
```

| Option               | Type | Required | Description                             |
| -------------------- | ---- | -------- | --------------------------------------- |
| `-c`, `--cascade-id` | TEXT | Yes      | Pending cascade ID                      |
| `--reason`           | TEXT | No       | Approval reason recorded on the cascade |
| `--json-output`      | FLAG | No       | Output raw JSON response                |

```bash
vamscli compliance cascade approve -c 3f0c1b2e-7d4a-4f0e-9b1c-2a6d8e5f4c71 --reason "Reviewed dependents"
```

---

## compliance cascade reject

Reject a pending cascade; it moves to `aborted` and nothing is executed.

```bash
vamscli compliance cascade reject [OPTIONS]
```

| Option               | Type | Required | Description                              |
| -------------------- | ---- | -------- | ---------------------------------------- |
| `-c`, `--cascade-id` | TEXT | Yes      | Pending cascade ID                       |
| `--reason`           | TEXT | No       | Rejection reason recorded on the cascade |
| `--json-output`      | FLAG | No       | Output raw JSON response                 |

```bash
vamscli compliance cascade reject -c 3f0c1b2e-7d4a-4f0e-9b1c-2a6d8e5f4c71 --reason "Dependents already re-evaluated"
```

Only a cascade in `pending_approval` can be approved or rejected; any other state is reported as not found.

---

## Audit Command

---

## compliance audit

Query the compliance audit trail: the global trail, optionally narrowed to one event type, or one asset's history.

```bash
vamscli compliance audit [OPTIONS]
```

| Option                | Type    | Required | Description                                                       |
| --------------------- | ------- | -------- | ----------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | No       | With `--asset-id`: one asset's history                            |
| `-a`, `--asset-id`    | TEXT    | No       | With `--database-id`: one asset's history                         |
| `--event-type`        | TEXT    | No       | Only entries of this event type (global listing only)             |
| `--start-date`        | TEXT    | No       | Earliest entry timestamp (ISO 8601)                               |
| `--end-date`          | TEXT    | No       | Latest entry timestamp (ISO 8601)                                 |
| `--max-items`         | INTEGER | No       | Entries per page (the API applies 100 when omitted; at most 500)  |
| `--limit`             | INTEGER | No       | Alias of `--max-items`                                            |
| `--starting-token`    | TEXT    | No       | Token for pagination (the previous page's `NextToken`)            |
| `--json-output`       | FLAG    | No       | Output raw JSON response (`{"entries": [...], "NextToken": ...}`) |

```bash
vamscli compliance audit
vamscli compliance audit --event-type quarantine_released --max-items 100
vamscli compliance audit --start-date 2026-09-01T00:00:00Z --end-date 2026-09-30T23:59:59Z
vamscli compliance audit --starting-token "token123"
vamscli compliance audit -d my-database -a my-asset
vamscli compliance audit -d my-database -a my-asset --json-output
```

Event types include `schema_bound_to_database`, `schema_bound_to_asset`, `schema_unbound_from_database`, `schema_unbound_from_asset`, `schema_deleted`, `compliance_check`, `evaluation_error` (a rule's tooling failed in an evaluation, or its schema could not be loaded; the details name the rules), `quarantine_released`, `exception_granted`, `cascade_triggered` and `cascade_approved`. `--database-id` and `--asset-id` are given together; `--event-type` is rejected with them because the per-asset route has no such filter.

:::note[The audit routes return one page per call]
Entries are returned most recent first, `--max-items` per page (`--limit` is the same option). The response carries a `NextToken` when more entries exist; pass it back as `--starting-token` with the same filters to read the next page. Without `--event-type` the global trail reads every event type in turn, and the token carries the position of that walk.
:::

---

## Related Documentation

-   [Compliance user guide](../../user-guide/compliance.md) -- Schema format, enforcement levels, and the quarantine workflow
-   [Compliance concepts](../../concepts/compliance.md) -- How bindings, evaluation and cascades fit together
-   [Metadata Commands](metadata.md) -- The metadata schemas a metadata rule validates against
-   [Workflow Commands](workflows.md) -- The workflows a pipeline rule executes
