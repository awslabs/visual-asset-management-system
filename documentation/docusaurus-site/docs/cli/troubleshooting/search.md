---
sidebar_label: Search
title: Search Troubleshooting
---

# Search Troubleshooting

This page covers issues encountered when using the VamsCLI `search` commands against the Amazon OpenSearch Service dual-index search system.

---

## Search Availability

### Search Commands Report That Search Is Disabled

**Symptoms:**

-   `vamscli search assets`, `search files`, `search simple`, or `search mapping` fails with "Search functionality is disabled for this environment"
-   The error suggests using `vamscli assets list` instead

**Cause:**

The `NOOPENSEARCH` feature switch is enabled in the deployment, so Amazon OpenSearch Service is not provisioned. All `search` subcommands check this feature switch before issuing a request and stop early when it is set.

**Resolution:**

1. Confirm the feature state:

    ```bash
    vamscli features list
    ```

    A `NOOPENSEARCH` entry in the enabled features confirms search is unavailable.

2. Use the non-search listing commands, which do not depend on Amazon OpenSearch Service:

    ```bash
    vamscli assets list
    vamscli database list-assets -d my-database
    ```

3. To enable full-text and metadata search, ask your VAMS administrator to provision Amazon OpenSearch Service for the deployment.

### Search Service Is Unavailable or the Endpoint Is Missing

**Symptoms:**

-   "Search service unavailable" errors when the deployment otherwise has search enabled
-   "Search endpoint not found" against an older deployment

**Cause:**

The Amazon OpenSearch Service domain is temporarily unreachable, or the deployment predates the dual-index search API.

**Resolution:**

-   Retry after a short delay, or confirm domain health with your administrator.
-   Verify the deployment version with `vamscli version`. The dual-index search system requires VAMS 2.2 or later.
-   Fall back to `vamscli assets list` or `vamscli database list-assets -d <database-id>` while the service is recovering.

---

## Authentication and Setup

### Profile Is Not Configured or Authentication Has Expired

**Symptoms:**

-   "Configuration not found for profile" before any search runs
-   "Authentication failed" or repeated 401 responses

**Cause:**

The active profile has not been initialized with `setup`, or the stored token is expired or invalid.

**Resolution:**

1. Configure and authenticate the profile:

    ```bash
    vamscli setup <api-gateway-url> --profile <profile-name>
    vamscli auth login -u <username> --profile <profile-name>
    ```

2. Check the current session:

    ```bash
    vamscli auth status
    ```

:::note
For external (non-Cognito) authentication, supply a token directly with `vamscli auth set-override --token <jwt>`. Override tokens are not auto-refreshed, so an expired override token fails immediately and must be replaced.
:::

---

## Search Parameters

### Invalid Entity Types in Simple Search

**Symptoms:**

-   `vamscli search simple --entity-types <value>` fails with "Invalid entity types"

**Cause:**

The `--entity-types` option accepts only `asset` and `file` (comma-separated). Any other value is rejected during parsing.

**Resolution:**

```bash
vamscli search simple -q "model" --entity-types asset
vamscli search simple -q "texture" --entity-types file
vamscli search simple -q "content" --entity-types asset,file
```

The `search assets` and `search files` commands set the entity type automatically and do not accept `--entity-types`.

### Metadata Query or Metadata Mode Is Rejected

**Symptoms:**

-   No results, or an invalid-parameter error, when using `--metadata-query`
-   An error when passing an unexpected value to `--metadata-mode`

**Cause:**

Metadata queries use `field:value` syntax with uppercase `AND`/`OR` operators, and `--metadata-mode` accepts only `key`, `value`, or `both` (default `both`).

**Resolution:**

```bash
# field:value syntax with the MD_ prefix
vamscli search assets --metadata-query "MD_str_product:Training"

# Combine conditions with uppercase AND / OR
vamscli search assets --metadata-query "MD_str_product:A AND MD_num_version:1"

# Wildcards belong in the value portion
vamscli search assets --metadata-query "MD_str_product:Train*"

# Valid metadata modes: key, value, both
vamscli search assets --metadata-query "product" --metadata-mode key
vamscli search assets --metadata-query "Training" --metadata-mode value
```

:::tip
Run `vamscli search mapping` to list the searchable fields per index. Metadata fields are prefixed with `MD_` (for example, `MD_str_product`, `MD_num_version`).
:::

### Invalid JSON in an Input File

**Symptoms:**

-   "Invalid JSON in input file" or "JSON input file not found" when a search reads parameters from a file

**Cause:**

The referenced file is missing, or its contents are not valid JSON (often a trailing comma or unquoted key).

**Resolution:**

Validate the file before reusing it:

```bash
python -m json.tool search_params.json
```

```json
{
    "query": "test",
    "database": "my-db"
}
```

---

## Filters

### Filter String Fails to Parse

**Symptoms:**

-   "Invalid JSON filter format" or "JSON filters must be an array" from the `--filters` option

**Cause:**

The `--filters` option accepts two formats: a JSON **array** of OpenSearch clauses, or a query-string expression. A JSON object (not wrapped in an array) and malformed JSON are both rejected.

