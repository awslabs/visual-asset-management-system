# CLAUDE.md — VAMS Playwright End-to-End Tests

> Auto-loaded when Claude Code works within `web/e2e/`. Covers what belongs in this directory, how to
> write specs that survive any sandbox, and the boundary between **tracked core specs** and
> **untracked ad-hoc specs**. For frontend patterns see `web/CLAUDE.md`.

---

## What these tests are for

Playwright drives the **deployed** application, so it is the only layer that proves a frontend change
reached users. Jest proves a component behaves; Playwright proves the built, published bundle behaves.

:::danger[A source-only fix proves nothing here]
These specs run against `E2E_BASE_URL`, which is **required** — `playwright.config.ts` throws when it
is unset rather than falling back to a hard-coded host, so a run cannot silently target the wrong
deployment. A fix that exists only in `web/src/` will still fail — the front end must be rebuilt and
published:

```bash
cd web && npm run build     # then deploy: cd infra && npx cdk deploy --all
```

Confirm the deployment took by matching the served main-bundle hash to the local one:

```bash
curl -s https://<host>/index.html | grep -oE '/assets/index-[A-Za-z0-9_-]+\.js'
ls web/dist/assets/index-*.js
```

A passing jest suite says nothing about what is deployed. This exact gap once hid every web fix in a
release: the source had the fix, the served bundle did not.
:::

---

## Two kinds of spec — know which you are writing

|           | **Core specs (tracked)**                               | **Ad-hoc specs (untracked)**                                |
| --------- | ------------------------------------------------------ | ----------------------------------------------------------- |
| Purpose   | Permanent smoke coverage of a page or shared component | Prove one specific change / fix works                       |
| Lifetime  | Lives with the page; updated when the page changes     | Deleted or left untracked after the change ships            |
| Data      | **Must not require specific data**                     | May target known seed data                                  |
| Naming    | `orchestration.{page}.spec.ts`, `viewers.spec.ts`      | `*.reviewfixes.spec.ts`, `_probe.spec.ts`, anything scratch |
| Committed | Yes                                                    | No — keep out of git                                        |

**Add a core spec only when a new page or shared component is added.** A fix to existing behavior gets
an ad-hoc spec, or a new assertion inside the relevant core spec if the behavior is permanent.

Prefix throwaway probe specs with `_` (e.g. `_probe.spec.ts`) and delete them when finished — a probe
left behind runs in every future suite.

---

## Rule 1: Core specs must pass against ANY environment

A sandbox may be empty, freshly seeded, or years old. A core spec must not depend on a particular
pipeline, workflow, execution, asset, or count existing.

```ts
// ✅ CORRECT — derive the subject from whatever exists, skip when there is none
const id = await firstCardId(page);
test.skip(!id, "No pipelines in this environment");
const items = await openCardMenu(page, id!);

// ❌ INCORRECT — hardcoded seed data; fails on a fresh sandbox and rots when seeds change
await openCardMenu(page, "wseed-pipe-006");
await expect(page.getByText(/wseed-wf-\d+/).first()).toBeVisible();
```

Never wait on data to prove the page loaded — wait on the page's own heading, then assert that the
list rendered **either rows or its empty state**:

```ts
await gotoOrchestration(page, "pipelines", "Pipelines"); // waits on the h1
await expectTableRendered(page); // rows OR empty state — both valid
```

`test.skip()` with a reason is the correct outcome when a precondition is genuinely absent. A skipped
test is honest; a test that passes because its assertion never ran is not.

A spec that needs a file of a particular type looks one up with `findAssetFiles` and skips when the
returned map has no entry for that extension — never a fixed database and asset id.

**Why this matters concretely:** the executions board is newest-first and server-paginated (pageSize
50). A spec that waited for a specific seeded workflow id passed for weeks, then failed once newer
executions pushed that id off page 1 — the app was fine, the assumption was not.

---

## Rule 2: Core specs must not mutate the environment

They run against shared sandboxes. Open dialogs and wizards to assert they work, then dismiss:

```ts
await items
    .filter({ hasText: /Execute/ })
    .first()
    .click();
await expect(page.getByRole("dialog")).toBeVisible();
await page.keyboard.press("Escape"); // never Launch
```

