---
sidebar_label: Executions
title: Execution Commands
---

# Execution Commands

Inspect and manage workflow executions across all assets. Executions may span files from multiple
assets, so these commands are keyed on the execution ID rather than an asset. To start an execution,
use [`workflow execute`](workflows.md#workflow-execute); for a single asset's execution history, use
[`workflow list-executions`](workflows.md#workflow-list-executions).

---

## execution list

List workflow executions globally, permission-filtered. You only see executions whose workflow you
can read and every one of whose assets you can read — each input file's asset, each asset named as
a metadata source, and the asset the run wrote to whenever it wrote to one. A run that wrote into an
asset you cannot read is therefore not listed, even when you can read every asset it read. A
results-only run writes to no asset and reads none, so workflow access is its whole gate. An asset that
has been permanently deleted is authorized on the database it lived in, so a run against a deleted
asset stays listed for whoever can read that database; an archived asset is unaffected and stays
authorized on its own record.

Supports rich filters and pagination. By default only recent executions — those started within the last
90 days — are listed; use `--filter-start-date` and `--filter-end-date` to query an explicit date range.
The applied window is returned as `filterStartDate` (and `filterEndDate` when supplied) in the response.

:::note[A short page reports why under `Warnings`]
The service fills a page by walking its query, and two bounds can stop it early: the per-page limit on
distinct assets it resolves for permission checks, and its per-request work budget. Whichever fired is
named in a `Warnings` block (a `warnings` array under `--json-output`), and a next token accompanies it
whenever the walk can continue. A page shorter than `--page-size`, or an empty page carrying a token, is
therefore a stated bound rather than an absence of matching executions. `--auto-paginate` collects the
bounds from every page it walked. A listing filtered to one workflow (`--workflow-id` with
`--workflow-database-id`) or one group (`--group-id`) reads that scope's own index, so the work budget
is not expected to fire there.
:::

Each execution reports the kinds of output it produced as `Output Types` (`File`, `Metadata`, `Result`,
in that order; `outputTypes` under `--json-output`). The line is omitted for a run that produced none,
and for a run recorded before output types were captured, which reports `outputTypes: []`. Read a
run's files with [`execution details files`](#execution-details-files) and its result text with
[`execution details results`](#execution-details-results).

Each execution reports its output target — `Output Type` (`asset`, or `none` for a results-only run)
and `Output Asset` as `databaseId:assetId`. Both lines are omitted for a results-only execution, which
writes no files and therefore has no destination asset.

```bash
vamscli execution list
vamscli execution list -w my-workflow --status RUNNING
vamscli execution list --filter-start-date 2026-01-01T00:00:00Z --filter-end-date 2026-02-01T00:00:00Z
vamscli execution list --group-id batch-2026-01 --auto-paginate
vamscli execution list --triggered-by user@example.com --json-output
```

| Option                            | Description                                                                                            |
| --------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `-w, --workflow-id`               | Filter by workflow ID                                                                                  |
| `--workflow-database-id`          | Filter by workflow database ID                                                                         |
| `--status`                        | Filter by execution status (e.g. `RUNNING`, `SUCCEEDED`, `FAILED`)                                     |
| `--trigger-type`                  | Filter by trigger type (`Manual` / `File-Upload`)                                                      |
| `--group-id`                      | Filter by `executionGroupId`                                                                           |
| `--triggered-by`                  | Filter by the user ID that triggered the execution                                                     |
| `--filter-start-date`             | Only executions started on/after this UTC date-time, as `YYYY-MM-DDTHH:MM:SSZ` (default: 90 days ago)  |
| `--filter-end-date`               | Only executions started on/before this UTC date-time, as `YYYY-MM-DDTHH:MM:SSZ` (optional upper bound) |
| `--page-size`                     | Items per page (max 100)                                                                               |
| `--auto-paginate` / `--max-items` | Fetch all pages (up to max-items)                                                                      |
| `--starting-token`                | Continuation token for manual pagination                                                               |

---

## execution details

Show an execution's full detail and traceability: per-pipeline status, input files, input metadata,
input configurations, and outputs (files, metadata, results).

```bash
vamscli execution details my-execution-id
vamscli execution details my-execution-id --include-sub-executions
vamscli execution details my-execution-id --json-output
```

`Workflow version` names the published workflow state machine version the run started on, marked
`(latest)` while the workflow still runs it or `(superseded)` once the workflow's state machine has
been redeployed since the run. A save from the workflow builder always redeploys it, as does an API
or CLI update that supplies the step list (`vamscli workflow update` with `--pipeline`,
`--specified-pipelines` or `--specified-pipelines-file`); a change to a setting one of its pipelines
contributes to the state machine (execution type, target, callback mode, timeouts, Deadline Cloud
job settings) redeploys it too.
The marker does not track the workflow-level settings or the pipeline names and descriptions: they are
read live and can change without a redeploy, so they may differ from what the run used whatever the
marker says. A number with no marker means the workflow no longer records a current version (for
example, the workflow was deleted), and a run started before versions were recorded shows
`not recorded`.

Asset and file metadata and database metadata are reported as separate row counts, each marked
independently when the response returned only part of that collection, and the metadata sources the run
read are named. The metadata rows themselves, and the full list of collections the response trimmed,
are available with `--json-output`. A pipeline step whose configuration body was too large to store
inline reports the Amazon S3 location of the complete body.

Every pipeline step lists the log sources known for it under `Logs available` — the step's own
invocation log, each location the pipeline registered, and the log group of a registered nested state
machine — each with the `logId` that [`execution logs --log-id`](#execution-logs) reads one source by.
With `--include-sub-executions`, a step that runs its own nested state machine or container job also
shows `Sub-processes`: each registered sub-process with its status, and beneath it every stage of that
sub-process with its own status, start and stop times, and error. A stage that failed but whose failure
the sub-process caught and reported is marked `(caught)`. The flag is off by default because deriving
stage status reads the sub-process's execution history; a capped read is marked
`(stages truncated in this response)` or `(history truncated: later stages may be missing)` where it
occurs.

| Option                     | Description                                                            |
| -------------------------- | ---------------------------------------------------------------------- |
| `--include-sub-executions` | Also report each step's registered sub-processes with per-stage status |
| `--json-output`            | Raw JSON response                                                      |

To read the metadata rows themselves in the formatted output — or to read a collection this command
reports as partial in full — use [`execution details metadata`](#execution-details-metadata). A
partial file collection is read in full with [`execution details files`](#execution-details-files), and
the run's full result text with [`execution details results`](#execution-details-results).

`execution details` is a command group: with only an execution ID it prints the whole detail view
described above, and its `metadata`, `files` and `results` subcommands each read one part of that view
in full.

:::warning[Renamed in 2.7.0]
`vamscli execution details-metadata` is now `vamscli execution details metadata`, with the same
options. The flat form is no longer accepted, so update any script that calls it.
:::

---

## execution details metadata

Page one metadata collection of an execution's detail view. `execution details` bounds its metadata
collections and reports each as a row count; this command reads a named collection a page at a time,
one row per line, until every row has been returned. Rows carry the same shape the details view returns
plus the pipeline that produced or read them.

```bash
vamscli execution details metadata my-execution-id
vamscli execution details metadata my-execution-id --collection output --auto-paginate
vamscli execution details metadata my-execution-id --pipeline-id my-pipeline --page-size 500
```

| Option             | Description                                                                       |
| ------------------ | --------------------------------------------------------------------------------- |
| `--collection`     | `input` (default), `inputDatabase`, or `output` — case-sensitive                  |
| `--pipeline-id`    | Only rows produced or read by this pipeline (one workflow step)                   |
| `--page-size`      | Rows per page (max 500; a larger value is clamped with a warning)                 |
| `--max-items`      | Maximum total rows to fetch — applies only with `--auto-paginate` (default 10000) |
| `--starting-token` | Continuation token for manual pagination                                          |
| `--auto-paginate`  | Fetch every page up to `--max-items`                                              |
| `--json-output`    | Output the raw JSON response                                                      |

`--collection` selects which of the detail view's metadata collections is read: `input` the asset and
file metadata the run read, `inputDatabase` the metadata-source databases' own metadata, and `output`
the metadata the pipelines wrote against their output files.

The row lines differ by collection. An `input` or `inputDatabase` row prints the entity it was read
from — `databaseId:assetId` and the asset-relative path — with its `scope` and the number of metadata
entries it carries, followed by the pipeline in brackets. A database-scope row belongs to no asset, so
its asset position renders as `-` and `scope=database` names it:

```text
Collection: input
Found 2 row(s):
  my-database:a1b2c3/models/building.fbx  scope=asset  4 entries  [convert-to-glb]
  my-database:-/  scope=database  2 entries  [convert-to-glb]
```

An `output` row prints the output file the metadata applies to and the key/value written against it:

```text
Collection: output
Found 1 row(s):
  /models/building.gltf  triangleCount=18204  [3d-conversion-pipeline]
```

The metadata key/value pairs of an input row are reported as an entry count rather than printed; use
`--json-output` for the pairs themselves.

Without `--auto-paginate` the command fetches one page and prints the continuation token when more rows
remain. A token is only valid alongside the `--collection` and `--pipeline-id` it was issued with, so
pass the same ones when resuming with `--starting-token`. `--auto-paginate` cannot be combined with
`--starting-token`, and `--max-items` without `--auto-paginate` is reported and ignored.

Auto-pagination stops at `--max-items`, or after 200 pages, whichever comes first. When it stops with
rows still available it says so, and reports the token to resume from.

---

## execution details files

Page one file collection of an execution's detail view. `execution details` bounds its file lists and
marks a partial one; this command reads a named collection a page at a time until every row has been
returned. Rows carry the same shape the details view returns; output rows also name the pipeline that
produced them. No Amazon S3 bucket or key is returned.

```bash
vamscli execution details files my-execution-id
vamscli execution details files my-execution-id --collection output --auto-paginate
vamscli execution details files my-execution-id --collection output --pipeline-id my-pipeline
```

| Option             | Description                                                                       |
| ------------------ | --------------------------------------------------------------------------------- |
| `--collection`     | `input` (default) or `output` — case-sensitive                                    |
| `--pipeline-id`    | Only output rows produced by this pipeline (one workflow step)                    |
| `--page-size`      | Rows per page (max 500; a larger value is clamped with a warning)                 |
| `--max-items`      | Maximum total rows to fetch — applies only with `--auto-paginate` (default 10000) |
| `--starting-token` | Continuation token for manual pagination                                          |
| `--auto-paginate`  | Fetch every page up to `--max-items`                                              |
| `--json-output`    | Output the raw JSON response                                                      |

`input` reads the workflow-level input files, each printed as `databaseId:assetId` and the
asset-relative path with the S3 version the run read. `output` reads the files the pipelines wrote,
each printed with its type, size and producing pipeline. There is no `inputDatabase` collection for
files. `--pipeline-id` applies to the `output` collection only.

Paging behaves as in [`execution details metadata`](#execution-details-metadata): a token is only valid
alongside the `--collection` and `--pipeline-id` it was issued with, and auto-pagination stops at
`--max-items` or after 200 pages, reporting the token to resume from.

---

## execution details results

Print an execution's result text — the concatenated result files its pipelines produced — followed by
a caption with the row count and byte size. A run that produced no result prints `(no result content)`
and reports 0 rows.

```bash
vamscli execution details results my-execution-id
vamscli execution details results my-execution-id -o result.txt
vamscli execution details results my-execution-id --auto-paginate --json-output
vamscli execution details results my-execution-id --starting-token <token>
```

| Option                                 | Description                                                               |
| -------------------------------------- | ------------------------------------------------------------------------- |
| `-o, --output`                         | Write the result text to this file; the caption still prints the size     |
| `--auto-paginate / --no-auto-paginate` | Read every page and concatenate them (default: on with `-o`, off without) |
| `--starting-token`                     | Read from this page (`NextToken` of a previous call)                      |
| `--json-output`                        | Output the raw JSON response                                              |

The service returns the result a page at a time, each page holding as many result rows as fit its
5 MiB response limit, with a `NextToken` while more remain. Without `--auto-paginate` one page is
printed and its token is shown (and carried in `--json-output`) for `--starting-token`; with it every
page is read and concatenated, stopping after 200 pages and reporting the token to resume from.
`TRUNCATED` in the caption (`resultContentTruncated: true` under `--json-output`) means a result row
was stored cut or a single row exceeded the response limit; that text is not retrievable. `-o` writes
the returned text in both output modes; with `--json-output` the JSON response is still printed.

---

## execution logs

Retrieve an execution's logs. `truncated` mode returns the stored log text, and — because the stored
log is often empty (it is captured before CloudWatch finishes ingesting the run's events) — falls back
to a live CloudWatch search for the same scope when the stored copy is empty. `full` mode always runs a
live CloudWatch search scoped to the execution (and optionally a single pipeline execution). The output
reports `Source: stored` or `Source: live` so you can tell which was returned.

Returned log text is redacted: credential-bearing values — authorization headers, bearer tokens, AWS
access-key IDs, JSON web tokens, and labelled secret fields such as `SecretAccessKey` and
`SessionToken` — are replaced with `<redacted>` before the logs are stored or returned.

```bash
vamscli execution logs my-execution-id
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec
vamscli execution logs my-execution-id --mode full --limit 200
```

`full` mode prints each group of logs under its own heading, and omits a heading it has nothing for:

| Section                 | Contents                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Events`                | The CloudWatch search over the workflow log group, scoped to the execution.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `State Machine History` | The Step Functions state-transition timeline (whole-execution requests). Available immediately, with no ingestion lag.                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `Log sources`           | With `--pipeline-execution-id`: every log known for the step — its `logId`, kind (`invocation`, `registered`, `subStateMachine`, `deadlineCloudJob`), source type, stage, and log group — and whether it was read: `read N`, `denied`, `notFound`, `error` (the read failed for another reason — throttling, an invalid CloudWatch token, an unparseable location), `empty`, `skipped` (past the per-request cap), or `unscoped` (a container log prefix searched with the execution-scope terms, which container output does not carry). |
| `Sub-Process Logs`      | With `--pipeline-execution-id`: the step invocation log, any logs the pipeline registered, a registered Deadline Cloud job's session logs, and any sub-execution history, sorted by time. Each line names the log group it came from and ends with the `logId` of its source in brackets; a nested state machine's history line carries the `logId` of the log group that machine writes to, and has no bracket when the machine has no logging destination.                                                                              |
| `Warnings`              | Logs that could not be read — a missing permission, or a registration list beyond the per-request cap. Shown rather than dropped, so partial output is not mistaken for complete output.                                                                                                                                                                                                                                                                                                                                                  |

The **step invocation log** is the log of the resource the workflow invoked for that step — for a
`Lambda` step, that function's own CloudWatch log group. It holds the reason a launch failed before
the pipeline's own logging began. `SQS`, `EventBridge`, and `DeadlineCloud` steps have no derivable
invocation log, so nothing is reported for them.

```bash
# Everything reachable for one step, including that step's own invocation log
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec --mode full
# One source alone, by the logId shown under Log sources (or under Logs available in execution details)
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec --mode full --log-id 3f9a0c1d2e4b5a67
# Only the sources and history of one stage of the step's nested state machine
vamscli execution logs my-execution-id --pipeline-execution-id my-pipeline-exec --mode full --stage-name PdalConverterBatchJob
```

| Option                        | Description                                                                      |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `--mode`                      | `truncated` (default) or `full`                                                  |
| `--pipeline-execution-id`     | Scope logs to one pipeline execution                                             |
| `--filter-pattern`            | (full) additional CloudWatch filter pattern                                      |
| `--limit`                     | (full) max events (capped at 1000)                                               |
| `--start-time` / `--end-time` | (full) epoch-millisecond window                                                  |
| `--next-token`                | (full) CloudWatch pagination token                                               |
| `--log-id`                    | (full, with `--pipeline-execution-id`) read one log source by its `logId`        |
| `--stage-name`                | (full, with `--pipeline-execution-id`) only the sources and history of one stage |

The seven `full`-mode options act on the live CloudWatch search only — `truncated` mode returns one
joined blob of stored text and no continuation token, so there is nothing there for them to narrow.
Supplying one without `--mode full` is rejected with a usage error rather than ignored, so a filtered
search that silently returned an unfiltered log is not mistaken for "no matching events". `--log-id`
and `--stage-name` read a single step's sources, so they additionally require `--pipeline-execution-id`
and are rejected without it. With `--log-id`, `Events` holds that source's events and `Next token`
continues that source alone; when the source is the log group a registered nested state machine writes
to (kind `subStateMachine`, or `registered` when the pipeline reported that group itself) the response
also carries its `State Machine History`. A `--log-id` the step does not have is reported with the
server's message, `Log source not found for this pipeline execution`.

---

## execution abort

Abort a running execution, or an entire execution group with `--group-id`. When aborting a group,
pass any member execution ID (the route is keyed on an execution ID); the group abort is bounded per
request and reports `moreRemaining` when more members remain.

A group abort terminates every active execution the caller can reach in the group, so it is confirmed
interactively unless `--yes` is passed. `--yes` is **required** with `--json-output`, where no prompt
is possible: without it the command emits `{"error": "Confirmation required", ...}` and exits
non-zero without aborting anything.

```bash
# Abort one execution
vamscli execution abort my-execution-id

# Abort every active execution in a group
vamscli execution abort my-execution-id --group-id batch-2026-01 --yes

# Non-interactive
vamscli execution abort my-execution-id --group-id batch-2026-01 --yes --json-output
```

A single abort reports `message` and, when a registered sub-process could not be stopped, two
sibling arrays: `warnings` (free text, one sentence per sub-process) and `notAbortable` -- one
`{resourceType, label, stageName, locator}` entry per registered sub-process of a type VAMS has no
stop API for (an externally owned pipeline's own job, for example). The execution still reads
`ABORTED`; a non-empty `notAbortable` means that compute keeps running until its owner's heartbeat or
timeout ends it. `locator` is the registered id or the final segment of a registered ARN. Both arrays
are absent from a clean abort. In CLI mode the warnings are listed and the `notAbortable` entries are
summarised on one `Left running` line; `--json-output` carries every field the API returned beside
`message`, so a field added later reaches a script without a CLI change.

```json
{
    "message": "Execution aborted",
    "warnings": [
        "Sub-process of type 'slurmJob' (slurm-00001) could not be aborted: VAMS cannot stop this resource type; it may still be running."
    ],
    "notAbortable": [
        {
            "resourceType": "slurmJob",
            "label": "Mock Slurm job",
            "stageName": "MockTrain",
            "locator": "slurm-00001"
        }
    ]
}
```

A group abort reports `groupId`, a per-member `results` list (`aborted`, `skipped-terminal`, or
`error`), `skippedInaccessibleCount`, and `moreRemaining`, plus the same two arrays gathered from its
members: each warning is prefixed with its member's execution ID, and each `notAbortable` entry
carries the member's `executionId`. In CLI mode the warnings follow a `Warnings:` heading and the
`Left running` line names each sub-process after its member (`e1: Mock Slurm job (slurmJob, in
MockTrain)`); `--json-output` returns the group fields with both arrays beside them.

---

## execution rerun

Re-run an execution, reconstructed from its stored records. This launches a new execution (new
execution ID); optionally reuse or assign an execution group.

```bash
vamscli execution rerun my-execution-id
vamscli execution rerun my-execution-id --execution-group-id batch-2026-02
```

A re-run reconstructs the original run's metadata sources along with its input files, so it reads the
same metadata the first run did. It reports the same warnings an execute does — a database whose
metadata could not be read, or metadata trimmed at the per-entity limit.

---

## execution permanent-delete

Permanently delete an execution's DynamoDB records (admin only). This does not touch Step Functions
history and requires the execution to not be in progress. It is irreversible; the CLI prompts for
confirmation unless `--yes` is passed. `--yes` is **required** with `--json-output`, where no prompt
is possible: without it the command emits `{"error": "Confirmation required", ...}` and exits
non-zero without deleting anything.

```bash
vamscli execution permanent-delete my-execution-id --yes
vamscli execution permanent-delete my-execution-id --yes --json-output
```

:::warning
Permanent delete removes the execution's traceability records (inputs, outputs, metadata, logs) from
DynamoDB. Abort a running execution before attempting to permanently delete it.
:::

---

## Related pages

-   [Workflows](workflows.md) — create workflows and start executions
-   [Pipelines](pipelines.md) — pipeline and template definitions
