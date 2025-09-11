# Tag Management Commands

This document covers VamsCLI tag and tag type management commands for organizing and categorizing assets in VAMS.

## Tag Management Commands

VamsCLI provides comprehensive tag and tag type management capabilities for organizing and categorizing assets in VAMS.

### `vamscli tag create`

Create a new tag in VAMS.

**Required Options:**

-   `--tag-name`: Tag name (required)
-   `--description`: Tag description (required)
-   `--tag-type-name`: Tag type name (required)

**Input/Output Options:**

-   `--json-input`: JSON input file path or JSON string with tag data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create a single tag
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"

# Create multiple tags with JSON input
vamscli tag create --json-input '{"tags":[{"tagName":"urgent","description":"Urgent","tagTypeName":"priority"},{"tagName":"low","description":"Low priority","tagTypeName":"priority"}]}'

# Create from JSON file
vamscli tag create --json-input @tags.json --json-output
```

**JSON Input Format:**

```json
{
    "tags": [
        {
            "tagName": "urgent",
            "description": "Urgent priority items",
            "tagTypeName": "priority"
        },
        {
            "tagName": "low",
            "description": "Low priority items",
            "tagTypeName": "priority"
        }
    ]
}
```

### `vamscli tag update`

Update an existing tag in VAMS.

**Required Options:**

-   `--tag-name`: Tag name to update (required)

**Options:**

-   `--description`: New tag description
-   `--tag-type-name`: New tag type name
-   `--json-input`: JSON input file path or JSON string with tag data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update tag description
vamscli tag update --tag-name "urgent" --description "Updated description"

# Update tag type
vamscli tag update --tag-name "urgent" --tag-type-name "new-priority"

# Update multiple fields
vamscli tag update --tag-name "urgent" --description "Updated" --tag-type-name "priority"

# Update with JSON input
vamscli tag update --json-input '{"tags":[{"tagName":"urgent","description":"Updated","tagTypeName":"priority"}]}'
```

### `vamscli tag delete`

Delete a tag from VAMS.

**Arguments:**

-   `tag_name`: Tag name to delete (positional argument, required)

**Options:**

-   `--confirm`: Confirm tag deletion (required for safety)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete a tag (requires confirmation)
vamscli tag delete urgent --confirm

# Delete with JSON output
vamscli tag delete urgent --confirm --json-output
```

**Safety Features:**

-   Requires explicit `--confirm` flag
-   Clear warnings about deletion

### `vamscli tag list`

List all tags in VAMS.

**Options:**

-   `--tag-type`: Filter tags by tag type
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List all tags
vamscli tag list

# Filter by tag type
vamscli tag list --tag-type priority

# JSON output for automation
vamscli tag list --json-output
```

**Output Features:**

-   Table format with tag name, type, and description
-   Required tag types marked with [R] indicator
-   Filtering by tag type name
-   Pagination support for large tag lists

## Tag Type Management Commands

### `vamscli tag-type create`

Create a new tag type in VAMS.

**Required Options:**

-   `--tag-type-name`: Tag type name (required)
-   `--description`: Tag type description (required)

**Options:**

-   `--required`: Mark this tag type as required
-   `--json-input`: JSON input file path or JSON string with tag type data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create a basic tag type
vamscli tag-type create --tag-type-name "priority" --description "Priority levels"

# Create a required tag type
vamscli tag-type create --tag-type-name "status" --description "Processing status" --required

# Create multiple tag types with JSON input
vamscli tag-type create --json-input '{"tagTypes":[{"tagTypeName":"priority","description":"Priority levels","required":"True"}]}'

# Create from JSON file
vamscli tag-type create --json-input @tag-types.json --json-output
```

**JSON Input Format:**

```json
{
    "tagTypes": [
        {
            "tagTypeName": "priority",
            "description": "Priority classification levels",
            "required": "True"
        },
        {
            "tagTypeName": "category",
            "description": "Asset categories",
            "required": "False"
        }
    ]
}
```

### `vamscli tag-type update`

Update an existing tag type in VAMS.

**Required Options:**

-   `--tag-type-name`: Tag type name to update (required)

**Options:**

-   `--description`: New tag type description
-   `--required/--not-required`: Update required flag
-   `--json-input`: JSON input file path or JSON string with tag type data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update description
vamscli tag-type update --tag-type-name "priority" --description "Updated description"

# Mark as required
vamscli tag-type update --tag-type-name "priority" --required

# Mark as not required
vamscli tag-type update --tag-type-name "priority" --not-required

# Update with JSON input
vamscli tag-type update --json-input '{"tagTypes":[{"tagTypeName":"priority","description":"Updated","required":"True"}]}'
```

### `vamscli tag-type delete`

Delete a tag type from VAMS.

**Arguments:**

-   `tag_type_name`: Tag type name to delete (positional argument, required)

**Options:**

-   `--confirm`: Confirm tag type deletion (required for safety)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete a tag type (requires confirmation)
vamscli tag-type delete priority --confirm

