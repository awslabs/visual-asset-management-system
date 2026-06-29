# Feature Design: Pop-up Visualizer Viewers from Asset File Search

**Feature ID:** VAMS-FE-001
**Status:** Implemented (Design 2026-06-17; scope revised 2026-06-22; as-built notes 2026-06-26 — see §0.5)
**Author:** Development Team
**Created:** 2026-06-17
**Target Release:** 2.6.0

---

## 0. Design Decisions (2026-06-22 Revision)

These decisions are authoritative and govern the rest of this document. Where earlier
sections conflict, these decisions win.

1. **Eye icon is additive, not a replacement.** The new "view" (eye) icon is added
   **alongside** the existing preview image/thumbnail in the preview column. The current
   preview thumbnail is **not** removed or replaced.

2. **Scope is file search only.** All changes are scoped to the **file view / file search**
   (`_rectype === "file"`). The **asset view is not modified**. Because asset-mode and
   file-mode rendering live in the same files (`SearchPageListView.tsx`), the code may
   *reference* asset-mode constructs, but **nothing new is hooked up to asset mode** in
   this effort.

3. **Visualizer accepts a `(databaseId, assetId)` pair per file.** Instead of passing one
   `assetId` + an array of files (all assumed to share that asset), the system passes a
   **`databaseId` and `assetId` for each individual file**. This is a **systematic change**
   across the visualizer entry point, all current viewers, and their configs. It removes
   the single-asset constraint and is the foundation that makes multi-file (and later
   multi-asset) viewing work correctly.

4. **Multi-select viewer mode (file search).** Add a mode under file search where the user
   can: (a) run multiple searches and **add files to a running selection** that persists
   across searches, (b) **view all selected files together** in the viewer, and (c)
   **clear the selection or exit** the mode.

5. **No comparison mode yet.** A "comparison mode" + mode dropdown were discussed but are
   **explicitly deferred**. Comparison gets complicated because different viewers support
   different file counts. It remains a Future Enhancement (§11).

6. **No mode system in the viewers (yet).** Do **not** build a full mode system inside the
   viewers. Only establish the architecture so a viewer can **accept multiple files**;
   future modes (comparison, etc.) then become easy add-ons. If a viewer already has any
   such mode scaffolding, leave it as-is.

---

## 0.5 Implementation Notes (As-Built, 2026-06-26)

The feature shipped with the following refinements discovered during implementation and live
testing. **Where these conflict with the original decisions above, these as-built notes win.**

