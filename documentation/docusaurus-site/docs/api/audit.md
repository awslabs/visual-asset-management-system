# Audit Log Search API

The Audit Log Search API searches the VAMS audit log groups (Amazon CloudWatch Logs) without AWS console access. It is part of core VAMS: the routes, the function and the handle table are always deployed and there is no feature switch. Access is decided only by authorization.

:::info[Authorization]
Two checks apply. The route check allows `/audit` (GET, POST, DELETE). Then each requested log group is checked as an `auditLog` object with the constraint field `auditLogGroup` set to the group key (action GET). If any named group is unknown, not deployed or not allowed, the whole request is refused with the same `403` and no group is named. The seeded `admin` role may search every group; other roles need the Audit Reader permission template or an `auditLog` constraint.
:::

## How a search works

1. `POST /audit/queries` validates the filters, builds the query on the server and returns a `queryId`. No query text is accepted.
2. `GET /audit/queries/{queryId}?wait=20` waits up to the configured long-poll time for a final status and returns progress. When the status is `Complete` it returns one page of rows. Follow `NextToken` with `startingToken`.
3. `GET /audit/queries/{queryId}/export` returns the same rows as CSV or NDJSON chunks.
4. `DELETE /audit/queries/{queryId}` stops a running search.

Logs Insights narrows the records. The API then parses each record, redacts it and checks it against every filter again, so a row is shown only when its visible fields match. No count of rows removed by that check is returned.

A handle belongs to the user who started the search. An unknown, malformed, expired, foreign or no-longer-allowed handle returns the same `404 Query not found`.

A handle is kept for `app.auditLogging.search.handleRetentionMinutes` (default 60) after its last start, poll, page, export or stop; every one of those requests extends it, and a running search is kept at least until its deadline. A handle past that time returns `404` even before the database removes it. The free-text (`contains`) terms of a search are kept only in the handle, which is encrypted, and are removed sooner: when the search is stopped, ends as `Failed`, `Cancelled` or `Timeout`, or its export has returned its last chunk. A completed search whose terms were removed returns `404` for further pages, so start a new search to read it again. The audit record of a search holds the number, total length and SHA-256 of the terms, never the terms.

## Endpoints

| Method | Path                              | Purpose                                                     |
| ------ | --------------------------------- | ----------------------------------------------------------- |
| GET    | `/audit/log-groups`               | Log groups the caller may search, filter options and limits |
| POST   | `/audit/queries`                  | Start a search (`202`)                                      |
| GET    | `/audit/queries/{queryId}`        | Long-poll status, statistics, one page of rows              |
| DELETE | `/audit/queries/{queryId}`        | Stop a search                                               |
| GET    | `/audit/queries/{queryId}/export` | One export chunk (`format=csv` or `ndjson`)                 |

### Start a search

```json
{
    "logGroups": ["auditActions"],
    "startTime": 1763341200,
    "endTime": 1763344800,
    "actions": ["assetUpdate"],
    "users": ["carol"],
    "outcomes": ["success"],
    "contains": ["report.usd"],
    "includeProbes": true,
    "sort": "desc"
}
```

`startTime` and `endTime` are required. Other keys are optional filters: `entityTypes`, `entityIds`, `apiKeyIds`, `databaseIds`, `assetIds`, `requestIds`. Lists hold at most 20 values. `users` matches the acting user, the on-behalf-of user and the user in the record prefix. `contains` takes ASCII letters, digits and ` ._:@+/-`, matched case-insensitively on the redacted values the viewer shows. The unknown key `query` or `regex` is refused.

### Poll

`status` is `Running`, `Complete`, `Failed`, `Cancelled` or `Timeout`. While `Running`, `Items` is empty and `statistics` shows `recordsMatched`, `recordsScanned` and `bytesScanned`. A complete search also reports `rowCount` and `duplicatesRemoved`, and `candidateLimitReached` when the candidate limit applied and a filter could not be pushed into the query (narrow the time range). `warnings` may hold `widerScan`, `candidateLimitMayApply` and `recheckTimeBudget`.

### Errors

| Status | Message                                                 | When                                                             |
| ------ | ------------------------------------------------------- | ---------------------------------------------------------------- |
| 400    | `Invalid audit search request`                          | Any filter, window or term fails validation                      |
| 400    | `Too many audit searches are running. Try again later.` | A concurrency cap is reached                                     |
| 403    | `Not Authorized`                                        | Route not allowed, or any named log group unknown or not allowed |
| 404    | `Query not found`                                       | Handle unknown, expired, foreign or no longer allowed            |

## Limits

Set under `app.auditLogging.search` in the deployment configuration (see the [configuration reference](../deployment/configuration-reference.md#audit-logging-appauditlogging)). Defaults: 31-day window, 1,000 rows, 5 open searches in VAMS and 2 per user, a 300-second deadline and a 20-second long poll. `GET /audit/log-groups` returns the effective values.

## What is recorded

The use of the search is itself audited in the Actions group: `auditLogGroupsList`, `auditQueryStart` (filters; free-text terms only as count, length and SHA-256), `auditQueryStatus`, `auditQueryResults`, `auditQueryStop` and `auditQueryExport`. Starts, results, stops and exports are recorded even when `recordReads` is off.

## IAM

The function may start Logs Insights queries on the nine audit log groups and read, stop and list its own queries. It holds no action that changes or deletes audit data.
