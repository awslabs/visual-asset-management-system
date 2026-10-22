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
                  #   Duplicate opens the form prefilled from any recipe) SettingsTab (the lease policy, GET/PUT /addon/edh/admin/settings)
  api/            # workstations.ts catalog.ts admin.ts containers.ts amiRecipes.ts (the apiClient callers)
                  #   queries.ts adminQueries.ts containerQueries.ts amiRecipeQueries.ts (TanStack hooks, module-local qk, polling cadence)
                  #   outcome.ts (EdhRequestError, toOutcomeTuple, unconfirmedNotice: a mutation EDH did not confirm, a start in progress)
  components/     # WorkstationStatusBadge.tsx DeleteWorkstationDialog.tsx TabStrip.tsx (roving-tabindex tablist shared by both pages)
                  #   EdhFeatureTabs.tsx NotEnabledPanel.tsx (both pages' "not enabled" notice) useTextFilter.ts (the toolbar search
                  #   the three EDH lists share) useInFlight.ts (the synchronous double-submit guard every EDH mutation handler claims)
  workstations/   # WorkstationsPage.tsx (one h1 above the Workstations / Simulations strip; the Simulations tab is selected from the
                  #   path; Remaining column and Renew lease action driven by useEdhCatalogLease and labels.ts leaseRemaining)
                  #   simulationsTab.ts (the page's route and the Simulations route beneath it)
                  #   LaunchWorkstationWizard.tsx (optional `Expires in (hours)` field while the lease policy is enabled and not
                  #   forced; offers only the instance types that run the chosen image's `architecture` by the catalog rule and that
                  #   its `launchInstanceTypes` names when it carries one, clears a type the image chosen next does not run or
                  #   offer, and shows the architecture on the image card and in Review)
                  #   ConnectLink.tsx (click-driven: opens the tab, then requests the URL) WindowsSignInDialog.tsx (the owner's
                  #   Windows user name and masked password, fetched on open through a non-cached mutation and reset on close;
                  #   opened from the row's Show Windows password action)
  containers/     # SimulationsTab.tsx: container catalog, job submit/list/kill per backend (EDH_CONTAINERS; eks experimental);
                  #   containerRoutes.ts (route templates)
  types.ts        # API shapes
  labels.ts       # The words shown for the OS family and EDH state tokens
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
-   The query hooks use the module-local `qk` factory (keys under `"edh"`, never colliding with the orchestration module's). `computeWorkstationRefetchInterval` polls every `WORKSTATION_POLL_TRANSITIONAL_MS` while a listed workstation is still changing state, every `WORKSTATION_POLL_STABLE_MS` once all are settled (EDH can still stop one on idle), and not at all for an empty list.

---

## Content Security Policy

The add-on contributes no CSP source: the DCV desktop opens in a new tab, not in a frame.

---

## Tests

-   Unit tests sit beside their sources. `edhLanes.test.tsx` covers the lane entries end to end: each route is lazy, parameterless and highlights its link; the Simulations route renders the Workstations page and stays behind the web-route check; both sections sit after "User", follow the switches (also when they arrive after mount) and the web-route check. `addons/addonFeatures.test.ts` checks the switch names against `ADDON_APP_FEATURES`.
-   `jest.mock` paths into the orchestration module are `../../../features/orchestration/...` from a subdirectory of this module.
-   Playwright: `web/e2e/edh.workstations.spec.ts` and `web/e2e/edh.admin.spec.ts` are core specs that skip, naming the feature list, without `EDH_WORKSTATIONS`; `gotoEdh(page, route, heading)` in `web/e2e/support/edhFixtures.ts` opens a page and waits on its heading. Launching, stopping or deleting a desktop belongs in an untracked ad-hoc spec (`web/e2e/CLAUDE.md` Rule 2).
-   Some infra docs tests read files of this module by path (`grep -rn "web/src/addons/edh" infra/test`); moving a file means updating them.
