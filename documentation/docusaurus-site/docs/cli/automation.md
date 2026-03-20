# Automation and Scripting

VamsCLI is designed for programmatic use in scripts, CI/CD pipelines, and bulk operations. This page covers JSON output mode, scripting patterns, CI/CD integration, pagination, and error handling.

## JSON Output Mode

Every VamsCLI command that produces output supports the `--json-output` flag. When enabled, the command outputs a single JSON object to stdout with no formatting, status messages, or color codes -- making it safe to parse with tools like `jq`.

```bash
vamscli database list --json-output
```

:::note[Behavior Differences in JSON Mode]
When `--json-output` is enabled:

- Status messages and progress indicators are suppressed
- Interactive confirmation prompts are skipped (use `--confirm` flags instead)
- Errors are returned as JSON objects to stderr with a non-zero exit code
- Password prompts are disabled (provide `--password` explicitly)
:::


## Scripting Patterns

### List All Assets in a Database

Iterate over all assets and process each one:

```bash
#!/bin/bash
DATABASE_ID="my-database"

# Fetch all assets as JSON and extract asset IDs
ASSET_IDS=$(vamscli assets list -d "$DATABASE_ID" --auto-paginate --json-output | \
    jq -r '.Items[].assetId')

for ASSET_ID in $ASSET_IDS; do
    echo "Processing asset: $ASSET_ID"
    vamscli assets get "$ASSET_ID" -d "$DATABASE_ID" --json-output | jq '.assetName'
done
```

### Bulk Upload Files from a Directory

Upload all files in a local directory to an existing asset:

```bash
#!/bin/bash
DATABASE_ID="my-database"
ASSET_ID="my-asset"

# Upload entire directory recursively
vamscli file upload ./data/ -d "$DATABASE_ID" -a "$ASSET_ID" --recursive

# Or upload specific file types
for FILE in ./models/*.gltf; do
    echo "Uploading: $FILE"
    vamscli file upload "$FILE" -d "$DATABASE_ID" -a "$ASSET_ID"
done
```

### Export All Metadata to JSON

Export metadata for every asset in a database:

```bash
#!/bin/bash
DATABASE_ID="my-database"
OUTPUT_DIR="./metadata-export"
mkdir -p "$OUTPUT_DIR"

ASSET_IDS=$(vamscli assets list -d "$DATABASE_ID" --auto-paginate --json-output | \
    jq -r '.Items[].assetId')

for ASSET_ID in $ASSET_IDS; do
    echo "Exporting metadata for: $ASSET_ID"
    vamscli metadata asset list -d "$DATABASE_ID" -a "$ASSET_ID" --json-output \
        > "$OUTPUT_DIR/${ASSET_ID}.json"
done
```

### Search and Filter Results with jq

Use OpenSearch to find assets and filter the results:

```bash
# Search for assets matching a query and extract specific fields
vamscli search assets -q "training" --json-output | \
    jq '[.hits.hits[]._source | {id: .str_assetid, name: .str_assetname, database: .str_databaseid}]'

# Find all GLTF files and export as CSV
vamscli search files --filters 'str_fileext:"gltf"' --output-format csv > gltf-files.csv

# Count assets by database
vamscli assets list --auto-paginate --json-output | \
    jq '[.Items[].databaseId] | group_by(.) | map({database: .[0], count: length})'
```

### Create Assets from a CSV File

```bash
#!/bin/bash
DATABASE_ID="my-database"

# CSV format: name,description,distributable
while IFS=',' read -r NAME DESCRIPTION DISTRIBUTABLE; do
    DIST_FLAG=""
    if [ "$DISTRIBUTABLE" = "true" ]; then
        DIST_FLAG="--distributable"
    else
        DIST_FLAG="--no-distributable"
    fi

    echo "Creating asset: $NAME"
    vamscli assets create -d "$DATABASE_ID" \
        --name "$NAME" \
        --description "$DESCRIPTION" \
        $DIST_FLAG \
        --json-output
done < assets.csv
```

## CI/CD Integration

### Authentication in CI/CD

For automated environments, use one of the following authentication approaches.

#### API Keys (Recommended for CI/CD)

Create an API key through the VAMS web interface or CLI, then use it as a token override:

```bash
vamscli auth login --user-id ci-bot@example.com --token-override "$VAMS_API_KEY"
```

#### Token Override with Service Tokens

If your organization uses a service token system, pass the token directly:

