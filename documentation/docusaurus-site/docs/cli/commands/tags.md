---
sidebar_label: Tags
title: Tag Commands
---

# Tag and Tag Type Commands

Manage tags and tag types for organizing and categorizing assets in VAMS. Tags belong to tag types, which define categories for classification.

---

## tag list

List all tags, optionally filtered by tag type.

```bash
vamscli tag list [--tag-type <TYPE>] [--json-output]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `--tag-type` | TEXT | No | Filter tags by tag type name |
| `--json-output` | Flag | No | Output raw JSON response |

---

## tag create

Create a new tag or multiple tags.

```bash
vamscli tag create [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `--tag-name` | TEXT | Conditional | Tag name (required unless using `--json-input`) |
| `--description` | TEXT | Conditional | Tag description |
| `--tag-type-name` | TEXT | Conditional | Tag type name |
| `--json-input` | TEXT | No | JSON input for batch creation |
| `--json-output` | Flag | No | Output raw JSON response |

### JSON input format (batch creation)

```json
{
    "tags": [
        {"tagName": "urgent", "description": "Urgent priority", "tagTypeName": "priority"},
        {"tagName": "low", "description": "Low priority", "tagTypeName": "priority"}
    ]
}
```

```bash
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
vamscli tag create --json-input @tags.json --json-output
```

---

## tag update

Update an existing tag's description or tag type.

```bash
vamscli tag update --tag-name "urgent" --description "Updated description"
vamscli tag update --tag-name "urgent" --tag-type-name "new-priority"
```

---

## tag delete

Delete a tag. Requires the `--confirm` flag.

```bash
vamscli tag delete urgent --confirm
```

---

## tag-type list

List all tag types, optionally including associated tags.

```bash
vamscli tag-type list [--show-tags] [--json-output]
```

---

## tag-type create

Create a new tag type or multiple tag types.

```bash
vamscli tag-type create [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `--tag-type-name` | TEXT | Conditional | Tag type name |
| `--description` | TEXT | Conditional | Tag type description |
| `--required` | Flag | No | Mark as required for asset classification |
| `--json-input` | TEXT | No | JSON input for batch creation |
| `--json-output` | Flag | No | Output raw JSON response |

### JSON input format

```json
{
    "tagTypes": [
        {"tagTypeName": "priority", "description": "Priority levels", "required": "True"},
        {"tagTypeName": "category", "description": "Asset categories", "required": "False"}
    ]
}
```

---

## tag-type update

Update a tag type's description or required status.

```bash
vamscli tag-type update --tag-type-name "priority" --description "Updated description"
vamscli tag-type update --tag-type-name "priority" --required
vamscli tag-type update --tag-type-name "priority" --not-required
```

---

## tag-type delete

Delete a tag type. Cannot delete tag types that are currently in use by tags.

```bash
vamscli tag-type delete priority --confirm
```

---

## Workflow Example

```bash
# Create tag types first
vamscli tag-type create --tag-type-name "priority" --description "Priority levels" --required
vamscli tag-type create --tag-type-name "category" --description "Asset categories"

# Create tags for each type
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
vamscli tag create --tag-name "model" --description "3D models" --tag-type-name "category"

# Verify
vamscli tag-type list --show-tags
vamscli tag list --tag-type priority
```

## Related Pages

- [Asset Commands](assets.md)
- [Search Commands](search.md)