# Delete with JSON output
vamscli tag-type delete priority --confirm --json-output
```

**Safety Features:**

-   Requires explicit `--confirm` flag
-   Cannot delete tag types that are currently in use by tags
-   Clear error messages with guidance

### `vamscli tag-type list`

List all tag types in VAMS.

**Options:**

-   `--show-tags`: Include associated tags in output
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List all tag types (table format)
vamscli tag-type list

# List with associated tags (detailed format)
vamscli tag-type list --show-tags

# JSON output for automation
vamscli tag-type list --json-output
```

**Output Features:**

-   Table format showing name, description, required status, and tag count
-   Detailed format with associated tags when using `--show-tags`
-   Required tag types clearly marked
-   Pagination support for large lists

## Tag Management Features

### Tag Organization

-   **Tag Types**: Organize tags into categories (priority, status, category, etc.)
-   **Required Types**: Mark tag types as required for asset classification
-   **Hierarchical Structure**: Tags belong to tag types for better organization

### Tag Usage

-   **Asset Tagging**: Tags can be applied to assets for categorization
-   **Filtering**: Filter assets and operations by tags
-   **Search**: Use tags to find and organize assets

### Tag Validation

-   **Type Validation**: Tags must reference existing tag types
-   **Name Validation**: Tag and tag type names follow VAMS naming conventions
-   **Dependency Checking**: Cannot delete tag types that are in use

### Batch Operations

-   **Multiple Creation**: Create multiple tags or tag types in single operation
-   **JSON Support**: Use JSON input for complex batch operations
-   **Atomic Operations**: Batch operations are processed atomically

## Tag Management Workflow Examples

### Basic Tag Setup

```bash
# First, create tag types
vamscli tag-type create --tag-type-name "priority" --description "Priority levels" --required
vamscli tag-type create --tag-type-name "category" --description "Asset categories"
vamscli tag-type create --tag-type-name "status" --description "Processing status" --required

# List tag types to verify
vamscli tag-type list --show-tags

# Create tags for each type
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
vamscli tag create --tag-name "high" --description "High priority" --tag-type-name "priority"
vamscli tag create --tag-name "low" --description "Low priority" --tag-type-name "priority"

vamscli tag create --tag-name "model" --description "3D models" --tag-type-name "category"
vamscli tag create --tag-name "texture" --description "Texture files" --tag-type-name "category"

vamscli tag create --tag-name "processing" --description "Currently processing" --tag-type-name "status"
vamscli tag create --tag-name "complete" --description "Processing complete" --tag-type-name "status"

# List all tags
vamscli tag list
```

### Batch Tag Creation

```bash
# Create multiple tag types at once
vamscli tag-type create --json-input '{
  "tagTypes": [
    {"tagTypeName": "priority", "description": "Priority levels", "required": "True"},
    {"tagTypeName": "category", "description": "Asset categories", "required": "False"},
    {"tagTypeName": "status", "description": "Processing status", "required": "True"}
  ]
}'

# Create multiple tags at once
vamscli tag create --json-input '{
  "tags": [
    {"tagName": "urgent", "description": "Urgent priority", "tagTypeName": "priority"},
    {"tagName": "high", "description": "High priority", "tagTypeName": "priority"},
    {"tagName": "model", "description": "3D models", "tagTypeName": "category"},
    {"tagName": "texture", "description": "Texture files", "tagTypeName": "category"}
  ]
}'
```

### Tag Management and Cleanup

```bash
# List tags filtered by type
vamscli tag list --tag-type priority
vamscli tag list --tag-type category

# Update tag descriptions
vamscli tag update --tag-name "urgent" --description "Critical priority items"

# Update tag type requirements
vamscli tag-type update --tag-type-name "category" --required

# Delete unused tags
vamscli tag delete unused-tag --confirm

# Try to delete tag type (will fail if in use)
vamscli tag-type delete old-type --confirm

# List tag types to see which have associated tags
vamscli tag-type list --show-tags
```

### Automation with JSON Files

```bash
# Create tag-types.json
cat > tag-types.json << EOF
{
  "tagTypes": [
    {"tagTypeName": "environment", "description": "Environment classification", "required": "True"},
    {"tagTypeName": "quality", "description": "Quality levels", "required": "False"}
  ]
}
EOF

# Create tags.json
cat > tags.json << EOF
{
  "tags": [
    {"tagName": "production", "description": "Production environment", "tagTypeName": "environment"},
    {"tagName": "staging", "description": "Staging environment", "tagTypeName": "environment"},
    {"tagName": "high-quality", "description": "High quality assets", "tagTypeName": "quality"},
    {"tagName": "draft", "description": "Draft quality assets", "tagTypeName": "quality"}
  ]
}
EOF

# Create tag types and tags
vamscli tag-type create --json-input @tag-types.json --json-output
vamscli tag create --json-input @tags.json --json-output

# Verify creation
vamscli tag-type list --show-tags --json-output
```

### Comprehensive Tag System Setup

