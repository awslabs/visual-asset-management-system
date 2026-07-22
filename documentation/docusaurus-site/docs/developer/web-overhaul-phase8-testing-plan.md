---
title: Web Overhaul Phase 8 — Seed + Playwright + Smoke Testing Plan
description: Plan for bulk-seeding prod14 and live-testing the new pipeline/workflow/execution UI end-to-end.
---

# Web Overhaul Phase 8 — Seed + Playwright + Smoke Testing Plan

> **Status:** Plan, pending execution. Prereq: Phases 1–7 complete + full pre-deploy review fixed
> (all 3 Critical / 17 Important / 15 Minor resolved; 262 unit tests green; build clean).
> **Target:** prod14 (`vams5.scheurik.people.aws.dev`, us-west-2, acct 008971672901), profile
> `aws-pan-spatial-computing+vams-app-Admin`, user `scheurik`.

The new orchestration UI is feature-complete and unit-tested. Phase 8 verifies it **live in a real
browser against the real backend**, at a data scale that forces pagination/grouping/filtering, and
confirms permission graying with a constrained user.

---

## 0. Prerequisites & environment

-   **Deploy:** the web changes are un-deployed (they live on `feature/pipelinesOverhaul`, un-pushed).
    The backend/API on prod14 is already the V2 overhaul (tested in Phases 4–6), so the new UI can run
    against it. Two options for serving the new UI:
    1. **Local dev server (recommended for iteration):** `cd web && npm run start` (port 3001), with
       `DEV_API_ENDPOINT` in `web/src/config.ts` pointed at the prod14 API (or Vite proxy `/api/*`).
       Fastest loop; no deploy.
    2. **Deploy build to prod14** (`cd infra && npx cdk deploy` of the static web stack) — closer to
       production but slower. Do this only after the local Playwright pass is green.
-   **Permission-template note:** the new `/executions` web route was added to the permission templates
    (Task 7.2) but those template files are **not auto-applied** — for the constrained-user permission
    test (spec 5 below) the `smoke-ro` role's constraints must include `/executions` web access. Re-run
    the Phase-6 `phase6_permissions.py` role setup (it builds `smoke-ro` from `global-readonly.json`,
    which now grants `/executions`) so the constrained user can reach the page.

---

## 1. Task S1 — Bulk seed script (`tools/smoketest/web_seed_bulk.py`)

**Goal:** create enough data to force multi-page pagination, category grouping, and every
filter/sort/group path — MODERATE scale per the design decision.

**Reuse:** the auth + `call()` helper from `tools/smoketest/overhaul_api_param_matrix.py` (reads
`%APPDATA%/vamscli/profiles/default/{config.json,auth_profile.json}` for `api_gateway_url` +
`id_token`; `call(method, path, body, qs)` with the Bearer header). Refresh the token first via
`python -m vamscli auth login --username scheurik --password '!11111111q1Q'`.

**What to create (idempotent — use a `webseed` id prefix so re-runs are detectable; skip if exists):**

-   **~80 pipelines** across ~6 categories (`conversion`, `genai`, `preview`, `simulation`,
    `metadata`, `misc`): mostly Lambda (pointing at the existing `vams-smoke-mock-*` lambdas), a handful
    SQS / EventBridge, and 2–3 DeadlineCloud (feature is enabled on prod14). Vary `enabled`/`archived`
    (~10% archived) to exercise the archived toggle. Give ~15 of them 1–2 templates each with a
    tagSchema (mix of the 6 tag types) so the template editor + wizard have real data.
-   **~50 workflows** across the same categories, referencing 1–4 pipelines each; a few results-only
    (`locationType:none` + `inputFileArity:none`); a few with `subDashboardUrl` set (Dashboard link);
    ~10% archived; assign a `fileUpload` trigger to ~5.
