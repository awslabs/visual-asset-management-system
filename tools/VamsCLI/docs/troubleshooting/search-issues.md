# Search Issues Troubleshooting

This guide helps resolve common issues with VamsCLI search commands.

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

1. **Invalid property filters JSON**:

    ```bash
    # ❌ Invalid JSON
    vamscli search assets --property-filters 'invalid json'

    # ✅ Valid JSON
    vamscli search assets --property-filters '[{"propertyKey":"str_description","operator":"=","value":"test"}]'
    ```

2. **Missing required property filter fields**:

    ```bash
    # ❌ Missing operator and value
    vamscli search assets --property-filters '[{"propertyKey":"str_description"}]'

    # ✅ Complete filter
    vamscli search assets --property-filters '[{"propertyKey":"str_description","operator":"=","value":"test"}]'
    ```

3. **Conflicting sort options**:

    ```bash
    # ❌ Cannot use both
    vamscli search assets -q "test" --sort-desc --sort-asc

    # ✅ Use one or the other
    vamscli search assets -q "test" --sort-desc
    ```

### Error: "JSON input file not found"

**Cause**: The specified JSON input file doesn't exist.

**Solutions**:

1. **Check file path**:

    ```bash
    # Verify file exists
    ls search_params.json

    # Use absolute path if needed
    vamscli search assets --jsonInput /full/path/to/search_params.json
    ```

2. **Create valid JSON input file**:
    ```json
    {
        "query": "training model",
        "database": "my-database",
        "operation": "AND",
        "from": 0,
        "size": 100
    }
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

**Cause**: VAMS API version doesn't support search functionality.

**Solutions**:

1. **Check API version**:

    ```bash
    vamscli version
    ```

2. **Verify VAMS deployment** supports search (version 2.2+)

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

2. **Large result sets without pagination**:

    ```bash
    # ❌ Requesting too many results at once
    vamscli search assets -q "model" --size 2000

    # ✅ Use reasonable page sizes
    vamscli search assets -q "model" --size 100 --max-results 1000
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
    vamscli search assets -q "model" --max-results 5000 --output-format csv
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

    # Check without filters
    vamscli search assets -q "model"  # Remove --database, --asset-type, etc.
    ```

2. **Check available fields**:

    ```bash
    # See what fields are available
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
    ```

### Unexpected Results

**Common Issues**:

1. **Case sensitivity in filters**:

    ```bash
    # OpenSearch may be case-sensitive for exact matches
    vamscli search assets --asset-type "3D-Model"  # May not match "3d-model"
    ```

2. **Tag format issues**:

    ```bash
    # Ensure tag names match exactly
    vamscli search assets --tags "Training"  # May not match "training"
    ```

3. **Property filter field names**:
    ```bash
    # Use correct field names from mapping
    vamscli search mapping  # Check available fields first
    ```

## JSON Input/Output Issues

### Invalid JSON Input

**Common Problems**:

1. **Malformed JSON**:

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

2. **Incorrect field names**:

    ```json
    // ❌ Wrong field name
    {
        "search_query": "test"
    }

    // ✅ Correct field name
    {
        "query": "test"
    }
    ```

### JSON Output Parsing Issues

**Solutions**:

1. **Use proper JSON parsing tools**:

    ```bash
    # Use jq for JSON processing
    vamscli search assets -q "model" --output-format json | jq '.[] | .assetName'

    # Or save to file first
    vamscli search assets -q "model" --output-format json > results.json
    ```

2. **Handle empty results**:
    ```bash
    # Check for empty results before processing
    vamscli search assets -q "nonexistent" --output-format json
    # Returns: []
    ```

## Feature Switch Issues

### Error: "Feature switch check failed"

**Cause**: Unable to retrieve feature switches from the API.

**Solutions**:

1. **Check authentication**:

    ```bash
    vamscli auth status
    ```

2. **Verify API connectivity**:

    ```bash
    vamscli version  # This checks API connectivity
    ```

3. **Re-authenticate if needed**:
    ```bash
    vamscli auth login -u <username>
    ```

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

## Common Error Messages and Solutions

| Error Message                                    | Cause                        | Solution                                  |
| ------------------------------------------------ | ---------------------------- | ----------------------------------------- |
| "Search is not available"                        | NOOPENSEARCH feature enabled | Use `vamscli assets list` instead         |
| "Invalid search parameters"                      | Malformed query parameters   | Check JSON syntax and required fields     |
| "Property filter missing required field"         | Incomplete property filter   | Include propertyKey, operator, and value  |
| "Cannot specify both --sort-desc and --sort-asc" | Conflicting sort options     | Use only one sort direction               |
| "JSON input file not found"                      | Missing input file           | Check file path and permissions           |
| "Search service unavailable"                     | OpenSearch service down      | Contact administrator or try later        |
| "Authentication failed"                          | Expired/invalid token        | Re-authenticate with `vamscli auth login` |

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
    vamscli search mapping  # Test basic connectivity
    vamscli search assets -q "test"  # Simple search
    ```

4. **Contact your VAMS administrator** for deployment-specific issues

## Related Documentation

-   [Search Operations Commands](../commands/search-operations.md) - Complete search command reference
-   [Setup and Authentication](../commands/setup-auth.md) - Initial configuration
-   [Global Options](../commands/global-options.md) - Profile and authentication options
-   [General Troubleshooting](general-troubleshooting.md) - Common VamsCLI issues
