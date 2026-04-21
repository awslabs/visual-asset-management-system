# Tags

Tags provide a flexible classification system for organizing and filtering assets. The VAMS tagging system consists of two components: **tag types** and **tags**.

## Tag types

A tag type defines a named category that groups related tags together. Tag types provide organizational structure and can enforce tagging requirements on assets.

| Field         | Description                                                                              |
| ------------- | ---------------------------------------------------------------------------------------- |
| `tagTypeName` | Unique name for the tag type (for example, `Project Phase`, `Classification`, `Region`). |
| `required`    | When set to `true`, every asset must have at least one tag from this tag type.           |

:::tip[Required tag types]
Marking a tag type as required is useful for enforcing organizational standards. For example, a `Classification` tag type marked as required ensures that every asset is classified before it can be considered complete.
:::

## Tags

A tag is an individual label associated with a tag type. Tags are assigned to assets and appear as filterable attributes in search and listing views.

| Field         | Description                                                                      |
| ------------- | -------------------------------------------------------------------------------- |
| `tagName`     | The display name of the tag (for example, `Design`, `Construction`, `As-Built`). |
| `tagTypeName` | The tag type this tag belongs to.                                                |

## How tags are assigned

Tags are assigned to assets at creation time or through subsequent updates. An asset can have multiple tags from different tag types. Tags are stored as a string array on the asset record and are indexed in Amazon OpenSearch Service for search.

```json
{
    "assetName": "Building-A-Scan",
    "databaseId": "construction-db",
    "tags": ["Design", "Phase-1", "Exterior"]
}
```

## Tag-based filtering

Tags are indexed in Amazon OpenSearch Service alongside other asset metadata. Users can filter assets by tag values in the search interface, enabling quick discovery of assets that share common characteristics.

## Tags and permissions

Tags are a constraint field in the VAMS [permissions model](permissions-model.md). Administrators can create permission rules that reference tags to control access at a granular level.

**Tag-based access control examples:**

-   Grant read-only access to assets tagged with `published`.
-   Deny modification of assets tagged with `locked` or `approved`.
-   Restrict a team to only assets tagged with their project name.

The `tags` field is evaluated using string matching operators (`contains`, `does_not_contain`, `equals`). For example, a deny constraint with `tags contains "locked"` prevents modification of any asset whose tag list includes the value `locked`.

:::warning[Tags are shared across databases]
Tags and tag types are global resources -- they are not scoped to individual databases. When configuring permissions for database-scoped roles, it is recommended to grant read-only access to tags and tag types to prevent users from modifying shared resources. See the [Permissions Model](permissions-model.md) for recommended constraint patterns.
:::

## Tag and tag type permissions

Access to tags and tag types is controlled through dedicated object types in the permissions model.

| Object Type | Constraint Field | Description                                                            |
| ----------- | ---------------- | ---------------------------------------------------------------------- |
| `tag`       | `tagName`        | Controls who can create, read, update, and delete individual tags.     |
| `tagType`   | `tagTypeName`    | Controls who can create, read, update, and delete tag type categories. |

## Related topics

-   [Assets](assets.md) -- the entities that tags are attached to
-   [Permissions Model](permissions-model.md) -- tag-based access control and deny overlay patterns
-   [Tags User Guide](../user-guide/tags.md) -- step-by-step tag management instructions