1. **Eye icon location — supersedes Decision #1's removal.** The per-row preview affordance is
   an **eye icon rendered in the File Path column cell** (to the left of the path), shown only
   in file mode for rows whose extension a viewer can render. (An earlier iteration removed the
   eye icon entirely; it was re-added here by request as the primary single-file-preview entry
   point, replacing the need to navigate into the asset's File Manager "Viewer Popup".) The eye
   uses a custom SVG via Cloudscape `Button iconSvg` because Cloudscape ships no eye icon
   (`view-full` renders as a fullscreen-brackets box).

2. **Selection model — check = in, uncheck = out.** Multi-select viewer mode mirrors exactly
   the currently checked rows: the running selection is *replaced* on each `onSelectionChange`
   (reducer action `SET_VIEWER_SELECTION`, deduped by key), so "View Selected (N)" always
   matches the checkboxes. (The original accumulate-only design left the count stale when a row
   was unchecked.)

3. **Per-file asset context fully threaded (Decision #3 completion).** `FileInfo` carries
   optional `assetId`/`databaseId`; `DynamicViewer` passes a `multiFiles: FileInfo[]` prop, and
   the enabled multi-file viewers (Three.js, Online3D) build each file's stream URL from that
   file's own context. Cross-asset multi-select now streams every file correctly (previously
   only the first asset's files loaded; others 400'd).

4. **Multi-file 3D layout — load at NATIVE coordinates, no geometry mutation.** When >1 model
   loads, each is added to the scene at its **own authored coordinates** — the viewer does
   **not** scale, recenter, or offset any geometry. Model coordinates are meaningful (scene
   construction now; diff mode later, which will use entirely new viewers), so they must be
   preserved exactly. Only the **camera** is framed to the combined bounds; the object-tree
   panel provides per-file show/hide/zoom for navigating individual files. (An earlier build
   normalized + spread models into a row for visibility — **removed** because it mutated
   geometry. Consequence: models at very different scales/origins may overlap or appear far
   apart; that is correct/native behavior and is the viewer's concern to improve later, not the
   system's.)

5. **Registry must be initialized on the search page.** `isViewableExtension()` queries
   `PluginRegistry`, which is only initialized when a viewer mounts. `ModernSearchContainer`
   now calls `initializePluginRegistry()` on mount so the eye icon / viewability gating works
   before any modal opens. Extension matching also normalizes the leading dot (`str_fileext`
   is `"ply"`, registry stores `".ply"`).

6. **`.ifc` enabled via self-hosted web-ifc.** Added `.ifc` to the Online3D viewer. The stock
   o3dv build loads web-ifc from a public CDN (jsdelivr), which breaks GovCloud/air-gapped CSP.
   The o3dv install script now copies web-ifc locally (`public/viewers/online3dviewer/libs/`),
   rewrites the CDN URL to the local copy, injects `SetWasmPath(...)`, and forces single-thread
   `Init(undefined, true)` (the multithreaded build needs SharedArrayBuffer + COOP/COEP and a
   worker, which isn't portable across deployments). Verified: IFC renders with zero CDN calls.

7. **`.bin` is intentionally non-viewable.** `.bin` files are GLTF binary buffers (geometry/
   animation data loaded *by* a `.gltf`), not standalone-viewable assets. They correctly show
   no eye icon. They appear as separate search rows only because every S3 object is indexed;
   hiding them from file search is a separate backend/indexing concern.

8. **Viewer coverage audit.** All sample file types map to a working viewer except: `.bin`
   (non-viewable by design), and `.usd`/`.usdz` + 3D-Tiles `.json` via Cesium, which require
   the `ALLOWUNSAFEEVAL` feature flag and are correctly hidden when it is off. `.e57/.las/.laz`
   show the Potree viewer but require the Potree pipeline to have generated octree preview files
   first (the viewer shows a clear message otherwise).

9. **Cross-engine multi-select is a viewer-level concern (deferred).** `DynamicViewer` selects a
   single viewer engine for a multi-file set, matching any selected extension (`canHandle` uses
   `.some()`). `.ifc` is handled only by Online3D; mesh formats (`.glb/.gltf/.obj/.ply`) by
   Three.js — and the two engines don't read each other's formats. So a mixed `.ifc` + mesh
   selection can render only the subset its chosen engine supports; the rest don't appear. This
   is an engine-capability gap, **not** the per-file streaming issue (note 3, fixed). The intent
   of the current work is to make the viewer **system** support multiple files at native
   coordinates; improving how individual/cross-engine viewers present mixed sets (per-engine
   grouping, tabs, or a future diff-mode viewer) is deliberately deferred to a later
   viewer-focused pass.

---

## 1. Problem Statement

When users perform an **asset file search** (file-mode search where each result row is an individual file within an asset), they must navigate away from the search results to view a file's content. This breaks workflow context, increases page loads, and makes it difficult to quickly verify or compare files during search-driven workflows.

**Scope clarification:** This feature applies **only to file-mode asset file search** — the search mode where `_rectype === "file"` and each row represents a single file (with `str_key`, `str_fileext`, `str_assetid`, `str_databaseid`). It does **not** apply to asset-mode search (where a row represents a whole asset).

**Pain Points:**
- Clicking a file result navigates to the dedicated file view page (context lost)
- No way to "quick peek" at a file without full navigation
- Reviewing/comparing multiple files from search requires opening multiple tabs
- Users performing batch file reviews spend excessive time navigating back and forth

---

## 2. Goals & Success Criteria

| Goal | Metric |
|------|--------|
| Reduce navigation for file preview | < 1 click to preview from file search results |
| Maintain search context | User can preview and return to exact search state |
| Additive preview affordance | Eye icon is added alongside the existing thumbnail; thumbnail never removed (Decision #1) |
| File-search scope only | No asset-view behavior changes (Decision #2) |
| Support all viewer types | All existing viewer plugins work in popup |
| Per-file asset context | Each file carries its own `(databaseId, assetId)`; viewer no longer assumes one asset (Decision #3) |
| Multi-select viewer mode | User can accumulate files across multiple searches and view them together (Decision #4) |
| Zero backend changes | Frontend-only implementation |
| Accessible | Keyboard navigable, screen reader compatible |

> **Out of scope (this phase):** comparison mode + mode dropdown (Decision #5), and any in-viewer
> mode system (Decision #6). The viewer is only made capable of *accepting* multiple files.

---

## 3. Current Architecture (Verified)

### Search Flow (Actual)
```
web/src/pages/search/SearchPage.tsx                  (thin page wrapper)
  └─ web/src/components/search/ModernSearchContainer.tsx   (state + API orchestrator)
       ├─ uses useSearchState() reducer  (selectedItems, filters, results, pagination)
       ├─ uses useSearchAPI()            (executeSearch → APIService.searchAssets)
       ├─ uses usePreferences()          (column prefs, page size, view mode)
       └─ web/src/components/search/SearchPageListView.tsx   (Cloudscape Table rendering)
            ├─ Asset mode (_rectype === "asset"): selectionType="multi", bulk action bar
            └─ File mode  (_rectype === "file"):  selectionType=undefined (NO selection today)
```

### Two Search Modes
| Mode | `_rectype` | Row represents | Key fields on row |
|------|-----------|----------------|-------------------|
| Asset | `"asset"` | A whole asset | `str_assetid`, `str_assetname`, `str_databaseid`, `list_tags` |
| **File (this feature)** | `"file"` | A single file | `str_key`, `str_fileext`, `str_assetid`, `str_assetname`, `str_databaseid`, `num_filesize`, `date_lastmodified` |

### Search Result Row Shape (Flattened)
Rows are flattened from OpenSearch hits in `SearchPageListView.tsx`:
```typescript
// items={state.result.hits.hits.map(hit => ({ ...hit._source, _id: hit._id, explanation: hit.explanation }))}
{
  _id: string;
  str_assetid?: string;
  str_assetname?: string;
  str_databaseid?: string;
  str_key?: string;        // full S3 file key (FILE MODE)
  str_fileext?: string;    // file extension (FILE MODE)
  num_filesize?: number;
  date_lastmodified?: string;
  list_tags?: string[];
  [key: string]: any;
}
```

> **Key insight:** In file mode, the row **already contains** the S3 key (`str_key`) and extension (`str_fileext`). No file-list API call is needed to open the viewer — we construct a `FileInfo` directly from the row.

### Existing Viewer Infrastructure (Actual Paths)
```
web/src/components/filemanager/modals/FileViewerModal.tsx   ← REUSE (modal wrapper)
web/src/visualizerPlugin/components/DynamicViewer.tsx       ← Plugin router
web/src/visualizerPlugin/components/ViewerSelector.tsx      ← Picks which viewer PLUGIN (not files)
web/src/visualizerPlugin/core/types.ts                      ← FileInfo interface
web/src/visualizerPlugin/core/PluginRegistry                ← Knows supported extensions per plugin
```

> Note: There is **no** `ViewerFileSelection.tsx` and no `web/src/components/viewers/` directory. The earlier draft referenced these incorrectly.

### Key Existing Component: FileViewerModal (Actual Props)
```typescript
// web/src/components/filemanager/modals/FileViewerModal.tsx
interface FileViewerModalProps {
    visible: boolean;
    onDismiss: () => void;
    files: FileInfo[];          // single OR multiple files — multi-file handled natively
    databaseId: string;         // fallback context after Decision #3 (per-file context wins)
    assetId: string;            // fallback context after Decision #3 (per-file context wins)
    assetVersionId?: string;
}
```

> **After Decision #3 (§7.0):** the authoritative asset context lives on each `FileInfo`
> (`assetId`/`databaseId`). The top-level `databaseId`/`assetId` props are retained only as a
> backward-compatible default for legacy single-file callers (file tree, asset view), keeping
> Decision #2 intact.

The modal:
- Renders a Cloudscape `<Modal size="max">` containing a `<DynamicViewer>`
- Title adapts: single file → filename; multiple files → file count
- Generates a unique `key` from file keys to force DynamicViewer re-mount when files change
- Blocks fullscreen in modal context (only `collapse` / `wide` viewer modes)
- `DynamicViewer` auto-selects a viewer when exactly one plugin is compatible; otherwise shows `ViewerSelector` for the user to choose

### FileInfo Interface (Actual — today)
```typescript
// web/src/visualizerPlugin/core/types.ts
export interface FileInfo {
    filename: string;
    key: string;
    isDirectory: boolean;
    versionId?: string;
    size?: number;
    dateCreatedCurrentVersion?: string;
    isArchived?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}
```

> **Today there is no per-file asset context.** `assetId`/`databaseId` are passed *once* on
> `FileViewerModal` → `DynamicViewer` → `ViewerPluginProps` and applied uniformly to every
> file (viewers build `database/{databaseId}/assets/{assetId}/download/stream/{key}` using the
> single shared pair plus `multiFileKeys[]`). Decision #3 changes this — see §7.0.

### Proven Reference Pattern: FileDetailsPanel
`web/src/components/filemanager/components/FileDetailsPanel.tsx` already triggers FileViewerModal for both single and multi-file selections:
```typescript
const handleFileViewerModal = () => {
    const files = getModalFiles();      // builds FileInfo[] from current selection
    setModalFiles(files);
    setShowFileViewerModal(true);
};
```
`getModalFiles()` filters out folders and maps each selected item to a `FileInfo`. **We mirror this pattern**, building `FileInfo[]` from file-search rows instead of file-tree selections.

---

## 4. Design Scenarios

All scenarios are scoped to **file-mode search only**.

### Scenario A: Per-Row Eye Icon + Multi-Select Bulk Action (RECOMMENDED)

**Description:** Add an "eye" icon button to each file-mode result row for single-file preview, AND enable multi-select in file mode with a "View Selected" bulk action for multi-file preview. Both open the existing FileViewerModal.

**Single-file flow:**
```
1. User runs a file search (results = individual files)
2. User clicks 👁️ on a row
3. Build FileInfo from row (str_key, str_fileext, num_filesize, etc.) — NO API call
4. FileViewerModal opens with that one file
5. User closes → exact same search results preserved
```

**Multi-file flow:**
```
1. User runs a file search
2. User checks multiple file rows (multi-select enabled in file mode)
3. User clicks "View Selected" in the bulk action bar
4. Build FileInfo[] from all selected rows
5. FileViewerModal opens with all files; DynamicViewer negotiates a multi-file-capable viewer
6. User closes → search results + selection preserved
```

**UI Mockup (file mode):**
```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Search  (File results)                          [View Selected (3)] [🔍 Search]  │
├───────┬───┬────────────────────┬────────────┬──────┬──────────┬────────┬──────────┤
│Preview│ ☑ │ File Path          │ Asset Name │ Type │ Size     │Modified│ Tags     │
├───────┼───┼────────────────────┼────────────┼──────┼──────────┼────────┼──────────┤
│  👁️   │ ☑ │ /pump/body.glb     │ Pump Asy v2│ glb  │ 2.4 MB   │ Jun 10 │ [mfg]    │
│  👁️   │ ☑ │ /pump/texture.png  │ Pump Asy v2│ png  │ 512 KB   │ Jun 10 │ [mfg]    │
│  👁️   │ ☑ │ /scan/site.e57     │ Site Scan  │ e57  │ 1.2 GB   │ Jun 09 │ [survey] │
│  ─    │ ☐ │ /docs/report.docx  │ Report Q4  │ docx │ 88 KB    │ Jun 01 │ [docs]   │ ← no viewer
└───────┴───┴────────────────────┴────────────┴──────┴──────────┴────────┴──────────┘
```

**Modal (reuses FileViewerModal, multi-file shown):**
```
┌──────────────────────────────────────────────────────────────────────────┐
│  3 files                              [Viewer: Three.js ▼]        [✕]    │
├──────────────────────────────────────────────────────────────────────────┤
│                       ┌───────────────────────┐                          │
│                       │   MULTI-FILE 3D VIEW  │                          │
│                       │   (body.glb + texture)│                          │
│                       └───────────────────────┘                          │
│  (dimmed background = file search results still visible)                  │
└──────────────────────────────────────────────────────────────────────────┘
```

**Advantages:**
- ✅ No file-list API call — `str_key`/`str_fileext` already on the row (instant open)
- ✅ Reuses FileViewerModal entirely (multi-file already supported)
- ✅ Mirrors proven `FileDetailsPanel.getModalFiles()` pattern
- ✅ Non-breaking (file path link to full view preserved)
- ✅ Multi-file viewing for free (DynamicViewer negotiates multi-file viewers)
- ✅ Viewer compatibility known instantly from `str_fileext`

**Disadvantages:**
- ⚠️ Introduces a multi-select viewer **mode** in file mode (new behavior; file mode has no selection today)
- ⚠️ Per-file asset context (Decision #3) is a systematic change touching all viewers (mitigated by a backward-compatible fallback)

**Implementation Files:**
| File | Change |
|------|--------|
| `web/src/visualizerPlugin/core/types.ts` | Add `assetId`/`databaseId` to `FileInfo` (Decision #3) |
| `web/src/visualizerPlugin/components/DynamicViewer.tsx` | Route per-file `{assetId, databaseId, key}` to the active viewer instead of one shared pair (Decision #3) |
| `web/src/visualizerPlugin/components/FileViewerModal.tsx` *(actual: `components/filemanager/modals/FileViewerModal.tsx`)* | Keep top-level `assetId`/`databaseId` as fallback; pass per-file context through |
| `web/src/visualizerPlugin/viewers/*` (all streaming viewers) | Build stream URL from each file's own context (Decision #3) |
| `web/src/visualizerPlugin/config/viewerConfig.json`, `viewers/manifest.ts` | No new viewers; verify `supportsMultiFile` accuracy |
| `web/src/components/search/SearchPageListView.tsx` | Augment existing preview column cell to add eye icon **alongside** thumbnail (Decision #1); file-mode-only changes (Decision #2) |
| `web/src/components/search/ModernSearchContainer.tsx` | Wire viewer-selection mode + modal state |
| `web/src/components/search/hooks/useSearchState.tsx` | Add **persistent** `viewerSelectMode` + `viewerSelection` slice (Decision #4) — separate from `SET_SELECTED_ITEMS`, which resets per search |

---

### Scenario B: Eye Icon Only (Single-File, No Multi-Select)

**Description:** Add only the per-row eye icon. Do not enable multi-select in file mode. Each preview shows exactly one file.

**Advantages:**
- ✅ Smallest change; no behavior change to file-mode selection
- ✅ Lowest risk

**Disadvantages:**
- ❌ No multi-file viewing (a primary user ask)
- ❌ Cannot compare/view related files (e.g., `.glb` + textures) together

**Implementation Files:**
| File | Change |
|------|--------|
| `web/src/components/search/SearchPageListView.tsx` | Add Preview column (file mode) + modal integration |

---

### Scenario C: Multi-Select Bulk Action Only (No Per-Row Icon)

**Description:** Enable multi-select in file mode and add only a "View Selected" bulk action. Single-file preview requires selecting one row then clicking the action.

**Advantages:**
- ✅ One consistent path (select → view) for 1..N files
- ✅ Matches the asset-mode bulk-action bar pattern

**Disadvantages:**
- ⚠️ Slower for the common "peek at one file" case (select + click vs single click)
- ⚠️ Less discoverable than an inline icon

**Implementation Files:**
| File | Change |
|------|--------|
| `web/src/components/search/SearchPageListView.tsx` | Enable file-mode selection + "View Selected" action + modal integration |

---

## 5. Scenario Comparison Matrix

| Criteria | A: Icon + Multi-Select | B: Icon Only | C: Multi-Select Only |
|----------|:-:|:-:|:-:|
| Single-file speed | Excellent (1 click) | Excellent (1 click) | Fair (select+click) |
| Multi-file viewing | ✅ Yes | ❌ No | ✅ Yes |
| Reuse of existing components | 95% | 95% | 95% |
| Behavior change to file mode | Selection enabled | None | Selection enabled |
| Implementation effort | ⭐⭐ Low-Med | ⭐ Low | ⭐⭐ Low |
| Backend changes | None | None | None |
| Risk level | Low | Very Low | Low |
| Time to implement | 2-3 days | 1-2 days | 2 days |

---

## 6. Recommended Implementation: Scenario A

Scenario A delivers both the fast single-file peek (eye icon) and multi-file viewing (multi-select + "View Selected"), reusing FileViewerModal and the existing selection reducer. The remaining sections detail Scenario A.

---

## 7. Technical Implementation Details (Scenario A)

### 7.0 Per-File Asset Context (Decision #3 — Systematic Change)

**This is the foundational change** that the rest of the feature builds on. Today the
visualizer takes a single `assetId` + `databaseId` and an array of files, implicitly assuming
**all files belong to one asset**. We change the contract so **each file carries its own
`assetId` and `databaseId`**.

**Why:** viewer plugins build download URLs as
`${config.api}database/${databaseId}/assets/${assetId}/download/stream/${fileKey}`. With a
single shared pair, files from different assets cannot be streamed in one viewer instance —
this is the root of the single-asset constraint (old §7.6). Moving the pair onto each file
removes that constraint and is the prerequisite for the multi-select viewer mode (Decision #4)
and, later, cross-asset/comparison modes.

**Contract change — extend `FileInfo` with required asset context:**
```typescript
// web/src/visualizerPlugin/core/types.ts
export interface FileInfo {
    filename: string;
    key: string;
    isDirectory: boolean;
    assetId: string;        // NEW — per-file owning asset
    databaseId: string;     // NEW — per-file owning database
    versionId?: string;
    size?: number;
    dateCreatedCurrentVersion?: string;
    isArchived?: boolean;
    primaryType?: string | null;
    previewFile?: string;
}
```

**Systematic propagation (all current viewers + their configs):**

| Layer | File(s) | Change |
|-------|---------|--------|
| Type | `visualizerPlugin/core/types.ts` | Add `assetId`/`databaseId` to `FileInfo`. Viewers receive context **per file** (e.g., via the file objects / a `multiFiles: FileInfo[]` prop) instead of relying solely on top-level `assetId`/`databaseId`. |
| Router | `visualizerPlugin/components/DynamicViewer.tsx` | Stop assuming one asset for `multiFileKeys`. Pass each file's `{assetId, databaseId, key}` through so the active viewer can build a correct per-file stream URL. |
| Viewers | every plugin under `visualizerPlugin/viewers/*` that streams files (ThreeJS, Potree, BabylonJS/PlayCanvas splat, Cesium, Online3D, Needle USD, Image, Video, Audio, PDF, Columnar, Text, HTML, VNTANA, VEERUM, Preview) | Build the download URL from **each file's own** `assetId`/`databaseId`, not a single shared pair. |
| Configs | `visualizerPlugin/config/viewerConfig.json`, `viewers/manifest.ts` | No new viewers; ensure `supportsMultiFile` accurately reflects each viewer. Single-file viewers continue to receive exactly one file. |

**Backward compatibility:** keep the top-level `assetId`/`databaseId` props on
`FileViewerModal`/`DynamicViewer` as an optional default/fallback for any single-file call
site, so existing callers (file tree, asset view) keep working unchanged. New file-search
call sites populate `FileInfo.assetId`/`databaseId` per row. This keeps Decision #2 intact —
asset view is untouched even though it shares these components.

> **Scope note (Decision #2):** This systematic change lives in shared visualizer code, so the
> asset view *references* the same types/components. That is acceptable — but **no asset-view
> code path is modified or newly wired** to per-file context as part of this effort. Asset view
> continues to pass a single `assetId`/`databaseId` via the fallback.

### 7.1 Building FileInfo from a Search Row

In file mode, every row already carries the data needed for a `FileInfo`. No `fetchAssetS3Files` call is required.

```typescript
// Maps a flattened file-mode search row → FileInfo
// NOTE (Decision #3): each row carries its own assetId/databaseId, so the FileInfo
// does too. This is what lets a multi-file selection span assets in one viewer call.
function searchRowToFileInfo(row: Record<string, any>): FileInfo {
    const key = row.str_key as string;
    const filename = key.split("/").pop() || key;
    return {
        filename,
        key,
        isDirectory: false,
        assetId: row.str_assetid,        // per-file owning asset (Decision #3)
        databaseId: row.str_databaseid,  // per-file owning database (Decision #3)
        size: row.num_filesize,
        dateCreatedCurrentVersion: row.date_lastmodified,
        isArchived: row.bool_archived === true,
        primaryType: row.str_primarytype ?? null,
        // versionId omitted → viewer uses current version
    };
}
```

> If `str_key` or `str_fileext` is missing on a row (data quality), treat the row as non-viewable (no eye icon, excluded from multi-view).

### 7.2 Viewer Compatibility Check (Use PluginRegistry, Not a Hardcoded List)

Rather than maintaining a separate `VIEWABLE_EXTENSIONS` constant that can drift from reality, query the existing `PluginRegistry`, which is the single source of truth for which extensions each viewer plugin supports.

```typescript
import { PluginRegistry } from "../../visualizerPlugin/core/PluginRegistry";

// True if ANY plugin can render this extension
function isViewableExtension(ext?: string): boolean {
    if (!ext) return false;
    const registry = PluginRegistry.getInstance();
    return registry.getCompatibleViewers([ext.toLowerCase()], false, false).length > 0;
}
```

The eye icon renders only when `isViewableExtension(row.str_fileext)` is true. For multi-select "View Selected", non-viewable rows are filtered out before constructing the `FileInfo[]`; if the resulting array is empty, show a notification ("No selected files can be visualized").

> If importing `PluginRegistry` into the search bundle is undesirable for code-splitting reasons, expose a lightweight `getSupportedExtensions()` helper from the visualizer plugin barrel and compare against it. Decision deferred to implementation; default is to use the registry directly since it is already a singleton.

### 7.3 Per-Row Eye Icon — Additive to the Existing Thumbnail (Decision #1)

**The eye icon does NOT replace the preview column or its thumbnail.** The existing preview
column already renders a thumbnail/preview image per row. We render the eye (view) button
**alongside** that thumbnail — both are visible. The eye button is only the *additional*
affordance; when a file isn't viewable, the eye is omitted but the thumbnail still renders
exactly as before.

```typescript
// The existing preview column is preserved. We augment its cell so the
// thumbnail (unchanged) and the new eye button render together.
const renderPreviewCell = (row: any) => (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
        {/* EXISTING thumbnail/preview image — unchanged, always rendered */}
        {renderExistingThumbnail(row)}

        {/* NEW additive eye button — only when the file is viewable */}
        {isViewableExtension(row.str_fileext) && (
            <Button
                variant="icon"
                iconName="view-full"
                ariaLabel={`Preview ${row.str_key}`}
                onClick={(e) => {
                    e.stopPropagation();
                    openViewer([searchRowToFileInfo(row)], row);
                }}
            />
        )}
    </SpaceBetween>
);

// File mode: augment the existing preview column's cell renderer (do not add a
// second column, do not remove the thumbnail). Asset mode is untouched (Decision #2).
const isFileMode = state?.filters?._rectype?.value === "file";
const columnDefinitions = isFileMode
    ? baseColumns.map((col) =>
          col.id === "preview" ? { ...col, cell: renderPreviewCell } : col
      )
    : baseColumns; // asset mode unchanged
```

> If file mode does not currently have a preview column at all, add one whose cell renders the
> same thumbnail used elsewhere **plus** the eye button — the principle is identical: the eye is
> additive to (never a replacement for) the thumbnail.

### 7.4 Multi-Select Viewer Mode (Decision #4)

Beyond the per-row eye icon (single-file peek), file search gains an explicit **multi-select
viewer mode**. The defining requirement is that the selection **persists across multiple
searches** — the user runs a search, adds some files, runs another search, adds more, then
views the whole accumulated set together. This is distinct from Cloudscape's built-in
`selectedItems`, which is tied to the current result set and resets when results change.

**Mode lifecycle (the three required capabilities):**

| Capability | Behavior |
|------------|----------|
| **Enter mode** | A "Multi-select to view" toggle in the file-search header enters viewer-selection mode. |
| **Add across searches** | While in mode, selecting rows (or an "Add to selection" affordance) appends viewable files to a **running selection that survives new searches / pagination**. A counter shows the running total. |
| **View together** | "View Selected (N)" opens `FileViewerModal` with the full accumulated `FileInfo[]`. |
| **Clear / exit** | "Clear selection" empties the running set; "Exit" leaves viewer-selection mode (and clears it). |

**Dedicated, search-independent selection state** (do **not** reuse `state.selectedItems`,
which resets per search). Add to the search reducer:

```typescript
// web/src/components/search/hooks/useSearchState.tsx
// New state slice for the persistent viewer selection
interface ViewerSelectionState {
    viewerSelectMode: boolean;          // are we in multi-select viewer mode?
    viewerSelection: FileInfo[];        // running selection, keyed by FileInfo.key
}

// New actions: ENTER_VIEWER_SELECT_MODE, EXIT_VIEWER_SELECT_MODE,
//              ADD_TO_VIEWER_SELECTION, REMOVE_FROM_VIEWER_SELECTION,
//              CLEAR_VIEWER_SELECTION
// Dedup by FileInfo.key when adding (same file from two searches = one entry).
```

**Header controls (file mode only — Decision #2):**

```tsx
{isFileMode && !viewerSelectMode && (
    <Button onClick={() => dispatch({ type: "ENTER_VIEWER_SELECT_MODE" })}>
        Multi-select to view
    </Button>
)}

{isFileMode && viewerSelectMode && (
    <SpaceBetween direction="horizontal" size="xs">
        <Button
            variant="primary"
            disabled={!viewerSelection.length}
            onClick={() => openViewer(viewerSelection)}
        >
            View Selected ({viewerSelection.length})
        </Button>
        <Button
            disabled={!viewerSelection.length}
            onClick={() => dispatch({ type: "CLEAR_VIEWER_SELECTION" })}
        >
            Clear selection
        </Button>
        <Button onClick={() => dispatch({ type: "EXIT_VIEWER_SELECT_MODE" })}>
            Exit
        </Button>
    </SpaceBetween>
)}
```

**Adding rows to the running selection.** In viewer-select mode, enable `selectionType="multi"`
in file mode and append the *viewable* rows of the current page to the running selection on
change (preserving prior picks from earlier searches):

```tsx
selectionType={isFileMode && viewerSelectMode ? "multi" : asAssetModeBefore}
onSelectionChange={({ detail }) => {
    if (isFileMode && viewerSelectMode) {
        const additions = detail.selectedItems
            .filter((r: any) => isViewableExtension(r.str_fileext))
            .map(searchRowToFileInfo);
        dispatch({ type: "ADD_TO_VIEWER_SELECTION", payload: additions }); // dedup by key
    }
}}
```

> The single-file eye icon (§7.3) remains available independent of this mode — it opens one
> file immediately without entering multi-select. Asset-mode selection behavior is unchanged
> (Decision #2).

### 7.5 Modal State & openViewer

Because each `FileInfo` now carries its own `assetId`/`databaseId` (§7.0), `openViewer` no
longer needs to derive and pass a single shared context. We still pass a top-level
`assetId`/`databaseId` (from the first file) purely as the **backward-compatible fallback** for
the modal/viewers; per-file context on each `FileInfo` is authoritative.

```tsx
const [viewerFiles, setViewerFiles] = useState<FileInfo[]>([]);
const [showViewerModal, setShowViewerModal] = useState(false);

const openViewer = (files: FileInfo[]) => {
    // Each FileInfo already has assetId/databaseId (Decision #3). No shared-asset assumption.
    setViewerFiles(files);
    setShowViewerModal(true);
};
```

```tsx
{showViewerModal && viewerFiles.length > 0 && (
    <FileViewerModal
        visible={showViewerModal}
        files={viewerFiles}
        // Fallback context only (single-file/legacy callers). Per-file context wins.
        databaseId={viewerFiles[0].databaseId}
        assetId={viewerFiles[0].assetId}
        onDismiss={() => {
            setShowViewerModal(false);
            setViewerFiles([]);
        }}
    />
)}
```

### 7.6 Mixed-Asset Selection (Resolved by Decision #3)

The original single-asset constraint existed only because `FileViewerModal` passed **one**
shared `assetId`/`databaseId` to all files. **Decision #3 removes that constraint** by giving
each `FileInfo` its own `assetId`/`databaseId`, so a viewer can stream files from different
assets within one modal instance.

| Selection | Behavior |
|-----------|----------|
| Single file (eye icon) | Always fine — one file, its own context |
| Multi-select, same asset | Open one modal; each file streams from its own (identical) context |
| Multi-select, spanning ≥2 assets | **Allowed** — each file streams from its own `assetId`/`databaseId`. No blocking guard. |

> **Viewer capability still applies.** Whether multiple files actually *render together* is
> governed by each viewer's `supportsMultiFile` flag and `DynamicViewer`'s negotiation — not by
> asset boundaries. A single-file-only viewer still receives one file. The cross-asset *capability*
> is enabled here; richer cross-asset UX (tabs, comparison) remains a Future Enhancement (§11)
> and is explicitly **not** built now (Decisions #5, #6).

The previous same-asset validation guard is **removed**. The only pre-open filter is
viewability (drop non-viewable rows; if the result is empty, notify "No selected files can be
visualized").

### 7.7 Why No Backend / API Changes

- File rows already include `str_key`, `str_fileext`, `str_assetid`, `str_databaseid`.
- `FileViewerModal` → `DynamicViewer` → viewer plugins perform their own asset-scoped file downloads using `assetId`/`databaseId`/`key`.
- Existing functions (`fetchAssetS3Files`, `fetchFileInfo`) are **not** needed for this flow.

### 7.8 Performance Considerations
- Zero added latency for single-file open (no fetch — direct from row).
- Viewer code is already lazy-loaded via the plugin system; modal mounts the viewer on open only.
- `versionId` is omitted, so viewers resolve the current version (matches existing file-view behavior from search).

---

## 8. Accessibility

| Requirement | Implementation |
|-------------|----------------|
| Keyboard navigation | Tab to eye icon / selection checkboxes / "View Selected"; Enter/Space to activate |
| Screen reader | `aria-label="Preview {str_key}"` on icon; bulk button announces count |
| Focus management | Focus trapped in modal when open, returns to trigger on close (Cloudscape Modal default) |
| Color contrast | Cloudscape design tokens (pre-validated) |
| Escape key | Closes modal (built into Cloudscape Modal / FileViewerModal) |

---

## 9. Testing Plan

| Test Type | Scope |
|-----------|-------|
| Unit | `searchRowToFileInfo` maps `str_assetid`/`str_databaseid` → `FileInfo.assetId`/`databaseId` (Decision #3) |
| Unit | `isViewableExtension` against registry; missing `str_key` → not viewable |
| Unit | Preview cell renders thumbnail **and** eye icon together; thumbnail still renders when not viewable (Decision #1, additive) |
| Unit | Eye icon / mode controls appear only in file mode; asset mode unchanged (Decision #2) |
| Unit | Viewer-selection reducer: enter/exit mode, add (dedup by key), remove, clear; selection **persists across simulated new searches** (Decision #4) |
| Unit | Per-file stream URL built from each file's own `assetId`/`databaseId` (Decision #3) |
| Integration | Click eye → modal opens with one correct FileInfo (its own context) |
| Integration | Multi-select mode → add across two searches → "View Selected" → modal with full accumulated set |
| Integration | Multi-select spanning ≥2 assets → opens (no block); each file streams from its own context (Decision #3) |
| Integration | Non-viewable-only selection → notification, no modal |
| Integration | Clear selection empties running set; Exit leaves mode and clears |
| E2E | file search → preview → close → search state + running selection preserved |
| Regression | Asset-mode selection/bulk actions unchanged; asset mode shows no eye affordance (Decision #2) |
| Regression | Existing single-file callers (file tree, asset view) still work via top-level fallback context |
| Visual | Modal with single-file vs multi-file viewers (glb, e57, image); thumbnail + eye coexist in row |
| Cross-browser | Chrome, Firefox, Safari, Edge |

---

## 10. Rollout & Feature Flag

Low risk (frontend-only, reuses proven viewer modal). A feature flag is optional.

```typescript
// Optional, only if gradual rollout / A-B desired
VAMS_APP_FEATURES.SEARCH_FILE_PREVIEW = "SEARCH_FILE_PREVIEW"
// Frontend: if (config.featuresEnabled.includes("SEARCH_FILE_PREVIEW")) { /* render preview column + action */ }
```

Recommendation: ship without a flag; the only behavior change is additive (eye column + file-mode selection).

---

## 11. Future Enhancements

| Enhancement | Description | Priority |
|-------------|-------------|----------|
| **Comparison mode + mode dropdown** | Side-by-side/split comparison of files, selected via a mode dropdown. **Explicitly deferred (Decision #5)** — complex because viewers support differing file counts. The per-file context (§7.0) and multi-file-capable viewers (Decision #6) are designed so this becomes an additive change later. | Deferred |
| **In-viewer mode system** | A full mode system inside viewers (comparison, grid, sequential, etc.). **Not built now (Decision #6)** — only the capability to *accept* multiple files is established. Any existing mode scaffolding in a viewer is left as-is. | Deferred |
| Cross-asset rich UX | Tabs/grouping for files from different assets. (Cross-asset *streaming* is already enabled by Decision #3; this is the richer presentation layer.) | Medium |
| Keyboard shortcuts | Arrow keys to step through file results while modal is open | Medium |
| Quick actions in modal | Download / Share link / Open full view buttons | Medium |
| Preload on hover | Begin viewer dependency load on row hover before click | Low |
| Asset-mode preview | Separate effort to bring previews to asset-mode search (out of scope here — Decision #2) | Low |

---

## 12. Dependencies & Constraints

- **Scope (Decision #2):** File-mode asset file search only (`_rectype === "file"`). Asset view is **not modified**; shared visualizer code may be *referenced* by asset view but nothing new is wired to it.
- **No backend changes** required.
- **Per-file asset context (Decision #3):** Each `FileInfo` carries its own `assetId`/`databaseId`. The old single-asset constraint is **removed**; cross-asset multi-selection streams correctly. Top-level `assetId`/`databaseId` remain as a backward-compatible fallback for legacy single-file callers.
- **Additive preview (Decision #1):** The eye icon supplements the existing thumbnail; the thumbnail is never removed.
- **Multi-select mode (Decision #4):** Selection persists across searches via a dedicated reducer slice (not `selectedItems`), with explicit clear/exit.
- **Deferred (Decisions #5, #6):** No comparison mode, no mode dropdown, no in-viewer mode system. Viewers are only made capable of accepting multiple files.
- **FileViewerModal edge case:** empty `files[]` must never be passed (guarded before opening).
- **Search index:** relies on `str_key`, `str_fileext`, `str_assetid`, `str_databaseid` being present on file-mode hits (already indexed).
- **GovCloud:** no CloudFront-specific URLs in viewer logic (already handled by existing plugins).

---

## Appendix A: Corrections from Initial Draft

This revision corrects the following inaccuracies found during codebase review:

| Initial draft claim | Correction |
|---------------------|------------|
| Main table is `AssetSearchTable.tsx` | Actual: `SearchPageListView.tsx` (+ `ModernSearchContainer.tsx`, `SearchPage.tsx`) |
| `FileViewerModal` at `web/src/components/viewers/` | Actual: `web/src/components/filemanager/modals/FileViewerModal.tsx` |
| `ViewerFileSelection.tsx` exists | Does not exist; `ViewerSelector.tsx` selects the viewer plugin, not files |
| `FileViewerModal` has `initialFileIndex` | Actual props: `visible, onDismiss, files, databaseId, assetId, assetVersionId?` |
| Use `APIService.getAssetFiles(...)` | No such method; not needed — file rows already carry `str_key`/`str_fileext`. (Asset-mode would use `fetchAssetS3Files`.) |
| Hardcode `VIEWABLE_EXTENSIONS` | Prefer `PluginRegistry.getCompatibleViewers(...)` as source of truth |
| Scope = asset preview generally | Scope narrowed to **file-mode search only** |
| Multi-select not considered | Now covered: file-mode multi-select + "View Selected"; FileViewerModal already supports `FileInfo[]` |

## Appendix B: 2026-06-22 Scope Revision (Decisions §0)

This revision applies six authoritative decisions (see §0). Net changes from the prior draft:

| Prior draft assumption | This revision |
|------------------------|---------------|
| Eye icon could replace the preview column | **Additive only** — eye renders alongside the existing thumbnail (Decision #1) |
| File mode may touch shared/asset code paths broadly | **File search only**; asset view referenced but **not wired/modified** (Decision #2) |
| One `assetId`/`databaseId` for the whole file array | **Per-file `assetId`/`databaseId`** on `FileInfo`; systematic across all viewers + configs, with a top-level fallback (Decision #3) |
| "View Selected" over Cloudscape `selectedItems` (resets per search) | **Persistent multi-select viewer mode** — accumulate across searches, view together, clear/exit (Decision #4) |
| Comparison mode + mode dropdown in scope | **Deferred** to Future Enhancements (Decision #5) |
| Full in-viewer mode system | **Not built**; viewers only made capable of accepting multiple files; existing scaffolding left as-is (Decision #6) |
| Cross-asset multi-select blocked (old §7.6 guard) | **Allowed** — guard removed; each file streams from its own context (consequence of Decision #3) |
