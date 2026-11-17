---
sidebar_label: Audit Log Search
title: Audit Log Search Commands
---

# Audit Log Search Commands

Search the VAMS audit logs from the command line: who did what, when, and to which entity. The commands use the same audit log search API as the web **Audit Log Search** page, so a caller sees the same log groups and records in both.

These commands are not `vamscli compliance audit`, which reads the compliance evaluation trail (bindings, evaluations, quarantine and exceptions). See [Compliance](compliance.md).

A search starts an Amazon CloudWatch Logs Insights query on the deployment (which is billed to it) and answers a **handle**. The commands wait for the search with the server's long poll (about 20 seconds per request), then read the rows. Audit records can be sensitive: write exports where only you can read them. The CLI log file and the `--verbose` echo never hold the `--contains` terms or the `--user`, `--entity-id` and `--api-key-id` values you type (only how many values and how many characters), nor rows or export text.

Audit log search is part of VAMS from v2.7.0 and has no feature switch. Who may search, and which log groups they may search, is decided by the API from the caller's role permissions (the `/audit` routes and the per-group `auditLog` permission). A deployment older than v2.7.0 answers `Audit log search is not supported by this deployment`.

Every command below takes `--json-output`; the JSON is the API response unchanged, with `queryId` added where a command reads a search.

---

## audit groups

List the log groups you may search, the actions and entity types written to them, and the deployment's search limits.

```bash
vamscli audit groups [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | FLAG | No       | Output raw JSON response |

```bash
vamscli audit groups
vamscli audit groups --json-output
```

An empty `logGroups` list is a normal answer for a user who may search none. `limits` holds `maxQueryWindowDays`, `maxRows`, `maxConcurrentQueries`, `maxConcurrentQueriesPerUser`, `queryDeadlineSeconds`, `longPollWaitSeconds`, `maxContainsTerms`, `maxContainsTermLength`, `pageSize` and `maxPageSize`.

---

## audit search

Start a search, wait for it and print the matching records.

```bash
vamscli audit search [OPTIONS]
```

| Option             | Type   | Required | Description                                                                                                                                                                                                                                                                                                        |
| ------------------ | ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--group`          | TEXT   | No       | Log group key to search. Repeatable. Default: every group you may search                                                                                                                                                                                                                                           |
| `--action`         | TEXT   | No       | Action name, for example `assetUpdate`. Repeatable                                                                                                                                                                                                                                                                 |
| `--entity-type`    | TEXT   | No       | Entity type, for example `asset`. Repeatable                                                                                                                                                                                                                                                                       |
| `--entity-id`      | TEXT   | No       | Entity ID. Repeatable                                                                                                                                                                                                                                                                                              |
| `--user`           | TEXT   | No       | Acting user, or the user acted for. Repeatable                                                                                                                                                                                                                                                                     |
| `--api-key-id`     | TEXT   | No       | ID of the API key that made the request. Repeatable                                                                                                                                                                                                                                                                |
| `--database`       | TEXT   | No       | Database ID. Repeatable                                                                                                                                                                                                                                                                                            |
| `--asset`          | TEXT   | No       | Asset ID. Repeatable                                                                                                                                                                                                                                                                                               |
| `--outcome`        | CHOICE | No       | `success`, `failed` or `denied`. Repeatable                                                                                                                                                                                                                                                                        |
| `--request-id`     | TEXT   | No       | Request ID. Repeatable                                                                                                                                                                                                                                                                                             |
| `--contains`       | TEXT   | No       | Text the record must contain. Repeatable, all must match, case-insensitive. At least 3 characters of letters, digits, space and `. _ : @ + / -`. The most terms and the longest term are the deployment's `maxContainsTerms` and `maxContainsTermLength` from `audit groups` (3 terms of 64 characters by default) |
| `--from`           | TEXT   | No       | Start of the window: relative (`90m`, `24h`, `7d`, `2w`), ISO 8601 or epoch seconds. Default `24h` ago                                                                                                                                                                                                             |
| `--to`             | TEXT   | No       | End of the window: relative, `now`, ISO 8601 or epoch seconds. Default `now`                                                                                                                                                                                                                                       |
| `--include-probes` | FLAG   | No       | Include permission-probe records (hidden by default)                                                                                                                                                                                                                                                               |
| `--order`          | CHOICE | No       | `asc` or `desc`: the order of the rows by time                                                                                                                                                                                                                                                                     |
| `--max-rows`       | INT    | No       | Most rows to read into the output (default 100)                                                                                                                                                                                                                                                                    |
| `--page-size`      | INT    | No       | Rows per request, 1 to 500                                                                                                                                                                                                                                                                                         |
| `--no-wait`        | FLAG   | No       | Start the search and print its handle without waiting                                                                                                                                                                                                                                                              |
| `--timeout`        | INT    | No       | Seconds to wait for the search to finish (default 360)                                                                                                                                                                                                                                                             |
| `--json-output`    | FLAG   | No       | Output raw JSON response                                                                                                                                                                                                                                                                                           |

