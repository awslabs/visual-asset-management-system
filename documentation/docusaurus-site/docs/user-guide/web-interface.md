# Web Interface Overview

This page provides a comprehensive tour of the VAMS web interface, covering each major page and its capabilities. For first-time setup, see [Getting Started](getting-started.mdx).

---

## Navigation Structure

The VAMS interface uses a persistent side navigation panel on the left and a top navigation bar. The side panel groups shared pages into six sections: **Home**, **Manage**, **Orchestrate & Automate**, **Admin - Data**, **Admin - Compliance**, and **Admin - Auth**. Your own pages, such as **API Key Management**, are in the profile menu in the top bar (see [Profile menu](#profile-menu)). Pages in both places are filtered based on your role permissions -- you will only see navigation items you are authorized to access. For the Compliance pages, see [Compliance](compliance.md). Add-ons a deployment enables can add navigation sections and pages of their own; see [Add-ons Overview](../developer/addons-overview.md).

:::info
If the navigation panel shows **No Access**, your account does not have any web route permissions assigned. Contact your administrator to request the necessary role assignments.

If it shows **Your pages are in the profile menu**, your role allows only pages that live in the profile menu. Open the menu with your user name in the top bar to reach them.

If it shows **Navigation could not be loaded**, the check for which pages you can open did not complete. Choose **Retry**. While the check has failed, Home still opens, and other pages show **Page access could not be checked** with a **Retry** button.
:::

The side navigation panel can be collapsed or expanded using the toggle control. VAMS remembers your panel state across page navigations within a session.

### Profile menu

The menu with your user name in the top bar lists the pages that belong to you, then **Sign out**:

| Item                   | Description                                                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **My Download Queue**  | Opens your download jobs across every asset, when the deployment enables Download as Zip. See [My Download Queue Page](#my-download-queue-page). |
| **API Key Management** | Opens your own API keys (**My Keys**). See [API keys](api-keys.md).                                                                              |
| **Sign out**           | Ends your session. It is always the last item and is available even when your pages cannot load.                                                 |

The page you are on is marked with a check mark in the menu, as the side navigation highlights the active page; it stays selectable. The menu lists only the pages your role allows. While the check for which pages you can open is loading, or if it did not complete, the menu shows **Sign out** only; the pages appear after you next move to another page. The menu is keyboard-operable: focus it, press **Enter** to open it, use the arrow keys to move, and press **Escape** to close it. On phone-width screens the menu is the user icon in the top bar; only on the narrowest screens (about 280 px) does it move into the **More** menu.

The **Settings** menu next to it holds the theme choice, which is stored in your browser. See [Setting your theme preference](getting-started.mdx#setting-your-theme-preference).

---

## Database Listing Page

The **Databases** page displays all databases you have access to in a searchable, sortable table.

![Database listing page showing all databases in a searchable table](/img/database_page_20261109_v2.7.png)

### Key Features

| Feature             | Description                                                                                                                                                     |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Create Database** | Opens a modal dialog to create a new database with a name, description, default bucket, and optional restrictions.                                              |
| **Edit Database**   | Modify an existing database's description, bucket assignment, metadata schema restrictions, and file extension restrictions.                                    |
| **Metadata modal**  | Select a database to view and edit its metadata in a dedicated modal.                                                                                           |
| **Map thumbnails**  | When Amazon Location Service is enabled, toggle the **Show map thumbnails** option to display geographic previews for databases that contain geospatial assets. |
| **Text filtering**  | Use the search box to filter databases by name or other visible fields.                                                                                         |
| **Column sorting**  | Select any column header to sort the table in ascending or descending order.                                                                                    |
| **Pagination**      | Navigate through large database lists using pagination controls.                                                                                                |

**Create Database** is disabled, with the reason shown on hover or focus, unless the user's role may call `POST /database`. **Edit** and **Delete Selected** appear only to users whose role may call `PUT /database/{databaseId}` and `DELETE /database/{databaseId}`. A role that may only list databases, such as one built from the Compliance Admin template, sees the list with its **Compliance** links. The editor reads the bucket list (`GET /buckets`, an administrator route) only when it opens. A database name opens the database's assets page when the role may call the route that page lists from (`POST /search`, or `GET /database/{databaseId}/assets` in a deployment without OpenSearch); otherwise it opens the database's compliance page, or is not a link when that page is not allowed either. The **Metadata** link appears only when the role may call `GET /database/{databaseId}/metadata`. On the compliance pages the same rule applies: the database name in the database compliance page's breadcrumb opens the database's assets page only when the role may call the route that page lists from, and an asset name on the database compliance, Quarantine and Audit Log pages opens the asset's page only when the role may call `GET /database/{databaseId}/assets/{assetId}`; otherwise each is plain text.

### Database Properties

Each database record displays the following information:

| Column                | Description                                                                    |
| --------------------- | ------------------------------------------------------------------------------ |
| **Database Name**     | The unique identifier for the database.                                        |
| **Description**       | A human-readable description of the database's purpose.                        |
| **Total Asset Count** | The number of active assets in the database, including assets you cannot open. |

---

## Asset Search Page

The **Assets and Files** page is the primary interface for discovering and browsing assets. A switch in the page title row chooses between two views: **Search**, available when Amazon OpenSearch or natural-language search is enabled, offers keyword and natural-language queries with filters and multiple view modes; **Asset list**, always available, lists the assets you are authorized to see with archive, permanent-delete, and unarchive actions. In both views an action your role does not allow, such as **Unarchive Selected** for a role built from the `database-user` template, is shown disabled with the reason. With only the asset list available, the page shows no switch. The active view is carried in the URL (`?tab=`).

The breadcrumb above the title ends with the page itself: on the all-databases page (`#/assets/`) it is the page title alone, and on a database's page it reads **Databases > Assets for {databaseId}**.

![Assets and Files page in the Search view: the Search and Asset list switch in the title row, the Keyword and Natural language switch above the search box, and the asset results for gearbox in the table](/img/asset_search_table_20261110_v2.7.png)

### View Modes

VAMS provides three ways to browse assets in the Search view:

| View Mode      | Description                                                                                                                                                                                         |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Table view** | A detailed tabular layout with sortable columns. Best for reviewing asset properties in bulk.                                                                                                       |
| **Card view**  | A grid of asset cards showing preview thumbnails. Best for visual browsing.                                                                                                                         |
| **Map view**   | A geographic map display showing asset locations. Available only when Amazon Location Service is enabled. In natural-language mode it pages through the natural-language answer, as the table does. |

### Search and Filtering

The Search view provides several mechanisms for finding assets:

-   **Keyword** / **Natural language** -- When both engines are enabled, choose how the query is read: keyword matching across indexed fields, or a natural-language description ranked by semantic similarity of each file's generated metadata, text, and renders. With only one engine enabled the control is fixed to that engine.
-   **Text search** -- Enter keywords to search across asset names and properties.
-   **Database filter** -- Filter assets to a specific database by navigating from the Databases page or using the database selector. On a database's page the filter is locked to that database and shows its name.
-   **Search notice and failures** -- Rare natural-language warnings, such as "Some result details couldn't be loaded. The matches are still shown.", appear in a dismissible **Search notice** above the results, as does a keyword search page the search time limit cut short ("The search ran out of time before every match on this page was checked ..."), whose count then shows as a minimum. A search that cannot finish, for example "Search is busy right now. Try again in a moment.", shows a **Search failed** notification with the reason and keeps every search control. See [Search and Discovery](search-and-discovery.md#the-query-band).
-   **Column customization** -- Show or hide table columns to focus on the properties that matter to you.
-   **Property filtering** -- Use the filter bar to build complex queries combining multiple properties with AND/OR logic (keyword mode, or natural-language mode with Amazon OpenSearch enabled).

### Preview Thumbnails

When assets have preview images, thumbnails are displayed alongside each asset in all view modes. Previews are generated automatically by configured pipelines or can be uploaded manually during asset creation.

### Navigating to an Asset

Select any asset row in the table view or any card in the card view to navigate to the [asset detail page](#asset-detail-page). You can also access an asset directly using its URL in the format `/#/databases/{databaseId}/assets/{assetId}`.

---

## Asset Detail Page

The asset detail page is the central hub for viewing and managing a single asset. It is divided into three main sections: the **details pane**, the **tabbed container**, and the **metadata section**.

![Asset detail page showing preview, properties, and tabbed container](/img/view_asset_page_20261109_v2.7.png)

### Breadcrumb Navigation

A breadcrumb trail at the top of the page shows your current location in the hierarchy: **Databases > {Database Name} > {Asset Name}**. Select any breadcrumb segment to navigate back to that level.

### Details Pane

The details pane displays the asset's core properties and provides action buttons.

| Element                           | Description                                                                                                                                 |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Asset Name**                    | The display name of the asset, shown as the container header.                                                                               |
| **Preview thumbnail**             | If a preview image exists, a clickable thumbnail is displayed. Select it to open a larger preview modal.                                    |
| **Asset Id**                      | The unique identifier assigned to the asset.                                                                                                |
| **Description**                   | The asset's description text.                                                                                                               |
| **Type / Distributable**          | The asset type (file extension or "folder" for multi-file assets) and whether downloads are enabled. An info tooltip explains these fields. |
| **Tags**                          | Any tags applied to the asset.                                                                                                              |
| **Version selector**              | A dropdown to switch between asset versions. The current (latest) version is selected by default.                                           |
| **Delete button**                 | Opens the asset deletion dialog (archive or permanent delete).                                                                              |
| **Edit button**                   | Opens the update asset modal to modify name, description, type, distributable status, and tags.                                             |
| **Subscribe / Subscribed button** | Toggle notification subscription for this asset's version change events.                                                                    |

### Tabbed Container

Below the details pane, a tabbed interface provides access to the following sections:

| Tab                | Description                                                                                                                                                                                                                                                                                                                                                |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **File Manager**   | A split-pane file browser with a directory tree on the left and file details on the right. Supports uploading, moving, copying, renaming, archiving, and deleting files. When viewing a specific version, the label updates to **File Manager (v\{versionId\})** and file operations are read-only.                                                        |
| **Relationships**  | View and manage asset-to-asset relationships. Supports parent, child, and related link types, including cross-database relationships.                                                                                                                                                                                                                      |
| **Executions**     | View workflow execution history for this asset and trigger new workflow executions.                                                                                                                                                                                                                                                                        |
| **Comments**       | View and add comments on the asset using a rich text editor.                                                                                                                                                                                                                                                                                               |
| **Versions**       | View the full version history of the asset, including version aliases, comments, and archive status.                                                                                                                                                                                                                                                       |
| **Download Queue** | Your download jobs for this asset (archive jobs from Download as Zip), with status and progress, a search box with a refresh button, **Show all users** when your role allows it, and **Download**, **Cancel**, and **Delete** actions. Shown when the deployment enables Download as Zip. See [Download as Zip and the Download Queue](zip-downloads.md). |

### Metadata Section

Below the tabbed container, the metadata section displays all key-value metadata pairs associated with the asset. When a metadata schema is applied to the database, the interface enforces schema-defined fields and types. In online mode, edits are saved directly to the backend.

---

## File Viewer Page

When you select a file in the file manager and choose to view it, VAMS opens a dedicated file viewer page. If only one viewer plugin supports the file extension, the page opens the file in it; if several do, you choose one from the viewer dropdown.

![File viewer page displaying a USDZ 3D model in the Three.js Viewer](/img/view_file_page_usdz_20261109_v2.7.png)

### Supported Viewer Types

VAMS includes 22 built-in viewer plugins covering 3D models, point clouds, Gaussian splats, images, video, audio, documents, and tabular data. The viewer is selected automatically only when exactly one viewer supports the file extension. If multiple viewers support the same extension, you choose one from a dropdown and can switch between them.

For the complete list of supported file viewers and extensions, see [File Viewers](../concepts/viewers.md).

:::tip
If a file's extension matches multiple viewers, the viewer dropdown lists them with the lowest priority number first, and the file opens once you choose one. If the file has a preview image, the file page also offers a **Preview** option that shows it in the Preview Viewer.
:::

---

## Upload Page

The **Create Asset** page uses a multi-step wizard to guide you through asset creation. For a detailed walkthrough, see [Upload Your First Asset](upload-first-asset.md).

The wizard consists of five steps:

1. **Asset Details** -- Enter the asset name, select a database, set distributable status, provide a description, and assign tags.
2. **Asset Metadata** -- Add key-value metadata pairs. If the database has a metadata schema, required fields are enforced.
3. **Asset Relationships** (optional) -- Define parent, child, or related relationships with existing assets.
4. **Select Files to Upload** (optional) -- Choose files or folders to upload, with drag-and-drop support. Optionally provide an overall preview image.
5. **Review and Upload** -- Review all details and submit.

---

## Pipeline and Workflow Management Pages

### Pipelines Page

The **Pipelines** page lists all registered processing pipelines. Pipelines define individual processing steps such as 3D model conversion, point cloud processing, preview thumbnail generation, or GenAI metadata generation.

Each pipeline entry shows its name and id, its execution type, its owning database, its category, and how many configuration templates it carries, together with **Disabled**, **Archived**, or **System** where any applies. Entries are grouped by category or by database, and the list can be filtered by execution type, status, and database. A **System** pipeline is owned by the deployment: its entry opens read-only, only its `enabled` switch can be changed, its templates can have their configuration body and tag schema edited but cannot be added or deleted, and the Edit and Archive actions are hidden.

![Pipelines page grouped by category, with the two SYSTEM - GenAI pipelines carrying the System badge](/img/pipelines_page_system_badge_20261110_v2.7.png)

Each entry's **⋮** actions menu holds **Edit**, **Templates**, and **Archive**, limited to the actions your permissions allow. **Create Pipeline** in the page header starts a new one. Deleting a template from the **Templates** list reports a warning, kept on the list, when an auto-triggered workflow's trigger had chosen it as this pipeline's default.

![Pipelines page listing two built-in pipelines grouped by category](/img/pipelines_page_20261109_v2.7.png)

### Workflows Page

The **Workflows** page lets you create and manage workflows that chain multiple pipelines into automated processing sequences. Workflows run automatically when one of their triggers matches an upload, or manually from the workflow's own **Execute** action or an asset's **Automation** menu.

![Workflows page listing two built-in workflows with pipeline, execution, and trigger counts](/img/workflows_page_20261109_v2.7.png)

Each entry shows the workflow's name and id, its database and category, how many pipelines it runs, how many executions it has, and how many triggers it defines — with the number currently enabled called out when some are off — together with **Disabled**, **Archived**, or **System** where any applies. Filters narrow the list by status, by whether the workflow has an enabled trigger, and by database. A **System** workflow opens read-only: only its `enabled` switch and its triggers' `enabled` switches can be changed.

Each entry's **⋮** actions menu holds **Edit**, **Execute**, **View Executions**, and **Archive**, limited to the actions your permissions allow.

Creating or editing a workflow opens a step-by-step wizard, whose **Pipelines** step lists the steps in order alongside a graph of the resulting sequence.

![Workflow editor showing pipeline steps and visual graph](/img/workflow_editor_20261109_v2.7.png)

### Executions Page

The **Executions** page lists workflow executions across every workflow and database you may see. Filters narrow the list by status, trigger, workflow, workflow database, and time window; the output columns identify the asset each run wrote to. Runs of system workflows are hidden until you select **Show system runs**, and shown runs carry a **System** badge. Selecting a run opens its detail view, with tabs for its inputs, pipelines, outputs, settings, and logs.

A **Skipped** row records file-upload trigger launches that the workflow's concurrency restriction refused while another execution held the asset, file or file version. One row counts every launch that execution refused, shows how many under the badge and gives the reason on hover. Its detail view lists the files and, when you may read it, links the execution that blocked them. Skipped rows have no Abort action. Choose **All except skipped** in the status filter to hide them.

![Executions page listing workflow executions with status, trigger, and output columns](/img/executions_page_20261109_v2.7.png)

## My Download Queue Page

When the deployment enables Download as Zip, the **My Download Queue** page (`#/zipdownloads`), opened from the profile menu in the top bar, lists your download jobs across every asset, newest first, with each job's asset, archive name, request ID, status, progress, and expiry. Archive jobs are the only kind of download job today. The search box finds jobs by asset ID, asset name, request ID, or archive name among the jobs loaded, and the refresh button beside it reloads the list. A job that has succeeded can be downloaded; a queued or running job can be cancelled; a finished job can be deleted. The page lists only your own jobs. See [Download as Zip and the Download Queue](zip-downloads.md).

---

## Settings and Theme Toggle

The **Settings** dropdown in the top navigation bar provides access to theme preferences. VAMS offers two themes:

| Theme           | Description                                                                       |
| --------------- | --------------------------------------------------------------------------------- |
| **Dark Theme**  | Default theme with a dark background. Reduces eye strain in low-light conditions. |
| **Light Theme** | Light background theme for bright environments.                                   |

Select a theme from the dropdown to switch immediately. The active theme is marked with a checkmark. Your preference persists across browser sessions.

---

## Hash-Based URLs and Deep Linking

VAMS uses hash-based routing (URLs contain `#/`), which ensures compatibility across all deployment modes including Amazon CloudFront and Application Load Balancer distributions. You can construct direct links to any page in VAMS using these URL patterns.

### URL Patterns

| Target                    | URL Pattern                                                                                                             |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Database list             | `https://<VAMS_WEBSITE>/#/databases/`                                                                                   |
| Specific database         | `https://<VAMS_WEBSITE>/#/databases/<databaseId>`                                                                       |
| Search page, a given view | `https://<VAMS_WEBSITE>/#/search?tab=asset-list` or `?tab=unified-search`                                               |
| Asset detail              | `https://<VAMS_WEBSITE>/#/databases/<databaseId>/assets/<assetId>`                                                      |
| File viewer               | `https://<VAMS_WEBSITE>/#/databases/<databaseId>/assets/<assetId>/file/<encodedFilePath>`                               |
| File at version           | `https://<VAMS_WEBSITE>/#/databases/<databaseId>/assets/<assetId>/file/<encodedFilePath>?version=<fileVersionId>`       |
| File at asset version     | `https://<VAMS_WEBSITE>/#/databases/<databaseId>/assets/<assetId>/file/<encodedFilePath>?assetVersion=<assetVersionId>` |
| Upload page               | `https://<VAMS_WEBSITE>/#/upload/`                                                                                      |
| Upload to a database      | `https://<VAMS_WEBSITE>/#/upload/<databaseId>`                                                                          |
| My Download Queue         | `https://<VAMS_WEBSITE>/#/zipdownloads`                                                                                 |
| Your API keys             | `https://<VAMS_WEBSITE>/#/auth/api-keys/?mode=user`                                                                     |
| All users' API keys       | `https://<VAMS_WEBSITE>/#/auth/api-keys/?mode=admin`                                                                    |

The `?mode=` value on the API keys page is a preference. The page opens the requested mode only if the API allows it for your role; otherwise it opens the mode your role does have (all users' keys for an administrator, your own keys for everyone else). The API still checks every call.

### File Path Encoding

File paths in URLs must be encoded using standard URL encoding (`encodeURIComponent` in JavaScript). Forward slashes in the file path become `%2F`.

**Examples:**

```
# File at asset root
https://vams.example.com/#/databases/building/assets/x7150abc/file/photo.jpg

# File in a subdirectory
https://vams.example.com/#/databases/building/assets/x7150abc/file/images%2Ffloor-plan.png

# File with specific version
https://vams.example.com/#/databases/building/assets/x7150abc/file/scan.e57?version=v2
```

:::tip[Sharing and bookmarking]
When sharing links or creating bookmarks, always include the `#/` portion of the URL. These deep links work without any server-side routing configuration and can be used in external tools, documentation, emails, or ticketing systems to link directly to specific VAMS content.
:::

:::note[Version query parameters]
When both `version` and `assetVersion` query parameters are provided, `assetVersion` takes priority. The `assetVersion` parameter shows the file as it existed at that specific asset version snapshot.
:::
