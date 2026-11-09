# CLAUDE.md - EDH Workstations Add-on (web module)

Auto-loaded when working within `web/src/addons/edh/`. The web half of the EDH Workstations add-on: its pages, components and API layer, and its entries in the add-on web lanes. See `web/CLAUDE.md` for the frontend-wide steering (section 7.5 for the lanes) and `.kiro/steering/ADDON_EDH_WORKSTATIONS.md` for the Kiro mirror (root `CLAUDE.md` Rule 11).

---

## Module Layout

The module is built with **Tailwind + Radix** and reuses the orchestration primitives: `features/orchestration/components/*` (DataTable, Dialog, ContextMenu, RowActionsButton, RefreshButton, SearchInput, StatusBadge, Stepper, ConfigEditor, controlStyles, ToastProvider, BusyControls, useReturnFocus), `permissions/useAllowedRoutes`, the `api/client` helpers `toTuple` and `unwrapMessage`, and `types`. The dependency runs one way: the module imports core, and core reaches the module only through the add-on lane files. Every row menu's trigger is a `RowActionsButton` labelled `Actions for <name>`, and every toolbar `SearchInput` carries a placeholder that names what it searches (`Search workstations`, `Search sessions`, `Search images`, `Search recipes`). Every EDH dialog passes the shared Dialog the `returnFocusRef` of the core `useReturnFocus` (`features/orchestration/components/useReturnFocus`), which notes the element that had focus as the dialog opened and returns focus there as it closes; the confirmations of the admin tabs and the Simulations tab are `admin/ConfirmActionDialog`, which uses it. A caller picks another element through `returnFocusTo`: a dialog opened from a row menu returns to the row's trigger, marked with `rowActionsFor(<row id>)` and found by `rowActionsTrigger` once the menu item has closed with its menu, and a dialog that removed a row or its button (a delete, a deactivation, an unregister, a retire) or added a row (a launch, a created recipe, a registered image or reservation, a submitted job) returns to a control the list keeps (`controlNamed`: the search box, or the refresh button of a list without one). The row a removing action removed, or may have removed when EDH did not confirm it, is noted in `useRemovedRowRef(<the dialog's target>)`, which clears the note each time the dialog opens, so a dialog reopened on a row the last action left listed returns to the row's trigger. A chosen control that is disabled as the dialog closes, as a refresh button is while its list is read, gives way to the opener; the AMI recipe and build mutations, like the reservation, workstation and container job mutations, resolve once the lists they changed are read again, so the Builds panel's refresh button is enabled as a retire closes. While an EDH dialog's request is in flight the dialog stays open: Escape, Cancel, the close button and a click outside close it only once the request is answered, so focus returns to the target the answer picks: each such dialog passes the shared Dialog's `busy` the same flag its buttons carry, which also gives the close button `aria-disabled` and the busy look meanwhile (the Dialog cancels a mouse press on its backdrop, so a click outside never moves focus to the page body). The held dialogs are the workstation Delete confirmation, the launch wizard, `admin/ConfirmActionDialog` (and so every admin and Simulations confirmation, the Builds panel's nested Retire included), the image registration, the AMI recipe form, the reservation form and the Submit job dialog; their tests share `expectHeldOpen` (`components/heldDialogTestSupport.ts`). The control that sent a request (each confirm and submit button, Retry registration, Connect, the Settings tab's Save) and the Cancel and fields beside it are `BusyButton` and `BusyInput` of the core `BusyControls` (`features/orchestration/components/BusyControls`): while busy they carry `aria-disabled="true"` rather than `disabled`, from which the browser would move focus to the page body, keep focus, ignore a click, Enter or Space, and the button reads a working label naming the action (`Deleting...`, `Starting build...`, `Saving...`); `disabled` stays for a control unavailable for another reason (no permission, an invalid step). A Retry registration button that leaves the Builds panel with focus on it, as it does once the re-read a retry waits for lists the build as Registering, hands focus to the panel's refresh button. A list whose toolbar search or filters match none of its rows shows `components/NoMatches` (`No <things> match "<text>".`) beneath the table's header row and in place of the cards; a list with no rows keeps its own empty state. The count beside the search of the Sessions, Images and AMI Recipes lists reads `<shown> of <total> <things>` while the search or filters hide rows, and `<total> <things>` otherwise (`countText` in `components/useTextFilter`).

