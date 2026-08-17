# Tags

Tags provide a flexible classification system for organizing and filtering assets. The VAMS tagging system consists of two components: **tag types** and **tags**.

:::note[Asset tags versus template tags]
The tags on this page classify assets. They are unrelated to a pipeline configuration template's `\{\{tagName\}\}` placeholders, which supply parameters to a processing run — see [System template tags](../api/pipelines.md#system-template-tags).
:::

## Tag types

A tag type defines a named category that groups related tags together. Tag types provide organizational structure and can enforce tagging requirements on assets.

| Field         | Description                                                                                                                                          |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tagTypeName` | Name for the tag type (for example, `Project Phase`, `Classification`, `Region`). Unique per database.                                              |
| `description` | Description of the tag type's purpose. Required when creating or updating a tag type.                                                                |
| `required`    | When set to `"True"`, every asset must have at least one tag from this tag type, as long as the tag type has tags. Stored as the string `"True"` or `"False"` (defaults to `"False"`). |
| `databaseId`  | Scope of the tag type. `GLOBAL` (or omitted) makes it available in every database; a database ID scopes it to that database. Fixed at creation.       |

:::tip[Required tag types]
Marking a tag type as required is useful for enforcing organizational standards. For example, a `Classification` tag type marked as required ensures that every asset is classified before it can be considered complete.
:::

A required tag type applies only while it has tags. A required tag type with no tags is not enforced: nothing exists that could satisfy it, so requiring it would make every asset in scope impossible to create or edit. This holds for global and database-specific tag types alike, and a required tag type stops being enforced again if its last tag is deleted. Asset creation and asset updates apply the same rule, so a tag type marked required before its tags are defined does not block work in the meantime.

Enforcement is also scoped like the tags themselves. An asset is constrained by the required tag types in its own database plus `GLOBAL`, evaluated against the tags available in those same scopes — another database's required tag type never applies.

## Tags

A tag is an individual label associated with a tag type. Tags are assigned to assets and appear as filterable attributes in search and listing views.

| Field         | Description                                                                      |
| ------------- | -------------------------------------------------------------------------------- |
| `tagName`     | The display name of the tag (for example, `Design`, `Construction`, `As-Built`). Unique per database. |
| `description` | Description of the tag's purpose. Required when creating or updating a tag.      |
| `tagTypeName` | The tag type this tag belongs to. Must be a tag type in the same scope as the tag. |
| `databaseId`  | Scope of the tag. `GLOBAL` (or omitted) makes it available in every database; a database ID scopes it to that database. Fixed at creation. |

## Global and database-specific tags

Every tag and tag type has a scope, set by its `databaseId`. A **global** tag (scope `GLOBAL`, the default) is available in every database. A **database-specific** tag is scoped to a single database and is visible only within it. This lets each database define its own vocabulary — a `manufacturing` database and a `media` database can each have a `Status` tag with a different meaning.

Tag and tag type names are unique within a database, not across the whole deployment. The same name can exist independently in different databases. Across scopes the rule is asymmetric: a **global** entry can be created for a name a database already uses, and the response carries a warning that both entries will appear on asset forms until the database-specific one is removed. The reverse is rejected — a database-specific tag or tag type cannot be created when a global entry of that name exists, because a database may not shadow the shared vocabulary. A database's identifier cannot be the reserved value `GLOBAL`.

A tag's tag type must be in the same scope as the tag itself. A global tag uses a global tag type; a database-specific tag uses a tag type scoped to that same database, and cannot use a global tag type. Each database therefore describes its own tags with its own categories, and a database-scoped tag never depends on a shared category that another database could change.

An asset belongs to exactly one database, and it resolves each of its tag names within that database plus `GLOBAL`. An asset can therefore carry its own database's tags and global tags, but never another database's tags.

While a name exists in both scopes, an asset in that database sees both entries — the asset stores the bare name, so the name satisfies both tag types and both appear in the tag picker, distinguished by their scope labels. This is the state the creation warning refers to; removing the database-specific entry returns the name to a single meaning.

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

An asset carries a list of tags, so the `tags` field is evaluated with the membership operators `is_one_of` and `is_not_one_of` rather than the pattern-matching operators used on single-valued fields. For example, a deny constraint with `tags is_one_of "locked"` prevents modification of any asset whose tag list includes the value `locked`; supply several values to match any of them.

:::note
The pattern-matching operators (`equals`, `contains`, `does_not_contain`, `starts_with`, `ends_with`) compare one string, so they cannot be applied to a tag list. A constraint that pairs them with the `tags` field is rejected when it is saved, with a message naming the two operators to use instead.
:::

:::note[Scoping tag administration]
Global tags and tag types are shared across every database, so it is recommended to grant database-scoped roles read-only access to global tags while restricting their write access to their own database with a `databaseId` constraint. See the [Permissions Model](permissions-model.md) for recommended constraint patterns.
:::

## Tag and tag type permissions

Access to tags and tag types is controlled through dedicated object types in the permissions model.

| Object Type | Constraint Field       | Description                                                                                     |
| ----------- | ---------------------- | ----------------------------------------------------------------------------------------------- |
| `tag`       | `tagName`, `databaseId` | Controls who can create, read, update, and delete individual tags. `databaseId` scopes administration to GLOBAL or a specific database. |
| `tagType`   | `tagTypeName`, `databaseId` | Controls who can create, read, update, and delete tag type categories. `databaseId` scopes administration to GLOBAL or a specific database. |

## Related topics

-   [Assets](assets.md) -- the entities that tags are attached to
-   [Permissions Model](permissions-model.md) -- tag-based access control and deny overlay patterns
-   [Tags User Guide](../user-guide/tags.md) -- step-by-step tag management instructions