```bash
vamscli auth login --user-id "$VAMS_SERVICE_USER" \
    --token-override "$VAMS_SERVICE_TOKEN" \
    --expires-at "+3600"
```

#### Saved Credentials (Development Only)

For development environments where security requirements are lower:

```bash
vamscli auth login -u "$VAMS_USERNAME" -p "$VAMS_PASSWORD" --save-credentials
```

:::warning[Credential Security]
Never store passwords in CI/CD pipeline definitions or version control. Use your CI/CD platform's secrets management to inject credentials as environment variables.
:::


### Profile-Based Multi-Environment Configuration

Configure profiles for each environment during your pipeline setup stage:

```bash
# Setup stage
vamscli --profile staging setup https://staging-vams.example.com --skip-version-check
vamscli --profile staging auth login --user-id ci-bot@example.com --token-override "$STAGING_TOKEN"

# Deployment stage
vamscli --profile staging assets create -d release-db \
    --name "Build $BUILD_NUMBER" \
    --description "Automated build artifacts" \
    --distributable \
    --json-output

vamscli --profile staging file upload ./dist/ \
    -d release-db -a "build-$BUILD_NUMBER" \
    --recursive --json-output
```

### Example GitHub Actions Workflow

```yaml
name: Upload Assets to VAMS
on:
  push:
    branches: [main]

jobs:
  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.13'

      - name: Install VamsCLI
        run: |
          cd tools/VamsCLI
          pip install -e .

      - name: Configure VamsCLI
        run: |
          vamscli setup ${{ secrets.VAMS_URL }} --skip-version-check
          vamscli auth login \
            --user-id ${{ secrets.VAMS_USER }} \
            --token-override "${{ secrets.VAMS_TOKEN }}"

      - name: Upload Build Artifacts
        run: |
          ASSET_RESULT=$(vamscli assets create \
            -d "${{ vars.VAMS_DATABASE }}" \
            --name "Release ${{ github.sha }}" \
            --description "Build from commit ${{ github.sha }}" \
            --distributable \
            --json-output)

          ASSET_ID=$(echo "$ASSET_RESULT" | jq -r '.assetId')

          vamscli file upload ./dist/ \
            -d "${{ vars.VAMS_DATABASE }}" \
            -a "$ASSET_ID" \
            --recursive --json-output
```

### Example GitLab CI Pipeline

```yaml
stages:
  - upload

upload-to-vams:
  stage: upload
  image: python:3.13
  script:
    - cd tools/VamsCLI && pip install -e . && cd ../..
    - vamscli setup "$VAMS_URL" --skip-version-check
    - vamscli auth login --user-id "$VAMS_USER" --token-override "$VAMS_TOKEN"
    - |
      ASSET_ID=$(vamscli assets create \
        -d "$VAMS_DATABASE" \
        --name "Pipeline $CI_PIPELINE_ID" \
        --description "GitLab pipeline build" \
        --distributable \
        --json-output | jq -r '.assetId')
    - vamscli file upload ./artifacts/ -d "$VAMS_DATABASE" -a "$ASSET_ID" --recursive --json-output
  variables:
    VAMS_URL: $VAMS_PROD_URL
    VAMS_USER: $VAMS_CI_USER
    VAMS_TOKEN: $VAMS_CI_TOKEN
    VAMS_DATABASE: $VAMS_PROD_DATABASE
```

## Retry Configuration

VamsCLI automatically retries requests that receive HTTP 429 (Too Many Requests) responses using exponential backoff with jitter. Customize retry behavior through environment variables:

| Environment Variable | Default | Description |
|---|---|---|
| `VAMS_CLI_MAX_RETRY_ATTEMPTS` | `5` | Maximum retry attempts per request |
| `VAMS_CLI_INITIAL_RETRY_DELAY` | `1.0` | Initial delay in seconds before first retry |
| `VAMS_CLI_MAX_RETRY_DELAY` | `60.0` | Maximum delay in seconds between retries |

For bulk operations that may trigger throttling, increase retry limits:

```bash
export VAMS_CLI_MAX_RETRY_ATTEMPTS=10
export VAMS_CLI_INITIAL_RETRY_DELAY=2.0
vamscli assets list --auto-paginate --json-output
```

## Pagination

### Auto-Pagination

Use `--auto-paginate` to automatically fetch all pages of results. This is the recommended approach for scripts that need complete data sets.

```bash
vamscli assets list -d my-database --auto-paginate --json-output
```

