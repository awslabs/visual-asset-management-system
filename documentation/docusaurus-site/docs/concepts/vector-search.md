# Vector search

Vector search lets a user describe what they are looking for in ordinary language — "a rusted pump housing", "drone footage of a bridge deck", "a floor plan with two stairwells" — and get back the files whose content matches, ranked by semantic similarity rather than by the words in their names. It is optional, enabled by the `app.vectorSearch` configuration section, and works with or without Amazon OpenSearch Service. When enabled, the deployment publishes the `VECTORSEARCH` feature switch, which the web interface, the CLI, and the MCP server read before offering natural-language search.

Vector search is on by default in the commercial AWS template, off by default in the AWS GovCloud (US) template (where it is supported once the Amazon Bedrock models are enabled manually), and unavailable in the AWS European Sovereign Cloud.

<!-- TODO(owner): diagram: vectorSearch_queryFlow.png — query flow (POST /search/nlp → embed → SearchVectors breadth + depth calls per target → merge, collapse, authorize, enrich → response), exported to documentation/diagrams/ and static/img/ -->

## What is embedded

Embeddings are produced by the [SYSTEM GenAI metadata pipeline](../pipelines/system-genai-metadata.md), which runs on every file upload. For each file version it analyzes, the pipeline composes an embedding text from, in order: the asset's name, description, and tags; the database id, the file path, and the file class; the `genai_*` block it generated (title, description, keywords, category and subcategory, style, materials, colors, objects, orientation, size estimate, text summary); human-readable attribute facts such as dimensions with units, counts, duration, resolution, pages, camera, and coordinate reference system; the metadata already recorded on the file, then on its asset, then on its database, and the file's attributes that no pipeline wrote; and, last, the extracted text excerpt. The typed `ext_*` fields the pipeline promotes from the attributes enter the text as those attribute facts rather than as separate keys; they surface on the file's **Metadata** tab and, with Amazon OpenSearch, in the metadata filters.

The existing metadata contributes whether or not the pipeline's template tag `SEED_WITH_EXISTING_METADATA` lets the analysis model see it, so a file is found by what users already recorded about it, its asset, and its database as well as by what the model generated. That block is budgeted — a value longer than 400 characters is cut, a GeoJSON value is reduced to its geometry type, and the lines are kept whole up to 12,000 characters in total — so a large metadata set never crowds out the generated description, and it is placed before the text excerpt so that when the embedding model's window cuts anything, it cuts the excerpt. Keys the pipeline itself writes (`sys_*`, `ext_*`, `genai_*`) are skipped because the current run contributes them fresh. The metadata the run reads is captured when the execution is launched and is bounded there as well: each entity's metadata is kept in key order up to 1,000 entries and 300 KiB, an entry that does not fit is skipped, and an entity whose metadata could not be read contributes nothing. A bounded or unreadable capture is reported in the execute request's response `warnings` and in the execute-workflow Lambda's log; a run launched by the upload trigger or by the reindexer has no caller to receive the warnings.