```bash
vamscli audit search --action assetUpdate --from 7d
vamscli audit search --user carol --outcome denied --from 2026-11-01 --to 2026-11-02
vamscli audit search --group auditActions --contains report.usd --json-output
vamscli audit search --from 24h --no-wait
```

Values within one filter are alternatives (`--action a --action b` matches either); different filters are combined. A filter takes at most 20 values. The `--contains` terms are checked before anything is sent against the limits `audit groups` reports (read once per command run, and the defaults of 3 terms and 64 characters when that read fails): a term that does not fit is refused, and the error does not repeat it. The server stays authoritative and validates every value again. The time window is limited by the deployment (`maxQueryWindowDays` in `audit groups`).

The JSON result has these stable keys: `queryId`, `status` (`Complete`), `statistics` (`recordsScanned`, `bytesScanned`, `rowCount`, `duplicatesRemoved`, and `candidateLimitReached` only when true), `Items` (the rows), `warnings`, and `NextToken` when more rows follow. When `--max-rows` cut the rows, `truncatedByMaxRows` is `true` and `NextToken` is absent: raise `--max-rows`, or read the rest with `audit results`. With `--no-wait` the result is `{"queryId", "status": "Running", "warnings"}`.

Notes on what the output means:

-   A search that ends `Failed`, `Cancelled` or `Timeout` exits 1 with an error that carries `queryId` and `status`. A stopped search returns no rows.
-   The text output prints the handle as soon as the search starts. If the command then fails or you press Ctrl-C, it prints the handle again to standard error with the commands to stop or read the search (the search keeps running, holds one of your concurrent-search slots and is billed until it ends), and a `--json-output` error carries `queryId`.
-   `candidateLimitReached` (printed as a warning) says that the search reached its row limit with a filter the query could not apply itself. Narrow the time range or add filters. A result without it is not proof that the result is complete.
-   `rowCount` and the rows are the records you may see. The CLI does not print or compute any count of records you may not see.
-   A request for a log group that is unknown, not deployed or not allowed to you answers one generic `Not Authorized`, which does not say which. Likewise one `not found` answers a handle that is unknown, expired, someone else's or no longer allowed.
-   When the deployment's cap on open searches is reached, the start is refused with `Too many audit searches are running. Try again later.` Stop searches you no longer need with `audit stop`.

---

## audit results

Read the rows of a search by its handle: one page, or every page up to a cap.

```bash
vamscli audit results --handle <queryId> [OPTIONS]
```

| Option          | Type   | Required | Description                                        |
| --------------- | ------ | -------- | -------------------------------------------------- |
| `--handle`      | TEXT   | Yes      | Search handle (`queryId`)                          |
| `--page-token`  | TEXT   | No       | `NextToken` of the previous page                   |
| `--all`         | FLAG   | No       | Read every page, up to `--max-rows`                |
| `--max-rows`    | INT    | No       | With `--all`, the most rows to read (default 1000) |
| `--page-size`   | INT    | No       | Rows per request, 1 to 500                         |
| `--order`       | CHOICE | No       | `asc` or `desc`                                    |
| `--wait`        | FLAG   | No       | Wait for a running search to finish first          |
| `--timeout`     | INT    | No       | Seconds to wait with `--wait` (default 360)        |
| `--json-output` | FLAG   | No       | Output raw JSON response                           |