By default, auto-pagination fetches up to 10,000 items. Override this limit with `--max-items`:

```bash
vamscli assets list -d my-database --auto-paginate --max-items 50000 --json-output
```

Control page sizes to manage memory usage and API throttling:

```bash
vamscli file list -d my-db -a my-asset --auto-paginate --page-size 500 --json-output
```

### Manual Pagination

For finer control, use `--starting-token` to manually advance through pages:

```bash
# First page
RESULT=$(vamscli assets list -d my-db --page-size 100 --json-output)
echo "$RESULT" | jq '.Items | length'

# Get next token
NEXT_TOKEN=$(echo "$RESULT" | jq -r '.NextToken // empty')

# Subsequent pages
if [ -n "$NEXT_TOKEN" ]; then
    vamscli assets list -d my-db --page-size 100 --starting-token "$NEXT_TOKEN" --json-output
fi
```

:::info[Pagination Constraints]
`--auto-paginate` and `--starting-token` are mutually exclusive. Use one or the other, not both.
:::


## JSON Input Files

Several commands accept complex input through `--json-input`. This parameter accepts either an inline JSON string or a file path.

### Inline JSON

```bash
vamscli assets create -d my-db \
    --json-input '{"assetName":"test","description":"Test asset","isDistributable":true}'
```

### File Reference

For the `metadata` commands, prefix file paths with `@`:

```bash
vamscli metadata asset update -d my-db -a my-asset --json-input @metadata.json
```

For other commands, provide the file path directly:

```bash
vamscli assets create -d my-db --json-input create-asset.json
```

:::tip[Complex Operations]
JSON input is particularly useful for:

- Creating assets with tags and complex metadata
- Bulk metadata updates with many fields
- Importing permission constraint templates
- Specifying exact file versions for asset version creation
:::


## Error Handling

### Exit Codes

VamsCLI uses standard exit codes:

| Exit Code | Meaning |
|---|---|
| `0` | Command completed successfully |
| `1` | Command failed (business logic error, invalid input, API error) |
| `2` | Invalid command usage (missing required options, unknown commands) |

### JSON Error Format

When `--json-output` is enabled, errors are output as JSON to stderr:

```json
{
  "error_type": "Asset Not Found",
  "message": "Asset 'my-asset' not found in database 'my-db'",
  "helpful_message": "Use 'vamscli assets list -d my-db' to see available assets."
}
```

### Error Handling in Scripts

Use exit codes to handle errors in bash scripts:

```bash
#!/bin/bash
set -e  # Exit on any error

# Attempt to get an asset, handle not-found gracefully
if RESULT=$(vamscli assets get my-asset -d my-db --json-output 2>/dev/null); then
    echo "Asset found: $(echo "$RESULT" | jq -r '.assetName')"
else
    echo "Asset not found, creating..."
    vamscli assets create -d my-db \
        --name "my-asset" \
        --description "Auto-created" \
        --distributable \
        --json-output
fi
```

### Handling Authentication Expiry

For long-running scripts, check authentication status and refresh tokens as needed:

```bash
#!/bin/bash

# Check if authenticated
AUTH_STATUS=$(vamscli auth status --json-output 2>/dev/null || echo '{"authenticated":false}')
IS_AUTH=$(echo "$AUTH_STATUS" | jq -r '.authenticated')

if [ "$IS_AUTH" != "true" ]; then
    echo "Re-authenticating..."
    vamscli auth login -u "$VAMS_USER" -p "$VAMS_PASSWORD" --json-output
fi

# Alternatively, attempt token refresh
vamscli auth refresh --json-output 2>/dev/null || \
    vamscli auth login -u "$VAMS_USER" -p "$VAMS_PASSWORD" --json-output
```

## Verbose Mode

Enable `--verbose` for debugging scripts and troubleshooting API issues. Verbose mode outputs detailed information including:

- API request URLs and headers
- Response status codes and timing
- Token validation details
- Retry attempt logs

```bash
vamscli --verbose assets list -d my-database
```

:::tip[Combining Verbose and JSON Output]
Verbose log messages are written to a rotating log file and do not interfere with `--json-output`. You can safely use both flags together in scripts where you need clean JSON output but also want log files for debugging.
:::


## Additional Resources

- [Getting Started](getting-started.md) -- First-time setup and authentication
- [Installation and Profile Management](installation.md) -- Profiles and configuration files
- [Command Reference](command-reference.md) -- Complete reference for all commands
