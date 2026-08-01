---
sidebar_label: Workflows
title: Workflow Commands
---

# Workflow Commands

Manage workflows, their file-upload triggers, and workflow execution. A workflow references one or
more pipelines and, when executed, runs them in order through an AWS Step Functions state machine.
Workflows are database-scoped (a workflow lives in a database, and `GLOBAL` workflows are shared).

For pipeline definitions and templates, see [Pipelines](pipelines.md). For execution operations
(details, logs, abort, re-run, delete) across all assets, see [Executions](executions.md).

---

## workflow list

List workflows in a database, or all workflows you can access.

```bash
vamscli workflow list
vamscli workflow list -d my-database
vamscli workflow list -d my-database --include-archived --auto-paginate
vamscli workflow list --json-output
```

| Option               | Description                                                        |
| -------------------- | ------------------------------------------------------------------ |
| `-d, --database-id`  | Database ID to list from (omit to list all accessible workflows)   |
| `--include-archived` | Include archived workflows                                         |
| `--page-size`        | Items per page                                                     |
| `--auto-paginate`    | Fetch all pages automatically (up to `--max-items`, default 10000) |
| `--starting-token`   | Continuation token for manual pagination                           |
| `--json-output`      | Emit the raw JSON response                                         |

---

## workflow get

Get a workflow and its triggers.

```bash
vamscli workflow get -d my-db -w my-workflow
```

---

## workflow create

Create a workflow referencing one or more pipelines. Each `--pipeline` ref is
`databaseId:pipelineId[:defaultTemplateId]` and may be repeated; alternatively supply the full
`specifiedPipelines` list as JSON.

```bash
# Reference two pipelines, one with a default template
vamscli workflow create -d my-db -n "Convert + Label" \
    --pipeline global:conversion-3d-basic:to-glb \
    --pipeline my-db:my-labeler

# Supply the pipeline list and system config from files
vamscli workflow create -d my-db -n "My Workflow" \
    --specified-pipelines-file pipelines.json \
    --system-config-file system.json
```

| Option                         | Description                                                                                                   |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `-d, --database-id`            | Database to create the workflow in (`GLOBAL` allowed)                                                         |
| `-n, --name`                   | Human-readable workflow name                                                                                  |
| `-w, --workflow-id`            | Explicit workflow ID (a GUID is generated when omitted)                                                       |
| `--pipeline`                   | Referenced pipeline `databaseId:pipelineId[:defaultTemplateId]` (repeatable)                                  |
| `--specified-pipelines[-file]` | Full `specifiedPipelines` list as inline JSON or a file                                                       |
| `--category`                   | Workflow category                                                                                             |
| `--description`                | Workflow description                                                                                          |
| `--system-config[-file]`       | `systemConfig` (input-file arity, asset scope, metadata inputs, concurrency, output target, trigger chaining) |
| `--sub-dashboard-url`          | Optional sub-dashboard URL                                                                                    |
| `--disabled`                   | Create the workflow disabled                                                                                  |

:::note
A `GLOBAL` workflow may only reference `GLOBAL` pipelines; a database workflow may reference `GLOBAL`
or same-database pipelines. Creating or updating a workflow deploys (or redeploys) its Step Functions
state machine.
:::

---

## workflow update

Update a workflow. Only supplied fields change; at least one is required. Changing the pipeline set
(`--pipeline` / `--specified-pipelines`) redeploys the state machine.

```bash
vamscli workflow update -d my-db -w my-workflow --description "Updated"
vamscli workflow update -d my-db -w my-workflow --pipeline my-db:new-pipeline
vamscli workflow update -d my-db -w my-workflow --disable
```

---

## workflow delete

Archive (soft-delete) a workflow. Archiving marks the workflow archived **and disables it**, so it is
hidden from `list` (unless `--include-archived` is passed) and cannot be executed. The workflow keeps its
ID: because workflow IDs are unique across every database, no other workflow can take that ID while the
archived record holds it. Use `workflow unarchive` to bring it back.

```bash
vamscli workflow delete -d my-db -w my-workflow
```

---

## workflow unarchive

Unarchive an archived workflow, returning it to the default listing and making it executable again.

```bash
vamscli workflow unarchive -d my-db -w my-workflow
vamscli workflow unarchive -d my-db -w my-workflow --keep-disabled
```

| Option              | Description                                                  |
| ------------------- | ------------------------------------------------------------ |
| `-d, --database-id` | Database containing the workflow                             |
| `-w, --workflow-id` | Archived workflow ID to unarchive                            |
| `--keep-disabled`   | Unarchive without re-enabling (leaves the workflow disabled) |
| `--json-output`     | Output the raw JSON response                                 |

List archived workflows with `workflow list -d my-db --include-archived` to find the ID to unarchive.