**Resolution:**

```bash
# Query-string format (simplest)
vamscli search assets --filters 'str_databaseid:"my-db"'

# JSON array format — note the surrounding brackets
vamscli search assets --filters '[{"query_string": {"query": "str_databaseid:\"my-db\""}}]'
```

A bare JSON object such as `'{"query_string": {"query": "test"}}'` is invalid; wrap it in `[ ... ]` or use the query-string form.

### Filter Returns Unexpected Results

**Symptoms:**

-   A filter that looks correct returns no results, or matches more than intended

**Cause:**

Query-string values must be quoted, field names must exist in the target index, and exact-match comparisons can be case-sensitive.

**Resolution:**

1. Quote values and combine clauses with uppercase `AND`/`OR`:

    ```bash
    vamscli search assets --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
    ```

2. Confirm field names against the mapping, and test clauses individually before combining them:

    ```bash
    vamscli search mapping
    vamscli search assets --filters 'str_databaseid:"my-db"'
    ```

3. For case-insensitive matching, use wildcards in the value:

    ```bash
    vamscli search assets --metadata-query "MD_str_product:*training*"
    ```

---

## Results and Output

### No Results Found

**Symptoms:**

-   A search that should match returns zero hits

**Cause:**

The query is too narrow, the items are archived, the wrong entity command is in use, or recent uploads have not finished indexing.

**Resolution:**

1. Broaden the query, then confirm the searchable fields:

    ```bash
    vamscli search assets -q "model"
    vamscli search mapping
    ```

2. Include archived items when relevant:

    ```bash
    vamscli search assets -q "model" --include-archived
    ```

3. Try the matching entity command — file attributes such as `--file-ext` apply to `search files`, not `search assets`:

    ```bash
    vamscli search files --filters 'str_fileext:"gltf"'
    ```

:::note
Amazon OpenSearch Service indexing is asynchronous. After a large upload or bulk metadata change, allow 30-60 seconds for newly indexed items to appear in search results.
:::

### Metadata Missing From or Unexpectedly Present in General Search

**Symptoms:**

-   Metadata terms are expected in a general `-q` search but do not match, or metadata noise appears when it is not wanted

**Cause:**

General search includes metadata fields by default. The `--include-metadata/--no-metadata` toggle controls this behavior.

**Resolution:**

```bash
# Default — metadata included in the general query
vamscli search assets -q "Training"

# Exclude metadata from the general query
vamscli search assets -q "Training" --no-metadata
```

### Match Explanations Do Not Appear

**Symptoms:**

-   `--explain-results` is set but no explanation text is shown

**Cause:**

Explanations are derived from field matches and only appear alongside search hits. A query with no hits, or matches on fields without highlightable content, produces little or no explanation.

**Resolution:**

```bash
vamscli search assets -q "model" --explain-results
vamscli search assets -q "model" --explain-results --output-format json
```

Confirm the query returns hits first; explanations accompany the results rather than appearing on an empty result set.

---

## Sorting and the Dual-Index Layout

### Sort or Field Reference Targets the Wrong Index

**Symptoms:**

-   Sorting by a field has no effect, or a field referenced in a filter is not found
-   `vamscli search mapping` shows two distinct sets of fields

**Cause:**

The dual-index system maintains separate mappings for assets and files. Each command searches its own index, so asset-only fields are not present in the file index and the reverse. Two field sets in the mapping output is expected.

**Resolution:**

Use fields that belong to the index being searched:

```bash
# Asset index fields
vamscli search assets --sort-field str_assetname

# File index fields
vamscli search files --sort-field str_key
```

Run `vamscli search mapping --output-format json` to see which fields belong to each index.

---

## Performance

### Searches Are Slow or Return Very Large Result Sets

**Symptoms:**

-   Queries take a long time or exhaust memory when formatting output

**Cause:**

Overly broad queries (for example, `-q "*"`), broad metadata-mode searches, wide wildcards, and large page sizes all increase load. The `--size` maximum is 2000 for `search assets`/`search files` and 1000 for `search simple`.

**Resolution:**

1. Narrow the query with filters and a specific database:

    ```bash
    vamscli search assets -q "model" --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
    ```

2. Prefer specific metadata modes and exact matches over `both` with broad wildcards:

    ```bash
    vamscli search assets --metadata-query "MD_str_product:Training" --metadata-mode value
    ```

3. Page through large result sets and export with CSV, which is more memory-efficient than table formatting:

    ```bash
    vamscli search assets -q "model" --from 0 --size 1000 --output-format csv > batch1.csv
    vamscli search assets -q "model" --from 1000 --size 1000 --output-format csv > batch2.csv
    ```

---

## Diagnostics

For detailed request and response information, run the CLI with the global `--verbose` flag before the command group:

```bash
vamscli --verbose search assets -q "test"
```

Verbose mode reports the full API request and response, timing, and detailed error information, which helps distinguish parameter problems from service-side errors.

---

## Related Pages

-   [Search Commands](../commands/search.md)
-   [Metadata Commands](../commands/metadata.md)
-   [General CLI Troubleshooting](./general.md)