For destructive actions, assert the **confirm** appears and says the right thing, then dismiss —
capturing a native `confirm()` without accepting it:

```ts
let confirmText = "";
page.once("dialog", async (d) => {
    confirmText = d.message();
    await d.dismiss();
});
await deleteButton.click();
await expect.poll(() => confirmText).toMatch(/cannot be undone/i);
```

An ad-hoc spec may create and clean up its own throwaway data. A core spec may not.

---

## Rule 3: Use the shared harness, don't re-derive selectors

`support/fixtures.ts` holds the durable selector knowledge. Import from it rather than rewriting
locators — the app's markup is not always guessable, and these were established empirically.

| Helper                                                        | Use for                                                                                            |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `gotoOrchestration(page, route, heading)`                     | Navigate + wait for first load (no data dependency)                                                |
| `orchestrationRoot(page)`                                     | The `.orchestration-root` container that scopes selectors on orchestration pages                   |
| `searchBox(page)`                                             | The orchestration filter-bar search input                                                          |
| `facet(page, label)`                                          | A native `<select>` filter                                                                         |
| `firstCardId(page)`                                           | Id of the first card, or `null` when the list is empty                                             |
| `openCardMenu(page, id)`                                      | Filter to a card and open its actions menu                                                         |
| `tableRows(page)` / `expectTableRendered(page)`               | Table rows / "rendered in any environment" assertion                                               |
| `menuSurface(items)`                                          | The open menu's own floating surface, from an item                                                 |
| `wizardRail(page)`                                            | The execute dialog's step rail (`navigation` "Execution steps")                                    |
| `rowValue(page, label)`                                       | The value cell of a label/value row in a detail panel                                              |
| `collectPageErrors(page)`                                     | Uncaught page errors, for crash-regression assertions                                              |
| `openAssetFile(page, databaseId, assetId, filename)`          | Open an asset's file in the File Visualizer through the File Manager, as a user does               |
| `treeNode(page, name)`                                        | A File Manager tree row by name, in the visible tree, for files and folders alike                  |
| `chooseViewer(page, name)`                                    | Pick a File Visualizer viewer matching a RegExp, by keyboard, and confirm it applied               |
| `chooseSelectOption(page, trigger, option, describeTrigger?)` | Choose a Cloudscape Select option matching a RegExp, by keyboard, from its trigger locator         |
| `apiContext(page)`                                            | The app's API base and Cognito ID token, for authenticated `page.request` calls                    |
| `findAssetFiles(page, extensions, options)`                   | First file per extension found via the API, or `null` without an API context                       |
| `findNestedAssetFile(page, options)`                          | First file inside a folder via the API, plus its stored key when known, or `null`                  |
| `findAssetsWithFiles(page, count, options)`                   | Up to `count` distributable assets with one top-level file each via the API, or `null`             |
| `selectedTreeNode(page)`                                      | The File Manager tree's single-selected row label, in the visible tree                             |
| `VIEWPORTS`                                                   | The layout widths: phone 390, tablet 768, laptop 1280, desktop 1440 (use with `test.use`)          |
| `pageOverflowX(page)` / `expectNoPageOverflowX(page, where)`  | How far the page scrolls sideways / assert it does not, naming the widest element                  |
| `expectReachable(locator, what)`                              | Scroll a control into view and assert it lies fully inside the viewport                            |
| `viewerSamples()`                                             | The `E2E_VIEWER_SAMPLES` seed fixture (seeded public viewer samples), or `null` when unset         |
| `openViewerFile(page, ref)`                                   | Load a file's ViewFile route as a fresh page and wait for its heading (one reload)                 |
| `pickViewerIfAsked(page, name, select?)`                      | Choose viewer `name` (config name) if asked, then ALWAYS assert it is the one selected             |
| `expectViewerSelected(page, name)`                            | Assert the picker shows viewer `name` and no viewer placeholder remains                            |
| `dataGrid(scope)`                                             | The Columnar viewer's react-data-grid root; scope header and row queries to it                     |
| `featuresEnabled(page)` / `missingFeatures(list, req)`        | The deployment's `featuresEnabled` list / the required features it lacks                           |
| `watchConsoleErrors(page, ignore?)`                           | Collect `console.error` and page errors not matching `CONSOLE_ERROR_IGNORE` (or your list)         |
| `largestCanvas(scope)`                                        | The largest visible `<canvas>`, a viewer's rendering surface                                       |
| `canvasStats(page, canvas, timeouts?)`                        | Pixel stats via `captureCanvas`: element shot, then bounded page clip of the canvas box            |
| `expectCanvasRendered(page, canvas, what, thresholds?)`       | Poll (90 s) until a canvas shows content rather than its clear colour                              |
| `watchCspViolations(page)`                                    | Record `securitypolicyviolation` events in the page and its frames (call before navigating)        |
| `expectNoNewCspViolations(csp, allowed, what)`                | Fail on any recorded violation no `allowed` pattern matches (`unexpectedCspViolations` lists them) |
| `watchRequestHosts(page, { extraHosts })`                     | Record request hosts; `thirdParty()` lists those outside the deployment                            |
| `expectSuperSplatRendered(page, iframe, what)`                | SuperSplat Editor drew the file: splat count > 0, canvas differs from the empty editor's           |
| `SUPERSPLAT_CSP_ALLOWED` / `CSP_CONSOLE_REPORT`               | The editor page's baseline CSP violations / the console copy of a CSP report                       |