```
web/src/addons/edh/
  pages/          # Route shells: EdhWorkstationsPage.tsx (renders workstations/WorkstationsPage, title "Workstations") and
                  #   EdhAdminPage.tsx (renders admin/EdhAdminPage, title "Workstation Admin"); keep page logic out of them
  admin/          # EdhAdminPage.tsx (Sessions / Capacity / Reservations / Images / AMI Recipes / Settings tabs, opening on the
                  #   first one the caller can read, each panel gated by useAllowedRoutes().can()) ImagesTab (kind, OS and
                  #   architecture filters with the search) InstancesTab (the Sessions panel: one Status column with the EDH
                  #   state beneath the badge, the owner's name with the EDH user id behind CopyIdButton, the session id's first
                  #   8 characters with CopyIdButton; below WORKSTATION_TABLE_MIN_WIDTH_PX one card per session, 10 per page as
                  #   the table pages) CapacityTab (the capacity dashboard, see Capacity Reservations; each
                  #   profile's allowed instance types as a count per architecture that expands into the types by family,
                  #   instanceTypesByFamily) ReservationsTab (GET /addon/edh/admin/capacity-reservations: the registry table with
                  #   Register / Edit / Unregister, each gated on its own route) reservationDisplay.ts (the reservation routes,
                  #   the tabs' description and empty state, and the count rules both tables share) RegisterImageDialog
                  #   ConfirmActionDialog CopyIdButton AmiRecipesTab AmiRecipeFormDialog AmiBuildsPanel (the AMI Recipes tab,
                  #   EDH_AMI_RECIPES; Duplicate opens the form prefilled from any recipe; Build now also sits beside the
                  #   newer-than-latest-build marker, and both open a ConfirmActionDialog naming the recipe before a build
                  #   starts) SettingsTab (the Workstation leases group, GET/PUT /addon/edh/admin/settings;
                  #   a policy SYSTEM_USER wrote reads "Set at deployment on <date>")
  api/            # workstations.ts catalog.ts admin.ts containers.ts amiRecipes.ts (the apiClient callers)
                  #   queries.ts adminQueries.ts containerQueries.ts amiRecipeQueries.ts (TanStack hooks, module-local qk, polling cadence)
                  #   outcome.ts (EdhRequestError, toOutcomeTuple, unconfirmedNotice: a mutation EDH did not confirm, a start in progress;
                  #   isReservationFull and the RESERVATION_FULL launch and start sentences)
  components/     # WorkstationStatusBadge.tsx (also describeLastError, describeEdhFailure and EdhFailureDetail, see
                  #   Launch Failures; describeStartFailure and StartFailureNotice, see Start Status) CapacityDetail.tsx
                  #   (the row's `Capacity:` line) DeleteWorkstationDialog.tsx TabStrip.tsx
                  #   (roving-tabindex tablist shared by both pages)
                  #   EdhFeatureTabs.tsx NotEnabledPanel.tsx (both pages' "not enabled" notice) useTextFilter.ts (the toolbar search
                  #   the EDH lists share, and countText) useInFlight.ts (the synchronous double-submit guard every EDH mutation handler claims)
                  #   useMinWidth.ts (whether the viewport is at least a width wide, following resizes)
                  #   heldDialogTestSupport.ts (tests only: expectHeldOpen, the close button and backdrop of a
                  #   dialog held open while its request is in flight)
                  #   NoMatches.tsx (the line a list shows when its search or filters match none of its rows)
                  #   RecordCards.tsx (WORKSTATION_TABLE_MIN_WIDTH_PX and the card list, card, fields and pager the narrow
                  #   layouts of My Workstations and the Sessions tab share)
                  #   WorkstationImageCell.tsx (My Workstations' Image cell: the name and the Newer image badge, see Image Name)
  workstations/   # WorkstationsPage.tsx (one h1 above the Workstations / Simulations strip; the Simulations tab is selected from the
                  #   path; Remaining column and Renew lease action driven by useEdhCatalogLease and labels.ts leaseRemaining;
                  #   below WORKSTATION_TABLE_MIN_WIDTH_PX (640) one card per workstation instead of the table;
                  #   the toolbar's `Updated <relative time>` caption (labels.ts updatedAgo), the poll cadence in the
                  #   refresh button's tooltip and accessible name)
                  #   simulationsTab.ts (the page's route and the Simulations route beneath it)
                  #   LaunchWorkstationWizard.tsx (optional `Expires in (hours)` field while the lease policy is enabled and not
                  #   forced; offers only the instance types that run the chosen image's `architecture` by the catalog rule and that
                  #   its `launchInstanceTypes` names when it carries one, clears a type the image chosen next does not run or
                  #   offer, and shows the architecture on the image card and in Review; see Launch Wizard Choices; a Capacity
                  #   select after Instance type,
                  #   see Capacity Reservations)
                  #   ConnectLink.tsx (click-driven: opens the tab, then requests the URL) OsSignInDialog.tsx (the owner's
                  #   desktop sign-in through useOsSignIn, a non-cached mutation fetched on open and reset on close: the
                  #   Windows user name and masked password, a directory account's user name alone, or Linux's "no
                  #   password is needed"; steps rendered per osFamily and credential; opened from the row's Show desktop
                  #   sign-in action on Windows and Linux rows with a session, enabled where Connect is)
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
-   **Actions.** The row menu offers Start only for a STOPPED row and Stop only for an ACTIVE one or a stalled start, and neither while `activity` is set; while it is `starting`, Delete is disabled with `A start of this workstation is in progress.` (the same hint the client-side in-flight claim gives). A STOPPED row EDH still reports in another state keeps Start disabled through the `The workstation is still <edhState>; …` hint. Delete stays last, and Show desktop sign-in stays with the reason connecting is unavailable as its disabled hint.
-   **Refused start.** `describeStartFailure` gives, for a STOPPED row with no `activity` whose `lastError` is `CAPACITY_UNAVAILABLE`, `RESERVATION_FULL`, `RESERVATION_ENDED` or `START_FAILED`, the sentence (`capacityStartFailureMessage` naming the instance type and `startFailure.availabilityZone`; `RESERVATION_FULL_START_MESSAGE` from `outcome.ts`; `LAST_ERROR_COPY.RESERVATION_ENDED` or `LAST_ERROR_COPY.START_FAILED`) and the detail line `AWS: <errorCode> in <availabilityZone>` (the code alone without a zone, nothing without a code). `StartFailureNotice` renders both beneath the badge on My Workstations and the admin Sessions tab, and the badge takes the sentence as its title. A new start of the row (`activity` `starting`) hides them.
-   **Stalled start.** `isStartStalled` (`labels.ts`) is true for a CREATING row with no `activity` whose `lastError` is `START_STALLED`: the session sweep found EDH still `pending` 30 minutes after it accepted the start. The badge reads **Start stalled** (warning colours, `⚠`, no pulsing dot); `describeStartFailure` gives `LAST_ERROR_COPY.START_STALLED` and no detail line, which `StartFailureNotice` shows on both pages; on My Workstations Stop and Delete are offered, Start is not, and Connect is inert; the row does not hold the list at the fast poll.
-   **Display session ended.** A record carrying `connectionUnavailableReason` `DISPLAY_SESSION_ENDED` (ACTIVE, running, not connectable, no `activity`) keeps its **Active** badge; `connectUnavailableReason` (`ConnectLink.tsx`) puts `DISPLAY_SESSION_ENDED_MESSAGE` under **Connect**, which stays clickable so the click asks EDH again (a brief broker gap clears itself; a session that really ended is refused with the same sentence), `isTransitionalWorkstation` treats the row as settled, and `useWorkstationConnect` re-reads the list after a refused connect so the row shows the reason. The case sits in `workstationStatusCases.json` with `connectionUnavailableMessage`, which the CLI's `get` prints as `Connection:`.
-   **Start handler.** A 202 is reported as **Workstation start requested** and left to the list; the start path no longer maps `RESERVATION_FULL` (the launch path still does).
-   **CLI parity.** `workstationStatusCases.json` lists records of every state x activity x lastError combination with the badge label, the CLI State column (the label's last word upper-cased, so **Start stalled** is `STALLED`), and the sentence and detail line both clients show. `components/WorkstationStatusBadge.cases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_start_status.py` both read it; change the words in both clients and the file together.

---

## Image Name

A workstation record carries `imageId` and, from the service, `imageName`, `recipeId`, `recipeDisplayName`, `recipeVersion` (snapshotted at launch, or resolved at read from the recipe builds for an older row), `latestRecipeVersion` and `imageLatest` (derived on every read); each is null when not known.

-   **Name.** `workstationImageName(row, catalogName)` gives `<recipeDisplayName> v<recipeVersion>` when both are present, else `imageName`, else the name `useEdhCatalogImages` lists for the id, else the id. The record wins over the catalog, so a workstation launched from a build the catalog has since retired keeps its name, also for a caller who may not read the catalog. `WorkstationImageCell` shows the name alone, and the id only when nothing names the image; the toolbar search matches the name and the id.
-   **Badge.** `newerImageNotice` returns `Newer image: v<latestRecipeVersion>` and the sentence `This workstation runs v<recipeVersion> of <recipe>. v<latest> is the latest build. A workstation keeps the image it was launched from; launch a new workstation to use the latest build.` only for `imageLatest === false` on ACTIVE, STOPPED or CREATING; never for `true`, `null` or absent, nor on DELETED or ORPHANED. The cell renders the words as the badge's text, the sentence as its `title` and, through `aria-describedby`, as an `sr-only` description. The admin Sessions tab has no image column and shows none of this.
-   **CLI parity.** `workstationImageCases.json` lists records with the name both clients show, the web's name with a catalog name, the badge and tooltip, the CLI Image column (the name plus ` (v<n> available)` where the web badges) and the `get` `Recipe:` and `Recipe version:` values. `components/WorkstationImageCell.cases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_workstation_image.py` both read it; change the words in both clients and the file together.

---

## Launch Wizard Choices

-   **Image groups.** `IMAGE_GROUPS` heads the image step's groups **VAMS desktops** (`category` `amiRecipe`) and **Standard images** (`baseline`); the step's legend is `sr-only`, since the stepper names the step. A recipe image's `v<recipeVersion>` badge shows only when `imageNameCarriesVersion` finds no `v<n>` word in its name. The card's description is the EDH stack's, into which the recipe's `description` is copied when a build is registered.
-   **Instance types.** `instanceTypeOptionLabel` (`labels.ts`) reads `<type> — <n> vCPU, <m> GiB` plus `, <count> × <GPU> (<gib> GiB)` per kind of GPU, and the bare type without EC2's facts. `groupInstanceTypes` puts the types with GPUs (`hasGpus`) under **GPU instance types** and the rest under **Other instance types**, each by family, then vCPUs, memory and name; types of one kind only are one list without a heading, and a list without facts is the catalog's name order. `preselectedInstanceType` takes the image's `defaultInstanceType` when the wizard offers it; it stands until the user chooses a type (`instanceTypeChosen`), and choosing an image that does not keep the chosen type gives way to that image's default. Review shows the option text.
-   **Name.** `defaultWorkstationName(image, existingNames)` gives `<workstationNameStem(image.name)>-<n>`: the name lower-cased, each run outside `[a-z0-9]` one hyphen, trimmed of hyphens and cut to `WORKSTATION_NAME_STEM_MAX` (40) characters; n is the lowest number from 1 that the caller's listed workstation names (`existingNames`, passed by My Workstations) do not take. It follows the image until the user types (`nameEdited`); validation is the request model's.
-   **Cancel.** The footer's **Cancel** shows on every step and calls `onClose`, as the dialog's X does.

---

## Capacity Reservations

An administrator registers EC2 capacity reservations; a launch may name one. With none registered, or none the caller's role grants (`edhCapacityReservation`), nothing on either page changes and every launch is on-demand.

-   **Launch wizard.** `useEdhCatalogCapacityReservations(open)` reads `GET /addon/edh/catalog/capacity-reservations` once, unfiltered; `matchingReservations` keeps the reservations whose `instanceType` equals the chosen type and whose `instancePlatform` suits the image's `osFamily` (`Linux/UNIX` prefix for linux, `Windows` for windows), the service's own rule. It also leaves out a reservation whose `edhSubnetAvailable` is `false` (EDH has no private subnet in its Availability Zone; the service lists none and refuses a launch into one); `null` (unknown) stays. The **Capacity** select after **Instance type** shows only when one matches: `On-demand` plus `<label> (<free>/<total> free)`, `(full, will launch on-demand)` for a full one that falls back, a disabled `(full)` for one that does not, and a disabled `<label> (view only)` for one whose `launchAllowed` is `false` (the role may see it but not launch into it; absent counts as allowed). While a view-only option is listed the hint adds `VIEW_ONLY_HINT`. The choice is `null` until the user picks (the first reservation with a free slot the role may launch into is then preselected, else On-demand; a view-only one never is); a change of instance type or image clears a chosen reservation that no longer suits it, never an explicit On-demand. Review shows **Capacity** only when the field was offered. The launch body carries `capacityReservationId` only for a reservation, so an on-demand body is the one it always was. A read the route refuses or fails hides the field.
-   **Refusals.** `createWorkstation` maps a `409` coded `RESERVATION_FULL` to `RESERVATION_FULL_LAUNCH_MESSAGE` (shown on Review; `isReservationFull` in `outcome.ts`). A start answers `202`, and a start refused for a full reservation comes back on the row as `lastError` `RESERVATION_FULL`, shown with `RESERVATION_FULL_START_MESSAGE` (see Start Status). Every other refusal (not registered, not active, no EDH subnet in the zone, type or platform mismatch, the `403` coded `RESERVATION_NOT_PERMITTED` for a view-only reservation, the `503` read failure) shows the service's message. `LAST_ERROR_COPY.RESERVATION_FULL` explains an orphaned launch. A start of a stopped row whose reservation has ended runs on-demand (the service switches the instance first); when that switch fails the row comes back STOPPED with `lastError` `RESERVATION_ENDED`, shown with `LAST_ERROR_COPY.RESERVATION_ENDED` (a string literal there: the infra docs test reads the map's literals).
-   **Rows.** `CapacityDetail` puts `Capacity: <label> (<id>)` (the id alone once unregistered), `Capacity: On-demand (reservation full)` (`capacityReservationFallbackReason` `full`, or absent on an older fallback row), or `Capacity: On-demand (reservation <label or id> ended)` (reason `ended`: the label, else `capacityReservationRequested`, else `On-demand (reservation ended)`) beneath the instance type, on My Workstations and the admin Sessions tab; an on-demand row shows nothing.
-   **Reservations tab.** `ReservationsTab` is the panel of the tab gated on `can("GET", CAPACITY_RESERVATIONS_ROUTE)`; Register on `POST` of that route, Edit on `PUT` and Unregister on `DELETE` of `CAPACITY_RESERVATION_ROUTE` (both in `reservationDisplay.ts`). It opens with `RESERVATIONS_DESCRIPTION`; with nothing registered it shows `NO_RESERVATIONS_TEXT` with the one Register button inside it, else Register in the header. The dialog checks the id (`^cr-[0-9a-f]{17}$`) and label (1-64 display-name characters) before any request and keeps a refusal inside it; Edit sends `label`, `fallbackToOnDemand` and `enabled`, never the id; Unregister confirms first and says EC2 is not changed. A row whose `edhSubnetAvailable` is `false` shows `No EDH subnet in <az>` (`edhSubnetNote`) beneath its State, in the warning colours with `⚠`. **Free / total** (`freeOfTotal`) shows EC2's counts only for an `active` reservation and `—` otherwise: EC2 keeps an expired or cancelled reservation's last counts.
-   **Capacity tab.** `CapacityTab` reads `GET /addon/edh/admin/capacity`, and also the admin sessions list and the registry where `can()` allows each (`ADMIN_INSTANCES_ROUTE`, `CAPACITY_RESERVATIONS_ROUTE`); a part whose route is denied is left out, and a failed read reports `Error loading <what>` in its own part. The cards are Total sessions, Running (sessions ACTIVE or with `activity` `starting`, `isRunningSession`), Distinct owners, Registered reservations and Reserved slots (`<in use> of <total> in use` across the active reservations, `reservedSlotsText`). Below: the sessions by EDH state; the reservations table (`Capacity reservation use`: **In use / total**, `inUseOfTotal`, total minus available, `—` unless active; **VAMS workstations**, the running sessions whose `capacityReservationId` names it) with **Manage reservations**, which selects the Reservations tab and focuses it; the running workstations by instance type with the active reservations of each type; and the virtual desktop profiles.
-   **CLI parity.** `capacityDisplayCases.json` lists workstation records with the Capacity detail both clients show; catalog options for one launch with the wizard's option text (null when not offered) and disabled flag, the preselected option, the Reservations State cell and note, the Free / total and In use / total cells, and the CLI catalog's STATE, FREE/TOTAL and LAUNCH cells; and `registryStates`, registered reservations in each state (active, expired, cancelled, notFound, unknown) with the same cells and `registryReservedSlots`. `capacityDisplayCases.test.tsx` and `tools/VamsCLI/tests/addon/test_edh_capacity_display.py` both read it (the MCP tests pass its items through); change the words in both clients and the file together.

---

## Content Security Policy

The add-on contributes no CSP source: the DCV desktop opens in a new tab, not in a frame.

---

## Tests

-   Unit tests sit beside their sources. `edhLanes.test.tsx` covers the lane entries end to end: each route is lazy, parameterless and highlights its link; the Simulations route renders the Workstations page and stays behind the web-route check; both sections sit after "User", follow the switches (also when they arrive after mount) and the web-route check. `addons/addonFeatures.test.ts` checks the switch names against `ADDON_APP_FEATURES`.
-   `jest.mock` paths into the orchestration module are `../../../features/orchestration/...` from a subdirectory of this module.
-   Playwright: `web/e2e/edh.workstations.spec.ts` and `web/e2e/edh.admin.spec.ts` are core specs that skip, naming the feature list, without `EDH_WORKSTATIONS`; `gotoEdh(page, route, heading)` in `web/e2e/support/edhFixtures.ts` opens a page and waits on its heading. Launching, stopping or deleting a desktop belongs in an untracked ad-hoc spec (`web/e2e/CLAUDE.md` Rule 2).
-   Some infra docs tests read files of this module by path (`grep -rn "web/src/addons/edh" infra/test`); moving a file means updating them.
