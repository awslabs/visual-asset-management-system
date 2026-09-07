# Metadata and Schemas

Metadata in VAMS provides a flexible, typed key-value system for attaching structured information to databases, assets, files, and asset links. **Metadata schemas** define the expected fields, types, and validation rules for metadata, enabling consistent data entry and governance across your organization.

## Entity types that support metadata

Metadata can be attached to four entity types within VAMS. Each entity type is stored separately, but they share a common metadata model.

| Entity Type    | Description                                                                          |
| -------------- | ------------------------------------------------------------------------------------ |
| **Database**   | Organization-level metadata attached to a database container.                        |
| **Asset**      | Metadata attached to an asset within a database. Versioned alongside asset versions. |
| **File**       | Metadata or attributes attached to individual files within an asset.                 |
| **Asset Link** | Metadata attached to a relationship between two assets.                              |

Each entity type has its own set of API operations. See the [Metadata API](../api/metadata.md) reference.

## Metadata items

Each metadata item consists of three components: a key, a value, and a value type.

```json
{
    "metadataKey": "location",
    "metadataValue": "{\"lat\": 47.6062, \"long\": -122.3321, \"alt\": 56.0}",
    "metadataValueType": "lla"
}
```

-   **metadataKey** -- A unique identifier for the metadata field (1-256 characters).
-   **metadataValue** -- The value stored as a string. Complex types such as coordinates and matrices are stored as JSON strings.
-   **metadataValueType** -- The data type that determines validation rules and UI rendering.

## Supported value types

VAMS supports 13 metadata value types, ranging from simple strings to geospatial coordinates and transformation matrices.

| Value Type               | Description                                                                                                         | Example Value                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `string`                 | Single-line text. No additional validation.                                                                         | `"Building A"`                                     |
| `multiline_string`       | Multi-line text. No additional validation.                                                                          | `"Line 1\nLine 2"`                                 |
| `inline_controlled_list` | Value must be one of a predefined set of options defined in the schema.                                             | `"approved"`                                       |
| `number`                 | Numeric value (integer or floating point).                                                                          | `"42.5"`                                           |
| `boolean`                | Boolean value. Must be `"true"` or `"false"`.                                                                       | `"true"`                                           |
| `date`                   | ISO 8601 date/time string.                                                                                          | `"2025-01-15T10:30:00Z"`                           |
| `xyz`                    | 3D coordinate. JSON object with `x`, `y`, `z` numeric keys.                                                         | `{"x": 1.0, "y": 2.0, "z": 3.0}`                   |
| `wxyz`                   | Quaternion rotation. JSON object with `w`, `x`, `y`, `z` numeric keys.                                              | `{"w": 1.0, "x": 0, "y": 0, "z": 0}`               |
| `matrix4x4`              | 4x4 transformation matrix. JSON array of 4 rows, each containing 4 numbers.                                         | `[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]`        |
| `geopoint`               | GeoJSON Point geometry.                                                                                             | `{"type": "Point", "coordinates": [-122.3, 47.6]}` |
| `geojson`                | Any valid GeoJSON geometry or feature.                                                                              | `{"type": "Polygon", "coordinates": [...]}`        |
| `lla`                    | Latitude, longitude, altitude coordinate. JSON object with `lat` (-90 to 90), `long` (-180 to 180), and `alt` keys. | `{"lat": 47.6, "long": -122.3, "alt": 56.0}`       |
| `json`                   | Arbitrary JSON data. Must be valid JSON.                                                                            | `{"custom": "data"}`                               |

:::info[All values stored as strings]
Regardless of type, all metadata values are stored as strings in Amazon DynamoDB. The `metadataValueType` field drives validation on create and update operations and determines how the VAMS web interface renders the value.
:::

## File metadata versus file attributes

VAMS distinguishes between two kinds of data that can be attached to individual files.