**Selector facts worth not rediscovering:**

-   The orchestration search input is `getByLabel("Search")`; match it by label, not by its placeholder
    ("Search pipelines", "Search workflows", "Search executions"), and note there are
    **two** "Search" inputs on the page (filter bar + global nav). Scope to `.orchestration-root`.
-   Filters are native `<select>`s labelled `Status`, `Execution Type`, `Database`, `Group by`,
    `Filter by status`, `Filter by trigger`, `Filter by group ID`, `Time window`.
-   Cards expose `Actions for {id}` buttons — the stable way to read a card's identity.
-   The template panel heading is an `h1` reading exactly `Templates`.
-   Card lists are server-paginated (pageSize 50); filter down to a card before locating it.
-   Execution trigger values use the **stored** vocabulary (`Manual`, `File-Upload`) — not `fileUpload`.
-   The **pipeline form is a three-step wizard** ("1 Basic", "2 Execution", "3 Settings") and opens on
    Basic. Fields on a later step do not exist in the DOM until you advance with the form's own `Next`
    button. Do **not** locate a step by name: `getByRole("button", { name: /Settings/ })` matches the
    global navigation's Settings button, not the step. The metadata-input toggles are on Settings.
-   The **execute flow is one dialog**: from the Executions board's `Execute workflow` button and the
    file manager's Automation menu its first step is the workflow picker — rows are `role=option` in a
    `role=listbox`, the search input is labelled `Workflow` — and `Continue` swaps in the wizard steps
    inside the same `role=dialog`. From a workflow card's Execute action the picker step is skipped.
    Every step shows the rail (`wizardRail(page)`); visited rows are buttons. The **`Launch` button
    exists only on the final step**: an assertion about the Inputs step must target `Next`; looking for
    `Launch` there finds nothing.
-   The executions board names the workflow's database `Workflow Database` (there is also an Output
    Type / Output Database / Output Asset ID group) and has **no** `Group` column.
-   **`[role="menu"]` is ambiguous on every page.** Each closed Cloudscape Select / ButtonDropdown keeps
    a zero-size `<ul role="menu">` in the DOM, and those come BEFORE the portalled Radix menu in
    document order — so `.first()` resolves to a hidden one whose background is `rgba(0, 0, 0, 0)`. Use
    `menuSurface(items)` to reach the open surface from a visible item.
-   Detail and quick-view panels render a field as a label span plus its value span, so a field's value
    is the label's next sibling. Use `rowValue(page, label)`; a page-wide `getByText` for a value's
    shape (a path, a slash, an id) matches dozens of unrelated elements.

When a page's markup changes, fix the helper once; every spec follows.

---

## Rule 4: Assert behavior, not implementation

Prefer role-based, user-visible assertions. Two cases deserve stronger checks than "is visible":

**Crash regressions** — assert no uncaught errors, not just that something rendered:

```ts
const errors = collectPageErrors(page);
await expectTableRendered(page);
expect(errors, `page errors: ${errors.join(" | ")}`).toHaveLength(0);
```

**Layering** — a z-index alone does not prove a dialog is clickable. Confirm it actually receives the
click (the Radix dialog once rendered _beneath_ the fixed Cloudscape TopNavigation):

```ts
const hit = await dialog.evaluate((el) => {
    const r = (el as HTMLElement).getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + 8);
    return !!top && (el as HTMLElement).contains(top);
});
expect(hit).toBe(true);
```

---

## Viewer render proof (shared by every viewer PR)

`viewers.spec.ts` "viewer render proof (seeded samples)" proves a viewer DRAWS a known file, not only
that it mounts. Add a viewer's cases to `renderCases` there; do not start another spec or harness.

**Samples.** Every subject is an entry of `e2e/fixtures/viewer-samples.json` (tracked; schema checked by
`web/src/visualizerPlugin/config/viewerSamplesManifest.test.ts`). An entry has `id`, `viewer` (a viewer id
from the viewer configs, or `none` for the subject of a `not-offered` case that no viewer may open), `path` (asset-relative key with a leading slash; folders allowed), `licence` and
`source`, and EXACTLY ONE of:

| Kind      | Fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Download  | `url` (https, pinned to a tag or commit), `size`, `sha256`; the seeder refuses a mismatch                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Generated | `generated`: `off-cube` or `text` (`content` sets the text; default three numbered lines)                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Derived   | `derivedFrom` (an id, or a list for `zip`) and `transform`: `prepend-utf8-bom`, `copy`, `utf16le-bom` / `utf16be-bom` / `utf16le` (the source's UTF-8 re-encoded, with or without a byte order mark), `zip` (optional `zipEntries` names), `splat-transform` (`version` pinned, optional `args`; output format from `path`'s extension), `ply-to-splat` (a float 3DGS `.ply` as raw `.splat` rows, which splat-transform cannot write), or `ksplat` (`.ply` to `.ksplat` with `@mkkellogg/gaussian-splats-3d`: `version` and `threeVersion` pinned, optional `level`) |

Optional: `requiresFeature` (a string or list of `featuresEnabled` entries, such as `ALLOWUNSAFEEVAL` or
`PHYSNA_ADDON`; a case skips with a reason when the deployment lacks one) and `expect` (free-form values the
case reads; `expect.minForegroundFraction` / `expect.minDistinctColours` lower a canvas case's render
thresholds for a model that fills little of the canvas, such as the splat samples' unicorn at about
0.6% of the pixels). VNTANA and VEERUM have no feature flag (they are enabled in `addonViewerConfig.json` at build
time), so their cases must skip themselves when that viewer is not offered. Verify the licence at its
source before adding a download; no sample binary is ever committed.