```bash
# Step 1: Create a comprehensive tag type system
vamscli tag-type create --tag-type-name "priority" --description "Task priority levels" --required
vamscli tag-type create --tag-type-name "category" --description "Asset categories" --required
vamscli tag-type create --tag-type-name "status" --description "Processing status" --required
vamscli tag-type create --tag-type-name "environment" --description "Environment classification"
vamscli tag-type create --tag-type-name "quality" --description "Quality assessment"
vamscli tag-type create --tag-type-name "format" --description "File format types"

# Step 2: Create priority tags
vamscli tag create --tag-name "critical" --description "Critical priority - immediate attention" --tag-type-name "priority"
vamscli tag create --tag-name "high" --description "High priority - important" --tag-type-name "priority"
vamscli tag create --tag-name "medium" --description "Medium priority - normal" --tag-type-name "priority"
vamscli tag create --tag-name "low" --description "Low priority - when time permits" --tag-type-name "priority"

# Step 3: Create category tags
vamscli tag create --tag-name "3d-model" --description "3D model files" --tag-type-name "category"
vamscli tag create --tag-name "texture" --description "Texture and material files" --tag-type-name "category"
vamscli tag create --tag-name "animation" --description "Animation files" --tag-type-name "category"
vamscli tag create --tag-name "audio" --description "Audio files" --tag-type-name "category"
vamscli tag create --tag-name "documentation" --description "Documentation files" --tag-type-name "category"

# Step 4: Create status tags
vamscli tag create --tag-name "draft" --description "Draft - work in progress" --tag-type-name "status"
vamscli tag create --tag-name "review" --description "Under review" --tag-type-name "status"
vamscli tag create --tag-name "approved" --description "Approved for use" --tag-type-name "status"
vamscli tag create --tag-name "archived" --description "Archived - no longer active" --tag-type-name "status"

# Step 5: Create environment tags
vamscli tag create --tag-name "production" --description "Production environment" --tag-type-name "environment"
vamscli tag create --tag-name "staging" --description "Staging environment" --tag-type-name "environment"
vamscli tag create --tag-name "development" --description "Development environment" --tag-type-name "environment"
vamscli tag create --tag-name "testing" --description "Testing environment" --tag-type-name "environment"

# Step 6: Verify the complete tag system
vamscli tag-type list --show-tags
```

### Tag System Maintenance

```bash
# Regular maintenance: review all tag types and their usage
vamscli tag-type list --show-tags

# Check specific tag type usage
vamscli tag list --tag-type priority
vamscli tag list --tag-type category

# Update tag descriptions for clarity
vamscli tag update --tag-name "critical" --description "Critical priority - requires immediate attention within 24 hours"
vamscli tag update --tag-name "3d-model" --description "3D model files including meshes, geometry, and related assets"

# Update tag type requirements based on usage patterns
vamscli tag-type update --tag-type-name "quality" --required  # Make quality required
vamscli tag-type update --tag-type-name "format" --not-required  # Make format optional

# Clean up unused tags
vamscli tag delete obsolete-tag --confirm
vamscli tag delete old-category --confirm

# Verify changes
vamscli tag-type list
vamscli tag list
```

### Tag Migration and Reorganization

```bash
# Step 1: Document current tag system
vamscli tag-type list --show-tags --json-output > current-tag-system.json
vamscli tag list --json-output > all-tags.json

# Step 2: Create new tag structure
vamscli tag-type create --tag-type-name "new-category" --description "Reorganized categories"

# Step 3: Create new tags under new structure
vamscli tag create --tag-name "models-3d" --description "3D model assets" --tag-type-name "new-category"
vamscli tag create --tag-name "materials" --description "Material and texture assets" --tag-type-name "new-category"

# Step 4: Update existing tags to new structure (if needed)
vamscli tag update --tag-name "existing-tag" --tag-type-name "new-category"

# Step 5: Clean up old structure (after ensuring no assets use old tags)
vamscli tag delete old-tag --confirm
vamscli tag-type delete old-type --confirm

# Step 6: Verify new structure
vamscli tag-type list --show-tags
```

## Tag Management Best Practices

### Tag Type Design

-   Create tag types before creating tags
-   Use descriptive names that clearly indicate the categorization purpose
-   Mark tag types as required when they're essential for asset classification
-   Keep tag type names consistent and follow naming conventions

### Tag Creation Strategy

-   Create comprehensive tag sets for each tag type
-   Use clear, descriptive tag names and descriptions
-   Avoid duplicate or overlapping tag meanings
-   Plan tag hierarchies before implementation

### Tag Maintenance

-   Regularly review tag usage and effectiveness
-   Update tag descriptions to maintain clarity
-   Remove unused tags to keep the system clean
-   Monitor tag type requirements based on usage patterns

### Automation and Consistency

-   Use JSON input for batch operations when setting up new environments
-   Create scripts for consistent tag system deployment
-   Document tag system design and usage guidelines
-   Implement tag governance policies for team environments

### Integration with Assets

-   Plan tag systems before creating assets
-   Use required tag types to ensure consistent asset classification
-   Train users on proper tag usage and conventions
-   Monitor tag usage patterns to optimize the system