| Concept             | Value Types        | Schema Support         | Versioned                       | Use Case                                                                    |
| ------------------- | ------------------ | ---------------------- | ------------------------------- | --------------------------------------------------------------------------- |
| **File metadata**   | All 13 value types | Yes (schema-validated) | Yes (saved with asset versions) | Structured, typed information -- coordinates, measurements, classifications |
| **File attributes** | `string` only      | Yes (schema-validated) | Yes (saved with asset versions) | Simple key-value labels -- processing status, source system identifiers     |

:::warning[Attribute type restriction]
File attributes only support the `string` value type. Attempting to create a file attribute with any other type will return a validation error.
:::

File metadata and file attributes are managed through the same file-metadata operations, with the kind you are working on named in the request. See [File Metadata](../api/metadata.md#file-metadata) in the Metadata API reference.

## Metadata schemas

Metadata schemas define the expected fields, types, and validation rules for metadata on a given entity type within a database. Schemas bring consistency and governance to metadata entry.

### Schema scope

Schemas can be scoped to a specific database or declared as `GLOBAL`.

-   **Database-specific schemas** apply only to entities within that database.
-   **GLOBAL schemas** apply across all databases and are useful for organization-wide standards.

When metadata is retrieved, VAMS aggregates all applicable schemas (both database-specific and GLOBAL) and enriches each metadata item with schema information such as field name, required status, display sequence, and default values.

### Schema entity types

Each schema targets a specific entity type, controlling which kind of metadata it governs.

| Schema Entity Type  | Governs                 |
| ------------------- | ----------------------- |
| `databaseMetadata`  | Database-level metadata |
| `assetMetadata`     | Asset-level metadata    |
| `fileMetadata`      | File-level metadata     |
| `fileAttribute`     | File-level attributes   |
| `assetLinkMetadata` | Asset link metadata     |

### Schema field definitions

Each schema contains an array of field definitions. A field definition specifies:

| Property                    | Type                    | Description                                                                                                   |
| --------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| `metadataFieldKeyName`      | String                  | The metadata key this field governs.                                                                          |
| `metadataFieldValueType`    | MetadataValueType       | The expected value type for this field.                                                                       |
| `required`                  | Boolean                 | Whether this field must be present on the entity.                                                             |
| `sequence`                  | Integer (optional)      | Display order in the UI (lower numbers appear first).                                                         |
| `dependsOnFieldKeyName`     | String array (optional) | Other field keys that this field depends on.                                                                  |
| `controlledListKeys`        | String array (optional) | Allowed values for `inline_controlled_list` fields. Required when the value type is `inline_controlled_list`. |
| `defaultMetadataFieldValue` | String (optional)       | Default value pre-populated when creating new metadata. Validated against the field's value type.             |

### Multi-schema overlay

Multiple schemas can apply to the same entity type within a database. When this happens, VAMS aggregates field definitions across all applicable schemas (including GLOBAL schemas). If two schemas define the same field key with conflicting settings, the metadata response includes a `metadataSchemaMultiFieldConflict` flag to alert users.

The schema name reported on each metadata item uses the format `SchemaName (databaseId)` to clarify which schema defined the field. When multiple schemas define the same field, names are comma-delimited.

### Schema enforcement

:::note[Validation on API operations only]
Schema validation is enforced when metadata is created or updated through the VAMS API. Metadata written directly to Amazon S3 by pipeline containers or imported through bulk operations is not validated against schemas at write time. Schema enrichment is applied when metadata is subsequently read through the API.
:::

Databases can optionally restrict metadata to schema-defined fields only. When this restriction is enabled and at least one schema exists for the entity type, the API rejects metadata keys that are not defined in any applicable schema.

### Required fields apply to existing records

Required-field validation is evaluated against the complete metadata state of an entity, not only against the keys carried in the request. A field marked `required` therefore governs records that were written before the field was defined.

When a field is added with `required: true`, or an existing field is changed from optional to required, VAMS rejects the next metadata create or update on any entity the schema covers that does not already hold a value for that field. The response reports which field is missing, for example `Schema validation failed: Required field 'projectCode' is missing`. A create or `update` operation is validated against the merged existing and incoming state, and a `replace_all` update against the submitted final state, so the rejection occurs even when the request changes only an unrelated key. A schema scoped to `GLOBAL` extends this behavior to entities in every database.

Metadata editing on an affected entity resumes once the requirement is met. Three approaches achieve this:

-   Supply the required field's value in the same request that carries the other metadata changes, for each affected entity.
-   Give the schema field a `defaultMetadataFieldValue`. VAMS applies the default to any entity that has no value for the field, which satisfies the requirement without editing each record individually.
-   Return the schema field to optional by clearing its `required` flag.

:::warning[Marking an existing field required is retroactive]
The VAMS web interface displays a warning when a schema edit changes an existing field from optional to required, naming the affected fields. A schema updated directly through the API returns no such warning and no count of the records that are now missing the field -- validate the change against the metadata already stored in the database before applying it.
:::

## Metadata operations

### Create

Create one or more metadata items on an entity. Each item must include a `metadataKey`, `metadataValue`, and `metadataValueType`. If a schema exists, the value is validated against the schema's type definition.

### Update

Update existing metadata items. VAMS supports two update modes:

| Update Type        | Behavior                                                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `update` (default) | Merge the provided items with existing metadata. Only the specified keys are updated; other keys remain unchanged.                       |
| `replace_all`      | Replace all existing metadata with the provided items. Keys not included in the request are deleted. Limited to 500 items per operation. |

### Delete

Delete metadata items by specifying a list of `metadataKeys` to remove. At least one key must be specified.

### Bulk operations

All create, update, and delete operations accept bulk payloads. A bulk operation reports per-item outcomes rather than failing as a whole: the response carries the total item count, the success and failure counts, the keys that succeeded, and, for each key that failed, the reason it failed. A partial failure is therefore visible and correctable without repeating the items that succeeded. For the response fields, see [Bulk Operation Response Format](../api/metadata.md#bulk-operation-response-format).

## Record limits

Each entity can store up to **500 metadata records**. This limit applies per entity instance (for example, per asset, per file, or per database). The `replace_all` update mode is also capped at 500 items per operation.

## Asset metadata versioning

Asset metadata is saved as part of asset version snapshots. When you create an asset version, the current metadata state is captured. You can retrieve metadata as it existed at any previous version by passing the `assetVersionId` parameter to the metadata GET endpoint.

This versioning also applies to file metadata and file attributes -- the snapshot captures the state of all files and their associated metadata at the time the version was created.

## Metadata in search

VAMS indexes metadata into Amazon OpenSearch Service to enable full-text and filtered search. Metadata and attributes are each stored in one flat key-value object per record, under a field named for the source:

| Field | Source                  | Indexed shape                                          |
| ----- | ----------------------- | ------------------------------------------------------ |
| `MD_` | Asset and file metadata | `"MD_": {"location": ..., "classification": ...}`      |
| `AB_` | File attributes         | `"AB_": {"source_system": ..., "processing_status": ...}` |

Keys are carried into those objects exactly as authored, with no prefix of their own. Keeping metadata and attributes in separate objects prevents key collisions between the two when they share a name, and lets a search target either one. Because each source occupies a single field, a deployment can introduce metadata keys freely without growing the index mapping.

A search request may address a key by its bare name, so `location` and `MD_location` reach the same field. See [Search](../api/search.md) for the full set of accepted spellings.

## CSV import and export

The VAMS web interface supports CSV-based bulk metadata operations. You can export an asset's metadata to CSV for offline editing, then re-import the modified CSV to update metadata in bulk. The CSV format preserves the key, value, and value type for each metadata item.

## Related topics

-   [Databases](databases.md) -- database-level metadata and schema restriction settings
-   [Assets](assets.md) -- asset-level metadata and versioning
-   [Files and Versions](files-and-versions.md) -- file metadata, file attributes, and version snapshots
-   [Permissions Model](permissions-model.md) -- controlling who can read and write metadata
-   [Tags](tags.md) -- tags as an alternative classification mechanism
