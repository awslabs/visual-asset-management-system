# Search and Discovery

VAMS provides a powerful search interface for locating assets and files across all databases. The search system is powered by Amazon OpenSearch Service and supports full-text queries, metadata filtering, and geospatial map visualization.

<!-- Screenshot needed: Full search page showing sidebar filters, search bar, and table results -->

## Search modes

The search page supports two entity types that you can toggle between using the mode selector in the sidebar.

| Entity type | Description | Selection |
|---|---|---|
| **Assets** | Searches across asset records (name, description, tags, type, metadata) | Multi-select enabled for bulk actions |
| **Files** | Searches across individual file records within assets (file path, size, extension, metadata, attributes) | Selection disabled |

:::tip
When you switch between Assets and Files mode, filters that do not apply to the new mode are automatically removed. For example, file extension filters are removed when switching to Assets mode.
:::


## Text search

The search bar at the top of the page performs a general query across all indexed fields. Type any term and press **Enter** or click the search button. The search runs against asset names, descriptions, tags, metadata values, file paths, and other indexed fields simultaneously.

Results include a relevance score. Click the information icon next to any result to see which fields matched and why the result was returned.

<!-- Screenshot needed: Search bar with a query entered and results showing explanation popovers -->

## Filters

The sidebar provides several categories of filters that narrow search results in real time. Filters are applied automatically as you change them.

### Basic filters

| Filter | Applies to | Description |
|---|---|---|
| **Database** | Assets, Files | Restrict results to a specific database. Automatically locked when viewing a database-specific page. |
| **Asset type** | Assets | Filter by the asset type classification (for example, `3D Model`, `Point Cloud`). |
| **Tags** | Assets | Filter by one or more tags assigned to assets. |
| **File extension** | Files | Filter by file extension (for example, `.e57`, `.las`, `.pdf`). |

### Advanced filters

| Filter | Applies to | Description |
|---|---|---|
| **Archived status** | Assets | Include archived assets in results. When enabled, an **Archived** column is automatically added to the table. |
| **Has child assets** | Assets | Filter to assets that have child asset links. |
| **Has parent assets** | Assets | Filter to assets that have parent asset links. |
| **Has related assets** | Assets | Filter to assets that have related asset links. |
| **File size** | Files | Filter files by size range. |
| **Last modified** | Files | Filter files by last modified date. |

### Metadata search

Metadata filters allow you to search by specific metadata key-value pairs attached to assets or files.

1. In the **Metadata Search** panel, click **Add Filter**.
2. Enter the metadata **key** (field name).
3. Select an **operator** (`=`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `exists`).
4. Enter the **value** to match.
5. Repeat to add multiple metadata filters.

You can configure two additional options for metadata search:

- **Search mode**: Choose whether to match against metadata **keys**, **values**, or **both**.
- **Operator**: Choose **AND** (all filters must match) or **OR** (any filter can match).

:::info
Metadata filters are applied server-side through Amazon OpenSearch Service. Only metadata that has been indexed is searchable.
:::


## View modes

VAMS provides multiple ways to view search results. Use the segmented control above the results area to switch between views.

### Table view

The default view displays results in a sortable, paginated table. Key features include:

- **Resizable columns** -- Drag column borders to adjust widths.
- **Sortable columns** -- Click any column header to sort ascending or descending. Sorting is performed server-side.
- **Sticky header** -- Column headers remain visible as you scroll.
- **Dual scroll bars** -- A synchronized scroll bar appears above the table when content overflows horizontally.
- **Clickable links** -- Asset names link to the asset detail page. Database names link to the database asset listing. In Files mode, file paths link to the asset detail page and navigate directly to that file.

**Asset columns include**: Preview, Asset Name, Database, Type, Tags, Description, Created Date, Created By, Version, and Archived status (when the archived filter is active).

**File columns include**: Preview, File Path, Asset Name, Database, Asset Type, Tags, File Size, Last Modified, and Asset Description.

<!-- Screenshot needed: Table view showing asset results with multiple columns -->

### Card view

The card view displays results as visual cards arranged in a responsive grid. Each card shows:

- Preview thumbnail (when enabled)
- Asset or file name as a clickable link
- Database badge
- Asset type badge
- Description excerpt (truncated to 100 characters)
- Tags (up to 3 displayed, with a count badge for additional tags)
- Metadata summary popover
- Creation date and author

Card sizes can be configured through preferences (small, medium, or large).

### Map view

:::note
Map view requires the **Location Services** feature to be enabled in your VAMS deployment and is only available in Assets mode.
:::


The map view plots assets on an interactive map based on their location metadata. Assets appear on the map if they have:

- A **location** metadata field containing an LLA-type JSON object with `longitude` and `latitude` properties, or
- Separate **latitude** and **longitude** metadata fields (string or number type).

When you switch to map view, location-related metadata filters are automatically added to ensure only geolocated assets are returned. Clicking a map marker opens a popup with the asset name, database, description, tags, and a **View Asset Details** button.

GeoJSON polygon data is rendered as filled shapes on the map with outline borders.

![Map View](/img/assets_mapView.png)

## Preview thumbnails

The **Show Thumbnails** toggle in the sidebar enables inline preview images in table, card, and map views.

- For **assets**, the thumbnail is loaded from the asset-level preview image. If a `previewFileKey` is available in the search index, it is used directly for faster loading without an additional API call.
- For **files**, the thumbnail shows the file-specific preview (for example, a rendered image of a 3D model or the first page of a PDF).

Clicking a thumbnail opens a full-size preview modal with download options.

:::tip
When the **Map Thumbnails** toggle is enabled (available only in Assets mode with Location Services), a static map image column appears in the table showing the geographic location of each asset.
:::


## Column customization

You can control which columns are visible in the table view through the **Preferences** panel in the sidebar:

1. Expand the **Display & Preferences** section in the sidebar.
2. Check or uncheck columns to show or hide them.
3. Columns are stored separately for Assets and Files modes, so each mode retains its own column configuration.

Column preferences are saved to your browser and persist across sessions.

## Sorting and pagination

- **Sorting** is performed server-side. Click any sortable column header to sort results. The current sort field and direction are indicated in the column header.
- **Pagination** controls appear at the bottom of the results. Page sizes of 10, 25, 50, or 100 are available through preferences. Navigate between pages using the pagination controls.
- The total result count is displayed in the table header. When the count is approximate (more results exist than can be precisely counted), a `+` suffix is shown.

## Direct URL navigation to files

You can navigate directly to a specific file within an asset using the URL format:

```
/#/databases/{databaseId}/assets/{assetId}/file/{filePath}
```

When clicking a file path in search results (Files mode), the application navigates to the asset detail page and automatically selects the corresponding file in the file tree.

## Bulk actions

In Assets mode, you can select multiple assets using the checkboxes and perform bulk actions:

| Action | Description |
|---|---|
| **Delete Selected** | Archive or permanently delete the selected assets. If any selected asset is already archived, the delete modal offers permanent deletion. |
| **Unarchive Selected** | Restore a single archived asset. This button appears when exactly one archived asset is selected. |
| **Create Asset** | Navigate to the upload page to create a new asset. |

:::warning
Permanently deleting an archived asset cannot be undone. The asset and all its files are removed from the system.
:::


## Limited search mode

If your deployment has Amazon OpenSearch Service disabled (the `NOOPENSEARCH` feature flag is active), the search page falls back to a basic asset listing mode. In this mode, full-text search, metadata filtering, and map view are unavailable. Only a paginated table of assets is displayed.
