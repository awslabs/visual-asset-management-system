# CLAUDE.md - EDH Workstations Add-on (web module)

Auto-loaded when working within `web/src/addons/edh/`. The web half of the EDH Workstations add-on: its pages, components and API layer, and its entries in the add-on web lanes. See `web/CLAUDE.md` for the frontend-wide steering (section 7.5 for the lanes) and `.kiro/steering/ADDON_EDH_WORKSTATIONS.md` for the Kiro mirror (root `CLAUDE.md` Rule 11).

---

## Module Layout

The module is built with **Tailwind + Radix** and reuses the orchestration primitives: `features/orchestration/components/*` (DataTable, Dialog, ContextMenu, RefreshButton, SearchInput, StatusBadge, Stepper, ConfigEditor, controlStyles, ToastProvider), `permissions/useAllowedRoutes`, the `api/client` helpers `toTuple` and `unwrapMessage`, and `types`. The dependency runs one way: the module imports core, and core reaches the module only through the add-on lane files.

```
web/src/addons/edh/
  pages/          # Route shells: EdhWorkstationsPage.tsx (renders workstations/WorkstationsPage, title "Workstations") and
                  #   EdhAdminPage.tsx (renders admin/EdhAdminPage, title "Workstation Admin"); keep page logic out of them
  admin/          # EdhAdminPage.tsx (Sessions / Capacity / Images / AMI Recipes / Settings tabs, opening on the first one the
                  #   caller can read, each panel gated by useAllowedRoutes().can()) ImagesTab InstancesTab (the Sessions panel)
                  #   CapacityTab (each profile's allowed instance types grouped by architecture) RegisterImageDialog
                  #   ConfirmActionDialog AmiRecipesTab AmiRecipeFormDialog AmiBuildsPanel (the AMI Recipes tab, EDH_AMI_RECIPES;
                  #   Duplicate opens the form prefilled from any recipe) SettingsTab (the lease policy, GET/PUT /addon/edh/admin/settings,
                  #   and below it CapacityReservationsSection, shown on GET /addon/edh/admin/capacity-reservations: the registry
                  #   table with Register / Edit / Unregister, each gated on its own route; see Capacity Reservations)
  api/            # workstations.ts catalog.ts admin.ts containers.ts amiRecipes.ts (the apiClient callers)
                  #   queries.ts adminQueries.ts containerQueries.ts amiRecipeQueries.ts (TanStack hooks, module-local qk, polling cadence)
                  #   outcome.ts (EdhRequestError, toOutcomeTuple, unconfirmedNotice: a mutation EDH did not confirm, a start in progress;
                  #   isReservationFull and the RESERVATION_FULL launch and start sentences)
  components/     # WorkstationStatusBadge.tsx (also describeLastError, describeEdhFailure and EdhFailureDetail, see
                  #   Launch Failures; describeStartFailure and StartFailureNotice, see Start Status) CapacityDetail.tsx
                  #   (the row's `Capacity:` line) DeleteWorkstationDialog.tsx TabStrip.tsx
                  #   (roving-tabindex tablist shared by both pages)
                  #   EdhFeatureTabs.tsx NotEnabledPanel.tsx (both pages' "not enabled" notice) useTextFilter.ts (the toolbar search
                  #   the three EDH lists share) useInFlight.ts (the synchronous double-submit guard every EDH mutation handler claims)
                  #   WorkstationImageCell.tsx (My Workstations' Image cell: the name, the id and the Newer image badge, see
                  #   Image Name)
  workstations/   # WorkstationsPage.tsx (one h1 above the Workstations / Simulations strip; the Simulations tab is selected from the
                  #   path; Remaining column and Renew lease action driven by useEdhCatalogLease and labels.ts leaseRemaining)
                  #   simulationsTab.ts (the page's route and the Simulations route beneath it)
                  #   LaunchWorkstationWizard.tsx (optional `Expires in (hours)` field while the lease policy is enabled and not
                  #   forced; offers only the instance types that run the chosen image's `architecture` by the catalog rule and that
                  #   its `launchInstanceTypes` names when it carries one, clears a type the image chosen next does not run or
                  #   offer, and shows the architecture on the image card and in Review; a Capacity select after Instance type,
                  #   see Capacity Reservations)
                  #   ConnectLink.tsx (click-driven: opens the tab, then requests the URL) WindowsSignInDialog.tsx (the owner's
                  #   Windows user name and masked password, fetched on open through a non-cached mutation and reset on close;
                  #   opened from the row's Show Windows password action)
  containers/     # SimulationsTab.tsx: container catalog, job submit/list/kill per backend (EDH_CONTAINERS; eks experimental);
                  #   containerRoutes.ts (route templates)
  types.ts        # API shapes
  labels.ts       # The words shown for the OS family and EDH state tokens, a workstation's image name
                  #   (workstationImageName) and its newer-image badge (newerImageNotice)
  capacityReservations.ts  # The Capacity rules: which reservations suit a launch, the option and Review text, the
                  #   preselection, the view-only hint, the no-subnet note (edhSubnetNote) and the row's Capacity text
                  #   (describeCapacity)
  capacityDisplayCases.json  # Test data shared with the CLI: records with the agreed Capacity detail, and catalog
                  #   options with the agreed wizard option, Settings State cell and CLI STATE/LAUNCH columns (see
                  #   Capacity Reservations)
  workstationStatusCases.json  # Test data shared with the CLI: records of every state x activity x lastError of the
                  #   start-status contract with the agreed badge, State column and start-failure lines (see Start Status)
  workstationImageCases.json  # Test data shared with the CLI: records of the image fields with the agreed image name,
                  #   badge and tooltip, CLI Image column and `get` recipe lines (see Image Name)
  edhLanes.test.tsx  # The module's lane entries, read through the real route table and side navigation
```

