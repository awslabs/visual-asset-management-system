---
sidebar_label: Tags
title: Tag Commands
---

# Tag and Tag Type Commands

Manage tags and tag types for organizing and categorizing assets in VAMS. Tags belong to tag types, which define classification categories. Tag types must exist before tags can reference them.

:::note
Both `tag` and `tag-type` create and update commands accept `--json-input` for batch operations. The value is either a JSON string or a path to a `.json` file (no `@` prefix). Batch operations are processed in a single request.
:::

---

## tag create

Create one or more tags in VAMS.

```bash
vamscli tag create [OPTIONS]
```

| Option            | Type | Required    | Description                                              |
| ----------------- | ---- | ----------- | -------------------------------------------------------- |
| `--tag-name`      | TEXT | Conditional | Tag name (required unless using `--json-input`)          |
| `--description`   | TEXT | Conditional | Tag description (required unless using `--json-input`)   |
| `--tag-type-name` | TEXT | Conditional | Tag type name (required unless using `--json-input`)     |
| `--json-input`    | TEXT | No          | JSON string or path to a JSON file with tag data (batch) |
| `--json-output`   | Flag | No          | Output raw JSON response                                 |

When `--json-input` is not used, `--tag-name`, `--description`, and `--tag-type-name` are all required. The referenced tag type must already exist.

The JSON input is a single flat tag object:

```json
{
    "tagName": "urgent",
    "description": "Urgent priority",
    "tagTypeName": "priority"
}
```

```bash
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
vamscli tag create --json-input '{"tagName":"urgent","description":"Urgent","tagTypeName":"priority"}'
vamscli tag create --json-input tags.json --json-output
```

---

## tag update

Update an existing tag's description and/or tag type.

```bash
vamscli tag update [OPTIONS]
```

| Option            | Type | Required    | Description                                               |
| ----------------- | ---- | ----------- | --------------------------------------------------------- |
| `--tag-name`      | TEXT | Conditional | Tag name to update (required unless using `--json-input`) |
| `--description`   | TEXT | No          | New tag description                                       |
| `--tag-type-name` | TEXT | No          | New tag type name                                         |
| `--json-input`    | TEXT | No          | JSON string or path to a JSON file with tag data (batch)  |
| `--json-output`   | Flag | No          | Output raw JSON response                                  |

When not using `--json-input`, `--tag-name` is required and at least one of `--description` or `--tag-type-name` must be provided. The command retrieves the current tag first and preserves any field not supplied.

```bash
vamscli tag update --tag-name "urgent" --description "Updated description"
vamscli tag update --tag-name "urgent" --tag-type-name "new-priority"
vamscli tag update --json-input '{"tagName":"urgent","description":"Updated","tagTypeName":"priority"}'
```

---

## tag delete

Permanently delete a tag from VAMS.

```bash
vamscli tag delete <TAG_NAME> [OPTIONS]
```

| Option          | Type | Required | Description                     |
| --------------- | ---- | -------- | ------------------------------- |
| `TAG_NAME`      | TEXT | Yes      | Tag name to delete (positional) |
| `--confirm`     | Flag | Yes      | Confirm deletion                |
| `--json-output` | Flag | No       | Output raw JSON response        |

:::warning[Confirmation required]
The `--confirm` flag is required to prevent accidental deletions. Without it, the command exits with an error.
:::

```bash
vamscli tag delete urgent --confirm
vamscli tag delete urgent --confirm --json-output
```

---

## tag list

List all tags, optionally filtered by tag type.

```bash
vamscli tag list [OPTIONS]
```

| Option          | Type | Required | Description                                     |
| --------------- | ---- | -------- | ----------------------------------------------- |
| `--tag-type`    | TEXT | No       | Filter tags by tag type name (case-insensitive) |
| `--json-output` | Flag | No       | Output raw JSON response                        |

The default output is a table of tag name, tag type, and description. Tags belonging to a required tag type are shown with an `[R]` indicator on the tag type. When more results are available, the output notes that additional tags can be retrieved through pagination.

```bash
vamscli tag list
vamscli tag list --tag-type priority
vamscli tag list --json-output
```

---

## tag-type create

Create one or more tag types in VAMS.

```bash
vamscli tag-type create [OPTIONS]
```