:::note[Unarchiving re-enables the workflow]
Because archiving also disables the workflow, unarchiving re-enables it — otherwise `workflow execute`
would reject the restored workflow as disabled. Pass `--keep-disabled` to clear only the archived flag
and leave the workflow disabled. Every other field is left as stored, so the workflow returns with its
original name, category, specified pipelines, and triggers intact.
:::

---

## workflow trigger

Manage a workflow's triggers. A trigger fires the workflow automatically when a matching file is
uploaded. The `fileUpload` trigger type is currently supported.

```bash
# List / get
vamscli workflow trigger list -d my-db -w my-workflow
vamscli workflow trigger get -d my-db -w my-workflow -t fileUpload

# Set (create or replace): fire on *.glb uploads
vamscli workflow trigger set -d my-db -w my-workflow \
    --input-file-filters '{"allow": ["*.glb"], "exclude": []}' --enable

# Set with per-pipeline default templates
vamscli workflow trigger set -d my-db -w my-workflow \
    --default-template-ids '{"global:conversion-3d-basic": "to-glb"}' --enable

# Delete
vamscli workflow trigger delete -d my-db -w my-workflow -t fileUpload
```

| Option (set)                    | Description                                            |
| ------------------------------- | ------------------------------------------------------ |
| `-t, --trigger-type`            | Trigger type (default `fileUpload`)                    |
| `--input-file-filters[-file]`   | `{allow: [...], exclude: [...]}` glob/ext/path filters |
| `--default-template-ids[-file]` | Map of `pipelineDatabaseId:pipelineId → templateId`    |
| `--enable / --disable`          | Whether the trigger auto-fires (default enabled)       |

---

## workflow execute

Execute a workflow on a set of input files. Input files may span multiple assets. Execution is
asset-less: files are supplied explicitly, not by running "on an asset". A `relativeFileKey` of `/`
selects the whole asset; `/folder/` selects a folder.

```bash
# One input file
vamscli workflow execute --workflow-database-id global -w my-workflow \
    --input-file my-db:asset1:/model.glb

# Multiple input files (may span assets) + per-pipeline template selection
vamscli workflow execute --workflow-database-id global -w my-workflow \
    --input-file my-db:asset1:/a.glb --input-file my-db:asset2:/b.glb \
    --pipeline-parameters '{"conversion-3d-basic": {"templateId": "to-obj"}}'

# From files + output-target override + execution group
vamscli workflow execute --workflow-database-id global -w my-workflow \
    --input-files-file inputs.json --pipeline-parameters-file params.json \
    --output-asset-id out-asset --output-database-id my-db \
    --execution-group-id batch-2026-01
```

| Option                                       | Description                                                                                                                                                                                                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--workflow-database-id`                     | The workflow's database (`GLOBAL` allowed)                                                                                                                                                                                                                         |
| `-w, --workflow-id`                          | Workflow to execute                                                                                                                                                                                                                                                |
| `--input-file`                               | `databaseId:assetId:relativeFileKey[:versionId]` (repeatable)                                                                                                                                                                                                      |
| `--input-files[-file]`                       | Full `inputFiles` list as inline JSON or a file                                                                                                                                                                                                                    |
| `--pipeline-parameters[-file]`               | Per-pipeline `{templateId, templateTags, customTemplateOverride}` keyed by pipelineId                                                                                                                                                                              |
| `--output-asset-id` / `--output-database-id` | Override the output target (when the workflow allows it)                                                                                                                                                                                                           |
| `--output-path-prefix`                       | Base path under the output asset for output files, inserted just above each file's own name; supports dynamic tags (e.g. `{{firstAssetFileFileNameNoExt}}`). Omit to inherit the workflow's default prefix; pass `""` to force the asset root. No `..`/backslashes |
| `--execution-group-id`                       | Group this execution under an executionGroupId                                                                                                                                                                                                                     |

A workflow may define a default output path prefix, which is used when `--output-path-prefix` is
omitted. Because the stored default keeps its template tags unresolved, one setting such as
`/{{jobName}}/` gives every run its own output folder. Pass an empty prefix to write at the asset root
instead:

```bash
vamscli workflow execute --workflow-database-id global -w my-workflow     --input-file my-db:asset1:/model.glb --output-path-prefix ""
```

The command prints the new `executionId`. Track it with the [Executions](executions.md) commands.

---

## workflow list-executions

List a single asset's workflow executions (per-asset history). For the global, cross-asset execution
list with rich filters, use [`execution list`](executions.md#execution-list).

```bash
vamscli workflow list-executions -d my-db -a my-asset
vamscli workflow list-executions -d my-db -a my-asset -w my-workflow --auto-paginate
```

:::note
Per-asset execution listing is limited to a page size of 50 due to Step Functions API throttling.
Use `--auto-paginate` to fetch more across pages.
:::

---

## Related pages

-   [Pipelines](pipelines.md) — pipeline and template definitions
-   [Executions](executions.md) — execution details, logs, abort, re-run, delete