---

## Lane Entries

| Lane             | File                        | EDH entries                                                                                                                                                                                                                                              |
| ---------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Routes           | `addons/addonRoutes.tsx`    | `/edh/workstations`, `/edh/workstations/simulations` (the same lazy page, opened on its Simulations tab) and `/edh/admin`; each `active` is the href of the nav link it highlights                                                                       |
| Navigation       | `addons/addonNavigation.ts` | "Virtual Workstations" (My Workstations, Simulations) in `addonNavMainSections`, after the core "Orchestrate & Automate" section; "Admin - Workstations" (Workstation Admin) in `addonNavAdminSections`, after the core admin sections and before "User" |
| Feature switches | `addons/addonFeatures.ts`   | `EDH_WORKSTATIONS`, `EDH_CONTAINERS`, `EDH_AMI_RECIPES`, each declared in `infra/common/addonAppFeatures.ts`                                                                                                                                             |
| Tailwind content | `web/tailwind.config.js`    | The one line `./src/addons/edh/**/*.{ts,tsx}` (the shells live in `pages/`, inside the glob)                                                                                                                                                             |

The web routes sit under `/edh/workstations` and `/edh/admin`, so the `web` grants in `edh-user.json`, `edh-admin.json` and the read-only default (`starts_with` `/edh/workstations`) cover the Simulations route too.

---

## Feature Switches

The pages read the switches through `useFeaturesEnabled()` (`context/RuntimeConfigContext.ts`), and the navigation entries carry them in `featuresEnabledRestriction`, so both follow the secure config when it lands after a first login.

-   `EDH_WORKSTATIONS` -- the Workstations and Workstation Admin pages and their two navigation sections. The web route exists in every deployment and the seeded admin role allows every route, while the API routes exist only where the add-on stack is deployed, so both pages render the "not enabled" panel without the switch rather than failing every request
-   `EDH_AMI_RECIPES` -- the **AMI Recipes** tab of the Workstation Admin page (`admin/AmiRecipesTab.tsx`); without it the tab is not in the strip
-   `EDH_CONTAINERS` -- the Simulations tab of the Workstations page and the "Simulations" nav link (`#/edh/workstations/simulations/`, restricted to `EDH_WORKSTATIONS` and `EDH_CONTAINERS`); the tab is shown to every caller and its panel is gated (`web/CLAUDE.md` 6.4): a caller who may not list jobs sees a no-access notice inside it

---

## Simulations Route

