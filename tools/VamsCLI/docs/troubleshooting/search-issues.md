# Search Issues Troubleshooting - Dual-Index OpenSearch

This guide helps resolve common issues with VamsCLI search commands using the dual-index OpenSearch system.

## Search Disabled Errors

### Error: "Search functionality is disabled for this environment"

**Cause**: The `NOOPENSEARCH` feature switch is enabled in your VAMS deployment.

**Solutions**:

1. **Check feature switches**:

    ```bash
    vamscli features list
    ```

    Look for `NOOPENSEARCH` in the enabled features list.

2. **Use alternative commands**:

    ```bash
    # Instead of search assets
    vamscli assets list

    # Instead of search assets in specific database
    vamscli database list-assets -d my-database
    ```

3. **Contact administrator**: If you need search functionality, contact your VAMS administrator to enable OpenSearch.

## Authentication and Setup Issues

### Error: "Configuration not found for profile"

**Cause**: VamsCLI is not configured for the specified profile.

**Solution**:

```bash
# Configure VamsCLI
vamscli setup <api-gateway-url> --profile <profile-name>

# Authenticate
vamscli auth login -u <username> --profile <profile-name>
```

### Error: "Authentication failed"

**Cause**: Authentication token is expired or invalid.

**Solutions**:

1. **Re-authenticate**:

    ```bash
    vamscli auth login -u <username>
    ```

2. **Check authentication status**:

    ```bash
    vamscli auth status
    ```

3. **Use token override** (for external authentication):
    ```bash
    vamscli search assets -q "model" --token-override <token> --user-id <user-id>
    ```

## Search Parameter Issues

### Error: "Invalid search parameters"

**Common Causes and Solutions**:

1. **Invalid entity types**:

    ```bash
    # ❌ Invalid entity type
    vamscli search simple --entity-types invalid

    # ✅ Valid entity types
    vamscli search simple --entity-types asset
    vamscli search simple --entity-types file
    vamscli search simple --entity-types asset,file
    ```

2. **Invalid metadata query syntax**:

    ```bash
    # ❌ Missing colon in field:value format
    vamscli search assets --metadata-query "MD_str_product Training"

    # ✅ Correct field:value format
    vamscli search assets --metadata-query "MD_str_product:Training"
    ```

3. **Invalid metadata mode**:

    ```bash
    # ❌ Invalid mode
    vamscli search assets --metadata-query "test" --metadata-mode invalid

    # ✅ Valid modes: key, value, both
    vamscli search assets --metadata-query "test" --metadata-mode key
    ```

### Error: "Invalid JSON in input file"

**Cause**: The JSON input file contains malformed JSON.

**Solutions**:

1. **Validate JSON syntax**:

    ```bash
    # Use a JSON validator or linter
    python -m json.tool search_params.json
    ```

2. **Check for common JSON errors**:

    ```json
    // ❌ Invalid - missing quotes, trailing comma
    {
        query: "test",
        database: "my-db",
    }

    // ✅ Valid JSON
    {
        "query": "test",
        "database": "my-db"
    }
    ```

## Metadata Search Issues

### No Results with Metadata Query

**Troubleshooting Steps**:

1. **Check metadata field names**:

    ```bash
    # View available metadata fields
    vamscli search mapping

    # Metadata fields start with MD_ prefix
    # Example: MD_str_product, MD_num_version
    ```

2. **Verify metadata field exists**:

    ```bash
    # Search for metadata field names
    vamscli search assets --metadata-query "product" --metadata-mode key
    ```

3. **Try different search modes**:

    ```bash
    # Search only values
    vamscli search assets --metadata-query "Training" --metadata-mode value

    # Search both keys and values
    vamscli search assets --metadata-query "Training" --metadata-mode both
    ```

4. **Use wildcards**:
    ```bash
    # Partial match with wildcard
    vamscli search assets --metadata-query "MD_str_product:Train*"
    ```

### Metadata Query Syntax Errors

**Common Issues**:

1. **Missing MD\_ prefix**:

    ```bash
    # ❌ Missing prefix (will be added automatically in some cases)
    vamscli search assets --metadata-query "str_product:Training"

    # ✅ Include MD_ prefix for clarity
    vamscli search assets --metadata-query "MD_str_product:Training"
    ```

