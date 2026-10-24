Verify VAMS documentation accuracy against source code.

## Instructions

1. Build the documentation to check for errors: `cd documentation/docusaurus-site && npm run build 2>&1`
2. Check for broken links in the build output (warning/error lines)
3. Cross-reference key documentation pages against source code:

### Verification checklist:

**Configuration Reference** (`docs/deployment/configuration-reference.md`):

-   Compare every field against `infra/config/config.ts` ConfigPublic interface
-   Verify default values match
-   Check for new config fields not yet documented

**API Reference** (`docs/api/` pages and `documentation/VAMS_API.yaml`):

-   Compare endpoints against the master route list in `backend/backend/common/apiRoutes.py` (`ALL_API_ROUTES`, which splices in `common/addonApiRoutes.py` `ADDON_ROUTES` and the fork's `custom/customApiRoutes.py` `CUSTOM_ROUTES`, empty upstream) and the route registrations, which are every `attachFunctionToApi(` call under `infra/lib/` plus the two `registry.register(` calls in `infra/lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct.ts` that register the anonymous `/api/amplify-config` and `/api/version` (`grep -rlE "attachFunctionToApi\(|registry\.register\(" infra/lib` lists the files; `apiRouteRegistry.ts` is the helper's definition). Besides the two API builder stacks, the search stack and add-on stacks such as the Physna one register their own routes, and an add-on's routes are registered only when that add-on is enabled. `infra/test/api/apiRouteBackendCdkParity.test.ts` checks the synthesized REST API against `apiRoutes.py` in both directions
-   Verify request/response models against `backend/backend/models/` Pydantic classes (including `models/addon/`)
-   Verify the `objectType` documentation (`docs/api/auth.md`, `docs/concepts/permissions-model.md`, the `objectType` schemas in `VAMS_API.yaml`) against the core object types in `backend/backend/common/constants.py` (`CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`), and confirm it states that a deployment can register more; the add-on types in `common/addonConstants.py` are checked against each add-on's own pages
-   Check for new endpoints not yet documented — remember the API is documented in **two** places (`VAMS_API.yaml` and the `docs/api/<domain>.md` pages) and both must agree

**AWS Resources** (`docs/architecture/aws-resources.md` and `docs/deployment/uninstall.md`):

-   Compare DynamoDB tables against `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`
-   Verify Lambda function list against `infra/lib/lambdaBuilder/` files
-   Verify each storage resource/log group documents its removal policy (RETAIN vs DESTROY) and whether it has a custom/explicit name (redeploy-collision flag)
-   Check an add-on's tables, Lambdas and other resources against its own resources and uninstall pages instead; `aws-resources.md` ("Add-on Resources") and `uninstall.md` ("Pre-uninstall: add-ons") only point to them

**CLI Command Reference** (`docs/cli/command-reference.md` and `docs/cli/commands/` pages):

-   Compare command groups against `tools/VamsCLI/vamscli/main.py` registered commands
-   Verify subcommands against each command file in `tools/VamsCLI/vamscli/commands/`
-   Verify documented endpoint paths against `tools/VamsCLI/vamscli/constants.py`
-   An add-on command group (`ADDON_COMMANDS` in `vamscli/commands/addon/`) has one `command-reference.md` row; check its subcommands, and the paths in `vamscli/addon_constants.py`, against the add-on's own CLI pages

**Viewer Plugins** (`docs/additional/viewer-plugins.md`, `docs/concepts/viewers.md`):

-   Compare against `web/src/visualizerPlugin/config/viewerConfig.json` plus `addonViewerConfig.json` (the add-on and licensed viewers), and check that enable instructions for a licensed viewer name `addonViewerConfig.json`

**Feature Flags** (`docs/overview/features.md`):

-   Compare against `infra/common/vamsAppFeatures.ts`. Check the add-on switches in `infra/common/addonAppFeatures.ts` against each add-on's own pages; a row the core table already carries for an add-on stays as it is

**Extension lanes** (`docs/developer/customizing-vams.md`, `docs/developer/addons-overview.md`):

-   Compare the lane table in "How the Extension Points Work" against the core, add-on and custom files that exist (each named file is present)
-   Compare the add-on table against `infra/lib/nestedStacks/addon/` and the entries in `addonViewerConfig.json`

**Add-on pages** (the "New or reclassified add-on" row of `documentation/CLAUDE.md`):

-   For each add-on under `infra/lib/nestedStacks/addon/`, its own pages exist in the sidebar's Add-ons category and cover what it adds; core pages carry the add-on only in the registry and rule-mandated entries that row lists, and a core user-guide page describes no add-on page and links at most once, without naming an add-on, to `docs/developer/addons-overview.md`
-   Run `cd infra && npx jest test/docs` for the core placement guard (`coreDocsAddonPlacement.test.ts`), and `npx jest test/addon/<addon>/docs` for an add-on that has docs guard tests

**Diagrams** (convention in `documentation/CLAUDE.md` Writing Style item 10):

-   Every `documentation/diagrams/source/drawio/<key>.drawio` is uncompressed XML (contains `<mxGraphModel>`) and has a PNG export with identical bytes in `documentation/diagrams/`, `documentation/docusaurus-site/static/img/` and `documentation/docusaurus-site/docs/assets/images/diagrams/`
-   Each diagram is embedded as `![Alt text](/img/<key>.png)` inside the section it illustrates, ahead of that section's subsections and of any Mermaid block in it (directly under the heading, or after the text that introduces the section), and still shows what that section describes
-   A feature's all-up overview diagram follows the main VAMS architecture diagram (large icons and labelled flows, no numbered badges or description panel) and is embedded where the feature is introduced, while its detail diagrams carry the badges and panel and sit in the sections they illustrate; a `<key>_dark` black-background variant has the overview's cells and is embedded nowhere
-   A feature diagram's `AWS VAMS (Existing)` box holds only resources every VAMS deployment already has; what the feature adds sits outside it, an integrated external system has its own box, and icon labels are a service name and a short functional name without handler, resource or configuration names

**Sidebar** (`sidebars.ts`):

-   Verify every doc file in `docs/` has a corresponding sidebar entry

4. Report findings: what's accurate, what's outdated, what's missing
5. Fix any discrepancies found
6. Rebuild and verify: `cd documentation/docusaurus-site && npm run build`
