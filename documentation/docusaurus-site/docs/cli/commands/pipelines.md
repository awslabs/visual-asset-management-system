---
sidebar_label: Pipelines
title: Pipeline Commands
---

# Pipeline Commands

Manage pipeline definitions, their configuration templates, and template tag schemas. A pipeline
describes a single processing step and how it is executed (Lambda, SQS, EventBridge, or Deadline
Cloud). Pipelines are database-scoped (`GLOBAL` pipelines are shared); workflows reference pipelines
to compose multi-step processing. See [Workflows](workflows.md).

---

## pipeline list

List pipelines in a database, or all pipelines you can access.

```bash
vamscli pipeline list
vamscli pipeline list -d my-database
vamscli pipeline list -d my-database --include-archived --json-output
```

| Option                             | Description                                         |
| ---------------------------------- | --------------------------------------------------- |
| `-d, --database-id`                | Database ID (omit to list all accessible pipelines) |
| `--include-archived`               | Include archived pipelines                          |
| `--page-size` / `--starting-token` | Pagination                                          |
| `--json-output`                    | Emit the raw JSON response                          |

---

## pipeline get

Get a pipeline and the descriptors of its templates.

```bash
vamscli pipeline get -d my-db -p my-pipeline
```

---

## pipeline create

Create a pipeline. `executionConfig` selects the execution type and its per-type resource block;
`systemConfig` sets input-file arity, asset scope, metadata inputs, and template requirements. Both
are JSON objects supplied inline or from a file.

```bash
# Lambda pipeline referencing an existing function
vamscli pipeline create -d my-db -n "My Converter" -p my-converter \
    --execution-config '{"executionType": "Lambda", "lambda": {"resourceId": "my-fn"}}'

# From files
vamscli pipeline create -d my-db -n "My Converter" \
    --execution-config-file exec.json --system-config-file system.json
```

| Option                         | Description                                                                 |
| ------------------------------ | --------------------------------------------------------------------------- |
| `-d, --database-id`            | Database to create the pipeline in (`GLOBAL` allowed)                       |
| `-n, --name`                   | Human-readable pipeline name                                                |
| `-p, --pipeline-id`            | Explicit pipeline ID (a GUID is generated when omitted)                     |
| `--category` / `--description` | Metadata                                                                    |
| `--execution-config[-file]`    | `executionConfig` (executionType + per-type block)                          |
| `--system-config[-file]`       | `systemConfig` (arity, asset scope, metadata inputs, template requirements) |
| `--disabled`                   | Create the pipeline disabled                                                |

:::note[Lambda auto-provisioning]
For a `Lambda` pipeline created without a `lambda.resourceId`, VAMS provisions a new Lambda function
for the pipeline (seeded from the sample pipeline package) so a developer can build it out in the
backend. Reference an existing function via `lambda.resourceId` to skip provisioning.
:::

:::warning[Deadline Cloud]
The `DeadlineCloud` execution type can only be created when the deployment has it enabled
(`app.pipelines.deadlineCloudExecutionTypeEnabled`). It is unavailable in the GovCloud and EU
Sovereign partitions.
:::

---

## pipeline update

Update a pipeline. Only supplied fields change; at least one is required.

```bash
vamscli pipeline update -d my-db -p my-pipeline --description "Updated"
vamscli pipeline update -d my-db -p my-pipeline --execution-config-file exec.json
vamscli pipeline update -d my-db -p my-pipeline --disable
```

---

## pipeline delete

Archive (soft-delete) a pipeline.

```bash
vamscli pipeline delete -d my-db -p my-pipeline
```

Archiving marks the pipeline archived **and disables it**, so it is hidden from the default listing and
cannot run. The pipeline keeps its ID: because pipeline IDs are unique across every database, no other
pipeline can take that ID while the archived record holds it. Use `pipeline unarchive` to bring it back.

---

## pipeline unarchive

Unarchive an archived pipeline, returning it to the default listing and making it usable again.

```bash
vamscli pipeline unarchive -d my-db -p my-pipeline
vamscli pipeline unarchive -d my-db -p my-pipeline --keep-disabled
```