With containers on, the Workstations page shows the Workstations / Simulations strip under one heading. The Simulations tab has its own route beneath the page's (`EDH_SIMULATIONS_PATH` in `workstations/simulationsTab.ts`): `WorkstationsAndSimulations` selects the tab with `useMatch(EDH_SIMULATIONS_PATH)` and switches it with `navigate(path, { replace: true })`, so the side navigation can link to and highlight either tab and a tab can be deep-linked. Both routes render the same lazy page component, so a tab switch keeps the page mounted. Keep the route paths in `addonRoutes.tsx` and the constants in `simulationsTab.ts` equal; `edhLanes.test.tsx` checks them.

---

## API Layer

The module keeps its own `api/` directory (`web/CLAUDE.md` Rule 3 lists an add-on module's `api/` as a service directory): the services import the module's `types.ts` and the orchestration client's `toTuple`, which `services/addons/` could not do without the services layer depending on a feature module. Components never import `apiClient`.

-   The callers return `[boolean, data]` tuples through `toTuple`; the EDH mutations use `toOutcomeTuple` from `outcome.ts`, which keeps the status and body a page reads for an unconfirmed outcome (`EdhRequestError`).
-   The query hooks use the module-local `qk` factory (keys under `"edh"`, never colliding with the orchestration module's). `computeWorkstationRefetchInterval` polls every `WORKSTATION_POLL_TRANSITIONAL_MS` while a listed workstation is still changing state (it carries an `activity`, is CREATING and not a stalled start (`isStartStalled`), or is ACTIVE and not yet connectable), every `WORKSTATION_POLL_STABLE_MS` once all are settled (EDH can still stop one on idle), and not at all for an empty list. A start's 202 record replaces its row in the cached list at once (without its `sessionBootstrap`), so the badge reads Starting before the next poll.

---

## Launch Failures

A failed launch shows, beneath its **Orphaned** badge, the sentence `describeLastError` gives for its `lastError` code (`LAST_ERROR_COPY`; the user guide and the overview quote each sentence, and `infra/test/addon/edh/docs/edhWorkstationsDocs.test.ts` checks them). When the row also carries `edhFailure` (the AWS error or setup checkpoint VAMS captured from EDH), `EdhFailureDetail` adds one more line of text: `AWS: <errorCode> (<operation>)`, `AWS: <errorCode>`, or `Desktop setup failed at <checkpoint>`, as `describeEdhFailure` formats it. A row whose `edhFailure` is null or absent, or names neither a code nor a checkpoint, gets no line. My Workstations and the admin **Sessions** tab (both views) render the same component, so the two pages always agree. EDH's own status text never reaches the web: the service stores only the parsed fields.

---

## Start Status

A start answers `202` and runs in the launch worker; the record carries `activity` (`starting` | `stopping` | null), which the service derives on every read, and `startFailure`.

-   **Badge.** `WorkstationStatusBadge` reads `activity` first: **Starting** (the creating colours) or **Stopping** (the stopped colours), both with the pulsing dot, whatever `state` says. A record without the field (`undefined`, a service that predates it) falls back to the old rule (ACTIVE and not connectable, or CREATING with a session, read Starting); `null` is the service saying neither, so the state decides.
-   **Actions.** While `activity` is `starting`, Start, Stop and Delete are disabled with `A start of this workstation is in progress.` (the same hint the client-side in-flight claim gives). Stopping keeps Start disabled through the `The workstation is still <edhState>; …` hint.
-   **Refused start.** `describeStartFailure` gives, for a STOPPED row with no `activity` whose `lastError` is `CAPACITY_UNAVAILABLE`, `RESERVATION_FULL`, `RESERVATION_ENDED` or `START_FAILED`, the sentence (`capacityStartFailureMessage` naming the instance type and `startFailure.availabilityZone`; `RESERVATION_FULL_START_MESSAGE` from `outcome.ts`; `LAST_ERROR_COPY.RESERVATION_ENDED` or `LAST_ERROR_COPY.START_FAILED`) and the detail line `AWS: <errorCode> in <availabilityZone>` (the code alone without a zone, nothing without a code). `StartFailureNotice` renders both beneath the badge on My Workstations and the admin Sessions tab, and the badge takes the sentence as its title. A new start of the row (`activity` `starting`) hides them.
-   **Stalled start (D16).** `isStartStalled` (`labels.ts`) is true for a CREATING row with no `activity` whose `lastError` is `START_STALLED`: the session sweep found EDH still `pending` 30 minutes after it accepted the start. The badge reads **Start stalled** (warning colours, `⚠`, no pulsing dot); `describeStartFailure` gives `LAST_ERROR_COPY.START_STALLED` and no detail line, which `StartFailureNotice` shows on both pages; on My Workstations Stop and Delete are enabled, Start stays disabled (`Only a stopped workstation can be started.`) and Connect inert; the row does not hold the list at the fast poll.
-   **Start handler.** A 202 is reported as **Workstation start requested** and left to the list; the start path no longer maps `RESERVATION_FULL` (the launch path still does).
-   **CLI parity.** `workstationStatusCases.json` lists records of every state x activity x lastError combination with the badge label, the CLI State column (the label's last word upper-cased, so **Start stalled** is `STALLED`), and the sentence and detail line both clients show. `components/WorkstationStatusBadge.cases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_start_status.py` both read it; change the words in both clients and the file together.

---

## Image Name

A workstation record carries `imageId` and, from the service, `imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion` (snapshotted at launch, or resolved at read from the recipe builds for an older row), `latestRecipeVersion` and `imageLatest` (derived on every read); each is null when not known.

-   **Name.** `workstationImageName(row, catalogName)` gives `<recipeDisplayName> v<recipeVersion>` when both are present, else `imageName`, else the name `useEdhCatalogImages` lists for the id, else the id. The record wins over the catalog, so a workstation launched from a build the catalog has since retired keeps its name, also for a caller who may not read the catalog. `WorkstationImageCell` shows the name with the id beneath it (the id once when nothing names the image), and the toolbar search matches the name and the id.
-   **Badge.** `newerImageNotice` returns `Newer image: v<latestRecipeVersion>` and the sentence `This workstation runs v<recipeVersion> of <recipe>. v<latest> is the latest build. A workstation keeps the image it was launched from; launch a new workstation to use the latest build.` only for `imageLatest === false` on ACTIVE, STOPPED or CREATING; never for `true`, `null` or absent, nor on DELETED or ORPHANED. The cell renders the words as the badge's text, the sentence as its `title` and, through `aria-describedby`, as an `sr-only` description. The admin Sessions tab has no image column and shows none of this.
-   **CLI parity.** `workstationImageCases.json` lists records with the name both clients show, the web's name with a catalog name, the badge and tooltip, the CLI Image column (the name plus ` (v<n> available)` where the web badges) and the `get` `Recipe:` and `Recipe version:` values. `components/WorkstationImageCell.cases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_workstation_image.py` both read it; change the words in both clients and the file together.

---

## Capacity Reservations

An administrator registers EC2 capacity reservations; a launch may name one. With none registered, or none the caller's role grants (`edhCapacityReservation`), nothing on either page changes and every launch is on-demand.

-   **Launch wizard.** `useEdhCatalogCapacityReservations(open)` reads `GET /addon/edh/catalog/capacity-reservations` once, unfiltered; `matchingReservations` keeps the reservations whose `instanceType` equals the chosen type and whose `instancePlatform` suits the image's `osFamily` (`Linux/UNIX` prefix for linux, `Windows` for windows), the service's own rule. It also leaves out a reservation whose `edhSubnetAvailable` is `false` (EDH has no private subnet in its Availability Zone; the service lists none and refuses a launch into one); `null` (unknown) stays. The **Capacity** select after **Instance type** shows only when one matches: `On-demand` plus `<label> (<free>/<total> free)`, `(full, will launch on-demand)` for a full one that falls back, a disabled `(full)` for one that does not, and a disabled `<label> (view only)` for one whose `launchAllowed` is `false` (the role may see it but not launch into it; absent counts as allowed). While a view-only option is listed the hint adds `VIEW_ONLY_HINT`. The choice is `null` until the user picks (the first reservation with a free slot the role may launch into is then preselected, else On-demand; a view-only one never is); a change of instance type or image clears a chosen reservation that no longer suits it, never an explicit On-demand. Review shows **Capacity** only when the field was offered. The launch body carries `capacityReservationId` only for a reservation, so an on-demand body is the one it always was. A read the route refuses or fails hides the field.
-   **Refusals.** `createWorkstation` maps a `409` coded `RESERVATION_FULL` to `RESERVATION_FULL_LAUNCH_MESSAGE` (shown on Review; `isReservationFull` in `outcome.ts`). A start answers `202`, and a start refused for a full reservation comes back on the row as `lastError` `RESERVATION_FULL`, shown with `RESERVATION_FULL_START_MESSAGE` (see Start Status). Every other refusal (not registered, not active, no EDH subnet in the zone, type or platform mismatch, the `403` coded `RESERVATION_NOT_PERMITTED` for a view-only reservation, the `503` read failure) shows the service's message. `LAST_ERROR_COPY.RESERVATION_FULL` explains an orphaned launch. A start of a stopped row whose reservation has ended runs on-demand (the service switches the instance first); when that switch fails the row comes back STOPPED with `lastError` `RESERVATION_ENDED`, shown with `LAST_ERROR_COPY.RESERVATION_ENDED` (a string literal there: the infra docs test reads the map's literals).
-   **Rows.** `CapacityDetail` puts `Capacity: <label> (<id>)` (the id alone once unregistered), `Capacity: On-demand (reservation full)` (`capacityReservationFallbackReason` `full`, or absent on an older fallback row), or `Capacity: On-demand (reservation <label or id> ended)` (reason `ended`: the label, else `capacityReservationRequested`, else `On-demand (reservation ended)`) beneath the instance type, on My Workstations and the admin Sessions tab; an on-demand row shows nothing.
-   **Settings.** `CapacityReservationsSection` sits below the lease form, shown on `can("GET", CAPACITY_RESERVATIONS_ROUTE)`; Register on `POST` of that route, Edit on `PUT` and Unregister on `DELETE` of `CAPACITY_RESERVATION_ROUTE`. The dialog checks the id (`^cr-[0-9a-f]{17}$`) and label (1-64 display-name characters) before any request and keeps a refusal inside it; Edit sends `label`, `fallbackToOnDemand` and `enabled`, never the id; Unregister confirms first and says EC2 is not changed. A row whose `edhSubnetAvailable` is `false` shows `No EDH subnet in <az>` (`edhSubnetNote`) beneath its State, in the warning colours with `⚠`.
-   **CLI parity.** `capacityDisplayCases.json` lists workstation records with the Capacity detail both clients show, and catalog options for one launch with the wizard's option text (null when not offered) and disabled flag, the preselected option, the Settings State cell and note, and the CLI catalog's STATE and LAUNCH cells. `capacityDisplayCases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_capacity_display.py` both read it (the MCP tests pass its items through); change the words in both clients and the file together.

---

## Content Security Policy

The add-on contributes no CSP source: the DCV desktop opens in a new tab, not in a frame.

---

## Tests

-   Unit tests sit beside their sources. `edhLanes.test.tsx` covers the lane entries end to end: each route is lazy, parameterless and highlights its link; the Simulations route renders the Workstations page and stays behind the web-route check; both sections sit after "User", follow the switches (also when they arrive after mount) and the web-route check. `addons/addonFeatures.test.ts` checks the switch names against `ADDON_APP_FEATURES`.
-   `jest.mock` paths into the orchestration module are `../../../features/orchestration/...` from a subdirectory of this module.
-   Playwright: `web/e2e/edh.workstations.spec.ts` and `web/e2e/edh.admin.spec.ts` are core specs that skip, naming the feature list, without `EDH_WORKSTATIONS`; `gotoEdh(page, route, heading)` in `web/e2e/support/edhFixtures.ts` opens a page and waits on its heading. Launching, stopping or deleting a desktop belongs in an untracked ad-hoc spec (`web/e2e/CLAUDE.md` Rule 2).
-   Some infra docs tests read files of this module by path (`grep -rn "web/src/addons/edh" infra/test`); moving a file means updating them.