Each vector item records which parts contributed as `sourceModalities`, in composition order: `asset-metadata`, `file-identity`, `genai-metadata`, `file-attributes`, `existing-file-metadata`, `existing-asset-metadata`, `existing-database-metadata`, `existing-file-attributes`, and `file-text`. A `videoTime` segment item ([Segment vectors](#segment-vectors)) also lists `segment-frames`; a `textChunk` segment item lists `asset-metadata`, `file-identity`, `genai-metadata`, and `file-text` only. The web interface shows these labels in the relevance popover beside each natural-language result, and the `_vector` block of the API response carries them. A label records that at least one line of that scope reached the text, not that the scope was captured whole.

That text is embedded with the configured Amazon Bedrock embeddings model — Amazon Titan Text Embeddings V2 at 1024 dimensions by default (`app.vectorSearch.embeddingModelId`, `app.vectorSearch.embeddingDimensions`) — and published as a `vector.embedding.ready` event that a single vector indexer in the search stack writes to the vector table.

Because the text is derived from renders and extracted content, a 3D model is found by what it looks like and a video by what its keyframes show, not only by what someone typed into its metadata. Any pipeline with `events:PutEvents` on the VAMS orchestration bus can contribute embeddings through the same event; the contract is documented on the pipeline page.

:::info[Embeddings require the analysis to succeed]
The pipeline writes an embedding only for an execution whose metadata analysis succeeded. When an Amazon Bedrock call fails, the execution is recorded as failed, its attributes are still saved, and the file version receives its embedding from a later run — a re-upload or a reindex.
:::

:::note[Metadata edits do not re-embed]
The embedding reflects the metadata that existed when the pipeline ran. A first upload usually carries asset and database metadata but no file metadata yet, and editing metadata later does not re-embed the file; re-executing the workflow on the same file version or running the vector reindexer picks up the current values.
:::

### Segment vectors

Two template tags of the pipeline add vectors beneath a file's whole-file vector. They are stored under the same file, so the lifecycle rules below apply to them by key prefix:

-   **Video time windows** — `VIDEO_SEGMENT_SECONDS` (off by default; a value of 2 or more turns it on) analyzes a video in fixed windows of that many seconds and embeds one vector per window, labelled by its time range, so a scene that lasts ten seconds in an hour of footage is found. Windows are fixed intervals; scene detection and audio transcription are not performed.
-   **Content chunks** — `CONTENT_CHUNKING` (on by default) embeds the full extracted text of a document, text, or data file in overlapping chunks, labelled by chunk number and page or sheet, so a 200-page manual is found by a topic on page 150 and not only by its first pages. Files above 50 MiB carry only their whole-file vector and no content chunks. Setting the tag to `false` keeps whole-file vectors only.

A search treats them as more chances for the file to match, never as more results. Every window or chunk of a file collapses into one hit that keeps the best score; the hit's `_vector.segmentHits` counts the windows or chunks that matched (`0` when only the whole-file vector did) and `_vector.bestSegment` names the winning one — its `segmentKey`, `segmentKind` (`videoTime` or `textChunk`), `segmentLabel`, and for a window `segmentStartMs` and `segmentEndMs` — or is `null` when the whole-file vector scored best. `nlp.itemsCollapsed` reports how many of a file's items were folded into its single hit. The web interface shows the best segment and the count in the relevance popover, and the CLI's table output names it in a `segment` column. A request with `includeSegments: false` searches whole-file vectors only; the web interface's **Search inside files** checkbox, on by default, sends it when cleared, and `vamscli search nlp --no-segments` and the MCP tool's `include_segments=false` are the same switch.

Segment vectors carry a known ranking bias: a window or a chunk embeds a short, focused text, so for a topic query its distance is not comparable with a whole file's composite vector, and documents with chunks tend to outrank files of other kinds. The file-type ordering below and the whole-file-only request are the mitigations; score normalization per kind is not performed.

Naming a file type in the query — "video of a forklift", "pdf about torque settings" — lists files of that type first. This is a soft boost, never a filter: files of other types still follow, so an incidental word cannot empty the answer, and `nlp.classIntent` echoes the classes the query named (`[]` when none). An explicit file-class filter remains a hard filter.

The matching timestamps or chunks themselves are not returned through the API; the per-window descriptions are kept as execution results so that a consumer can read them without re-analyzing the video. Content chunks serve a single vector query: VAMS does not generate answers from them, re-rank them, or combine them with keyword retrieval.

## Where embeddings live

Embeddings are stored in an Amazon DynamoDB table with a DynamoDB vector index. Each item is one file version — or one video time window or content chunk of it ([Segment vectors](#segment-vectors)) — keyed by `databaseId:assetId` and a sort key derived from the file path, its Amazon S3 version id, and the segment key (empty on the whole-file item), so a file's segment items share its prefix and follow it through every lifecycle change. Each item carries the vector, the source text, the modalities that contributed, and the identity of the pipeline execution that produced it. The vector index is named `vec-<model>-<dimensions>` (for example `vec-amazon-titan-embed-text-v2-0-1024`), uses cosine distance, and is created only when vector search is enabled; the table itself exists in every deployment.

Seven attributes on every item are indexed as filters and are always written: `databaseId`, `isLatest`, `isArchived`, `fileClass`, `fileExt`, `embeddingModelId`, and `segmentKind` (`none` on a whole-file item, `videoTime` or `textChunk` on a segment item). They are what a query narrows on before similarity is computed. See [AWS Resources](../architecture/aws-resources.md) and [Data Model](../architecture/data-model.md).

## Only the latest live version is searchable

Every file version keeps its own item, but a query matches only items whose `isLatest` is `"true"` and — unless the caller asks for archived content — whose `isArchived` is `"false"`. The vector indexer maintains both flags from the file and asset lifecycle events VAMS already emits:

| Event                                  | Effect on the file's vector items                                                             |
| -------------------------------------- | --------------------------------------------------------------------------------------------- |
| A new version of the file is uploaded  | The new version's item becomes latest; every other version of that file is marked not latest. |
| The file is archived                   | All of the file's items are marked archived and drop out of default results.                  |
| The file is unarchived                 | All of the file's items are marked live again.                                                |
| The asset is archived                  | Every item of every file in the asset is marked archived.                                     |
| The asset is unarchived (record only)  | Items keep their markers — the asset record is restored, files stay as they were archived.    |
| The asset is unarchived with its files | Each file's own unarchive marks that file's items live.                                       |
| The file is permanently deleted        | All of the file's items are deleted.                                                          |
| The asset is permanently deleted       | All of the asset's items are deleted.                                                         |

Searching a specific older version is not supported through the API; the per-version items make that possible later without re-embedding.

## Searching

Natural-language search is one API — `POST /search/nlp` — reached from three surfaces:

-   **Web** — the **Search** tab of the Assets and Files page offers a **Keyword / Natural language** toggle when both Amazon OpenSearch and vector search are enabled, and natural-language mode alone when only vector search is. See [Search and Discovery](../user-guide/search-and-discovery.md).
-   **CLI** — `vamscli search nlp` (for example `vamscli search nlp -q "…"`) with database, entity-type, file-class, file-extension, archived, size, and segment options. See [Search commands](../cli/commands/search.md).
-   **MCP** — the read-tier `search_nlp` tool. See [Agentic Development](../developer/agentic-development.md).

All three are gated on the `VECTORSEARCH` feature switch; the route does not exist on a deployment where vector search is off.

A request embeds the query with the same model as the index, then searches the vector index once for a caller who can read every database, or once per accessible database otherwise, evaluating up to 100 candidates per call. When segment vectors are included, each target is searched twice: a breadth call over whole-file vectors alone, so one long document's many chunks cannot fill the window for every other file, and a depth call over all items. Hits are merged by distance, collapsed to one per file, joined to their asset records, and checked against the caller's permissions with the same object-level authorization as every other asset read, so a user sees only files in assets they may access. The response uses the same envelope as keyword search — each hit carries `_score` (cosine similarity as a 0–1 value), the familiar `str_*` fields, and a `_vector` block with the distance, model id, source modalities, file class, `segmentHits`, and `bestSegment` — so tables, formatters, and agents work unchanged. In asset mode the hits are grouped by asset and the best-scoring file decides the asset's position. The `nlp` block reports the databases searched, the candidates evaluated, `itemsCollapsed` (the items folded into single hits), `classIntent` (the file classes the query named), and `truncated`, which is `true` when a whole-file search window filled or when more than 200 databases were in scope; `hits.total.relation` is then `gte`. Only the breadth calls set it, because only whole-file items — one per file — bound how many files the answer can hold; a full depth window, which a single chunked manual can fill, is reported as the `warnings` entry `segments:window_full` and does not mark the answer truncated; it is emitted only when no whole-file window filled. Each cause has its own `warnings` entry: `truncated:window`, `truncated:targets`, and `segments:window_full`. While the vector index is being created or rebuilt, the route answers `503` with `Vector index is being built`. See [Search API](../api/search.md).

## Filters with and without Amazon OpenSearch

Vector search does not need Amazon OpenSearch. The filters below are applied by the vector query itself and work in every deployment:

| Filter                               | Applied as                                                                         |
| ------------------------------------ | ---------------------------------------------------------------------------------- |
| Database                             | Intersected with the caller's accessible databases before the query                |
| Include archived                     | Drops the `isArchived = "false"` clause                                            |
| Include segments (`includeSegments`) | `false` adds `segmentKind = "none"`, so only whole-file vectors are searched       |
| File class, file extension           | Pushed into the query when a single value is given; post-filtered when several are |
| Entity type (`file` or `asset`)      | `asset` groups file hits by asset                                                  |

When an OpenSearch mode is enabled, the request additionally accepts the keyword search's `filters`, `metadataQuery`, `metadataSearchMode`, `geoSearch`, and `tags`, and enriches each hit with the indexed document (`MD_`, `AB_`, preview key, dates). VAMS runs one OpenSearch query over the vector hits' document ids, keeps only the hits that satisfy the constraints, and merges the documents in. When OpenSearch is off, those fields are ignored and the response lists a warning for each (`opensearch:fields_ignored`). An OpenSearch error during enrichment degrades to a warning (`opensearch:enrichment_failed`) rather than failing the search.

Keyword search and metadata filtering as described in [Metadata and Schemas](metadata-and-schemas.md#metadata-in-search) continue to require Amazon OpenSearch.

## Reindexing

The vector indexer is idempotent, so redelivered events are harmless. To rebuild the vector table — after enabling the feature on an existing deployment, or after the model-change procedure below — invoke the vector reindexer Lambda function (its name is published under the deployment's `resourceNames/lambdaFunctions/vectorReindexer` parameter) with a payload whose only required key is `operation`:

```json
{ "operation": "enqueue" }
```

-   `"clear"` deletes every vector item, paging and re-invoking itself until the table is empty.
-   `"enqueue"` enumerates the latest live version of every file that matches the system workflow's input filters and launches the SYSTEM GenAI metadata workflow for each through a paced queue whose concurrency is `app.vectorSearch.indexingConcurrency` (default 5).
-   `"both"` clears to completion, then enqueues.

Four optional keys narrow or resume an `enqueue`: `dryRun` (boolean — enumerate and report without launching), `limit` (integer — a cap on the number of files enqueued), `databaseId` (string — one database only), and `startAfter` (string — the continuation token a previous invocation returned when it hit the 15-minute cap). Omit a key rather than passing an empty or zero value; unknown top-level keys are rejected.

The `vectorBackfill` step of the data migration under `infra/deploymentDataMigration/` wraps this call (`--clear-vectors` selects `"both"`; `--dry-run` and `--limit` are forwarded as `dryRun` and `limit`). The launched executions appear in the executions list with trigger type `System-Reindex` and an execution group id per 1,000-file chunk of the run (`vec-<runId>-<chunk>`), so each chunk can be watched, aborted, or purged as a group. See [Reindex utilities](../developer/utilities/reindex.md).

## Changing the embedding model

The vector index is bound to one model and one dimension count. To move to a different embeddings model:

1. While vector search is still enabled, invoke the vector reindexer with `{"operation": "clear"}` and wait until it reports the table empty. Clearing first avoids write rejections on old-model items during the swap.
2. Set `app.vectorSearch.enabled` to `false` and deploy. Wait until `DescribeTable` on the vector table shows no `VectorIndexes` entry — AWS CloudFormation returns while the index is still deleting, and creating the next index during that window fails with `LimitExceededException`.
3. Set the new `app.vectorSearch.embeddingModelId` (and `embeddingDimensions` if the model's differ), set `enabled` back to `true`, and deploy. The new index has a new name.
4. Run the migration's `vectorBackfill` step without `--clear-vectors` (`{"operation": "enqueue"}`). `POST /search/nlp` returns `503` until the index is active.

## Costs

Vector search adds two kinds of Amazon Bedrock usage — one embeddings call per analyzed file version (plus one per video window or content chunk) and one per query — plus the analysis model calls of the pipeline that produces the source text, and Amazon DynamoDB storage and reads for the vector table. Every shipped default uses on-demand pricing. See [Costs](../overview/costs.md).

## Limits

-   A vector query evaluates at most 100 candidates per call (the DynamoDB `SearchVectors` `TopK` ceiling), and there is no cursor; the response reports `truncated` when a whole-file window filled and the `segments:window_full` warning when only a segment window did.
-   Only the latest live version of a file is searchable.
-   The matching time windows or chunks of a file are not returned; a file is one hit, and `_vector.bestSegment` names only its best match. Content chunks serve a single vector query: retrieval-augmented generation, re-ranking, and hybrid keyword retrieval are not offered.
-   Only text embeddings are exercised; multimodal embedding models are not documented or supported.
-   DynamoDB vector search is not available in the AWS European Sovereign Cloud; `app.vectorSearch.enabled` must be `false` there.
-   Lambda functions call Amazon Bedrock over standard (non-FIPS) endpoints even when `app.useFips` is `true`.
-   Changing `embeddingDimensions` on a populated index is unsupported; use the model-change procedure, which clears the table first.

## Related topics

-   [SYSTEM GenAI Metadata Generation pipeline](../pipelines/system-genai-metadata.md) — what produces the embeddings
-   [System pipelines](../pipelines/system-pipelines.md) — the category the pipeline belongs to
-   [Metadata and Schemas](metadata-and-schemas.md) — keyword search over metadata
-   [Search and Discovery](../user-guide/search-and-discovery.md) — the web search page
-   [OpenSearch](../developer/opensearch.md) — how keyword search relates to vector search