| Option              | Description                                                  |
| ------------------- | ------------------------------------------------------------ |
| `-d, --database-id` | Database containing the pipeline                             |
| `-p, --pipeline-id` | Archived pipeline ID to unarchive                            |
| `--keep-disabled`   | Unarchive without re-enabling (leaves the pipeline disabled) |
| `--json-output`     | Output the raw JSON response                                 |

List archived pipelines with `pipeline list -d my-db --include-archived`, or retrieve one directly with
`pipeline get -d my-db -p my-pipeline --include-archived`, to find the ID to unarchive.

:::note[Unarchiving re-enables the pipeline]
Because archiving also disables the pipeline, unarchiving re-enables it — otherwise the pipeline would
return in a state where it cannot run. Pass `--keep-disabled` to clear only the archived flag and leave
the pipeline disabled. Every other field is left as stored, so the pipeline returns with its original
name, category, execution config, system config, and templates intact.
:::

---

## pipeline template

Manage a pipeline's configuration templates. A template is a named, reusable configuration body (JSON,
YAML, OpenJD, XML, or raw) with an optional tag schema. One pipeline can serve multiple conversion
matrices via multiple templates (for example, one template per output format).

```bash
# List / get
vamscli pipeline template list -d my-db -p my-pipeline
vamscli pipeline template get -d my-db -p my-pipeline -t to-glb

# Create (config body + tag schema from files)
vamscli pipeline template create -d my-db -p my-pipeline -n "OBJ output" -t to-obj \
    --config-body-file obj-config.json --tag-schema-file tags.json

# Update / delete
vamscli pipeline template update -d my-db -p my-pipeline -t to-obj --allow-custom-edit
vamscli pipeline template delete -d my-db -p my-pipeline -t to-obj
```

| Option (create/update)                | Description                                                           |
| ------------------------------------- | --------------------------------------------------------------------- |
| `-n, --name`                          | Template name                                                         |
| `-t, --template-id`                   | Explicit template ID (a GUID is generated when omitted, on create)    |
| `--config-format`                     | `json` / `yaml` / `openjd` / `xml` / `raw`                            |
| `--config-body[-file]`                | The configuration body (inline or from a file)                        |
| `--web-form-json` / `--web-form-file` | Optional web-form definition                                          |
| `--allow-custom-edit`                 | Allow per-execution custom override of the config (see note)          |
| `--default`                           | Set this template as the pipeline's default (see note)                |
| `--input-instructions`                | Instructions shown to the user                                        |
| `--overrides[-file]`                  | Per-template overrides (arity, metadata inputs, asset scope, filters) |
| `--tag-schema[-file]`                 | Inline tag schema (list of field definitions)                         |

:::note[Create vs update flags]
`create` takes the bare enabling flags `--allow-custom-edit` and `--default`. `update` takes the paired toggle forms `--allow-custom-edit/--no-custom-edit` and `--default/--no-default`, so an update can also clear either setting.
:::

:::warning[Template delete is permanent]
`pipeline template delete` is a hard delete: the template row, any offloaded S3 config bodies, and the
tag schema are all removed, and there is no archived copy to restore. This differs from
`pipeline delete` and `workflow delete`, which archive. The command prompts for confirmation, and
`--yes` is required in `--json-output` mode where no prompt is possible.
:::

---

## pipeline tag-schema

Get or replace a template's tag schema. Tags are substituted into the template body at execution and
validated against the schema. Each field has a `tagKey` and a `type`
(`string` / `integer` / `number` / `boolean` / `string-list` / `enum`).

```bash
vamscli pipeline tag-schema get -d my-db -p my-pipeline -t to-glb

vamscli pipeline tag-schema set -d my-db -p my-pipeline -t to-glb \
    --fields '[{"tagKey": "quality", "type": "enum", "enumValues": ["low", "high"], "required": true}]'

vamscli pipeline tag-schema set -d my-db -p my-pipeline -t to-glb --fields-file tags.json
```

---

## Related pages

-   [Workflows](workflows.md) — compose pipelines into workflows and execute them
-   [Executions](executions.md) — inspect and manage executions