2. **Invalid AND/OR syntax**:

    ```bash
    # ❌ Lowercase operators don't work
    vamscli search assets --metadata-query "MD_str_product:A and MD_num_version:1"

    # ✅ Use uppercase AND/OR
    vamscli search assets --metadata-query "MD_str_product:A AND MD_num_version:1"
    ```

3. **Incorrect wildcard usage**:

    ```bash
    # ❌ Wildcard in wrong position
    vamscli search assets --metadata-query "MD_str_product*:Training"

    # ✅ Wildcard in value part
    vamscli search assets --metadata-query "MD_str_product:Train*"
    ```

## Entity Type Filtering Issues

### Searching Wrong Index

**Problem**: Getting file results when searching for assets, or vice versa.

**Solution**: The `assets` and `files` commands automatically set the correct entity type:

```bash
# ✅ Automatically searches asset index only
vamscli search assets -q "model"

# ✅ Automatically searches file index only
vamscli search files -q "texture"

# ✅ Use simple search to control entity types explicitly
vamscli search simple -q "model" --entity-types asset
vamscli search simple -q "texture" --entity-types file
vamscli search simple -q "content" --entity-types asset,file
```

### Mixed Results Confusion

**Problem**: Getting both assets and files in simple search results.

**Solution**: Use `--entity-types` to filter:

```bash
# Search only assets
vamscli search simple -q "model" --entity-types asset

# Search only files
vamscli search simple -q "model" --entity-types file
```

## API and Network Issues

### Error: "Search service unavailable"

**Cause**: OpenSearch service is down or unreachable.

**Solutions**:

1. **Check VAMS deployment status** with your administrator
2. **Try again later** - the service may be temporarily unavailable
3. **Use alternative commands**:
    ```bash
    vamscli assets list
    vamscli database list-assets -d <database-id>
    ```

### Error: "Search endpoint not found"

**Cause**: VAMS API version doesn't support the dual-index search system.

**Solutions**:

1. **Check API version**:

    ```bash
    vamscli version
    ```

2. **Verify VAMS deployment** supports dual-index search (version 2.2+)

3. **Contact administrator** to upgrade VAMS if needed

## Performance Issues

### Slow Search Performance

**Causes and Solutions**:

1. **Too broad search query**:

    ```bash
    # ❌ Very broad search
    vamscli search assets -q "*"

    # ✅ More specific search
    vamscli search assets -q "training model" -d specific-database
    ```

2. **Searching both indexes unnecessarily**:

    ```bash
    # ❌ Simple search without entity type (searches both indexes)
    vamscli search simple -q "model"

    # ✅ Specify entity type to search one index
    vamscli search simple -q "model" --entity-types asset
    ```

3. **Use filters to narrow results**:
    ```bash
    # ✅ Add filters to reduce result set
    vamscli search assets -q "model" -d specific-db --asset-type "3d-model"
    ```

### Memory Issues with Large Exports

**Solutions**:

1. **Use CSV format for large exports**:

    ```bash
    vamscli search assets -q "model" --output-format csv > results.csv
    ```

2. **Limit result size**:

    ```bash
    vamscli search assets -q "model" --size 500 --output-format csv
    ```

3. **Use pagination for processing**:
    ```bash
    # Process in chunks
    vamscli search assets -q "model" --from 0 --size 1000 --output-format csv > batch1.csv
    vamscli search assets -q "model" --from 1000 --size 1000 --output-format csv > batch2.csv
    ```

## Search Result Issues

### No Results Found

**Troubleshooting Steps**:

