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
-   Verify the `objectType` documentation (`docs/api/auth.md`, `docs/concepts/permissions-model.md`, the `objectType` schemas in `VAMS_API.yaml`, and the other pages in the "New permission object type or constraint field" row in `documentation/CLAUDE.md`) against the core object types in `backend/backend/common/constants.py` (`CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`) plus `common/addonConstants.py`, and confirm it states that a deployment can register more
-   Check for new endpoints not yet documented — remember the API is documented in **two** places (`VAMS_API.yaml` and the `docs/api/<domain>.md` pages) and both must agree

**AWS Resources** (`docs/architecture/aws-resources.md` and `docs/deployment/uninstall.md`):

-   Compare DynamoDB tables against `infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts`
-   Verify Lambda function list against `infra/lib/lambdaBuilder/` files
-   Verify each storage resource/log group documents its removal policy (RETAIN vs DESTROY) and whether it has a custom/explicit name (redeploy-collision flag)

**CLI Command Reference** (`docs/cli/command-reference.md` and `docs/cli/commands/` pages):

-   Compare command groups against `tools/VamsCLI/vamscli/main.py` registered commands
-   Verify subcommands against each command file in `tools/VamsCLI/vamscli/commands/`
-   Verify documented endpoint paths against `tools/VamsCLI/vamscli/constants.py`

**Viewer Plugins** (`docs/additional/viewer-plugins.md`, `docs/concepts/viewers.md`):

-   Compare against `web/src/visualizerPlugin/config/viewerConfig.json` plus `addonViewerConfig.json` (the add-on and licensed viewers), and check that enable instructions for a licensed viewer name `addonViewerConfig.json`

**Feature Flags** (`docs/overview/features.md`):

-   Compare against `infra/common/vamsAppFeatures.ts` plus `infra/common/addonAppFeatures.ts`

**Extension lanes** (`docs/developer/customizing-vams.md`, `docs/developer/addons-overview.md`):

-   Compare the lane table in "How the Extension Points Work" against the core, add-on and custom files that exist (each named file is present)
-   Compare the add-on table against `infra/lib/nestedStacks/addon/` and the entries in `addonViewerConfig.json`

**Sidebar** (`sidebars.ts`):

-   Verify every doc file in `docs/` has a corresponding sidebar entry

4. Report findings: what's accurate, what's outdated, what's missing
5. Fix any discrepancies found
6. Rebuild and verify: `cd documentation/docusaurus-site && npm run build`
