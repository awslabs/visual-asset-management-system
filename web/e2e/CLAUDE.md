# CLAUDE.md — VAMS Playwright End-to-End Tests

> Auto-loaded when Claude Code works within `web/e2e/`. Covers what belongs in this directory, how to
> write specs that survive any sandbox, and the boundary between **tracked core specs** and
> **untracked ad-hoc specs**. For frontend patterns see `web/CLAUDE.md`.

---

## What these tests are for

Playwright drives the **deployed** application, so it is the only layer that proves a frontend change
reached users. Jest proves a component behaves; Playwright proves the built, published bundle behaves.

:::danger[A source-only fix proves nothing here]
These specs run against `E2E_BASE_URL` (default `https://vams5.scheurik.people.aws.dev`). A fix that
exists only in `web/src/` will still fail — the front end must be rebuilt and published:

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

| Helper                                          | Use for                                                |
| ----------------------------------------------- | ------------------------------------------------------ |
| `gotoOrchestration(page, route, heading)`       | Navigate + wait for first load (no data dependency)    |
| `searchBox(page)`                               | The orchestration filter-bar search input              |
| `facet(page, label)`                            | A native `<select>` filter                             |
| `firstCardId(page)`                             | Id of the first card, or `null` when the list is empty |
| `openCardMenu(page, id)`                        | Filter to a card and open its actions menu             |
| `tableRows(page)` / `expectTableRendered(page)` | Table rows / "rendered in any environment" assertion   |
| `menuSurface(items)`                            | The open menu's own floating surface, from an item     |
| `rowValue(page, label)`                         | The value cell of a label/value row in a detail panel  |
| `collectPageErrors(page)`                       | Uncaught page errors, for crash-regression assertions  |

**Selector facts worth not rediscovering:**

-   The orchestration search input is `getByLabel("Search")` — it has **no placeholder**, and there are
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
-   The **execute wizard's `Launch` button exists only on the final step**. An assertion about the input
    stage must target `Next`; looking for `Launch` there finds nothing.
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

## Running the suite

```bash
cd web
export E2E_USERNAME=<user> E2E_PASSWORD=<pass>   # never hardcode credentials
npm run e2e                                       # all specs
npm run e2e:headed                                # watch a run
npx playwright test e2e/orchestration.pipelines.spec.ts --retries=0 --workers=2
E2E_BASE_URL=http://localhost:3001 npm run e2e    # against a local dev server
```

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