**Seeding.** `tools/VamsCLI/examples/seed_viewer_samples.py --profile <name> [--samples id,id] [--cache dir]`
validates the manifest, produces the samples (with `--samples`, a derived sample's sources are produced but not uploaded), uploads them
to one distributable asset (`e2e-viewers-db` / `e2e-viewers-samples`) and writes
`e2e/fixtures/viewer-samples-seed.json`; export its path as `E2E_VIEWER_SAMPLES`. `splat-transform` runs
through `npm exec --min-release-age=7` at the pinned version.

**A case** names `sample`, `viewer` (the viewer's exact config `name`; the case asserts that viewer
opened, and a web jest test checks the name), `kind` (`canvas`, `pdf`, `grid`, `text`, `text-binary`, `text-encoding`, `not-offered`, `refused` — the viewer's refusal message), and optionally
`select` (viewer picker RegExp), `requiresFeature` / `withoutFeature`, `fetches` (a URL part a response
must succeed for), `thresholds`, `cspAllowed`, `consoleAllowed` (console lines that case alone may log,
such as Chromium's own `416` line for an empty file; never widen `CONSOLE_ERROR_IGNORE` for one case)
and `reads` (the statuses, in order, of the presigned GETs of the sample's file: `[206]`, `[206, 200]`
or `[416]`, how the bounded text loader read it). A text sample's `viewer` must list the sample's
`extensionToken` (so `.gitignore` is `(none)`, `.env.example` is `.example`); the manifest jest test
checks it. Every case fails on console or page errors
(`watchConsoleErrors`), on any CSP violation outside `cspAllowed` (`expectNoNewCspViolations`; list the
development baseline's violations EXACTLY, so only new ones fail) and on any host outside the deployment
(`watchRequestHosts`). The `supersplat` kind checks inside the editor's iframe with
`expectSuperSplatRendered` and passes `SUPERSPLAT_CSP_ALLOWED` as `cspAllowed`. Run the spec on the development build first and record the pixel stats it logs;
a case that passes there and fails on a change is a regression.

---

## Running the suite

```bash
cd web
export E2E_BASE_URL=https://<your-deployment-host>   # required; the config throws without it
export E2E_USERNAME=<user> E2E_PASSWORD=<pass>       # never hardcode credentials
npm run e2e                                           # all specs
npm run e2e:headed                                    # watch a run
npx playwright test e2e/orchestration.pipelines.spec.ts --retries=0 --workers=2
E2E_BASE_URL=http://localhost:3001 npm run e2e        # against a local dev server
E2E_VIEWER_ASSET=<databaseId>/<assetId> npx playwright test e2e/viewers.spec.ts  # one fixture asset
E2E_WEBGPU=1 E2E_REQUIRE_WEBGPU=1 npx playwright test e2e/viewers.spec.ts       # SuperSplat render checks
```

The SuperSplat Editor renders only through WebGPU, which headless Chromium hides. `E2E_WEBGPU=1` runs
the SuperSplat cases in a separate `chromium-webgpu` project launched with WebGPU on, and takes them out
of the `chromium` project, so every other case still runs in a browser without WebGPU. Without an
adapter the SuperSplat cases skip with the reason; `E2E_REQUIRE_WEBGPU=1` makes that a failure. On a machine with no GPU, Chromium's software adapter loses the device while the editor
loads a file (v3.1.1 and v3.7.0 alike), so run the render checks where a GPU is available.

`retries: 1` is configured, so a spec that fails then passes is reported as **flaky**, not failed —
and Playwright's own exit code counts flaky as success. Read the summary line, not the exit status:
wrapping the run in a shell pipeline makes the exit code the _pipeline's_, which is how a run with
25 failures can appear to have exited 0.

`auth.setup.ts` logs in once through the Amplify Authenticator and saves `storageState` to
`e2e/.auth/admin.json`; every other spec reuses it. The state is reused for 45 minutes — repeated
rapid Cognito SRP logins can trip the edge WAF with a 403. Force a refresh with `E2E_FORCE_LOGIN=1`
or by deleting the file. If the account has MFA, run `npm run e2e:auth` headed once and complete the
challenge manually.

**`auth.setup.ts` is the one harness every spec depends on — keep it tracked and working.**

---

## Adding a core spec for a new page

1. Add the page's route to the `gotoOrchestration` union in `support/fixtures.ts` if it is a new
   orchestration route.
2. Create `orchestration.{page}.spec.ts` and assert, in this order: the page renders without a crash;
   its controls exist; its filters offer the expected values; and one interaction per row action.
3. Derive every subject from the environment (Rule 1) and mutate nothing (Rule 2).
4. Add any new selector knowledge to `support/fixtures.ts`, not inline in the spec.
5. Run against a deployed environment and confirm the result is `passed` or an explicit `skipped` —
   never a silent pass.

---

## Anti-patterns

1. **Hardcoding seed ids** (`wseed-pipe-006`) in a core spec — see Rule 1.
2. **Waiting on data to prove page load** — wait on the heading; data may legitimately be absent.
3. **Mutating shared state** in a core spec (launching an execution, archiving, deleting) — Rule 2.
4. **Re-deriving selectors inline** instead of importing from `support/fixtures.ts` — Rule 3.
5. **Committing an ad-hoc or `_probe` spec** — these are per-change and stay untracked.
6. **Hardcoding credentials or the base URL** — use `E2E_USERNAME` / `E2E_PASSWORD` / `E2E_BASE_URL`.
7. **Claiming a web fix is verified without checking the deployed bundle hash** — see the warning above.