-   **~300 executions** across statuses/groups/assets: launch mock workflows (fast callback) to produce
    SUCCEEDED; a batch via `mock-fail` for FAILED; a couple aborted (launch + abort) for ABORTED; some
    sharing an `executionGroupId` (group filtering); spread across `smoke-db` assets so the asset-tab
    and workflow-filtered views have data. (Executions accrue from launching the seeded workflows in a
    loop; cap the launch rate to avoid the WAF/upload path — these are asset-less/results-only launches,
    so no large bodies.)

**Output:** print counts created + a few sample ids (a workflow with a Dashboard link, a
results-only workflow, an execution group id) for the Playwright specs to target. Leave the data in
place (it's seed data, like the existing mocks). Do NOT commit the seed output.

**Gate:** the script runs clean and `GET /pipelines`, `GET /workflows`, `GET /workflows/executions`
return the expected inflated counts (verify via the CLI or a direct call).

---

## 2. Task S2 — Playwright harness setup (`web/playwright.config.ts` + auth storageState)

-   Add dev deps: `@playwright/test`; `npx playwright install chromium`.
-   `web/playwright.config.ts`: `baseURL` = `http://localhost:3001` (dev server) — or the deployed URL
    if testing option 2; `use: { storageState: "web/e2e/.auth/state.json" }`; single chromium project;
    `webServer` optionally auto-starting `npm run start`.
-   **Auth handshake (the one interactive step):** the app uses Cognito SRP login (no headless
    password flow). Two approaches:
    1. **Recommended — reuse the SPA's stored session:** a one-time `web/e2e/auth.setup.ts` that
       navigates to the app, performs the Cognito login via the Amplify Authenticator UI once (using
       `scheurik` / the test password from an env var, NOT hardcoded), and saves `storageState`
       (localStorage holds the tokens the app reads). Subsequent specs reuse it. Because SRP + possible
       MFA can't always run fully headless, this setup may need a human to complete the first login;
       the saved `storageState` then drives all specs unattended.
    2. **Token injection:** read the CLI's `auth_profile.json` id_token and inject it into
       localStorage the way `Auth.tsx`/`appCache` expect, seeding a valid session without the UI login.
       Faster + fully unattended, but must exactly match the app's token storage keys — verify against
       `web/src/services/appCache.ts` + `authTokenUtils.ts`. Prefer this if the storage shape is
       stable.
-   Decide 1 vs 2 at execution time based on how cleanly the token can be injected.

---

## 3. Task S3 — Playwright spec suite (`web/e2e/orchestration.spec.ts`, grouped by area)

Each `test()` drives the real UI and asserts real outcomes. Group into files if it grows.

**Pipelines**

-   List renders category-grouped; the seed's ~80 pipelines force pagination — page through; group
    expand/collapse works.
-   Filter: free-text by name/id; facet by execution type (incl. DeadlineCloud facet only when the
    flag/existing-DC condition holds); enabled/archived toggle reveals/hides archived.
-   Create a Lambda pipeline; create a DeadlineCloud pipeline (farm/queue fields appear, callback
    locked Enabled); edit one; archive one (confirm dialog) → disappears from default list.
-   **DeadlineCloud disable-after-create:** (needs the flag toggled off — do this last / on a separate
    config, or assert the read-only-banner + hidden-Edit path via a pipeline whose type is DC while the
    facet shows it) — verify Edit is blocked/read-only and Archive still offered.
-   Template editor: open a pipeline's templates, create a template (Monaco config body + tagSchema
    builder with a reserved-key rejection), see the live DynamicTagForm preview; Create/Edit/Archive
    buttons gated.

**Workflows**

-   List category-grouped + filter + archived toggle; Dashboard link opens a new tab (assert
    `target=_blank`).
-   Builder: create a workflow, drag-reorder pipeline cards, pick per-pipeline default templates,
    set `locationType:none` and assert `inputFileArity` locks to `none` (hard coupling), see the DAG
    preview update, trigger a validation warning (add an archived pipeline) and confirm Save is
    blocked / warnings shown; save → appears in list.