| Option            | Type | Required    | Description                                                 |
| ----------------- | ---- | ----------- | ----------------------------------------------------------- |
| `--tag-type-name` | TEXT | Conditional | Tag type name (required unless using `--json-input`)        |
| `--description`   | TEXT | Conditional | Tag type description (required unless using `--json-input`) |
| `--required`      | Flag | No          | Mark this tag type as required for asset classification     |
| `--json-input`    | TEXT | No          | JSON string or path to a JSON file with tag type data       |
| `--json-output`   | Flag | No          | Output raw JSON response                                    |

When `--json-input` is not used, `--tag-type-name` and `--description` are both required. When `--json-input` is supplied, it provides the tag type data directly and the individual options are not required.

The JSON input is a single flat tag type object, where the `required` field is the string `"True"` or `"False"`:

```json
{
    "tagTypeName": "priority",
    "description": "Priority levels",
    "required": "True"
}
```

```bash
vamscli tag-type create --tag-type-name "priority" --description "Priority levels"
vamscli tag-type create --tag-type-name "status" --description "Processing status" --required
vamscli tag-type create --json-input '{"tagTypeName":"priority","description":"Priority levels","required":"True"}'
vamscli tag-type create --json-input tag-types.json --json-output
```

---

## tag-type update

Update an existing tag type's description and/or required flag.

```bash
vamscli tag-type update [OPTIONS]
```

| Option                          | Type | Required    | Description                                                    |
| ------------------------------- | ---- | ----------- | -------------------------------------------------------------- |
| `--tag-type-name`               | TEXT | Conditional | Tag type name to update (required unless using `--json-input`) |
| `--description`                 | TEXT | No          | New tag type description                                       |
| `--required` / `--not-required` | Flag | No          | Update the required flag                                       |
| `--json-input`                  | TEXT | No          | JSON string or path to a JSON file with tag type data          |
| `--json-output`                 | Flag | No          | Output raw JSON response                                       |

When not using `--json-input`, `--tag-type-name` is required and at least one of `--description` or `--required` / `--not-required` must be provided. The command retrieves the current tag type first and preserves any field not supplied.

```bash
vamscli tag-type update --tag-type-name "priority" --description "Updated description"
vamscli tag-type update --tag-type-name "priority" --required
vamscli tag-type update --tag-type-name "priority" --not-required
vamscli tag-type update --json-input '{"tagTypeName":"priority","description":"Updated","required":"True"}'
```

---

## tag-type delete

Permanently delete a tag type from VAMS.

```bash
vamscli tag-type delete <TAG_TYPE_NAME> [OPTIONS]
```

| Option          | Type | Required | Description                          |
| --------------- | ---- | -------- | ------------------------------------ |
| `TAG_TYPE_NAME` | TEXT | Yes      | Tag type name to delete (positional) |
| `--confirm`     | Flag | Yes      | Confirm deletion                     |
| `--json-output` | Flag | No       | Output raw JSON response             |

:::warning[Confirmation required]
The `--confirm` flag is required to prevent accidental deletions. A tag type that is currently in use by one or more tags cannot be deleted; delete those tags first.
:::

```bash
vamscli tag-type delete priority --confirm
vamscli tag-type delete priority --confirm --json-output
```

---

## tag-type list

List all tag types, optionally including the tags associated with each type.

```bash
vamscli tag-type list [OPTIONS]
```

| Option          | Type | Required | Description                                |
| --------------- | ---- | -------- | ------------------------------------------ |
| `--show-tags`   | Flag | No       | Include associated tags in a detailed view |
| `--json-output` | Flag | No       | Output raw JSON response                   |

The default output is a table of name, description, required status, and tag count. Adding `--show-tags` switches to a detailed view that lists the tags associated with each tag type. When more results are available, the output notes that additional tag types can be retrieved through pagination.

```bash
vamscli tag-type list
vamscli tag-type list --show-tags
vamscli tag-type list --json-output
```

---

## Workflow Example

Create tag types before the tags that reference them, then verify the result.

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

For repeatable setup, define the structure in JSON files and apply them in batch:

```bash
vamscli tag-type create --json-input tag-types.json --json-output
vamscli tag create --json-input tags.json --json-output
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [Metadata Commands](metadata.md)
-   [Search Commands](search.md)