1. **Verify search terms**:

    ```bash
    # Try broader search
    vamscli search assets -q "model"


2. **Check available fields**:

    ```bash
    # See what fields are available in both indexes
    vamscli search mapping
    ```

3. **Verify database access**:

    ```bash
    # List accessible databases
    vamscli database list

    # Check specific database
    vamscli database get -d <database-id>
    ```

4. **Try different search types**:

    ```bash
    # Try file search instead of asset search
    vamscli search files -q "model"

    # Try simple search
    vamscli search simple -q "model"
    ```

5. **Check if items are archived**:
    ```bash
    # Include archived items in search
    vamscli search assets -q "model" --include-archived
    ```

### Unexpected Results

**Common Issues**:

1. **Metadata not included in general search**:

    ```bash
    # ❌ Metadata excluded from general search
    vamscli search assets -q "Training" --no-metadata

    # ✅ Include metadata in general search (default)
    vamscli search assets -q "Training"
    # OR explicitly include
    vamscli search assets -q "Training" --include-metadata
    ```

2. **Wrong entity type**:

    ```bash
    # ❌ Searching assets when you want files
    vamscli search assets --file-ext "gltf"  # Won't work

    # ✅ Use files command for file search
    vamscli search files --file-ext "gltf"
    ```

3. **Case sensitivity in filters**:
    ```bash
    # OpenSearch may be case-sensitive for exact matches
    # Try both cases or use wildcards
    vamscli search assets --asset-type "3d-model"
    vamscli search assets --metadata-query "MD_str_product:*training*"
    ```

## Explain Results Issues

### Explanations Not Showing

**Problem**: Using `--explain-results` but not seeing explanations.

**Troubleshooting**:

1. **Check output format**:

    ```bash
    # ✅ Explanations show in table format
    vamscli search assets -q "model" --explain-results

    # ✅ Explanations included in JSON
    vamscli search assets -q "model" --explain-results --output-format json
    ```

2. **Verify results have matches**:

    ```bash
    # Explanations only appear when there are search hits
    vamscli search assets -q "model" --explain-results
    ```

3. **Check for highlights**:
    - Explanations are based on field matches
    - If no fields match, explanation may be minimal

## Debug Mode

For detailed error information, use debug mode:

```bash
# Enable debug output
vamscli search assets -q "test" --debug
```

This will show:

-   Full API request/response details
-   Stack traces for errors
-   Detailed timing information
-   Entity type filtering details
-   Metadata search query construction

## Common Error Messages and Solutions

| Error Message                | Cause                        | Solution                                    |
| ---------------------------- | ---------------------------- | ------------------------------------------- |
| "Search is not available"    | NOOPENSEARCH feature enabled | Use `vamscli assets list` instead           |
| "Invalid search parameters"  | Malformed query parameters   | Check parameter syntax and values           |
| "Invalid entity types"       | Wrong entity type specified  | Use "asset", "file", or "asset,file"        |
| "Invalid metadata query"     | Malformed metadata query     | Check field:value format and AND/OR syntax  |
| "Search service unavailable" | OpenSearch service down      | Contact administrator or try later          |
| "Authentication failed"      | Expired/invalid token        | Re-authenticate with `vamscli auth login`   |
| "Search endpoint not found"  | Old VAMS version             | Upgrade to VAMS 2.2+ for dual-index support |

## Getting Help

If you continue to experience issues:

1. **Check feature switches**:

    ```bash
    vamscli features list
    ```

2. **Verify setup and authentication**:

    ```bash
    vamscli auth status
    vamscli version
    ```

3. **Test with minimal parameters**:

    ```bash
    # Test basic connectivity
    vamscli search mapping

    # Test simple search
    vamscli search simple -q "test"

    # Test asset search
    vamscli search assets -q "test"
    ```

4. **Try simple search first**:

    ```bash
    # Simple search is easier to troubleshoot
    vamscli search simple -q "model" --entity-types asset
    ```

5. **Check dual-index mappings**:

    ```bash
    # Verify both indexes are available
    vamscli search mapping --output-format json
    ```

6. **Contact your VAMS administrator** for deployment-specific issues

## Dual-Index Specific Issues

### Results from Wrong Index

**Problem**: Getting file results when expecting assets, or vice versa.

**Solution**: Commands automatically filter by entity type:

```bash
# ✅ Assets command only searches asset index
vamscli search assets -q "model"

# ✅ Files command only searches file index
vamscli search files -q "texture"

# ✅ Simple search lets you control entity types
vamscli search simple -q "content" --entity-types asset
```

### Mapping Shows Two Indexes

**This is expected behavior**: The dual-index system has separate mappings for assets and files.

```bash
# View both index mappings
vamscli search mapping