-   Triggers sub-page: assign a `fileUpload` trigger with filters + default templates; enable/disable.
-   Execute from a workflow card → the in-place wizard opens (no navigation).

**Execute wizard (the centerpiece)**

-   From the Workflows page and from an asset tab: arity none/one/multi input selection; output-target
    override when allowed (locked otherwise); per-pipeline template select + tag form; custom-override
    path (when allowCustomTemplateOverride) + `allowCustomEdit` raw Monaco edit.
-   **Hard-error gate:** leave a required tag empty → Launch disabled + the pipeline named; a workflow
    with a disabled/archived pipeline → blocked + named. Satisfy → Launch enabled → launches; returned
    warnings surfaced; underlying list refetches and shows the new RUNNING execution (no navigation).

**Executions board + results**

-   Global page: ~300 executions force pagination (Load-more / infinite paging); filter by status /
    workflow / trigger / group; current-first ordering (RUNNING on top); live status transition
    (launch → watch RUNNING→SUCCEEDED via polling without manual refresh).
-   Row actions gated: Logs + Permanent-Delete hidden for non-admin (covered in spec 5), present for
    admin; Abort only on non-terminal; group-abort; rerun; permanent-delete confirm requires typing
    CONFIRM.
-   **Results:** quick-view drawer shows overall status/results text in-place; "Open full details" →
    `/executions/{id}` shows Inputs, per-Pipeline timeline with the exact rendered config body
    (`<pre>` by default, Monaco on toggle) + template/tags snapshot, Outputs (files/metadata/results),
    and admin-gated Logs.

**Asset tab**

-   On a `smoke-db` asset's Workflows tab: the embedded asset-scoped ExecutionsBoard shows the asset's
    executions with live polling; Execute button opens the in-place wizard pre-scoped to the asset.

**Permissions (constrained user)**

-   Log in (second storageState) as the `smoke-ro` user (from Phase-6 setup, which now grants
    `/executions` web). Assert: Pipelines/Workflows/Executions pages load (reads allowed); Create /
    Edit / Archive / Execute actions are HIDDEN/disabled; Logs + Permanent-Delete hidden; and that the
    data returned is the backend-filtered set.

**Cross-cutting**

-   Dark-mode toggle: new pages track `.awsui-dark-mode`; **assert no Tailwind bleed** — screenshot the
    existing Cloudscape Assets page in both modes and confirm no visual regression vs a baseline.

**Gate:** all specs pass; fix any UI bug found and re-run. Capture a short run summary.

---

## 4. Task S4 — Triage + fix any live findings, then finalize

-   Any bug Playwright surfaces → fix (subagent, TDD where logic), re-run the affected spec + unit
    suite, commit.
-   When green: update `.superpowers/sdd/progress.md`; note the transfer-timeout GPU observation is
    separate/non-blocking; list the two backend follow-ups (executions `includeArchived`/status list
    param; per-workflow execution-count summary field).
-   **Then the un-commit step (per the standing plan):** once ALL web dev + testing is accepted,
    `git reset --mixed 3f9e3d96` on `feature/pipelinesOverhaul` so the entire web overhaul rejoins the
    working tree as UNSTAGED changes alongside the existing unstaged smoke-test/config edits (no commits
    remain). This is the final step and requires explicit go-ahead.

---

## 5. Sequencing

1. S1 seed (script + run) → verify counts.
2. S2 harness + auth storageState.
3. S3 specs, run headless; iterate S4 fixes until green.
4. Optional: deploy build to prod14 + re-run a smoke subset against the deployed URL.
5. Un-commit to unstaged (explicit go-ahead).

## 6. Open decisions for execution time

-   Local dev server vs deploy for the Playwright target (recommend local first).
-   Auth: storageState-from-UI-login vs token-injection (recommend token-injection if the storage shape
    is clean).
-   Whether to toggle the DeadlineCloud feature flag off in a test config to exercise the
    disable-after-create path live, or rely on the unit tests for that branch.