```bash
vamscli audit results --handle <queryId>
vamscli audit results --handle <queryId> --page-token 100
vamscli audit results --handle <queryId> --all --max-rows 500 --json-output
```

A handle expires after `handleRetentionMinutes` (60 by default) without use; each read extends it. After expiry the API answers that the search was not found. While the status is `Running` no rows are returned. The `contains` terms of a completed search are removed when it is stopped, fails, or its last export chunk is read, and its pages then answer not found: start a new search.

---

## audit show

Show every field of one record of a search, found by its `recordId` in the search's rows.

```bash
vamscli audit show --handle <queryId> --record-id <recordId> [--json-output]
```

The API has no single-record route, so `show` reads the pages of the search in order until it finds the record. It works on a `Complete` search whose handle has not expired. Records written before records carried a `recordId` cannot be addressed by `show` (the command says how many rows had none); read them with `audit results`. A search that used `--contains` cannot be read, shown or exported again after its export finished or after it was stopped: the API drops the terms then, and the answer is `not found`. Strings in a record are already redacted by the API, and control and bidirectional characters are shown as `\uXXXX`.

---

## audit stop

Stop a running search and free its concurrency slot.

```bash
vamscli audit stop --handle <queryId> [--json-output]
```

Stopping your own search is not destructive and needs no `--confirm`. A search that has already ended keeps its status. A stopped search returns no rows.

---

## audit export

Export the rows of a `Complete` search as CSV or NDJSON.

```bash
vamscli audit export --handle <queryId> [OPTIONS]
```

| Option           | Type   | Required | Description                                                            |
| ---------------- | ------ | -------- | ---------------------------------------------------------------------- |
| `--handle`       | TEXT   | Yes      | Search handle (`queryId`)                                              |
| `--format`       | CHOICE | No       | `csv` (default) or `ndjson`                                            |
| `-o`, `--output` | PATH   | No       | File to write. Default: standard output. Required with `--json-output` |
| `--overwrite`    | FLAG   | No       | Replace the output file if it exists                                   |
| `--json-output`  | FLAG   | No       | Output raw JSON response                                               |

```bash
vamscli audit export --handle <queryId> --format csv -o audit.csv
vamscli audit export --handle <queryId> --format ndjson > audit.ndjson
vamscli audit export --handle <queryId> -o audit.csv --json-output
```

The text is written exactly as the API returns it, chunk after chunk, and is never parsed, re-quoted or evaluated. The API prefixes CSV cells that start with `=`, `+`, `-`, `@`, a tab or a carriage return with `'`, so a spreadsheet does not run them as formulas. The folder of `--output` must already exist (the command stops before the first request with `The folder for '<file>' does not exist` otherwise). A file is written beside its target and moved into place at the end, so a failed export leaves no partial file. A search that used `--contains` can be exported once: after the last chunk, or after `audit stop`, its pages and exports answer `not found`. With `--json-output` the result is `{"queryId", "format", "output", "rowCount", "warnings"}`.

---

## Scripting

-   Every command exits `0` on success and `1` on any error the command reports. A usage error (a missing or invalid option, for example `--page-size 501` or `--outcome bogus`) exits `2`. `--json-output` prints one JSON document to standard output, and on error `{"error", "error_type"}` plus `queryId` and `status` for a search that ended badly or timed out.
-   Start a search with `--no-wait`, store `queryId`, and read it later with `audit results --handle` (a handle lives for `handleRetentionMinutes` after its last use).
-   Poll sequentially: the server holds one waiting request per handle, and `audit search` and `audit results --wait` already do so.