# Output will show:
# === Asset Index ===
# (asset fields)
#
# === File Index ===
# (file fields)
```

### Field Not Found in Index

**Problem**: Trying to search or sort by a field that doesn't exist in the target index.

**Solution**:

1. **Check which fields are in which index**:

    ```bash
    vamscli search mapping
    ```

2. **Use correct fields for each entity type**:

    ```bash
    # ✅ Asset-specific fields
    vamscli search assets --sort-field str_assetname

    # ✅ File-specific fields
    vamscli search files --sort-field str_key

    # ❌ Don't use file fields in asset search
    vamscli search assets --sort-field str_key  # Won't work
    ```

## Performance Optimization

### Slow Metadata Searches

**Solutions**:

1. **Use specific metadata mode**:

    ```bash
    # ❌ Searching both keys and values (slower)
    vamscli search assets --metadata-query "Training" --metadata-mode both

    # ✅ Search only values if you know it's a value
    vamscli search assets --metadata-query "Training" --metadata-mode value
    ```

2. **Use exact matches when possible**:

    ```bash
    # ❌ Wildcard search (slower)
    vamscli search assets --metadata-query "MD_str_product:*Training*"

    # ✅ Exact match (faster)
    vamscli search assets --metadata-query "MD_str_product:Training"
    ```

3. **Combine with filters**:
    ```bash
    # ✅ Narrow search with filters
    vamscli search assets --metadata-query "MD_str_product:Training" --filters 'str_databaseid:"specific-db"'
    ```

### Large Result Sets

**Solutions**:

1. **Use pagination**:

    ```bash
    # Fetch results in smaller batches
    vamscli search assets -q "model" --size 100
    ```

2. **Add more filters**:

    ```bash
    # Narrow results with filters
    vamscli search assets -q "model" --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model" AND list_tags:"training"'
    ```

3. **Use CSV for large exports**:
    ```bash
    # CSV is more memory-efficient
    vamscli search assets -q "model" --output-format csv > results.csv
    ```

## Filter Syntax Issues

### Error: "Invalid JSON filter format"

**Cause**: Malformed JSON in `--filters` argument.

**Solutions**:

1. **Check JSON syntax**:

    ```bash
    # ❌ Invalid JSON
    --filters '[{"query_string": {"query": "test"}]'  # Missing closing brace

    # ✅ Valid JSON
    --filters '[{"query_string": {"query": "test"}}]'
    ```

2. **Escape quotes properly**:

    ```bash
    # ✅ Proper escaping in JSON format
    --filters '[{"query_string": {"query": "str_databaseid:\"my-db\""}}]'
    ```

3. **Use query string format instead**:
    ```bash
    # ✅ Simpler query string format
    --filters 'str_databaseid:"my-db"'
    ```

### Error: "JSON filters must be an array"

**Cause**: JSON filter is not an array.

**Solution**:

```bash
# ❌ JSON object (not array)
--filters '{"query_string": {"query": "test"}}'

# ✅ JSON array
--filters '[{"query_string": {"query": "test"}}]'

# ✅ Or use query string format
--filters 'str_databaseid:"test"'
```

### Filter Not Working as Expected

**Troubleshooting**:

1. **Verify field names**:

    ```bash
    # Check available fields
    vamscli search mapping
    ```

2. **Check filter syntax**:

    ```bash
    # ❌ Missing quotes around values
    --filters 'str_databaseid:my-db'

    # ✅ Values must be quoted
    --filters 'str_databaseid:"my-db"'
    ```

3. **Test filters individually**:

    ```bash
    # Test each filter separately
    vamscli search assets --filters 'str_databaseid:"my-db"'
    vamscli search assets --filters 'str_assettype:"3d-model"'

    # Then combine
    vamscli search assets --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
    ```

4. **Use simple mode for troubleshooting**:
    ```bash
    # Simple mode is easier to debug
    vamscli search simple -d my-db --asset-type "3d-model" --entity-types asset
    ```

## Related Documentation

-   [Search Operations Commands](../commands/search-operations.md) - Complete search command reference
-   [Setup and Authentication](../commands/setup-auth.md) - Initial configuration
-   [Metadata Management](../commands/metadata-management.md) - Working with metadata
-   [Global Options](../commands/global-options.md) - Profile and authentication options
-   [General Troubleshooting](general-troubleshooting.md) - Common VamsCLI issues
